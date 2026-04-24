"""Tests for the SQLite migration runner (step 2.0)."""

from __future__ import annotations

import sqlite3

from posrat.storage import CURRENT_SCHEMA_VERSION, apply_migrations


def _open_memory_db() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def test_apply_migrations_bootstraps_schema_version() -> None:
    """Fresh DB gets the schema_version table and correct version number."""
    db = _open_memory_db()
    try:
        version = apply_migrations(db)
        assert version == CURRENT_SCHEMA_VERSION

        stored = db.execute("SELECT version FROM schema_version").fetchone()
        assert stored is not None
        assert stored[0] == CURRENT_SCHEMA_VERSION
    finally:
        db.close()


def test_apply_migrations_is_idempotent() -> None:
    """Calling apply_migrations twice must not fail or duplicate rows."""
    db = _open_memory_db()
    try:
        first = apply_migrations(db)
        second = apply_migrations(db)
        assert first == second == CURRENT_SCHEMA_VERSION

        rows = db.execute("SELECT version FROM schema_version").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == CURRENT_SCHEMA_VERSION
    finally:
        db.close()


def test_apply_migrations_enables_foreign_keys_pragma() -> None:
    """Connection must have PRAGMA foreign_keys = ON after migration."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        pragma = db.execute("PRAGMA foreign_keys").fetchone()
        assert pragma is not None
        assert pragma[0] == 1
    finally:
        db.close()


def test_exams_table_exists_and_has_expected_columns() -> None:
    """Migration v2 (plus v10 extensions) must create ``exams`` with documented columns.

    v2 creates the core four columns (id/name/description/created_at).
    v10 adds four nullable metadata columns for the Runner — default
    question count, time limit, passing_score and target_score — so
    that legacy exams keep working while new exams can opt in.
    """

    db = _open_memory_db()
    try:
        apply_migrations(db)
        rows = db.execute("PRAGMA table_info(exams)").fetchall()
        columns = {row[1]: row for row in rows}
        assert set(columns) == {
            "id",
            "name",
            "description",
            "created_at",
            "default_question_count",
            "time_limit_minutes",
            "passing_score",
            "target_score",
        }

        # id is PRIMARY KEY, name and created_at are NOT NULL.
        assert columns["id"][5] == 1  # pk flag
        assert columns["name"][3] == 1  # notnull flag
        assert columns["created_at"][3] == 1
        # description + four v10 columns must all be nullable (legacy-safe).
        for nullable in (
            "description",
            "default_question_count",
            "time_limit_minutes",
            "passing_score",
            "target_score",
        ):
            assert columns[nullable][3] == 0, f"{nullable} must be nullable"
    finally:
        db.close()


def test_exams_table_accepts_null_runner_metadata() -> None:
    """Legacy-style INSERT (no v10 columns) must succeed with NULL values.

    The migration runner upgrades existing ``.sqlite`` files in place; the
    v10 columns are nullable so old INSERT statements without the new
    fields keep working. Downstream DAOs (``create_exam`` / ``get_exam``)
    will then read ``NULL`` → ``None`` into the Pydantic ``Exam`` model.
    """

    db = _open_memory_db()
    try:
        apply_migrations(db)
        db.execute(
            "INSERT INTO exams (id, name, description, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("legacy-exam", "Legacy", None, "2025-01-01T00:00:00Z"),
        )
        row = db.execute(
            "SELECT default_question_count, time_limit_minutes, "
            "passing_score, target_score FROM exams WHERE id = ?",
            ("legacy-exam",),
        ).fetchone()
        assert row is not None
        # All four Runner metadata columns default to NULL for legacy rows.
        assert tuple(row) == (None, None, None, None)
    finally:
        db.close()


def test_exams_table_accepts_runner_metadata_values() -> None:
    """Fresh INSERTs (post-v10) can persist all four metadata fields.

    This is the happy path for the upcoming ``create_exam`` DAO round-trip
    (step 7A.3): raw points (700 / 1000) plus 65-question / 90-minute
    defaults matching the Visual CertExam AIF-C01 screenshot the user
    shared in the planning step.
    """

    db = _open_memory_db()
    try:
        apply_migrations(db)
        db.execute(
            "INSERT INTO exams "
            "(id, name, description, created_at, default_question_count, "
            "time_limit_minutes, passing_score, target_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "aif-c01",
                "AIF-C01",
                "AWS AI Practitioner",
                "2026-04-23T10:00:00Z",
                65,
                90,
                700,
                1000,
            ),
        )
        row = db.execute(
            "SELECT default_question_count, time_limit_minutes, "
            "passing_score, target_score FROM exams WHERE id = ?",
            ("aif-c01",),
        ).fetchone()
        assert row is not None
        assert tuple(row) == (65, 90, 700, 1000)
    finally:
        db.close()



def test_exams_table_rejects_duplicate_primary_key() -> None:
    """Inserting two rows with the same id must fail (PK constraint)."""
    import sqlite3 as _sqlite3

    db = _open_memory_db()
    try:
        apply_migrations(db)
        db.execute(
            "INSERT INTO exams (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            ("exam-1", "First", None, "2025-01-01T00:00:00Z"),
        )
        try:
            db.execute(
                "INSERT INTO exams (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                ("exam-1", "Duplicate", None, "2025-01-02T00:00:00Z"),
            )
        except _sqlite3.IntegrityError:
            return
        raise AssertionError("Duplicate exam id must raise IntegrityError")
    finally:
        db.close()


def _seed_exam(db: sqlite3.Connection, exam_id: str = "exam-1") -> None:
    db.execute(
        "INSERT INTO exams (id, name, description, created_at) VALUES (?, ?, ?, ?)",
        (exam_id, "Seed", None, "2025-01-01T00:00:00Z"),
    )


def test_questions_table_has_expected_columns() -> None:
    """Migration v3 (plus v8 + v9 extensions) must expose the documented columns.

    v3 creates the core seven columns; v8 adds ``complexity`` / ``section``
    for the Designer Properties panel (both nullable, no backfill); v9
    adds ``allow_shuffle`` as ``NOT NULL DEFAULT 0`` so existing rows
    upgrade in place with shuffle off — matching the model's
    ``bool = False`` default.
    """

    db = _open_memory_db()
    try:
        apply_migrations(db)
        rows = db.execute("PRAGMA table_info(questions)").fetchall()
        columns = {row[1]: row for row in rows}
        assert set(columns) == {
            "id",
            "exam_id",
            "type",
            "text",
            "explanation",
            "image_path",
            "order_index",
            "complexity",
            "section",
            "allow_shuffle",
        }
        assert columns["id"][5] == 1  # pk flag
        for not_null in (
            "exam_id",
            "type",
            "text",
            "order_index",
            "allow_shuffle",
        ):
            assert columns[not_null][3] == 1, f"{not_null} must be NOT NULL"
        for nullable in (
            "explanation",
            "image_path",
            "complexity",
            "section",
        ):
            assert columns[nullable][3] == 0, f"{nullable} must be nullable"
        # allow_shuffle default is "0" (SQLite stores it as a string in
        # PRAGMA table_info for the dflt_value column).
        assert columns["allow_shuffle"][4] == "0"
    finally:
        db.close()




def test_questions_type_check_constraint_rejects_unknown_value() -> None:
    """CHECK(type IN (...)) must reject values outside the allowed set."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        try:
            db.execute(
                "INSERT INTO questions (id, exam_id, type, text, explanation,"
                " image_path, order_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("q-1", "exam-1", "bogus", "text", None, None, 0),
            )
        except sqlite3.IntegrityError:
            return
        raise AssertionError("Unknown question type must raise IntegrityError")
    finally:
        db.close()


def test_questions_foreign_key_cascades_on_exam_delete() -> None:
    """Deleting an exam must cascade and remove its questions."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        db.execute(
            "INSERT INTO questions (id, exam_id, type, text, explanation,"
            " image_path, order_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("q-1", "exam-1", "single_choice", "What?", None, None, 0),
        )
        db.commit()

        (count_before,) = db.execute(
            "SELECT COUNT(*) FROM questions"
        ).fetchone()
        assert count_before == 1

        db.execute("DELETE FROM exams WHERE id = ?", ("exam-1",))
        db.commit()

        (count_after,) = db.execute(
            "SELECT COUNT(*) FROM questions"
        ).fetchone()
        assert count_after == 0
    finally:
        db.close()


def _seed_exam_with_question(
    db: sqlite3.Connection,
    exam_id: str = "exam-1",
    question_id: str = "q-1",
) -> None:
    _seed_exam(db, exam_id)
    db.execute(
        "INSERT INTO questions (id, exam_id, type, text, explanation,"
        " image_path, order_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (question_id, exam_id, "single_choice", "Pick one", None, None, 0),
    )


def test_choices_table_has_expected_columns() -> None:
    """Migration v4 must create ``choices`` with the documented columns."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        rows = db.execute("PRAGMA table_info(choices)").fetchall()
        columns = {row[1]: row for row in rows}
        assert set(columns) == {
            "id",
            "question_id",
            "text",
            "is_correct",
            "order_index",
        }
        assert columns["id"][5] == 1  # pk flag
        for not_null in ("question_id", "text", "is_correct", "order_index"):
            assert columns[not_null][3] == 1, f"{not_null} must be NOT NULL"
        # is_correct default is "0".
        assert columns["is_correct"][4] == "0"
    finally:
        db.close()


def test_choices_is_correct_check_constraint_rejects_invalid_value() -> None:
    """CHECK(is_correct IN (0,1)) must reject values outside the boolean range."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam_with_question(db)
        try:
            db.execute(
                "INSERT INTO choices (id, question_id, text, is_correct, order_index)"
                " VALUES (?, ?, ?, ?, ?)",
                ("c-1", "q-1", "bad", 2, 0),
            )
        except sqlite3.IntegrityError:
            return
        raise AssertionError("is_correct outside 0/1 must raise IntegrityError")
    finally:
        db.close()


def test_choices_foreign_key_cascades_on_question_delete() -> None:
    """Deleting a question must cascade and remove its choices."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam_with_question(db)
        db.executemany(
            "INSERT INTO choices (id, question_id, text, is_correct, order_index)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                ("c-1", "q-1", "A", 1, 0),
                ("c-2", "q-1", "B", 0, 1),
            ],
        )
        db.commit()

        (count_before,) = db.execute("SELECT COUNT(*) FROM choices").fetchone()
        assert count_before == 2

        db.execute("DELETE FROM questions WHERE id = ?", ("q-1",))
        db.commit()

        (count_after,) = db.execute("SELECT COUNT(*) FROM choices").fetchone()
        assert count_after == 0
    finally:
        db.close()


def test_hotspot_tables_exist_after_migration_v5() -> None:
    """Migration v5 must create ``hotspot_options`` and ``hotspot_steps``."""
    db = _open_memory_db()
    try:
        apply_migrations(db)

        opt_cols = {
            row[1] for row in db.execute(
                "PRAGMA table_info(hotspot_options)"
            ).fetchall()
        }
        assert opt_cols == {"id", "question_id", "text", "order_index"}

        step_cols = {
            row[1] for row in db.execute(
                "PRAGMA table_info(hotspot_steps)"
            ).fetchall()
        }
        assert step_cols == {
            "id",
            "question_id",
            "prompt",
            "correct_option_id",
            "order_index",
        }
    finally:
        db.close()


def test_hotspot_tables_cascade_on_question_delete() -> None:
    """Deleting a hotspot question removes its options and steps."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        db.execute(
            "INSERT INTO questions (id, exam_id, type, text, explanation,"
            " image_path, order_index) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("q-hs", "exam-1", "hotspot", "Pick", None, None, 0),
        )
        db.execute(
            "INSERT INTO hotspot_options (id, question_id, text, order_index)"
            " VALUES (?, ?, ?, ?)",
            ("o-1", "q-hs", "EC2", 0),
        )
        db.execute(
            "INSERT INTO hotspot_steps (id, question_id, prompt,"
            " correct_option_id, order_index) VALUES (?, ?, ?, ?, ?)",
            ("s-1", "q-hs", "compute", "o-1", 0),
        )
        db.commit()

        db.execute("DELETE FROM questions WHERE id = ?", ("q-hs",))
        db.commit()

        (opt_count,) = db.execute(
            "SELECT COUNT(*) FROM hotspot_options"
        ).fetchone()
        (step_count,) = db.execute(
            "SELECT COUNT(*) FROM hotspot_steps"
        ).fetchone()
        assert opt_count == 0
        assert step_count == 0
    finally:
        db.close()


def test_sessions_table_has_expected_columns() -> None:
    """Migration v6 (plus v11 extensions) must create ``sessions`` with the documented columns.

    v6 creates the core five columns; v11 adds five nullable snapshot
    columns for the multi-user Runner (candidate_name, question_count,
    time_limit_minutes, passing_score, target_score) so completed
    sessions retain their pass/fail rules regardless of later Designer
    edits to the source exam.
    """

    db = _open_memory_db()
    try:
        apply_migrations(db)
        rows = db.execute("PRAGMA table_info(sessions)").fetchall()
        columns = {row[1]: row for row in rows}
        assert set(columns) == {
            "id",
            "exam_id",
            "mode",
            "started_at",
            "finished_at",
            "candidate_name",
            "question_count",
            "time_limit_minutes",
            "passing_score",
            "target_score",
        }
        assert columns["id"][5] == 1  # pk flag
        for not_null in ("exam_id", "mode", "started_at"):
            assert columns[not_null][3] == 1, f"{not_null} must be NOT NULL"
        # finished_at + all five v11 snapshot columns are nullable.
        for nullable in (
            "finished_at",
            "candidate_name",
            "question_count",
            "time_limit_minutes",
            "passing_score",
            "target_score",
        ):
            assert columns[nullable][3] == 0, f"{nullable} must be nullable"
    finally:
        db.close()


def test_sessions_table_accepts_null_snapshot_metadata() -> None:
    """Legacy-style sessions INSERT (no v11 columns) must succeed with NULL values."""

    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        db.execute(
            "INSERT INTO sessions (id, exam_id, mode, started_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("s-legacy", "exam-1", "training", "2025-01-01T00:00:00Z", None),
        )
        row = db.execute(
            "SELECT candidate_name, question_count, time_limit_minutes,"
            " passing_score, target_score FROM sessions WHERE id = ?",
            ("s-legacy",),
        ).fetchone()
        assert row is not None
        assert tuple(row) == (None, None, None, None, None)
    finally:
        db.close()


def test_sessions_table_accepts_snapshot_values() -> None:
    """Fresh INSERTs (post-v11) persist all snapshot metadata fields."""

    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        db.execute(
            "INSERT INTO sessions (id, exam_id, mode, started_at,"
            " finished_at, candidate_name, question_count,"
            " time_limit_minutes, passing_score, target_score)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "s-1",
                "exam-1",
                "exam",
                "2025-01-01T00:00:00Z",
                None,
                "Alice",
                65,
                90,
                700,
                1000,
            ),
        )
        row = db.execute(
            "SELECT candidate_name, question_count, time_limit_minutes,"
            " passing_score, target_score FROM sessions WHERE id = ?",
            ("s-1",),
        ).fetchone()
        assert row is not None
        assert tuple(row) == ("Alice", 65, 90, 700, 1000)
    finally:
        db.close()



def test_sessions_mode_check_constraint_rejects_unknown_value() -> None:
    """CHECK(mode IN ('training','exam')) rejects other values."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        try:
            db.execute(
                "INSERT INTO sessions (id, exam_id, mode, started_at, finished_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("s-1", "exam-1", "bogus", "2025-01-01T00:00:00Z", None),
            )
        except sqlite3.IntegrityError:
            return
        raise AssertionError("Unknown session mode must raise IntegrityError")
    finally:
        db.close()


def test_sessions_foreign_key_cascades_on_exam_delete() -> None:
    """Deleting an exam must cascade and remove its sessions."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam(db)
        db.execute(
            "INSERT INTO sessions (id, exam_id, mode, started_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?)",
            ("s-1", "exam-1", "training", "2025-01-01T00:00:00Z", None),
        )
        db.commit()

        (count_before,) = db.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()
        assert count_before == 1

        db.execute("DELETE FROM exams WHERE id = ?", ("exam-1",))
        db.commit()

        (count_after,) = db.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()
        assert count_after == 0
    finally:
        db.close()


def _seed_session(
    db: sqlite3.Connection,
    exam_id: str = "exam-1",
    session_id: str = "s-1",
) -> None:
    db.execute(
        "INSERT INTO sessions (id, exam_id, mode, started_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, exam_id, "training", "2025-01-01T00:00:00Z", None),
    )


def test_answers_table_has_expected_columns() -> None:
    """Migration v7 must create ``answers`` with the documented columns."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        rows = db.execute("PRAGMA table_info(answers)").fetchall()
        columns = {row[1]: row for row in rows}
        assert set(columns) == {
            "id",
            "session_id",
            "question_id",
            "given_json",
            "is_correct",
            "time_ms",
        }
        assert columns["id"][5] == 1  # pk flag
        for not_null in (
            "session_id",
            "question_id",
            "given_json",
            "is_correct",
        ):
            assert columns[not_null][3] == 1, f"{not_null} must be NOT NULL"
        assert columns["time_ms"][3] == 0
    finally:
        db.close()


def test_answers_is_correct_check_constraint_rejects_invalid_value() -> None:
    """CHECK(is_correct IN (0,1)) must reject other values."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam_with_question(db)
        _seed_session(db)
        try:
            db.execute(
                "INSERT INTO answers (id, session_id, question_id, given_json,"
                " is_correct, time_ms) VALUES (?, ?, ?, ?, ?, ?)",
                ("a-1", "s-1", "q-1", "{}", 7, None),
            )
        except sqlite3.IntegrityError:
            return
        raise AssertionError("is_correct outside 0/1 must raise IntegrityError")
    finally:
        db.close()


def test_answers_foreign_key_cascades_on_session_delete() -> None:
    """Deleting a session must cascade and remove its answers."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam_with_question(db)
        _seed_session(db)
        db.execute(
            "INSERT INTO answers (id, session_id, question_id, given_json,"
            " is_correct, time_ms) VALUES (?, ?, ?, ?, ?, ?)",
            ("a-1", "s-1", "q-1", '{"choice_id":"c-1"}', 1, 1234),
        )
        db.commit()

        (count_before,) = db.execute("SELECT COUNT(*) FROM answers").fetchone()
        assert count_before == 1

        db.execute("DELETE FROM sessions WHERE id = ?", ("s-1",))
        db.commit()

        (count_after,) = db.execute("SELECT COUNT(*) FROM answers").fetchone()
        assert count_after == 0
    finally:
        db.close()


def test_migration_v8_upgrades_existing_v7_database_in_place() -> None:
    """Applying migrations to a v7 DB must add the new columns without data loss.

    Real users have SQLite files already on disk — the v8 bump cannot
    require a dump/reload. We simulate that by stopping the runner at
    v7, seeding a row with the pre-v8 column layout, then calling
    ``apply_migrations`` again and checking that the seeded row still
    exists and that ``complexity`` / ``section`` default to ``NULL``.
    """

    from posrat.storage.schema import MIGRATIONS

    db = _open_memory_db()
    try:
        # Step the runner manually to v7 so we capture "pre-v8" state.
        db.execute("PRAGMA foreign_keys = ON")
        for version in sorted(v for v in MIGRATIONS if v <= 7):
            db.executescript(MIGRATIONS[version])
            db.execute(
                "UPDATE schema_version SET version = ?", (version,)
            )
        db.commit()

        _seed_exam_with_question(db)
        db.commit()

        # Now apply the rest — must reach CURRENT_SCHEMA_VERSION without
        # tripping over the pre-existing row.
        apply_migrations(db)
        final = db.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        assert final[0] == CURRENT_SCHEMA_VERSION

        row = db.execute(
            "SELECT id, text, complexity, section FROM questions"
            " WHERE id = ?",
            ("q-1",),
        ).fetchone()
        assert row is not None
        assert row[0] == "q-1"
        assert row[1] == "Pick one"
        assert row[2] is None  # complexity defaults to NULL on upgrade
        assert row[3] is None  # section defaults to NULL on upgrade
    finally:
        db.close()


def test_answers_foreign_key_cascades_on_question_delete() -> None:

    """Deleting a question must cascade and remove related answers."""
    db = _open_memory_db()
    try:
        apply_migrations(db)
        _seed_exam_with_question(db)
        _seed_session(db)
        db.execute(
            "INSERT INTO answers (id, session_id, question_id, given_json,"
            " is_correct, time_ms) VALUES (?, ?, ?, ?, ?, ?)",
            ("a-1", "s-1", "q-1", "{}", 1, None),
        )
        db.commit()

        db.execute("DELETE FROM questions WHERE id = ?", ("q-1",))
        db.commit()

        (count_after,) = db.execute("SELECT COUNT(*) FROM answers").fetchone()
        assert count_after == 0
    finally:
        db.close()
