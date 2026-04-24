"""Per-user session state stashed in ``app.storage.user`` for the Runner.

The Runner's page body needs to remember, across refreshes and new
tabs from the same browser, *which* session is in progress and *where*
in the question list the candidate is. Rather than spread ``setdefault``
calls across UI code, this module centralises the storage shape as a
plain dict serialised under a single key.

Keys in the stash (all JSON-serialisable — ``app.storage.user`` lives
in a signed browser cookie):

* ``session_id`` — :class:`posrat.models.Session.id` of the active
  session. ``None`` when no session is running.
* ``exam_path`` — absolute string path to the ``.sqlite`` file hosting
  the session's exam. Pinned so we don't need a DB-wide session lookup
  on every keystroke.
* ``exam_id`` — convenience mirror of ``Session.exam_id``.
* ``mode`` — ``"training"`` or ``"exam"``.
* ``question_ids`` — list of question ids in presentation order,
  exactly as returned by :func:`posrat.runner.orchestrator.start_runner_session`.
* ``current_index`` — zero-based cursor into ``question_ids``.
* ``started_at`` — ISO-8601 timestamp pinned for the timer widget.
* ``time_limit_minutes`` — snapshot of the session's timer budget
  (``None`` when no timer).
* ``candidate_name`` — displayed in the page header.

The Runner clears the stash when the user finishes / abandons a
session so the picker page knows to show the list again rather than
resuming.
"""

from __future__ import annotations

from typing import Any, Optional


#: Key under which the Runner session stash lives inside
#: :data:`app.storage.user`. Kept separate from
#: :data:`posrat.designer.browser.OPEN_EXAM_STORAGE_KEY` so the Runner
#: and the Designer cannot overwrite each other's state.
RUNNER_SESSION_STORAGE_KEY = "runner_session"


def build_runner_session_stash(
    *,
    session_id: str,
    exam_path: str,
    exam_id: str,
    mode: str,
    question_ids: list[str],
    started_at: str,
    time_limit_minutes: Optional[int],
    candidate_name: str,
) -> dict[str, Any]:
    """Return the JSON-serialisable dict stashed in ``app.storage.user``.

    Kept as a pure function so tests can assert the shape without
    going through NiceGUI. The Runner page uses this in its "start
    session" handler and in session-resume flows.

    Two extra dicts are initialised to empty and populated lazily by
    the question view during the session:

    * ``choice_orders`` — per-question ``list[choice_id]`` capturing
      the shuffled presentation order. Pinned at first-render time so
      A/B/C/D labels and previous-answer pre-fill survive prev/next
      navigation.
    * ``given_answers`` — per-question payload dict (``{"choice_id":
      ...}`` / ``{"choice_ids": [...]}`` / ``{"step_option_ids": {...}}``)
      of the candidate's last submission. Used to re-seed the input
      widgets when the candidate navigates back to a previously
      answered question.
    * ``feedback_pending_for`` — set to the current question id while
      a wrong-answer card is being displayed (training mode only); the
      view then locks the inputs and highlights correct/wrong rows.
      Cleared by the "Continue" handler.
    """

    return {
        "session_id": session_id,
        "exam_path": exam_path,
        "exam_id": exam_id,
        "mode": mode,
        "question_ids": list(question_ids),
        "current_index": 0,
        "started_at": started_at,
        "time_limit_minutes": time_limit_minutes,
        "candidate_name": candidate_name,
        "choice_orders": {},
        "given_answers": {},
        "feedback_pending_for": None,
    }


def is_session_stash_complete(stash: Any) -> bool:
    """Return ``True`` when ``stash`` has every key the Runner page needs.

    Guards against partial storage from older builds — if the cookie
    carries a stash missing (say) ``time_limit_minutes`` the Runner
    should fall back to "no session" rather than crashing on a
    KeyError. Only the original 7.1-era keys are required here; the
    newer ``choice_orders`` / ``given_answers`` / ``feedback_pending_for``
    keys are defensively defaulted by their consumers (see
    :func:`posrat.runner.page._get_choice_order` for example) so a
    mid-migration stash from a running server upgrade does not wipe an
    in-progress session.
    """

    if not isinstance(stash, dict):
        return False
    required = {
        "session_id",
        "exam_path",
        "exam_id",
        "mode",
        "question_ids",
        "current_index",
        "started_at",
        "time_limit_minutes",
        "candidate_name",
    }
    return required.issubset(stash.keys())



def advance_session_stash(stash: dict[str, Any]) -> bool:
    """Increment ``stash['current_index']`` in place; return ``True`` when
    the cursor now points *past* the last question (session finished).

    Using a helper rather than hand-rolling ``stash["current_index"] += 1``
    at every call site keeps the "am I done?" check centralised — the
    Runner page just checks the returned bool to decide whether to
    render the next question or navigate to the results screen.
    """

    stash["current_index"] = int(stash.get("current_index", 0)) + 1
    return stash["current_index"] >= len(stash.get("question_ids", []))


__all__ = [
    "RUNNER_SESSION_STORAGE_KEY",
    "advance_session_stash",
    "build_runner_session_stash",
    "is_session_stash_complete",
]
