"""SQL DDL statements for the POSRAT ``system.sqlite`` database.

Each entry in :data:`MIGRATIONS` describes the SQL executed to move the
system database from version ``N-1`` to version ``N``. Version ``0`` is
the empty database. Step 10.1 introduces version ``1`` which only
creates the ``schema_version`` bookkeeping table itself; real domain
tables (``users``, ``user_exam_access``, ``exam_access_requests``) are
added in follow-up steps (10.2, 10.8).

The migration history is intentionally *separate* from
:mod:`posrat.storage.schema` — per-exam databases and the cross-cutting
system database evolve independently, so they use their own version
counters.
"""

from __future__ import annotations

# Mapping: target version -> SQL script executed to reach it.
# Keep statements idempotent-friendly (CREATE TABLE without IF NOT EXISTS
# is fine because the migration runner guards execution by the stored
# version).
MIGRATIONS: dict[int, str] = {
    # Step 10.1 — bookkeeping table only. Mirrors the bootstrap pattern
    # used by :mod:`posrat.storage.schema` so the system database starts
    # life with a known version marker before any domain tables are
    # added.
    1: """
    CREATE TABLE schema_version (
        version INTEGER NOT NULL
    );
    INSERT INTO schema_version (version) VALUES (0);
    """,
    # Step 10.2 — users table.
    # Columns:
    #   * username          — primary key, case-sensitive (reverse-proxy
    #                         integrations may forward lowercase-only so
    #                         the DAO normalises input before INSERT).
    #   * password_hash     — bcrypt hash for internal accounts; NULL
    #                         for proxy-provisioned accounts (which are
    #                         authenticated by the trusted header on
    #                         each request, not by a stored secret).
    #   * display_name      — free-text human-readable name (optional).
    #   * auth_source       — 'internal' | 'proxy' (CHECK enforced).
    #   * is_admin          — 0/1, gates /admin.
    #   * can_use_designer  — 0/1, gates /designer.
    #   * created_at        — ISO-8601 UTC timestamp.
    #   * last_login_at     — ISO-8601 UTC timestamp, NULL before first
    #                         successful authentication.
    # A "proxy account with a password_hash" combination is nonsensical
    # — the DAO and the model both guard against it. The SQL layer only
    # enforces the string-set for auth_source and leaves nuanced cross-
    # field invariants to Pydantic (mirrors the pattern already used for
    # sessions / exams).
    2: """
    CREATE TABLE users (
        username TEXT PRIMARY KEY,
        password_hash TEXT,
        display_name TEXT,
        auth_source TEXT NOT NULL
            CHECK(auth_source IN ('internal','proxy')),
        is_admin INTEGER NOT NULL DEFAULT 0
            CHECK(is_admin IN (0, 1)),
        can_use_designer INTEGER NOT NULL DEFAULT 0
            CHECK(can_use_designer IN (0, 1)),
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    CREATE INDEX idx_users_auth_source ON users (auth_source);
    """,
    # Step 10.8 — per-exam ACL tables.
    # Two tables, modelled separately so the Runner can distinguish
    # "approved access" (can take the exam) from "pending request"
    # (sees a disabled card with a "Requested" badge) in a single
    # join:
    #
    #   * ``user_exam_access`` — granted access. The ``is_paid`` column
    #     is a placeholder for a future monetisation hook (the user
    #     mentioned wanting a per-exam paywall down the road); today
    #     every row is inserted with ``0`` and admins ignore the field.
    #   * ``exam_access_requests`` — the candidate-initiated request
    #     queue. ``status`` is either ``pending``, ``approved``, or
    #     ``rejected``; admins flip it from the admin panel.
    #
    # Exam identifiers are stored as **plain TEXT** rather than a FK
    # to a per-exam database (there is no ``exams`` table in the
    # system DB — those live in per-exam ``.sqlite`` files). That
    # means cascade deletes cannot fire automatically when an admin
    # drops an exam file; :mod:`posrat.system.admin_exams` (step
    # 10.12) is responsible for scrubbing ACL rows by ``exam_id``
    # inside the same logical operation as the file delete.
    3: """
    CREATE TABLE user_exam_access (
        username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        exam_id TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        is_paid INTEGER NOT NULL DEFAULT 0 CHECK(is_paid IN (0, 1)),
        PRIMARY KEY (username, exam_id)
    );
    CREATE INDEX idx_user_exam_access_exam ON user_exam_access (exam_id);

    CREATE TABLE exam_access_requests (
        username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
        exam_id TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('pending','approved','rejected')),
        decided_at TEXT,
        decided_by TEXT,
        PRIMARY KEY (username, exam_id)
    );
    CREATE INDEX idx_exam_access_requests_status
        ON exam_access_requests (status);
    """,
    # Phase 11 step 1 — AI chat settings (singleton row).
    #
    # The AI chat panel (floating robot bottom-right + header button) is
    # gated by a single globally-configured LLM provider. POSRAT targets
    # AWS Bedrock via Strands Agents; credentials come from the
    # process-wide boto3 default chain (``AWS_PROFILE`` / env vars /
    # IMDS / SSO cache) so this table deliberately stores **no secrets**
    # — only the chooseable parameters (model id, region, system prompt,
    # MCP config).
    #
    # Singleton pattern: a ``CHECK(id = 1)`` constraint keeps the table
    # at most one row wide. ``INSERT OR REPLACE INTO ai_settings (id,
    # ...) VALUES (1, ...)`` in the DAO upserts it; the admin panel
    # renders the current row or a blank form if it's missing.
    #
    # ``mcp_config_json`` stores the Claude Desktop-compatible JSON
    # (``{"mcpServers": {...}}``) as a TEXT blob. Validation happens
    # in the application layer on save; the column is just a dumb
    # string so operators can paste snippets from aws-knowledge-mcp
    # docs verbatim.
    4: """
    CREATE TABLE ai_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
        model_id TEXT NOT NULL,
        region TEXT NOT NULL,
        system_prompt TEXT,
        mcp_config_json TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    # Phase 14 step 1 — per-user feature gating for AI chat + Runner
    # explanation visibility.
    #
    # Two independent flags added as ``NOT NULL DEFAULT 1`` so existing
    # rows upgrade in place *without* a visible change — everyone who
    # already has an account keeps both features. Admins can then turn
    # either one off per user from the ``/admin`` Users tab.
    #
    # ``can_use_ai_chat`` gates the floating AI FAB (Designer + Runner
    # training mode) and the smart-toy header button. The global
    # ``ai_settings.enabled`` flag still wins: if the admin turned AI
    # chat off system-wide, the per-user flag has no visible effect.
    #
    # ``can_see_explanation`` gates the "Explanation / reference" block
    # that the Runner shows (training wrong-answer feedback strip +
    # finished-session review cards). Designer editing is never
    # gated — exam authors must always see the field they are editing.
    5: """
    ALTER TABLE users ADD COLUMN can_use_ai_chat INTEGER NOT NULL
        DEFAULT 1 CHECK(can_use_ai_chat IN (0, 1));
    ALTER TABLE users ADD COLUMN can_see_explanation INTEGER NOT NULL
        DEFAULT 1 CHECK(can_see_explanation IN (0, 1));
    """,
}


