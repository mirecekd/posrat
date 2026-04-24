"""Runner history — session result summaries across all exams.

The picker page shows two panels: exams on the left (runnable), results
on the right (finished/in-progress sessions). This module owns the
read-only scan for the right panel:

* :class:`SessionResultSummary` — frozen dataclass per session with
  score + metadata.
* :func:`list_session_results(data_dir)` — walks every ``.sqlite`` in
  the data dir and flattens all sessions into one sorted list (newest
  first).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from posrat.designer.browser import list_exam_files
from posrat.runner.orchestrator import SessionScore, compute_session_score
from posrat.storage import get_exam, list_sessions, open_db


@dataclass(frozen=True)
class SessionResultSummary:
    """One row on the Runner's history panel.

    Flattens a :class:`~posrat.models.Session` together with its
    :class:`SessionScore` and the owning exam's display name, so the
    UI layer only has to render values without opening the DB again.
    """

    exam_path: Path
    exam_id: str
    exam_name: str
    session_id: str
    candidate_name: Optional[str]
    mode: str
    started_at: str
    finished_at: Optional[str]
    score: SessionScore

    @property
    def is_finished(self) -> bool:
        """Return ``True`` when ``finished_at`` is present (session closed)."""

        return bool(self.finished_at)


def _summarise_file_sessions(path: Path) -> list[SessionResultSummary]:
    """Return all session summaries stored in a single exam ``.sqlite``.

    Opens the file, pulls every session + exam header + computed score,
    then closes the connection. Silently returns ``[]`` on non-POSRAT
    files or unreadable DBs — the picker never crashes on a stray file
    in the data dir.
    """

    try:
        db = open_db(path)
    except sqlite3.DatabaseError:
        return []

    try:
        # Need the exam id first so we can list its sessions. Skip
        # files without an ``exams`` table (stray SQLite DBs).
        try:
            exam_row = db.execute(
                "SELECT id, name FROM exams LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return []

        if exam_row is None:
            return []

        exam_id = str(exam_row["id"])
        exam_name = str(exam_row["name"])

        sessions = list_sessions(db, exam_id)
        summaries: list[SessionResultSummary] = []
        for session in sessions:
            score = compute_session_score(session)
            summaries.append(
                SessionResultSummary(
                    exam_path=path,
                    exam_id=exam_id,
                    exam_name=exam_name,
                    session_id=session.id,
                    candidate_name=session.candidate_name,
                    mode=session.mode,
                    started_at=session.started_at,
                    finished_at=session.finished_at,
                    score=score,
                )
            )
        return summaries
    finally:
        db.close()


def list_session_results(data_dir: Path) -> list[SessionResultSummary]:
    """Return all sessions from every exam under ``data_dir``.

    Walks :func:`list_exam_files` and concatenates the per-file
    summaries. The result is sorted by ``started_at`` **descending**
    so the newest attempts show up first — candidates usually care
    about their latest runs, older history scrolls below.

    Files that fail to parse (invalid DB, no exam row, …) are
    silently skipped so one corrupted backup does not hide all valid
    history.
    """

    all_summaries: list[SessionResultSummary] = []
    for path in list_exam_files(data_dir):
        all_summaries.extend(_summarise_file_sessions(path))

    all_summaries.sort(key=lambda s: s.started_at, reverse=True)
    return all_summaries


__all__ = [
    "SessionResultSummary",
    "list_session_results",
]
