"""JSON validation and SQLite import helpers (step 2.8).

Keep the logic dumb and linear: load bytes / string, hand them to Pydantic,
and reuse :func:`posrat.storage.create_exam` to persist. That way every
fail-fast guard baked into the models is exercised before a single row
reaches SQLite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from posrat.models import Exam
from posrat.storage import create_exam

PathLike = Union[str, Path]


def load_exam_from_json_str(payload: str) -> Exam:
    """Parse ``payload`` (JSON) and return a validated :class:`Exam`.

    Pydantic raises :class:`pydantic.ValidationError` on any structural or
    cross-field inconsistency; the caller gets a full error tree instead
    of a silently-half-loaded exam.
    """
    return Exam.model_validate_json(payload)


def load_exam_from_json_file(path: PathLike) -> Exam:
    """Read ``path`` and return a validated :class:`Exam`."""
    text = Path(path).read_text(encoding="utf-8")
    return load_exam_from_json_str(text)


def import_exam_from_json_file(
    db: sqlite3.Connection,
    path: PathLike,
) -> Exam:
    """Load + validate the JSON at ``path`` and persist it via ``db``.

    Returns the validated :class:`Exam` so callers can immediately use it
    without re-loading.
    """
    exam = load_exam_from_json_file(path)
    create_exam(db, exam)
    return exam
