"""Unit tests for :mod:`posrat.designer.browser`.

Scope covers the pure helpers introduced in steps 4.1.b and 4.1.c
(``resolve_data_dir``, ``list_exam_files``, ``open_exam_from_file``); UI
rendering still relies on the coarser import-only smoke tests in
``tests/test_app_smoke.py`` until a NiceGUI ``user`` fixture gets wired in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from posrat.designer import (
    ALLOWED_QUESTION_TYPES,
    ASSETS_DIRNAME,
    BLANK_QUESTION_ID_PREFIX,
    BLANK_QUESTION_TEXT,
    DATA_DIR_ENV,
    DEFAULT_DATA_DIR,
    EXAM_FILE_SUFFIX,
    EXPORTS_DIRNAME,
    MAX_IMAGE_SIZE_BYTES,
    MOVE_DOWN,
    MOVE_UP,
    OPEN_EXAM_STORAGE_KEY,
    QUESTION_LIST_TEXT_PREVIEW,
    SEARCH_QUERY_STORAGE_KEY,
    DEFAULT_MULTI_CHOICE_COUNT,
    DEFAULT_SINGLE_CHOICE_COUNT,
    add_blank_question_to_file,
    attach_image_to_question_in_file,

    change_question_type_in_file,
    clear_question_image_in_file,
    create_exam_file,
    delete_question_from_file,
    export_exam_to_json_in_file,
    filter_questions,
    format_question_label,
    list_exam_files,

    load_questions_from_file,
    move_question_in_file,
    open_exam_from_file,
    reorder_questions_in_file,
    replace_question_choices_in_file,
    replace_question_hotspot_in_file,
    resolve_assets_dir,
    resolve_data_dir,
    resolve_exports_dir,
    resolve_question_image_path,
    update_question_complexity_in_file,
    update_question_explanation_in_file,
    update_question_section_in_file,
    update_question_text_in_file,
)






from posrat.designer.browser import (
    _generate_blank_question,
    _morph_question_to_type,
    _truncate_question_text,
)
from posrat.models import Choice, Exam, Question
from posrat.models.hotspot import Hotspot, HotspotOption, HotspotStep
from posrat.storage import create_exam, open_db



def test_default_data_dir_constants() -> None:
    """Data dir defaults match the Phase 4 decisions: ``./data/`` + env override."""

    assert DATA_DIR_ENV == "POSRAT_DATA_DIR"
    assert DEFAULT_DATA_DIR == Path("data")
    assert EXAM_FILE_SUFFIX == ".sqlite"


def test_resolve_data_dir_uses_env_when_set(tmp_path: Path, monkeypatch) -> None:
    """When the env var points at a real path, resolve returns (and creates) it."""

    target = tmp_path / "custom-exams"
    monkeypatch.setenv(DATA_DIR_ENV, str(target))

    resolved = resolve_data_dir()

    assert resolved == target
    assert resolved.is_dir()  # created on demand


def test_resolve_data_dir_creates_default_when_env_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """Without the env var the default ``./data/`` is created relative to CWD."""

    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = resolve_data_dir()

    assert resolved == DEFAULT_DATA_DIR
    assert (tmp_path / DEFAULT_DATA_DIR).is_dir()


def test_resolve_data_dir_ignores_empty_env(tmp_path: Path, monkeypatch) -> None:
    """Empty env var is treated as "unset" (matches our storage-secret helper)."""

    monkeypatch.setenv(DATA_DIR_ENV, "")
    monkeypatch.chdir(tmp_path)

    resolved = resolve_data_dir()

    assert resolved == DEFAULT_DATA_DIR
    assert (tmp_path / DEFAULT_DATA_DIR).is_dir()


def test_list_exam_files_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    """Non-existent data dir yields ``[]`` rather than raising."""

    assert list_exam_files(tmp_path / "does-not-exist") == []


def test_list_exam_files_returns_only_sqlite_files_sorted(tmp_path: Path) -> None:
    """Only ``*.sqlite`` regular files are returned, alphabetically sorted."""

    # Intentionally create files in non-alphabetical order so the test also
    # covers the sort behaviour, not just the filter.
    (tmp_path / "zeta.sqlite").touch()
    (tmp_path / "alpha.sqlite").touch()
    (tmp_path / "mid.sqlite").touch()
    # Noise files that must be filtered out.
    (tmp_path / "notes.txt").touch()
    (tmp_path / "backup.sqlite.bak").touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "inner.sqlite").touch()

    result = list_exam_files(tmp_path)

    assert [p.name for p in result] == ["alpha.sqlite", "mid.sqlite", "zeta.sqlite"]
    assert all(p.parent == tmp_path for p in result)


def _build_sample_exam(exam_id: str = "exam-1") -> Exam:
    """Return a minimal :class:`Exam` suitable for round-trip persistence tests."""

    return Exam(
        id=exam_id,
        name="Sample Exam",
        description="Fixture for open_exam_from_file tests.",
        questions=[
            Question(
                id="q1",
                type="single_choice",
                text="Pick A",
                explanation="Because.",
                choices=[
                    Choice(id="q1-a", text="A", is_correct=True),
                    Choice(id="q1-b", text="B", is_correct=False),
                ],
            ),
        ],
    )


def test_open_exam_from_file_loads_exam(tmp_path: Path) -> None:
    """Creating an exam then re-opening via ``open_exam_from_file`` round-trips."""

    db_path = tmp_path / "sample.sqlite"
    exam = _build_sample_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    loaded = open_exam_from_file(db_path)

    assert loaded == exam


def test_open_exam_from_file_raises_when_empty(tmp_path: Path) -> None:
    """A valid but exam-less ``.sqlite`` surfaces as :class:`ValueError`."""

    db_path = tmp_path / "empty.sqlite"
    # Open (and migrate) but intentionally do not insert any exam row.
    open_db(db_path).close()

    with pytest.raises(ValueError):
        open_exam_from_file(db_path)


def test_open_exam_storage_key_constant() -> None:
    """Storage key stays stable — it's part of the app.storage.user contract."""

    assert OPEN_EXAM_STORAGE_KEY == "open_exam"


def test_create_exam_file_creates_empty_exam_db(tmp_path: Path) -> None:
    """``create_exam_file`` yields a migrated DB with a single empty exam row."""

    new_path = create_exam_file(
        tmp_path,
        exam_id="new-exam",
        name="Fresh Exam",
        description="Shiny and empty",
    )

    assert new_path == tmp_path / f"new-exam{EXAM_FILE_SUFFIX}"
    assert new_path.is_file()

    loaded = open_exam_from_file(new_path)
    assert loaded.id == "new-exam"
    assert loaded.name == "Fresh Exam"
    assert loaded.description == "Shiny and empty"
    assert loaded.questions == []


def test_create_exam_file_accepts_none_description(tmp_path: Path) -> None:
    """Description is optional — default ``None`` round-trips cleanly."""

    new_path = create_exam_file(tmp_path, exam_id="no-desc", name="No Desc")

    loaded = open_exam_from_file(new_path)
    assert loaded.description is None


def test_create_exam_file_raises_when_path_exists(tmp_path: Path) -> None:
    """Refusing to overwrite protects users from clobbering existing data."""

    create_exam_file(tmp_path, exam_id="dup", name="First")

    with pytest.raises(FileExistsError):
        create_exam_file(tmp_path, exam_id="dup", name="Second")


def test_create_exam_file_rejects_empty_id(tmp_path: Path) -> None:
    """Empty exam id is rejected by Pydantic before touching the filesystem."""

    with pytest.raises(ValueError):
        create_exam_file(tmp_path, exam_id="", name="No ID")

    # Nothing should have been created on disk.
    assert list(tmp_path.iterdir()) == []


def test_create_exam_file_persists_runner_metadata(
    tmp_path: Path,
) -> None:
    """New exam Runner metadata round-trips to disk.

    The Designer's "New exam" dialog forwards three optional Runner
    fields via kwargs: ``default_question_count`` (pre-fills "Take N
    questions" in the Runner mode dialog), ``passing_score`` (drives
    the pass/fail banner on the results screen) and
    ``time_limit_minutes`` (drives the timer). The Runner reads them
    through :class:`RunnerExamSummary`, so the create→load→pick chain
    must preserve the values byte-for-byte.
    """

    new_path = create_exam_file(
        tmp_path,
        exam_id="certif",
        name="Certif",
        description=None,
        default_question_count=65,
        passing_score=700,
        time_limit_minutes=90,
    )

    loaded = open_exam_from_file(new_path)
    assert loaded.default_question_count == 65
    assert loaded.passing_score == 700
    assert loaded.time_limit_minutes == 90


def test_create_exam_file_defaults_runner_metadata_to_none(
    tmp_path: Path,
) -> None:
    """Omitting the optional Runner metadata keeps the columns NULL.

    Matching the old pre-7A contract: an exam without a passing score,
    duration or default question count behaves as "ungraded, untimed,
    no pre-fill" in the Runner. The mode dialog branches on
    :class:`Optional[int]` semantics, so ``None`` must stay ``None``
    (not 0 / not an empty string).
    """

    new_path = create_exam_file(tmp_path, exam_id="plain", name="Plain")

    loaded = open_exam_from_file(new_path)
    assert loaded.default_question_count is None
    assert loaded.passing_score is None
    assert loaded.time_limit_minutes is None




def _build_multi_question_exam(exam_id: str = "multi") -> Exam:
    """Return an :class:`Exam` with three questions covering both choice types.

    Used to verify ``list_questions`` ordering and round-tripping through
    ``load_questions_from_file``. Hotspot coverage is left to dedicated
    storage tests to keep this fixture small.
    """

    return Exam(
        id=exam_id,
        name="Multi Question Exam",
        description=None,
        questions=[
            Question(
                id="q-one",
                type="single_choice",
                text="First question",
                choices=[
                    Choice(id="q-one-a", text="A", is_correct=True),
                    Choice(id="q-one-b", text="B", is_correct=False),
                ],
            ),
            Question(
                id="q-two",
                type="multi_choice",
                text="Second question",
                choices=[
                    Choice(id="q-two-a", text="A", is_correct=True),
                    Choice(id="q-two-b", text="B", is_correct=True),
                    Choice(id="q-two-c", text="C", is_correct=False),
                ],
            ),
            Question(
                id="q-three",
                type="single_choice",
                text="Third question",
                choices=[
                    Choice(id="q-three-a", text="A", is_correct=False),
                    Choice(id="q-three-b", text="B", is_correct=True),
                ],
            ),
        ],
    )


def test_load_questions_from_file_returns_db_questions(tmp_path: Path) -> None:
    """``load_questions_from_file`` round-trips all questions in insertion order."""

    db_path = tmp_path / "multi.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    loaded = load_questions_from_file(db_path, exam.id)

    assert loaded == exam.questions
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]


def test_load_questions_from_file_returns_empty_for_fresh_exam(
    tmp_path: Path,
) -> None:
    """A brand-new empty exam DB yields ``[]`` rather than raising."""

    new_path = create_exam_file(tmp_path, exam_id="empty", name="Empty Exam")

    assert load_questions_from_file(new_path, "empty") == []


def test_load_questions_from_file_returns_empty_for_unknown_exam_id(
    tmp_path: Path,
) -> None:
    """Unknown exam id is indistinguishable from "no questions" — both are ``[]``."""

    new_path = create_exam_file(tmp_path, exam_id="real", name="Real Exam")

    # ``list_questions`` does not distinguish "exam missing" from "exam empty"
    # — both paths return ``[]``, which matches our UI contract (render the
    # "no questions" placeholder in either case).
    assert load_questions_from_file(new_path, "does-not-exist") == []


def test_question_list_text_preview_constant() -> None:
    """Preview length is part of the UI contract — guard against accidental changes."""

    assert QUESTION_LIST_TEXT_PREVIEW == 80


def test_truncate_question_text_returns_short_text_verbatim() -> None:
    """Texts shorter than the limit pass through unchanged (whitespace normalised)."""

    assert _truncate_question_text("Pick A", limit=20) == "Pick A"


def test_truncate_question_text_collapses_whitespace() -> None:
    """Multi-line / multi-space texts are flattened to a single line."""

    assert _truncate_question_text("line1\n  line2\t  end", limit=40) == (
        "line1 line2 end"
    )


def test_truncate_question_text_ellipsises_long_text() -> None:
    """Texts above the limit are truncated and suffixed with a single ellipsis."""

    long_text = "x" * 200
    result = _truncate_question_text(long_text, limit=10)

    assert len(result) == 10
    assert result.endswith("…")
    assert result == "x" * 9 + "…"


def test_format_question_label_is_one_based() -> None:
    """``Q1`` for index 0, ``Q2`` for index 1 — matches the user-facing mockup."""

    assert format_question_label(0) == "Q1"
    assert format_question_label(1) == "Q2"
    assert format_question_label(48) == "Q49"
    assert format_question_label(333) == "Q334"


def test_format_question_label_rejects_negative_index() -> None:
    """Negative indices are a bug somewhere upstream — fail loudly, not silently."""

    with pytest.raises(ValueError):
        format_question_label(-1)


def test_blank_question_constants() -> None:

    """Default id prefix and placeholder text are part of the UI contract."""

    assert BLANK_QUESTION_ID_PREFIX == "q-"
    assert BLANK_QUESTION_TEXT == "New question"


def test_default_choice_count_constants() -> None:
    """Default seed counts are part of the user-facing contract.

    Values come from the Visual CertExam mockup: single_choice defaults
    to A/B/C/D (4 rows), multi_choice defaults to 6 rows (matches the
    typical AWS-style "Choose two / three" question). Guarded as
    constants so the Designer UI, the seed helper and the
    ``_morph_question_to_type`` growth logic share a single source of
    truth.
    """

    assert DEFAULT_SINGLE_CHOICE_COUNT == 4
    assert DEFAULT_MULTI_CHOICE_COUNT == 6


def test_generate_blank_question_has_single_choice_shape() -> None:
    """Generated question passes ``single_choice`` validation with 4 choices.

    Updated from the original 2-choice default: the Visual CertExam-style
    Designer ships A/B/C/D pre-filled so the user rarely has to click
    "Přidat volbu". The first choice is marked correct — that both
    satisfies the exactly-one-correct invariant and matches what
    ``_handle_edit_single_choices_click`` expects on first paint.
    """

    question = _generate_blank_question(existing_ids=set())

    assert question.type == "single_choice"
    assert question.text == BLANK_QUESTION_TEXT
    assert question.id.startswith(BLANK_QUESTION_ID_PREFIX)
    assert len(question.choices) == DEFAULT_SINGLE_CHOICE_COUNT
    assert sum(1 for c in question.choices if c.is_correct) == 1
    # Labels follow the A/B/C/D convention so the UI can render them
    # straight from ``choice.text`` without a separate letter column.
    assert [c.text for c in question.choices] == ["A", "B", "C", "D"]



def test_generate_blank_question_avoids_existing_ids() -> None:
    """When a candidate id clashes, the helper re-rolls until it's unique."""

    # Pre-populate existing_ids with "everything except a known hex" so the
    # loop has to roll at least once. Easier proxy: pass an arbitrary large
    # set and assert the returned id is not in it.
    existing = {f"{BLANK_QUESTION_ID_PREFIX}{i:08x}" for i in range(100)}

    question = _generate_blank_question(existing_ids=existing)

    assert question.id not in existing


def test_add_blank_question_to_file_appends_question(tmp_path: Path) -> None:
    """First call on a fresh exam persists a valid ``single_choice`` question."""

    new_path = create_exam_file(tmp_path, exam_id="blank", name="Blank Exam")

    new_id = add_blank_question_to_file(new_path, "blank")

    loaded = load_questions_from_file(new_path, "blank")
    assert [q.id for q in loaded] == [new_id]
    assert loaded[0].type == "single_choice"
    assert loaded[0].text == BLANK_QUESTION_TEXT
    assert len(loaded[0].choices) == DEFAULT_SINGLE_CHOICE_COUNT
    assert sum(1 for c in loaded[0].choices if c.is_correct) == 1



def test_add_blank_question_to_file_generates_unique_ids(tmp_path: Path) -> None:
    """Repeated calls produce distinct ids and grow the question list in order."""

    new_path = create_exam_file(tmp_path, exam_id="growing", name="Growing Exam")

    first_id = add_blank_question_to_file(new_path, "growing")
    second_id = add_blank_question_to_file(new_path, "growing")
    third_id = add_blank_question_to_file(new_path, "growing")

    assert len({first_id, second_id, third_id}) == 3

    loaded = load_questions_from_file(new_path, "growing")
    assert [q.id for q in loaded] == [first_id, second_id, third_id]


def test_add_blank_question_to_file_raises_for_unknown_exam(tmp_path: Path) -> None:
    """Missing exam id is a :class:`LookupError` from the question DAO."""

    new_path = create_exam_file(tmp_path, exam_id="real", name="Real Exam")

    with pytest.raises(LookupError):
        add_blank_question_to_file(new_path, "does-not-exist")


def test_delete_question_from_file_removes_middle_question(tmp_path: Path) -> None:
    """Deleting a middle question leaves the remaining two in their original order."""

    db_path = tmp_path / "delete-middle.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    removed = delete_question_from_file(db_path, "q-two")

    assert removed is True
    remaining = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in remaining] == ["q-one", "q-three"]


def test_delete_question_from_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Deleting an unknown id is an idempotent no-op — ``False`` + list intact."""

    db_path = tmp_path / "delete-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    removed = delete_question_from_file(db_path, "q-does-not-exist")

    assert removed is False
    remaining = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in remaining] == ["q-one", "q-two", "q-three"]


def test_delete_question_from_file_cascades_choices(tmp_path: Path) -> None:
    """Deleting a question also removes its choices via FK CASCADE."""

    db_path = tmp_path / "delete-cascade.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    removed = delete_question_from_file(db_path, "q-two")
    assert removed is True

    # Raw SQL check: choices for ``q-two`` must be gone while siblings
    # survive. Re-open the DB read-only-ish and inspect the choices table
    # directly so we know the CASCADE really fired instead of the DAO
    # silently leaving orphans.
    db = open_db(db_path)
    try:
        rows = db.execute(
            "SELECT question_id FROM choices ORDER BY question_id, id"
        ).fetchall()
    finally:
        db.close()

    question_ids = {row["question_id"] for row in rows}
    assert question_ids == {"q-one", "q-three"}
    assert all(row["question_id"] != "q-two" for row in rows)


def test_move_direction_constants() -> None:
    """Direction constants are part of the public contract — guard their values."""

    assert MOVE_UP == "up"
    assert MOVE_DOWN == "down"


def test_reorder_questions_in_file_persists_new_order(tmp_path: Path) -> None:
    """Pure reorder helper round-trips a permutation through the DB."""

    db_path = tmp_path / "reorder.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    reorder_questions_in_file(
        db_path, exam.id, ["q-three", "q-one", "q-two"]
    )

    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-three", "q-one", "q-two"]


def test_reorder_questions_in_file_rejects_invalid_permutation(
    tmp_path: Path,
) -> None:
    """ValueError from the DAO propagates; disk order is unchanged."""

    db_path = tmp_path / "reorder-reject.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        reorder_questions_in_file(db_path, exam.id, ["q-one", "q-two"])

    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]


def test_move_question_in_file_up_swaps_with_predecessor(tmp_path: Path) -> None:
    """Moving the middle row up swaps it with the first row."""

    db_path = tmp_path / "move-up.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    changed = move_question_in_file(db_path, exam.id, "q-two", MOVE_UP)

    assert changed is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-two", "q-one", "q-three"]


def test_move_question_in_file_down_swaps_with_successor(tmp_path: Path) -> None:
    """Moving the middle row down swaps it with the last row."""

    db_path = tmp_path / "move-down.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    changed = move_question_in_file(db_path, exam.id, "q-two", MOVE_DOWN)

    assert changed is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-three", "q-two"]


def test_move_question_in_file_returns_false_at_top_edge(tmp_path: Path) -> None:
    """Moving the first row up is a no-op (False) and the order stays put."""

    db_path = tmp_path / "move-top.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    changed = move_question_in_file(db_path, exam.id, "q-one", MOVE_UP)

    assert changed is False
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]


def test_move_question_in_file_returns_false_at_bottom_edge(tmp_path: Path) -> None:
    """Moving the last row down is a no-op (False) and the order stays put."""

    db_path = tmp_path / "move-bottom.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    changed = move_question_in_file(db_path, exam.id, "q-three", MOVE_DOWN)

    assert changed is False
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]


def test_move_question_in_file_rejects_unknown_direction(tmp_path: Path) -> None:
    """Only ``MOVE_UP`` / ``MOVE_DOWN`` are accepted — anything else raises."""

    db_path = tmp_path / "move-bad-dir.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        move_question_in_file(db_path, exam.id, "q-one", "sideways")


def test_move_question_in_file_rejects_unknown_question(tmp_path: Path) -> None:
    """Missing question id surfaces as ValueError before touching the DB order."""

    db_path = tmp_path / "move-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        move_question_in_file(db_path, exam.id, "q-ghost", MOVE_UP)

    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]


def test_search_query_storage_key_constant() -> None:
    """Storage key stays stable — it's part of the ``app.storage.user`` contract."""

    assert SEARCH_QUERY_STORAGE_KEY == "designer_search_query"


def test_filter_questions_empty_query_returns_all_as_shallow_copy() -> None:
    """Empty query means "no filter" — result equals input and is a fresh list."""

    questions = _build_multi_question_exam().questions

    result = filter_questions(questions, "")

    assert result == questions
    assert result is not questions  # shallow copy, callers can mutate freely


def test_filter_questions_whitespace_only_query_is_treated_as_empty() -> None:
    """Pure whitespace should not hide any rows — users don't mean to filter."""

    questions = _build_multi_question_exam().questions

    result = filter_questions(questions, "   \t\n  ")

    assert [q.id for q in result] == ["q-one", "q-two", "q-three"]


def test_filter_questions_matches_text_case_insensitive() -> None:
    """Case-insensitive substring match on ``Question.text``."""

    questions = _build_multi_question_exam().questions

    assert [q.id for q in filter_questions(questions, "second")] == ["q-two"]
    assert [q.id for q in filter_questions(questions, "SECOND")] == ["q-two"]
    # "question" appears in all three — case-insensitive substring keeps them all.
    assert [q.id for q in filter_questions(questions, "Question")] == [
        "q-one",
        "q-two",
        "q-three",
    ]


def test_filter_questions_matches_id_case_insensitive() -> None:
    """Case-insensitive substring match on ``Question.id`` as well as text."""

    questions = _build_multi_question_exam().questions

    # "q-th" only matches ``q-three`` on id; it would not match on text.
    assert [q.id for q in filter_questions(questions, "q-th")] == ["q-three"]
    # Upper-case query still matches the lower-case id.
    assert [q.id for q in filter_questions(questions, "Q-TWO")] == ["q-two"]


def test_filter_questions_no_match_returns_empty() -> None:
    """A query that matches neither id nor text of any row returns ``[]``."""

    questions = _build_multi_question_exam().questions

    assert filter_questions(questions, "nothing-here") == []


def test_filter_questions_preserves_input_order() -> None:
    """Filtering keeps the original list order — UI relies on this for Move up/down."""

    questions = _build_multi_question_exam().questions

    # "question" matches all three rows; they must come back in the original
    # order (q-one, q-two, q-three) so the Designer Move up/down edge logic
    # stays in sync with the on-disk sequence.
    result = filter_questions(questions, "question")
    assert [q.id for q in result] == ["q-one", "q-two", "q-three"]


def test_update_question_text_in_file_rewrites_text(tmp_path: Path) -> None:
    """Happy path: new text is persisted verbatim and helper returns ``True``."""

    db_path = tmp_path / "edit-text.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_text_in_file(
        db_path, "q-two", "Revised second question"
    )

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    texts = {q.id: q.text for q in loaded}
    assert texts["q-two"] == "Revised second question"
    # Siblings stay untouched — we only rewrote one row.
    assert texts["q-one"] == "First question"
    assert texts["q-three"] == "Third question"


def test_update_question_text_in_file_preserves_type_choices_and_order(
    tmp_path: Path,
) -> None:
    """Editing text must leave ``type``, ``choices`` and list order intact."""

    db_path = tmp_path / "edit-preserves.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    # ``q-two`` is a multi_choice question with 3 choices (2 correct). After
    # the text edit we expect the full Question payload to round-trip
    # untouched save for the text field.
    original_two = next(q for q in exam.questions if q.id == "q-two")

    updated = update_question_text_in_file(db_path, "q-two", "New text")

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]  # order

    edited = next(q for q in loaded if q.id == "q-two")
    assert edited.type == original_two.type
    assert edited.choices == original_two.choices
    assert edited.explanation == original_two.explanation
    assert edited.image_path == original_two.image_path
    assert edited.hotspot == original_two.hotspot
    assert edited.text == "New text"


def test_update_question_text_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` and disk untouched."""

    db_path = tmp_path / "edit-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_text_in_file(
        db_path, "q-does-not-exist", "whatever"
    )

    assert updated is False
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.text for q in loaded] == [
        "First question",
        "Second question",
        "Third question",
    ]


def test_update_question_text_in_file_rejects_empty_text(tmp_path: Path) -> None:
    """Empty text mirrors the Pydantic ``min_length=1`` contract — raises early."""

    db_path = tmp_path / "edit-empty.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        update_question_text_in_file(db_path, "q-two", "")

    # Failure must leave disk state untouched — the helper validates *before*
    # opening the DB, so even a malformed call cannot half-apply.
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.text for q in loaded] == [
        "First question",
        "Second question",
        "Third question",
    ]


def test_update_question_explanation_in_file_sets_new_text(tmp_path: Path) -> None:
    """Happy path: non-empty explanation is persisted verbatim, ``True`` returned."""

    db_path = tmp_path / "edit-expl.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_explanation_in_file(
        db_path, "q-two", "Because A+B cover the scenario."
    )

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    by_id = {q.id: q for q in loaded}
    assert by_id["q-two"].explanation == "Because A+B cover the scenario."
    # Siblings' ``explanation`` stays at its original ``None`` (fixture has
    # no explanation on any row) — text edit must not leak across rows.
    assert by_id["q-one"].explanation is None
    assert by_id["q-three"].explanation is None


def test_update_question_explanation_in_file_accepts_none(tmp_path: Path) -> None:
    """Passing ``None`` clears the explanation to SQL NULL (user emptied the field)."""

    db_path = tmp_path / "edit-expl-clear.sqlite"
    # Seed ``q-two`` with a non-None explanation so we can observe the clear.
    exam = Exam(
        id="expl-clear",
        name="Explanation Clear Exam",
        questions=[
            Question(
                id="q-one",
                type="single_choice",
                text="First",
                explanation="old rationale",
                choices=[
                    Choice(id="q-one-a", text="A", is_correct=True),
                    Choice(id="q-one-b", text="B", is_correct=False),
                ],
            ),
        ],
    )
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_explanation_in_file(db_path, "q-one", None)

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert loaded[0].explanation is None


def test_update_question_explanation_in_file_preserves_other_fields(
    tmp_path: Path,
) -> None:
    """Explanation edit must not touch ``text`` / ``type`` / ``choices`` / order."""

    db_path = tmp_path / "edit-expl-preserves.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    original_two = next(q for q in exam.questions if q.id == "q-two")

    updated = update_question_explanation_in_file(
        db_path, "q-two", "Fresh rationale"
    )

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]  # order

    edited = next(q for q in loaded if q.id == "q-two")
    assert edited.type == original_two.type
    assert edited.text == original_two.text  # text untouched
    assert edited.choices == original_two.choices
    assert edited.image_path == original_two.image_path
    assert edited.hotspot == original_two.hotspot
    assert edited.explanation == "Fresh rationale"


def test_update_question_explanation_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` and disk untouched."""

    db_path = tmp_path / "edit-expl-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_explanation_in_file(
        db_path, "q-does-not-exist", "whatever"
    )

    assert updated is False
    loaded = load_questions_from_file(db_path, exam.id)
    # Fixture questions have ``explanation=None`` for every row; unknown-id
    # call must leave all of them in that state.
    assert [q.explanation for q in loaded] == [None, None, None]


def test_allowed_question_types_constant() -> None:
    """Constant lists the three model types in the public dropdown order."""

    assert ALLOWED_QUESTION_TYPES == (
        "single_choice",
        "multi_choice",
        "hotspot",
    )


def test_morph_question_single_to_multi_preserves_and_expands_choices() -> None:
    """single_choice → multi_choice keeps existing choices and tops up to 6.

    Multi-choice defaults to 6 rows per the Visual CertExam convention, so
    a 3-choice single_choice that morphs to multi_choice grows to 6 rows
    with the 3 fresh extras marked ``is_correct=False`` (no impact on the
    ≥1-correct invariant). Original rows must survive byte-for-byte.
    """

    source = Question(
        id="src",
        type="single_choice",
        text="Hello",
        explanation="rationale",
        choices=[
            Choice(id="src-a", text="A", is_correct=True),
            Choice(id="src-b", text="B", is_correct=False),
            Choice(id="src-c", text="C", is_correct=False),
        ],
    )

    morphed = _morph_question_to_type(source, "multi_choice")

    assert morphed.type == "multi_choice"
    assert morphed.text == source.text  # preserved
    assert morphed.explanation == source.explanation  # preserved
    # First 3 choices byte-for-byte identical — only new rows appended.
    assert morphed.choices[: len(source.choices)] == source.choices
    assert len(morphed.choices) == DEFAULT_MULTI_CHOICE_COUNT
    # Fresh rows added at the tail are not correct (keeps the user in
    # control of what counts) and carry unique ids.
    assert all(not c.is_correct for c in morphed.choices[len(source.choices):])
    assert len({c.id for c in morphed.choices}) == len(morphed.choices)
    assert morphed.hotspot is None


def test_morph_question_single_to_multi_keeps_choices_when_already_enough() -> None:
    """A single_choice with ≥6 choices morphs to multi_choice byte-for-byte.

    ``_morph_question_to_type`` only tops up when ``len(source) <
    target_default_count``; once we're at or above the target, the list
    is reused verbatim. Verified against the 6-choice threshold so the
    growth heuristic does not secretly append an unused row.
    """

    source = Question(
        id="src",
        type="single_choice",
        text="Already large",
        choices=[
            Choice(id="src-a", text="A", is_correct=True),
            Choice(id="src-b", text="B", is_correct=False),
            Choice(id="src-c", text="C", is_correct=False),
            Choice(id="src-d", text="D", is_correct=False),
            Choice(id="src-e", text="E", is_correct=False),
            Choice(id="src-f", text="F", is_correct=False),
        ],
    )

    morphed = _morph_question_to_type(source, "multi_choice")

    assert morphed.type == "multi_choice"
    assert morphed.choices == source.choices
    assert len(morphed.choices) == DEFAULT_MULTI_CHOICE_COUNT



def test_morph_question_multi_to_single_keeps_only_first_correct() -> None:
    """multi → single: first correct stays, other correct flags are cleared.

    With the new default-count growth logic a 3-choice multi_choice that
    morphs to single_choice pads up to 4 rows (single_choice default).
    The first choice stays correct, the two remaining originals are
    demoted to ``is_correct=False``, and the single padding row added
    at the tail is also non-correct — so the result has *exactly one*
    correct, satisfying single_choice's invariant.
    """

    source = Question(
        id="src",
        type="multi_choice",
        text="Pick any",
        choices=[
            Choice(id="src-a", text="A", is_correct=True),
            Choice(id="src-b", text="B", is_correct=True),
            Choice(id="src-c", text="C", is_correct=True),
        ],
    )

    morphed = _morph_question_to_type(source, "single_choice")

    assert morphed.type == "single_choice"
    # Length grows to the single_choice default (4 = 3 originals + 1
    # fresh padding row).
    assert len(morphed.choices) == DEFAULT_SINGLE_CHOICE_COUNT
    # First row keeps is_correct=True, every later row is cleared.
    assert morphed.choices[0].is_correct is True
    assert [c.is_correct for c in morphed.choices[1:]] == [False, False, False]
    # Exactly one correct — single_choice invariant must hold on the result.
    assert sum(1 for c in morphed.choices if c.is_correct) == 1



def test_morph_question_single_to_hotspot_seeds_payload() -> None:
    """single → hotspot drops choices and seeds minimal valid hotspot payload."""

    source = Question(
        id="src",
        type="single_choice",
        text="Keep me",
        explanation="keep me too",
        choices=[
            Choice(id="src-a", text="A", is_correct=True),
            Choice(id="src-b", text="B", is_correct=False),
        ],
    )

    morphed = _morph_question_to_type(source, "hotspot")

    assert morphed.type == "hotspot"
    assert morphed.choices == []
    assert morphed.hotspot is not None
    # Seeded payload: 2 options + 1 step. Step must reference one of the
    # options (Pydantic validator enforces this — but we still double-check
    # to catch regressions where the seed drifts out of sync).
    assert len(morphed.hotspot.options) >= 2
    assert len(morphed.hotspot.steps) >= 1
    option_ids = {opt.id for opt in morphed.hotspot.options}
    assert morphed.hotspot.steps[0].correct_option_id in option_ids
    # Text / explanation survive the morph.
    assert morphed.text == source.text
    assert morphed.explanation == source.explanation


def test_morph_question_hotspot_to_single_seeds_default_choices() -> None:
    """hotspot → single drops the hotspot payload and seeds 2 default choices."""

    source = Question(
        id="src",
        type="hotspot",
        text="Map things",
        explanation="ctx",
        hotspot=Hotspot(
            options=[
                HotspotOption(id="src-opt-a", text="A"),
                HotspotOption(id="src-opt-b", text="B"),
            ],
            steps=[
                HotspotStep(
                    id="src-step-1",
                    prompt="Pick A",
                    correct_option_id="src-opt-a",
                ),
            ],
        ),
    )

    morphed = _morph_question_to_type(source, "single_choice")

    assert morphed.type == "single_choice"
    assert morphed.hotspot is None
    assert len(morphed.choices) >= 2
    assert sum(1 for c in morphed.choices if c.is_correct) == 1
    # Text / explanation survive the morph.
    assert morphed.text == source.text
    assert morphed.explanation == source.explanation


def test_morph_question_same_type_is_noop_copy() -> None:
    """Morphing to the current type yields an equal but distinct Question."""

    source = Question(
        id="src",
        type="single_choice",
        text="Hello",
        choices=[
            Choice(id="src-a", text="A", is_correct=True),
            Choice(id="src-b", text="B", is_correct=False),
        ],
    )

    morphed = _morph_question_to_type(source, "single_choice")

    assert morphed == source
    assert morphed is not source  # helper returns a fresh copy


def test_morph_question_rejects_unknown_type() -> None:
    """Unknown type raises ``ValueError`` before touching the payload."""

    source = Question(
        id="src",
        type="single_choice",
        text="Hello",
        choices=[
            Choice(id="src-a", text="A", is_correct=True),
            Choice(id="src-b", text="B", is_correct=False),
        ],
    )

    with pytest.raises(ValueError):
        _morph_question_to_type(source, "pixel_hotspot")


def test_change_question_type_in_file_single_to_multi(tmp_path: Path) -> None:
    """Happy path: single_choice row flips to multi_choice and grows to 6 choices.

    The fixture's ``q-one`` has 2 choices; morphing to multi_choice pads
    it up to the :data:`DEFAULT_MULTI_CHOICE_COUNT` = 6 default so the
    user gets a familiar "Choose two / three" skeleton. Original rows
    stay byte-for-byte intact; the tail is filled with fresh non-correct
    rows whose ids don't clash with the originals.
    """

    db_path = tmp_path / "change-type-s2m.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    original_one = next(q for q in exam.questions if q.id == "q-one")

    updated = change_question_type_in_file(db_path, "q-one", "multi_choice")

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]  # order

    edited = next(q for q in loaded if q.id == "q-one")
    assert edited.type == "multi_choice"
    assert edited.text == original_one.text  # preserved
    # Original 2 choices survive verbatim at the head of the list.
    assert edited.choices[: len(original_one.choices)] == original_one.choices
    # Tail padded to the multi_choice default; fresh rows are never
    # correct so the ≥1 correct invariant is still satisfied by the
    # original correct row alone.
    assert len(edited.choices) == DEFAULT_MULTI_CHOICE_COUNT
    assert all(not c.is_correct for c in edited.choices[len(original_one.choices):])
    assert len({c.id for c in edited.choices}) == len(edited.choices)



def test_change_question_type_in_file_choice_to_hotspot(tmp_path: Path) -> None:
    """choice → hotspot seeds hotspot payload and wipes the choices list."""

    db_path = tmp_path / "change-type-c2h.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = change_question_type_in_file(db_path, "q-two", "hotspot")

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-two")

    assert edited.type == "hotspot"
    assert edited.choices == []
    assert edited.hotspot is not None
    assert len(edited.hotspot.options) >= 2
    assert len(edited.hotspot.steps) >= 1
    # Siblings untouched — change_question_type rewrites one row only.
    assert [q.type for q in loaded if q.id != "q-two"] == [
        "single_choice",
        "single_choice",
    ]


def test_change_question_type_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` and disk untouched."""

    db_path = tmp_path / "change-type-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = change_question_type_in_file(db_path, "q-ghost", "multi_choice")

    assert updated is False
    loaded = load_questions_from_file(db_path, exam.id)
    # Every original question type survived.
    assert [q.type for q in loaded] == [
        "single_choice",
        "multi_choice",
        "single_choice",
    ]


def test_change_question_type_in_file_rejects_unknown_type(tmp_path: Path) -> None:
    """Unknown type raises ``ValueError`` *before* the DB is opened."""

    db_path = tmp_path / "change-type-bad.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        change_question_type_in_file(db_path, "q-one", "pixel_hotspot")

    # Fail-fast validation must not leak any changes to disk. Every row
    # keeps its original type.
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.type for q in loaded] == [
        "single_choice",
        "multi_choice",
        "single_choice",
    ]


def test_replace_question_choices_in_file_rewrites_single_choice(
    tmp_path: Path,
) -> None:
    """Happy path: new single_choice list is persisted verbatim."""

    db_path = tmp_path / "replace-single.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    new_choices = [
        Choice(id="q-one-x", text="New A", is_correct=False),
        Choice(id="q-one-y", text="New B", is_correct=True),
        Choice(id="q-one-z", text="New C", is_correct=False),
    ]

    updated = replace_question_choices_in_file(db_path, "q-one", new_choices)

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-one")
    assert edited.type == "single_choice"
    assert [(c.id, c.text, c.is_correct) for c in edited.choices] == [
        ("q-one-x", "New A", False),
        ("q-one-y", "New B", True),
        ("q-one-z", "New C", False),
    ]
    # Siblings untouched.
    assert next(q for q in loaded if q.id == "q-two").choices == (
        exam.questions[1].choices
    )


def test_replace_question_choices_in_file_rewrites_multi_choice(
    tmp_path: Path,
) -> None:
    """Multi_choice row also supported — 2 correct pass validation."""

    db_path = tmp_path / "replace-multi.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    new_choices = [
        Choice(id="q-two-x", text="NewA", is_correct=True),
        Choice(id="q-two-y", text="NewB", is_correct=True),
    ]

    updated = replace_question_choices_in_file(db_path, "q-two", new_choices)

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-two")
    assert edited.type == "multi_choice"
    assert [c.id for c in edited.choices] == ["q-two-x", "q-two-y"]


def test_replace_question_choices_in_file_preserves_text_and_type(
    tmp_path: Path,
) -> None:
    """Choice rewrite must not touch ``text`` / ``type`` / order / explanation."""

    db_path = tmp_path / "replace-preserves.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    original_one = next(q for q in exam.questions if q.id == "q-one")

    updated = replace_question_choices_in_file(
        db_path,
        "q-one",
        [
            Choice(id="q-one-x", text="X", is_correct=True),
            Choice(id="q-one-y", text="Y", is_correct=False),
        ],
    )

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-one", "q-two", "q-three"]  # order

    edited = next(q for q in loaded if q.id == "q-one")
    assert edited.type == original_one.type
    assert edited.text == original_one.text
    assert edited.explanation == original_one.explanation
    assert edited.image_path == original_one.image_path
    assert edited.hotspot == original_one.hotspot


def test_replace_question_choices_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` + disk untouched."""

    db_path = tmp_path / "replace-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = replace_question_choices_in_file(
        db_path,
        "q-ghost",
        [
            Choice(id="ghost-a", text="A", is_correct=True),
            Choice(id="ghost-b", text="B", is_correct=False),
        ],
    )

    assert updated is False
    loaded = load_questions_from_file(db_path, exam.id)
    # Each original choice list survives.
    assert loaded == exam.questions


def test_replace_question_choices_in_file_rejects_invalid_single_choice(
    tmp_path: Path,
) -> None:
    """Rewriting single_choice with 2 correct choices fails Pydantic invariant."""

    db_path = tmp_path / "replace-bad-single.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    # Two correct choices on a single_choice question must be rejected by
    # the Pydantic validator (exactly-one-correct invariant).
    with pytest.raises(ValueError):
        replace_question_choices_in_file(
            db_path,
            "q-one",
            [
                Choice(id="q-one-a", text="A", is_correct=True),
                Choice(id="q-one-b", text="B", is_correct=True),
            ],
        )

    # Disk must still hold the original choices — Pydantic validation runs
    # before ``update_question`` touches the DB.
    loaded = load_questions_from_file(db_path, exam.id)
    original_one = next(q for q in exam.questions if q.id == "q-one")
    edited_one = next(q for q in loaded if q.id == "q-one")
    assert edited_one.choices == original_one.choices


def test_replace_question_choices_in_file_rejects_too_few_choices(
    tmp_path: Path,
) -> None:
    """Rewriting single_choice with a 1-choice list fails Pydantic ≥2 invariant."""

    db_path = tmp_path / "replace-too-few.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        replace_question_choices_in_file(
            db_path,
            "q-one",
            [Choice(id="q-one-only", text="Only", is_correct=True)],
        )

    loaded = load_questions_from_file(db_path, exam.id)
    original_one = next(q for q in exam.questions if q.id == "q-one")
    edited_one = next(q for q in loaded if q.id == "q-one")
    assert edited_one.choices == original_one.choices


def test_replace_question_choices_in_file_accepts_all_correct_multi(
    tmp_path: Path,
) -> None:
    """multi_choice accepts an all-correct subset (≥1 correct invariant).

    Regression guard for the 5.4 editor: the UI checkbox model lets the
    user mark every row correct, which must round-trip cleanly through
    the pure helper without tripping Pydantic. Also ensures siblings
    (the single_choice ``q-one`` and ``q-three``) stay byte-for-byte
    identical.
    """

    db_path = tmp_path / "replace-multi-all-correct.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    new_choices = [
        Choice(id="q-two-x", text="Xylophone", is_correct=True),
        Choice(id="q-two-y", text="Yellow", is_correct=True),
        Choice(id="q-two-z", text="Zebra", is_correct=True),
    ]

    updated = replace_question_choices_in_file(db_path, "q-two", new_choices)
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    edited_two = next(q for q in loaded if q.id == "q-two")
    assert edited_two.type == "multi_choice"
    assert [(c.id, c.text, c.is_correct) for c in edited_two.choices] == [
        ("q-two-x", "Xylophone", True),
        ("q-two-y", "Yellow", True),
        ("q-two-z", "Zebra", True),
    ]

    # Siblings untouched.
    originals = {q.id: q for q in exam.questions}
    for loaded_q in loaded:
        if loaded_q.id == "q-two":
            continue
        assert loaded_q == originals[loaded_q.id]


def test_replace_question_choices_in_file_rejects_multi_without_correct(
    tmp_path: Path,
) -> None:
    """multi_choice with zero correct options raises + disk unchanged.

    Pydantic's ``multi_choice`` invariant requires ≥1 correct option.
    The pure helper builds the replacement ``Question`` before touching
    the DB, so the ``ValueError`` fires before any write — verified via
    the sibling-order + choices invariant after the raise.
    """

    db_path = tmp_path / "replace-multi-zero-correct.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        replace_question_choices_in_file(
            db_path,
            "q-two",
            [
                Choice(id="q-two-x", text="X", is_correct=False),
                Choice(id="q-two-y", text="Y", is_correct=False),
            ],
        )

    loaded = load_questions_from_file(db_path, exam.id)
    original_two = next(q for q in exam.questions if q.id == "q-two")
    edited_two = next(q for q in loaded if q.id == "q-two")
    assert edited_two.choices == original_two.choices


def _build_hotspot_exam(exam_id: str = "hotspot-exam") -> Exam:
    """Return an :class:`Exam` with one hotspot question plus siblings.

    Used by the 5.5 hotspot editor tests to verify
    :func:`replace_question_hotspot_in_file` rewrites only the target
    question's hotspot payload without touching siblings. Includes one
    choice-based question as well so "siblings stay untouched"
    assertions actually have something to guard.
    """

    return Exam(
        id=exam_id,
        name="Hotspot Exam",
        description=None,
        questions=[
            Question(
                id="q-choice",
                type="single_choice",
                text="Choice sibling",
                choices=[
                    Choice(id="q-choice-a", text="A", is_correct=True),
                    Choice(id="q-choice-b", text="B", is_correct=False),
                ],
            ),
            Question(
                id="q-hot",
                type="hotspot",
                text="Match services",
                hotspot=Hotspot(
                    options=[
                        HotspotOption(id="q-hot-o1", text="Option 1"),
                        HotspotOption(id="q-hot-o2", text="Option 2"),
                    ],
                    steps=[
                        HotspotStep(
                            id="q-hot-s1",
                            prompt="Step 1",
                            correct_option_id="q-hot-o1",
                        ),
                    ],
                ),
            ),
        ],
    )


def test_replace_question_hotspot_in_file_rewrites_payload(tmp_path: Path) -> None:
    """Happy path: new hotspot (3 options, 2 steps) round-trips cleanly."""

    db_path = tmp_path / "replace-hotspot.sqlite"
    exam = _build_hotspot_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    new_hotspot = Hotspot(
        options=[
            HotspotOption(id="q-hot-x", text="X"),
            HotspotOption(id="q-hot-y", text="Y"),
            HotspotOption(id="q-hot-z", text="Z"),
        ],
        steps=[
            HotspotStep(
                id="q-hot-step-a",
                prompt="Pick X",
                correct_option_id="q-hot-x",
            ),
            HotspotStep(
                id="q-hot-step-b",
                prompt="Pick Z",
                correct_option_id="q-hot-z",
            ),
        ],
    )

    updated = replace_question_hotspot_in_file(db_path, "q-hot", new_hotspot)
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-hot")
    assert edited.type == "hotspot"
    assert edited.hotspot is not None
    assert [o.id for o in edited.hotspot.options] == [
        "q-hot-x",
        "q-hot-y",
        "q-hot-z",
    ]
    assert [(s.id, s.prompt, s.correct_option_id) for s in edited.hotspot.steps] == [
        ("q-hot-step-a", "Pick X", "q-hot-x"),
        ("q-hot-step-b", "Pick Z", "q-hot-z"),
    ]


def test_replace_question_hotspot_in_file_preserves_text_and_siblings(
    tmp_path: Path,
) -> None:
    """Hotspot rewrite must not touch ``text`` / other fields nor siblings."""

    db_path = tmp_path / "replace-hotspot-preserves.sqlite"
    exam = _build_hotspot_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    original_hot = next(q for q in exam.questions if q.id == "q-hot")
    original_choice = next(q for q in exam.questions if q.id == "q-choice")

    new_hotspot = Hotspot(
        options=[HotspotOption(id="q-hot-only", text="Only")],
        steps=[
            HotspotStep(
                id="q-hot-only-step",
                prompt="Must pick Only",
                correct_option_id="q-hot-only",
            ),
        ],
    )

    updated = replace_question_hotspot_in_file(db_path, "q-hot", new_hotspot)
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    assert [q.id for q in loaded] == ["q-choice", "q-hot"]  # order preserved

    edited = next(q for q in loaded if q.id == "q-hot")
    assert edited.text == original_hot.text
    assert edited.explanation == original_hot.explanation
    assert edited.image_path == original_hot.image_path
    assert edited.type == "hotspot"
    assert edited.choices == []
    assert edited.hotspot is not None
    assert [o.id for o in edited.hotspot.options] == ["q-hot-only"]

    # Sibling choice question stays byte-for-byte identical.
    sibling = next(q for q in loaded if q.id == "q-choice")
    assert sibling == original_choice


def test_replace_question_hotspot_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` + disk untouched."""

    db_path = tmp_path / "replace-hotspot-unknown.sqlite"
    exam = _build_hotspot_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    new_hotspot = Hotspot(
        options=[HotspotOption(id="ghost-o1", text="X")],
        steps=[
            HotspotStep(
                id="ghost-s1",
                prompt="Step",
                correct_option_id="ghost-o1",
            ),
        ],
    )

    updated = replace_question_hotspot_in_file(
        db_path, "q-does-not-exist", new_hotspot
    )

    assert updated is False
    # Every original question survives untouched.
    loaded = load_questions_from_file(db_path, exam.id)
    assert loaded == exam.questions


def test_replace_question_hotspot_in_file_rejects_invalid_hotspot() -> None:
    """Pydantic rejects a Hotspot with a dangling step reference.

    This guards the invariant at the :class:`Hotspot` constructor level
    — we don't even get to the DB. Kept as a unit test of the model
    layer because the helper relies on it: if Pydantic ever stopped
    enforcing the cross-ref, the helper could persist a broken payload.
    """

    with pytest.raises(ValueError):
        Hotspot(
            options=[HotspotOption(id="opt-a", text="A")],
            steps=[
                HotspotStep(
                    id="step-1",
                    prompt="Pick ghost",
                    correct_option_id="opt-missing",
                ),
            ],
        )


def test_replace_question_hotspot_in_file_rejects_on_choice_question(
    tmp_path: Path,
) -> None:
    """Pushing a hotspot payload into a choice-based row raises.

    :class:`posrat.models.Question`'s cross-validator demands
    ``type == "hotspot"`` whenever ``hotspot`` is set, so the helper's
    implicit ``Question(type=current.type, hotspot=new_hotspot, ...)``
    construction surfaces a ``ValueError`` before ``update_question`` is
    reached — matching the "validate before touching disk" invariant we
    rely on for the 5.3 / 5.4 editors too.
    """

    db_path = tmp_path / "replace-hotspot-wrong-type.sqlite"
    exam = _build_hotspot_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    new_hotspot = Hotspot(
        options=[HotspotOption(id="ignored", text="X")],
        steps=[
            HotspotStep(
                id="ignored-step",
                prompt="Step",
                correct_option_id="ignored",
            ),
        ],
    )

    with pytest.raises(ValueError):
        replace_question_hotspot_in_file(db_path, "q-choice", new_hotspot)

    # Disk state is untouched — the choice sibling keeps its original
    # payload.
    loaded = load_questions_from_file(db_path, exam.id)
    original_choice = next(q for q in exam.questions if q.id == "q-choice")
    edited_choice = next(q for q in loaded if q.id == "q-choice")
    assert edited_choice == original_choice









# --- 5.7 Image upload helpers ----------------------------------------


def test_image_constants() -> None:
    """Image upload constants are part of the UI contract — guard values."""

    assert ASSETS_DIRNAME == "assets"
    assert MAX_IMAGE_SIZE_BYTES == 5_000_000


def test_resolve_assets_dir_creates_subfolder(tmp_path: Path) -> None:
    """``resolve_assets_dir`` creates ``assets/`` under data_dir on first call."""

    assets = resolve_assets_dir(tmp_path)

    assert assets == tmp_path / ASSETS_DIRNAME
    assert assets.is_dir()


def test_resolve_question_image_path_returns_none_for_empty() -> None:
    """A question without an attachment maps to ``None`` verbatim."""

    assert resolve_question_image_path(Path("/tmp/anywhere"), None) is None
    assert resolve_question_image_path(Path("/tmp/anywhere"), "") is None


def test_resolve_question_image_path_joins_relative(tmp_path: Path) -> None:
    """Non-empty relative ``image_path`` is joined against ``<data_dir>/assets``."""

    resolved = resolve_question_image_path(tmp_path, "my-exam/abc.png")

    assert resolved == tmp_path / ASSETS_DIRNAME / "my-exam" / "abc.png"


def test_attach_image_to_question_in_file_writes_and_records(
    tmp_path: Path,
) -> None:
    """Happy path: bytes land in assets dir, DB row points at them, sibling intact."""

    db_path = tmp_path / "img.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    data_dir = tmp_path
    payload = b"fake-png-bytes"

    updated = attach_image_to_question_in_file(
        db_path, data_dir, "q-two", payload, "diagram.PNG"
    )
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-two")

    # ``image_path`` is exam-relative and suffix-preserving (case-folded).
    assert edited.image_path is not None
    assert edited.image_path.startswith("multi/")
    assert edited.image_path.endswith(".png")

    # File on disk has exactly the bytes we wrote.
    resolved = resolve_question_image_path(data_dir, edited.image_path)
    assert resolved is not None
    assert resolved.is_file()
    assert resolved.read_bytes() == payload

    # Other fields + siblings stay untouched.
    original_two = next(q for q in exam.questions if q.id == "q-two")
    assert edited.type == original_two.type
    assert edited.text == original_two.text
    assert edited.choices == original_two.choices
    for loaded_q in loaded:
        if loaded_q.id == "q-two":
            continue
        assert loaded_q.image_path is None


def test_attach_image_to_question_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op + nothing ends up in assets/."""

    db_path = tmp_path / "img-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    data_dir = tmp_path
    updated = attach_image_to_question_in_file(
        db_path, data_dir, "q-ghost", b"bytes", "x.png"
    )
    assert updated is False

    assets_dir = data_dir / ASSETS_DIRNAME
    # ``resolve_assets_dir`` may have run during a prior call, so the
    # folder can exist — but no per-exam subdir should have been seeded
    # for the ghost call.
    if assets_dir.is_dir():
        # No file was written for any known exam id either — the early
        # return fires before the write.
        all_files = list(assets_dir.rglob("*"))
        assert not any(p.is_file() for p in all_files)


def test_attach_image_to_question_in_file_rejects_oversize(
    tmp_path: Path,
) -> None:
    """Blobs over the cap are refused *before* the DB is opened or a file written."""

    db_path = tmp_path / "img-toobig.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    too_big = b"x" * (MAX_IMAGE_SIZE_BYTES + 1)

    with pytest.raises(ValueError):
        attach_image_to_question_in_file(
            db_path, tmp_path, "q-one", too_big, "huge.png"
        )

    # Disk state invariant: the question still has no image attached.
    loaded = load_questions_from_file(db_path, exam.id)
    assert all(q.image_path is None for q in loaded)


def test_attach_image_to_question_in_file_rejects_bad_suffix(
    tmp_path: Path,
) -> None:
    """Non-whitelisted extensions raise before the DB opens."""

    db_path = tmp_path / "img-bad-ext.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    with pytest.raises(ValueError):
        attach_image_to_question_in_file(
            db_path, tmp_path, "q-one", b"bytes", "payload.exe"
        )

    loaded = load_questions_from_file(db_path, exam.id)
    assert all(q.image_path is None for q in loaded)


def test_attach_image_normalises_traversal_filename(tmp_path: Path) -> None:
    """A path-traversal-looking filename cannot escape the exam subdir.

    We regenerate the base name from a UUID, so no matter what the
    uploaded ``original_filename`` contains — ``../``, null bytes,
    Unicode lookalikes — the file lands strictly inside
    ``<data_dir>/assets/<exam_id>/``.
    """

    db_path = tmp_path / "img-trav.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    data_dir = tmp_path
    updated = attach_image_to_question_in_file(
        db_path, data_dir, "q-one", b"payload", "../../etc/passwd.png"
    )
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-one")
    assert edited.image_path is not None

    # image_path must live under the exam's own asset subdir — no
    # ``..`` segments, no escape.
    assert edited.image_path.startswith("multi/")
    assert ".." not in edited.image_path

    resolved = resolve_question_image_path(data_dir, edited.image_path)
    assert resolved is not None
    assert resolved.is_file()
    assert resolved.parent == data_dir / ASSETS_DIRNAME / "multi"


def test_clear_question_image_in_file_sets_null(tmp_path: Path) -> None:
    """Happy path: attach then clear sets ``image_path`` back to ``None``."""

    db_path = tmp_path / "img-clear.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    attach_image_to_question_in_file(
        db_path, tmp_path, "q-two", b"payload", "x.jpg"
    )

    cleared = clear_question_image_in_file(db_path, "q-two")
    assert cleared is True

    loaded = load_questions_from_file(db_path, exam.id)
    edited = next(q for q in loaded if q.id == "q-two")
    assert edited.image_path is None


def test_clear_question_image_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — matches other ``clear_*`` helpers."""

    db_path = tmp_path / "img-clear-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    cleared = clear_question_image_in_file(db_path, "q-ghost")
    assert cleared is False


# --- 5.8 Exportovat JSON ----------------------------------------------


from datetime import datetime
import json


def test_exports_dirname_constant() -> None:
    """Exports subdir name is part of the Designer contract — guard value."""

    assert EXPORTS_DIRNAME == "exports"


def test_resolve_exports_dir_creates_subfolder(tmp_path: Path) -> None:
    """``resolve_exports_dir`` creates ``exports/`` under data_dir on first call."""

    exports = resolve_exports_dir(tmp_path)

    assert exports == tmp_path / EXPORTS_DIRNAME
    assert exports.is_dir()


def test_export_exam_to_json_in_file_writes_parseable_bundle(
    tmp_path: Path,
) -> None:
    """Happy path: exported JSON parses back through :class:`Exam` cleanly.

    Also verifies the deterministic timestamp seam (``now=...``) — we pin
    the date so the resulting filename is stable enough to assert against.
    """

    db_path = tmp_path / "export-happy.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    pinned = datetime(2026, 4, 22, 13, 45, 7)
    target = export_exam_to_json_in_file(
        db_path, exam.id, tmp_path, now=pinned
    )

    assert target == tmp_path / EXPORTS_DIRNAME / f"{exam.id}-20260422-134507.json"
    assert target.is_file()

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["id"] == exam.id
    assert payload["name"] == exam.name
    assert [q["id"] for q in payload["questions"]] == [
        "q-one",
        "q-two",
        "q-three",
    ]

    # Round-trip invariant: the written JSON must reconstruct the same
    # Exam that produced it.
    reloaded = Exam.model_validate(payload)
    assert reloaded == exam


def test_export_exam_to_json_in_file_raises_for_unknown_exam(
    tmp_path: Path,
) -> None:
    """Unknown exam id surfaces :class:`LookupError`; no file is written."""

    db_path = tmp_path / "export-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    pinned = datetime(2026, 4, 22, 13, 45, 7)
    with pytest.raises(LookupError):
        export_exam_to_json_in_file(
            db_path, "does-not-exist", tmp_path, now=pinned
        )

    # The exports dir may exist (it's created eagerly), but no actual
    # JSON file should have been written for the failing call.
    exports_dir = tmp_path / EXPORTS_DIRNAME
    if exports_dir.is_dir():
        written = list(exports_dir.glob("*.json"))
        assert written == []


def test_export_exam_to_json_in_file_does_not_touch_source_db(
    tmp_path: Path,
) -> None:
    """Export is read-only against the source DB — question list unchanged."""

    db_path = tmp_path / "export-readonly.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    pinned = datetime(2026, 4, 22, 13, 45, 7)
    export_exam_to_json_in_file(db_path, exam.id, tmp_path, now=pinned)

    # On-disk exam must still be byte-for-byte the original.
    loaded = load_questions_from_file(db_path, exam.id)
    assert loaded == exam.questions


# --- 5.9 Save-state scaffold ------------------------------------------


from posrat.designer import (
    DIRTY_LABEL_TEXT,
    SAVED_LABEL_TEXT,
    is_open_exam_dirty,
)


def test_save_state_label_constants() -> None:
    """Label captions are part of the UI contract — guard their values."""

    assert SAVED_LABEL_TEXT == "Saved"
    assert DIRTY_LABEL_TEXT == "Unsaved changes"


def test_is_open_exam_dirty_returns_false_scaffold() -> None:
    """Step 5.9 scaffold: every per-field dialog commits straight to disk,
    so there is never any in-memory dirt to track. The helper stays at
    ``False`` until the Phase 9 live Properties panel wires in proper
    per-field dirty tracking.
    """

    assert is_open_exam_dirty() is False


# --- 8.5 Complexity + Section helpers ---------------------------------


def test_update_question_complexity_in_file_sets_value(tmp_path: Path) -> None:
    """Happy path: in-range int is persisted and helper returns ``True``."""

    db_path = tmp_path / "edit-cx.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_complexity_in_file(db_path, "q-two", 4)

    assert updated is True
    loaded = load_questions_from_file(db_path, exam.id)
    by_id = {q.id: q for q in loaded}
    assert by_id["q-two"].complexity == 4
    # Siblings kept at None — edit only one row.
    assert by_id["q-one"].complexity is None
    assert by_id["q-three"].complexity is None


def test_update_question_complexity_in_file_accepts_none(tmp_path: Path) -> None:
    """Passing ``None`` clears the rating back to SQL NULL."""

    db_path = tmp_path / "edit-cx-clear.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    update_question_complexity_in_file(db_path, "q-one", 3)
    cleared = update_question_complexity_in_file(db_path, "q-one", None)
    assert cleared is True

    loaded = load_questions_from_file(db_path, exam.id)
    assert next(q for q in loaded if q.id == "q-one").complexity is None


def test_update_question_complexity_in_file_rejects_out_of_range(
    tmp_path: Path,
) -> None:
    """Out-of-range ints raise Pydantic ``ValueError`` before any write lands."""

    db_path = tmp_path / "edit-cx-bad.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    for bad in (0, 6, -1, 99):
        with pytest.raises(ValueError):
            update_question_complexity_in_file(db_path, "q-one", bad)

    # Disk state untouched — no partial application possible because
    # the Pydantic probe fires before the UPDATE statement.
    loaded = load_questions_from_file(db_path, exam.id)
    assert all(q.complexity is None for q in loaded)


def test_update_question_complexity_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` and disk untouched."""

    db_path = tmp_path / "edit-cx-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_complexity_in_file(db_path, "q-ghost", 3)
    assert updated is False


def test_update_question_section_in_file_sets_value(tmp_path: Path) -> None:
    """Happy path: non-empty tag is persisted verbatim, ``True`` returned."""

    db_path = tmp_path / "edit-sec.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_section_in_file(db_path, "q-two", "Compute")
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    by_id = {q.id: q for q in loaded}
    assert by_id["q-two"].section == "Compute"
    assert by_id["q-one"].section is None
    assert by_id["q-three"].section is None


def test_update_question_section_in_file_trims_whitespace(tmp_path: Path) -> None:
    """Leading / trailing whitespace is stripped — matches the model validator."""

    db_path = tmp_path / "edit-sec-trim.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_section_in_file(db_path, "q-one", "  IAM  ")
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    assert next(q for q in loaded if q.id == "q-one").section == "IAM"


def test_update_question_section_in_file_empty_coerces_to_none(
    tmp_path: Path,
) -> None:
    """Empty / whitespace-only string collapses to SQL NULL (not ``""``)."""

    db_path = tmp_path / "edit-sec-empty.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    # Seed a value first, then clear via empty string.
    update_question_section_in_file(db_path, "q-one", "Compute")
    updated = update_question_section_in_file(db_path, "q-one", "   ")
    assert updated is True

    loaded = load_questions_from_file(db_path, exam.id)
    assert next(q for q in loaded if q.id == "q-one").section is None


def test_update_question_section_in_file_accepts_none(tmp_path: Path) -> None:
    """Explicit ``None`` clears the tag to SQL NULL."""

    db_path = tmp_path / "edit-sec-none.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    update_question_section_in_file(db_path, "q-one", "IAM")
    cleared = update_question_section_in_file(db_path, "q-one", None)
    assert cleared is True

    loaded = load_questions_from_file(db_path, exam.id)
    assert next(q for q in loaded if q.id == "q-one").section is None


def test_update_question_section_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` and disk untouched."""

    db_path = tmp_path / "edit-sec-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_section_in_file(db_path, "q-ghost", "Section")
    assert updated is False


def test_update_question_allow_shuffle_in_file_toggles_value(
    tmp_path: Path,
) -> None:
    """Happy path: toggling allow_shuffle flips the stored boolean."""

    from posrat.designer import (
        update_question_allow_shuffle_in_file,
    )

    db_path = tmp_path / "edit-shuffle.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    # All fixture questions default to allow_shuffle=False.
    loaded_before = load_questions_from_file(db_path, exam.id)
    assert all(q.allow_shuffle is False for q in loaded_before)

    updated = update_question_allow_shuffle_in_file(db_path, "q-two", True)
    assert updated is True

    loaded_after = load_questions_from_file(db_path, exam.id)
    by_id = {q.id: q for q in loaded_after}
    assert by_id["q-two"].allow_shuffle is True
    # Siblings untouched — edit only one row.
    assert by_id["q-one"].allow_shuffle is False
    assert by_id["q-three"].allow_shuffle is False

    # Toggle back off — bool persists and the column does not get stuck.
    cleared = update_question_allow_shuffle_in_file(db_path, "q-two", False)
    assert cleared is True
    loaded_final = load_questions_from_file(db_path, exam.id)
    assert next(q for q in loaded_final if q.id == "q-two").allow_shuffle is False


def test_update_question_allow_shuffle_in_file_returns_false_for_unknown_id(
    tmp_path: Path,
) -> None:
    """Unknown id is an idempotent no-op — ``False`` and disk untouched."""

    from posrat.designer import (
        update_question_allow_shuffle_in_file,
    )

    db_path = tmp_path / "edit-shuffle-unknown.sqlite"
    exam = _build_multi_question_exam()
    db = open_db(db_path)
    try:
        create_exam(db, exam)
    finally:
        db.close()

    updated = update_question_allow_shuffle_in_file(db_path, "q-ghost", True)
    assert updated is False


# --------------------------------------------------------------------------- #
# Phase 7A.5 — Exam-level Runner metadata update helpers                      #
# --------------------------------------------------------------------------- #


def test_update_exam_default_question_count_happy_path(tmp_path) -> None:
    """``update_exam_default_question_count_in_file`` persists a fresh value."""

    from posrat.designer.browser import (
        create_exam_file,
        update_exam_default_question_count_in_file,
    )
    from posrat.designer.browser import open_exam_from_file

    path = create_exam_file(
        tmp_path, "aif-c01", "AIF-C01", description=None
    )

    assert update_exam_default_question_count_in_file(
        path, "aif-c01", 65
    ) is True

    reloaded = open_exam_from_file(path)
    assert reloaded.default_question_count == 65
    # Other metadata fields remain None.
    assert reloaded.time_limit_minutes is None
    assert reloaded.passing_score is None
    assert reloaded.target_score is None


def test_update_exam_default_question_count_can_clear_value(tmp_path) -> None:
    """Passing ``None`` clears the stored default back to SQL NULL."""

    from posrat.designer.browser import (
        create_exam_file,
        update_exam_default_question_count_in_file,
    )
    from posrat.designer.browser import open_exam_from_file

    path = create_exam_file(tmp_path, "e1", "E1")

    update_exam_default_question_count_in_file(path, "e1", 42)
    assert update_exam_default_question_count_in_file(
        path, "e1", None
    ) is True

    assert open_exam_from_file(path).default_question_count is None


def test_update_exam_default_question_count_rejects_zero(tmp_path) -> None:
    """``>= 1`` range from Pydantic must reject zero before UPDATE runs.

    The probe constructs a throwaway :class:`Exam` so the Pydantic
    :class:`ValidationError` fires **before** the SQL statement — disk
    state must remain unchanged.
    """

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_default_question_count_in_file,
    )
    from pydantic import ValidationError

    path = create_exam_file(tmp_path, "e1", "E1")
    update_exam_default_question_count_in_file(path, "e1", 50)

    with pytest.raises(ValidationError):
        update_exam_default_question_count_in_file(path, "e1", 0)

    # Pre-existing value preserved.
    assert open_exam_from_file(path).default_question_count == 50


def test_update_exam_default_question_count_returns_false_for_unknown_id(
    tmp_path,
) -> None:
    """Unknown exam id is an idempotent no-op (``False``, no disk changes)."""

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_default_question_count_in_file,
    )

    path = create_exam_file(tmp_path, "e1", "E1")

    assert update_exam_default_question_count_in_file(
        path, "ghost", 99
    ) is False
    # Real exam stays untouched.
    assert open_exam_from_file(path).default_question_count is None


def test_update_exam_time_limit_minutes_roundtrip(tmp_path) -> None:
    """Happy path + clear cycle for ``time_limit_minutes``."""

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_time_limit_minutes_in_file,
    )

    path = create_exam_file(tmp_path, "e1", "E1")
    assert update_exam_time_limit_minutes_in_file(path, "e1", 90) is True
    assert open_exam_from_file(path).time_limit_minutes == 90

    assert update_exam_time_limit_minutes_in_file(path, "e1", None) is True
    assert open_exam_from_file(path).time_limit_minutes is None


def test_update_exam_time_limit_minutes_rejects_zero(tmp_path) -> None:
    from posrat.designer.browser import (
        create_exam_file,
        update_exam_time_limit_minutes_in_file,
    )
    from pydantic import ValidationError

    path = create_exam_file(tmp_path, "e1", "E1")
    with pytest.raises(ValidationError):
        update_exam_time_limit_minutes_in_file(path, "e1", 0)


def test_update_exam_passing_score_roundtrip(tmp_path) -> None:
    """Happy path for ``passing_score`` with no stored ``target_score``."""

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_passing_score_in_file,
    )

    path = create_exam_file(tmp_path, "e1", "E1")
    assert update_exam_passing_score_in_file(path, "e1", 700) is True
    assert open_exam_from_file(path).passing_score == 700


def test_update_exam_passing_score_rejects_above_target(tmp_path) -> None:
    """Cross-field validator: passing > target is refused before UPDATE.

    Setup seeds ``target_score = 1000`` via the target helper, then
    attempts to push ``passing_score = 1100`` — the probe re-runs the
    ``passing_score <= target_score`` validator and raises before the
    SQL statement. On-disk passing_score remains unset.
    """

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_passing_score_in_file,
        update_exam_target_score_in_file,
    )
    from pydantic import ValidationError

    path = create_exam_file(tmp_path, "e1", "E1")
    update_exam_target_score_in_file(path, "e1", 1000)

    with pytest.raises(ValidationError):
        update_exam_passing_score_in_file(path, "e1", 1100)

    # ``passing_score`` untouched, ``target_score`` untouched too.
    loaded = open_exam_from_file(path)
    assert loaded.passing_score is None
    assert loaded.target_score == 1000


def test_update_exam_target_score_rejects_below_stored_passing(tmp_path) -> None:
    """Lowering ``target_score`` below stored ``passing_score`` must fail.

    The cross-field validator is symmetric: once ``passing_score = 700``
    is persisted, subsequent ``target_score = 500`` attempts are
    rejected so pass-fail evaluation cannot trip over an impossible
    threshold.
    """

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_passing_score_in_file,
        update_exam_target_score_in_file,
    )
    from pydantic import ValidationError

    path = create_exam_file(tmp_path, "e1", "E1")
    update_exam_target_score_in_file(path, "e1", 1000)
    update_exam_passing_score_in_file(path, "e1", 700)

    with pytest.raises(ValidationError):
        update_exam_target_score_in_file(path, "e1", 500)

    # Target untouched.
    assert open_exam_from_file(path).target_score == 1000


def test_update_exam_target_score_rejects_zero(tmp_path) -> None:
    from posrat.designer.browser import (
        create_exam_file,
        update_exam_target_score_in_file,
    )
    from pydantic import ValidationError

    path = create_exam_file(tmp_path, "e1", "E1")
    with pytest.raises(ValidationError):
        update_exam_target_score_in_file(path, "e1", 0)


def test_update_exam_metadata_fields_are_independent(tmp_path) -> None:
    """Each helper touches exactly one column, never the siblings.

    Seed all four fields, then flip only one at a time and assert the
    other three survive verbatim. This pins the "targeted UPDATE per
    field" contract that keeps blur-save from stomping on concurrent
    edits to a different field.
    """

    from posrat.designer.browser import (
        create_exam_file,
        open_exam_from_file,
        update_exam_default_question_count_in_file,
        update_exam_passing_score_in_file,
        update_exam_target_score_in_file,
        update_exam_time_limit_minutes_in_file,
    )

    path = create_exam_file(tmp_path, "e1", "E1")
    update_exam_default_question_count_in_file(path, "e1", 65)
    update_exam_time_limit_minutes_in_file(path, "e1", 90)
    update_exam_target_score_in_file(path, "e1", 1000)
    update_exam_passing_score_in_file(path, "e1", 700)

    # Flip default_question_count only.
    assert update_exam_default_question_count_in_file(
        path, "e1", 100
    ) is True
    loaded = open_exam_from_file(path)
    assert loaded.default_question_count == 100
    assert loaded.time_limit_minutes == 90
    assert loaded.passing_score == 700
    assert loaded.target_score == 1000

    # Flip passing_score only.
    assert update_exam_passing_score_in_file(path, "e1", 650) is True
    loaded = open_exam_from_file(path)
    assert loaded.passing_score == 650
    assert loaded.default_question_count == 100
    assert loaded.time_limit_minutes == 90
    assert loaded.target_score == 1000
