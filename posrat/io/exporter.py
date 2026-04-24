"""Export helpers: SQLite connection → JSON bundle payload (step 2.9).

Mirror of :mod:`posrat.io.validator`. Reconstruct the :class:`Exam` via
the DAO (which already orders questions/choices by ``order_index`` and
raises :class:`NotImplementedError` for hotspots), then dump it through
Pydantic so the output is guaranteed to round-trip back via
:func:`posrat.io.load_exam_from_json_str`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from posrat.models import Exam
from posrat.storage import get_exam

PathLike = Union[str, Path]


def dump_exam_to_json(
    db: sqlite3.Connection,
    exam_id: str,
    *,
    indent: int | None = 2,
) -> str:
    """Return a JSON string for the exam with ``exam_id``.

    Raises :class:`LookupError` when the exam is missing so callers do
    not have to inspect a silent empty string.
    """
    exam = get_exam(db, exam_id)
    if exam is None:
        raise LookupError(f"exam id not found: {exam_id!r}")
    if indent is None:
        return exam.model_dump_json()
    return exam.model_dump_json(indent=indent)


def export_exam_to_json_file(
    db: sqlite3.Connection,
    exam_id: str,
    path: PathLike,
    *,
    indent: int | None = 2,
) -> Exam:
    """Write the exam with ``exam_id`` to ``path`` as JSON and return it."""
    exam = get_exam(db, exam_id)
    if exam is None:
        raise LookupError(f"exam id not found: {exam_id!r}")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if indent is None:
        target.write_text(exam.model_dump_json(), encoding="utf-8")
    else:
        target.write_text(
            exam.model_dump_json(indent=indent), encoding="utf-8"
        )
    return exam
