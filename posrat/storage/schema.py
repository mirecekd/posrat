"""SQL DDL statements grouped by schema version.

Each entry in :data:`MIGRATIONS` describes the SQL executed to move the
database from version ``N-1`` to version ``N``. Version ``0`` is the empty
database (no tables). Step 2.0 introduces version ``1`` which only creates
the ``schema_version`` bookkeeping table itself; real domain tables are
added in follow-up steps.
"""

from __future__ import annotations

# Mapping: target version -> SQL script executed to reach it.
# Keep statements idempotent-friendly (CREATE TABLE without IF NOT EXISTS is
# fine because the migration runner guards execution by the stored version).
MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE schema_version (
        version INTEGER NOT NULL
    );
    INSERT INTO schema_version (version) VALUES (0);
    """,
    2: """
    CREATE TABLE exams (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL
    );
    """,
    3: """
    CREATE TABLE questions (
        id TEXT PRIMARY KEY,
        exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
        type TEXT NOT NULL CHECK(type IN ('single_choice','multi_choice','hotspot')),
        text TEXT NOT NULL,
        explanation TEXT,
        image_path TEXT,
        order_index INTEGER NOT NULL
    );
    CREATE INDEX idx_questions_exam_order ON questions (exam_id, order_index);
    """,
    4: """
    CREATE TABLE choices (
        id TEXT PRIMARY KEY,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        is_correct INTEGER NOT NULL DEFAULT 0 CHECK(is_correct IN (0, 1)),
        order_index INTEGER NOT NULL
    );
    CREATE INDEX idx_choices_question_order ON choices (question_id, order_index);
    """,
    5: """
    CREATE TABLE hotspot_options (
        id TEXT PRIMARY KEY,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        order_index INTEGER NOT NULL
    );
    CREATE INDEX idx_hotspot_options_question_order
        ON hotspot_options (question_id, order_index);

    CREATE TABLE hotspot_steps (
        id TEXT PRIMARY KEY,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        prompt TEXT NOT NULL,
        correct_option_id TEXT NOT NULL,
        order_index INTEGER NOT NULL
    );
    CREATE INDEX idx_hotspot_steps_question_order
        ON hotspot_steps (question_id, order_index);
    """,
    6: """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
        mode TEXT NOT NULL CHECK(mode IN ('training','exam')),
        started_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX idx_sessions_exam_started
        ON sessions (exam_id, started_at);
    """,
    7: """
    CREATE TABLE answers (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        given_json TEXT NOT NULL,
        is_correct INTEGER NOT NULL CHECK(is_correct IN (0, 1)),
        time_ms INTEGER
    );
    CREATE INDEX idx_answers_session ON answers (session_id);
    CREATE INDEX idx_answers_question ON answers (question_id);
    """,
    # Step 8.5 — Designer Properties panel:
    #   * complexity: optional 1..5 difficulty rating (range enforced by
    #     the Pydantic model; SQLite only stores the int or NULL so
    #     legacy rows keep working without backfill).
    #   * section: optional free-text tag ("Compute", "IAM", ...).
    # ALTER TABLE ... ADD COLUMN is idempotent-friendly for fresh DBs
    # because the migration runner only executes this script when the
    # stored version is below 8 — old databases upgrade in place, new
    # databases get the columns as part of the fresh schema climb.
    8: """
    ALTER TABLE questions ADD COLUMN complexity INTEGER;
    ALTER TABLE questions ADD COLUMN section TEXT;
    """,
    # Step 8.5b — Allow shuffle choices:
    # Stored as INTEGER (0 / 1) because SQLite lacks a native boolean.
    # Default 0 so existing rows keep their deterministic choice order
    # without the Runner opt-in — matches the model's ``bool = False``
    # default.
    9: """
    ALTER TABLE questions ADD COLUMN allow_shuffle INTEGER NOT NULL DEFAULT 0;
    """,
    # Step 7A.2 — Runner-facing exam metadata:
    #   * default_question_count — default Runner sample size (prefill)
    #   * time_limit_minutes — default session timer budget
    #   * passing_score — raw-point passing threshold (e.g. 700)
    #   * target_score — raw-point "100 %" mark (e.g. 1000)
    # All four are nullable so legacy exams keep working without backfill
    # — the Runner will fall back to "take all questions, no timer, no
    # pass/fail" when any of them are NULL. Ranges are enforced by the
    # Pydantic model (Exam.default_question_count >= 1 etc.), SQLite just
    # stores the raw integers.
    10: """
    ALTER TABLE exams ADD COLUMN default_question_count INTEGER;
    ALTER TABLE exams ADD COLUMN time_limit_minutes INTEGER;
    ALTER TABLE exams ADD COLUMN passing_score INTEGER;
    ALTER TABLE exams ADD COLUMN target_score INTEGER;
    """,
    # Step 7B.2 — Session snapshot metadata (multi-user Runner):
    #   * candidate_name — who took the test (pinned at start)
    #   * question_count — how many questions were sampled for this
    #     session (can be < the exam's total)
    #   * time_limit_minutes / passing_score / target_score — snapshots
    #     of the exam's metadata at session start. Pinning them on the
    #     session row means later Designer edits to the source exam
    #     cannot retroactively change pass/fail rules of already-started
    #     or completed sessions.
    # All five are nullable so legacy sessions keep loading without
    # backfill; the Runner treats NULLs as "sensible defaults" (no
    # candidate name, no cap, no timer, no pass/fail).
    11: """
    ALTER TABLE sessions ADD COLUMN candidate_name TEXT;
    ALTER TABLE sessions ADD COLUMN question_count INTEGER;
    ALTER TABLE sessions ADD COLUMN time_limit_minutes INTEGER;
    ALTER TABLE sessions ADD COLUMN passing_score INTEGER;
    ALTER TABLE sessions ADD COLUMN target_score INTEGER;
    """,
}




