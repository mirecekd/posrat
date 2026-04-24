"""Smoke tests for the Designer bulk-import dialog (Fáze 8.3).

The dialog itself is a NiceGUI callback tree — we don't unit-test the
visual layer here (that would require booting a browser). Instead we
exercise the pure, side-effect-free helpers in
:mod:`posrat.designer.import_dialog` and its ``_parse_uploaded_bytes``
tempfile dance; the actual parser + persistence have their own
dedicated suites.
"""

from __future__ import annotations

from posrat.designer.import_dialog import (
    MAX_IMPORT_FILE_BYTES,
    PENDING_IMPORT_SELECTION_KEY,
    PENDING_IMPORT_STORAGE_KEY,
    _format_question_type,
    _parse_uploaded_bytes,
    _show_bulk_import_dialog,
    _truncate,
)


def test_module_imports() -> None:
    """Importing the module must not touch NiceGUI global state."""

    assert MAX_IMPORT_FILE_BYTES > 10_000_000
    assert PENDING_IMPORT_STORAGE_KEY != PENDING_IMPORT_SELECTION_KEY
    assert callable(_show_bulk_import_dialog)


def test_truncate_clips_and_appends_ellipsis() -> None:
    text = "A " * 100  # 200 chars
    out = _truncate(text, limit=20)
    assert len(out) == 20
    assert out.endswith("…")


def test_truncate_leaves_short_strings_untouched() -> None:
    assert _truncate("short", limit=80) == "short"


def test_truncate_collapses_whitespace() -> None:
    assert _truncate("a\n\n  b\tc", limit=80) == "a b c"


def test_format_question_type_maps_known_types() -> None:
    assert _format_question_type("single_choice") == "Single"
    assert _format_question_type("multi_choice") == "Multi"
    assert _format_question_type("hotspot") == "Hotspot"


def test_format_question_type_passthrough_for_unknown() -> None:
    assert _format_question_type("custom") == "custom"


def test_parse_uploaded_bytes_runs_registered_examtopics_parser() -> None:
    """End-to-end: the helper wraps bytes in a tempfile and hits the
    registered ExamTopics parser, returning a usable :class:`ParseResult`.
    """
    import posrat.importers.examtopics  # noqa: F401 — ensure registration

    rtf = (
        b"{\\rtf1\\ansi Q1\\'0d"
        b"What is X?\\par "
        b"\\par "
        b"A. Alpha\\par "
        b"B. Beta\\par "
        b"\\par "
        b"Answer: A\\par "
        b"}"
    )
    result = _parse_uploaded_bytes("examtopics", "mini.rtf", rtf)
    assert len(result.questions) == 1
    assert len(result.parse_errors) == 0
    correct = [c for c in result.questions[0].choices if c.is_correct]
    assert [c.letter for c in correct] == ["A"]


def test_show_bulk_import_dialog_signature_accepts_preselect() -> None:
    """The per-format toolbar buttons pass ``preselect_source_id``;
    the helper must accept it as a keyword argument without raising.

    Structural (introspection-only) test because actually opening the
    dialog requires a NiceGUI request context. The inspection locks
    in the public signature contract — regressions where someone
    removes the kwarg break this test immediately.
    """
    import inspect

    sig = inspect.signature(_show_bulk_import_dialog)
    assert "preselect_source_id" in sig.parameters
    param = sig.parameters["preselect_source_id"]
    # Default must be ``None`` so existing call sites without preselect
    # continue to work (legacy behaviour preserved).
    assert param.default is None


def test_parse_uploaded_bytes_runs_registered_certexam_pdf_parser() -> None:
    """The in-memory tempfile dance must also dispatch to the PDF
    parser when ``source_id="certexam_pdf"``. We skip if the
    reference fixture isn't on disk — no built-in PDF is small
    enough to embed inline."""
    import os
    from pathlib import Path

    import pytest

    import posrat.importers.certexam_pdf  # noqa: F401 — ensure registration

    path = Path(
        os.environ.get(
            "POSRAT_CERTEXAM_PDF", "/mnt/c/DATA/certy/export-exam_designer.pdf"
        )
    )
    if not path.is_file():
        pytest.skip("real CertExam PDF fixture not available")

    result = _parse_uploaded_bytes(
        "certexam_pdf", path.name, path.read_bytes()
    )
    assert len(result.questions) > 100
    assert result.source_metadata.get("passing_score") == "700"
