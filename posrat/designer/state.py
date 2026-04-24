"""Designer — per-user UI state for the 3-panel layout.

The Exam Explorer highlights one question at a time; the Properties and
Main Editor panels then render that selected question's fields. To keep
selection surviving Designer refreshes (add question, move up/down,
edit text / type / choices / explanation / image, delete) we stash the
selected question id inside :data:`nicegui.app.storage.user`.

Deliberately a tiny module: the 3-panel render helpers only need one
storage key plus a handful of get/set helpers, and keeping them in
their own file keeps ``posrat/designer/browser.py`` focused on pure
DAO-level helpers and the (legacy) dialog UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from nicegui import app

from posrat.designer.browser import (
    OPEN_EXAM_STORAGE_KEY,
    load_questions_for_open_exam,
)
from posrat.models import Question


#: Key used inside ``app.storage.user`` to persist the id of the Question
#: currently highlighted in the Exam Explorer. ``None`` (or the key
#: missing) means "no selection" — the Properties panel renders an
#: empty state and the Editor shows a placeholder.
SELECTED_QUESTION_ID_STORAGE_KEY = "designer_selected_question_id"


def get_selected_question_id() -> str | None:
    """Return the currently selected question id, or ``None``.

    Reads straight from :data:`nicegui.app.storage.user`; callers inside
    a NiceGUI request context can therefore treat this as a pure view
    onto the user-scoped session. Outside a request context (e.g. in
    unit tests that do not spin up a server) NiceGUI raises, so tests
    stub the storage themselves instead of hitting this helper.
    """

    value = app.storage.user.get(SELECTED_QUESTION_ID_STORAGE_KEY)
    if value is None:
        return None
    return str(value)


def select_question(question_id: str | None) -> None:
    """Store ``question_id`` as the currently selected row.

    Passing ``None`` clears the selection (e.g. after deleting the
    selected question or closing the exam). The storage cookie stays
    small — one short string per user — so there's no downside to
    writing it eagerly on every selection change.
    """

    if question_id is None:
        app.storage.user.pop(SELECTED_QUESTION_ID_STORAGE_KEY, None)
        return
    app.storage.user[SELECTED_QUESTION_ID_STORAGE_KEY] = str(question_id)


def get_selected_question() -> Optional[Question]:
    """Return the currently selected :class:`Question`, fresh from disk.

    Loads the full question list for the opened exam via
    :func:`load_questions_for_open_exam` and filters by the selected id.
    Returning ``None`` both when nothing is selected and when the
    selected id no longer matches any row (e.g. another tab deleted it)
    lets the UI branch once on ``None`` for the "render placeholder"
    case. Deliberately *does not* auto-heal a dangling selection — the
    calling render code handles that with its own refresh invalidation
    so the user sees a transient placeholder instead of a silent jump
    to a different question.
    """

    selected_id = get_selected_question_id()
    if selected_id is None:
        return None
    for question in load_questions_for_open_exam():
        if question.id == selected_id:
            return question
    return None


def ensure_selection_valid(questions: list[Question]) -> str | None:
    """Normalise the stored selection against ``questions`` and return it.

    Helper called from the Explorer render: if no selection exists yet
    but the exam has at least one question, pick the first one so the
    Properties / Editor panels have something to show on first paint.
    If the current selection points at a question that no longer
    exists, fall back to the first row as well — avoids a stale
    highlight after a delete. Returning the resolved id (or ``None``
    for an empty exam) spares the caller a second ``get_*`` round-trip.

    The helper mutates user storage directly, so it must be called from
    a NiceGUI request context. That matches the Designer render paths
    exclusively; tests exercise the underlying ``get/select`` pair
    instead because they bypass the storage entirely.
    """

    known_ids = {q.id for q in questions}
    current = get_selected_question_id()

    if current in known_ids:
        return current

    # Either nothing selected yet, or the selection went stale. Pick
    # the first question if any, otherwise clear.
    if questions:
        fallback = questions[0].id
        select_question(fallback)
        return fallback

    select_question(None)
    return None


def clear_selection_if_id(question_id: str) -> None:
    """Clear the selection iff it currently points at ``question_id``.

    Called after deleting a question so the Explorer does not keep a
    dangling id in storage. Other selection states are left alone so a
    parallel-tab edit does not silently drop a still-valid highlight.
    """

    if get_selected_question_id() == question_id:
        select_question(None)


__all__ = [
    "SELECTED_QUESTION_ID_STORAGE_KEY",
    "clear_selection_if_id",
    "ensure_selection_valid",
    "get_selected_question",
    "get_selected_question_id",
    "select_question",
]
