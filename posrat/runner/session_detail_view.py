"""Session detail view — per-question review of a finished session.

Rendered by :func:`posrat.runner.page._render_runner_body` when the
per-user stash under :data:`RUNNER_DETAIL_STORAGE_KEY` points to a
completed session (drill-down from the history panel). Reads the
full :class:`~posrat.runner.session_detail.SessionDetail` via the
pure loader and renders:

* A summary card at the top (exam name, candidate, score, pass/fail
  chip, unanswered count).
* A chronological list of per-question cards. Each card reuses the
  same inline-highlight style as the training feedback (green row
  for the correct choice, red row for a wrong pick) so the candidate
  sees their mistake in place without scrolling to a separate answer
  key.
* A "Back to history" button that clears the detail stash and sends
  the user back to the picker/history panel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from nicegui import app, ui

from posrat.models import Question
from posrat.runner.session_detail import (
    QuestionReview,
    SessionDetail,
    load_session_detail,
)
from posrat.runner.view_helpers import choice_row_classes, letter_for


#: Per-user storage key holding the drill-down target for the
#: history panel. Shape: ``{"exam_path": "...", "session_id": "..."}``.
#: Separate from :data:`posrat.runner.session_state.RUNNER_SESSION_STORAGE_KEY`
#: so viewing a past result does not interfere with an in-progress
#: run's stash.
RUNNER_DETAIL_STORAGE_KEY = "runner_detail"


def render_session_detail(stash: dict) -> None:
    """Render the full per-question review for a past session."""

    exam_path = Path(str(stash.get("exam_path") or ""))
    session_id = str(stash.get("session_id") or "")

    detail = load_session_detail(exam_path, session_id)
    if detail is None:
        ui.label("Detail is not available (file or session missing).").classes(
            "text-negative"
        )
        _render_back_button()
        return

    _render_summary_card(detail)

    ui.separator().classes("q-my-md")

    if not detail.reviews:
        ui.label("Session has no recorded answers.").classes(
            "text-caption text-grey"
        )
    else:
        with ui.column().classes("q-gutter-md w-full"):
            for review in detail.reviews:
                _render_question_card(review)

    _render_back_button()


def _render_summary_card(detail: SessionDetail) -> None:
    """Top card: exam name, candidate, mode, score, pass/fail chip."""

    sess = detail.session
    score = detail.score

    with ui.card().classes("w-full").props("bordered"):
        with ui.row().classes("items-center q-gutter-sm w-full no-wrap"):
            ui.label(detail.exam_name).classes("text-h6 col-grow")
            _render_state_chip(detail)

        details = [
            f"Candidate: {sess.candidate_name or '(unknown)'}",
            f"Mode: {sess.mode}",
            f"Started: {sess.started_at}",
        ]
        if sess.finished_at:
            details.append(f"Finished: {sess.finished_at}")
        ui.label(" · ".join(details)).classes("text-caption text-grey")

        ui.separator().classes("q-my-sm")

        score_parts = [
            f"Correct: {score.correct_count} / {score.total_count}"
        ]
        if score.percent is not None:
            score_parts.append(f"{score.percent:.1f} %")
        if score.raw_score is not None and sess.target_score is not None:
            score_parts.append(
                f"{score.raw_score} / {sess.target_score} points"
            )
        ui.label(" · ".join(score_parts)).classes("text-body1")

        if detail.unanswered_count:
            ui.label(
                f"Unanswered: {detail.unanswered_count}"
            ).classes("text-caption text-grey")


def _render_state_chip(detail: SessionDetail) -> None:
    """Render the pass/fail/in-progress chip used in the summary card."""

    if not detail.session.finished_at:
        ui.badge("in progress").props("color=grey")
        return
    passed = detail.score.passed
    if passed is True:
        ui.badge("PASS").props("color=positive")
    elif passed is False:
        ui.badge("FAIL").props("color=negative")
    else:
        ui.badge("completed").props("color=secondary")


def _render_question_card(review: QuestionReview) -> None:
    """Render one review card: Q text + highlighted choices + explanation."""

    question = review.question
    with ui.card().classes("w-full").props("bordered"):
        # Header row: Q-index + Correct/Wrong chip.
        with ui.row().classes("items-center q-gutter-sm w-full no-wrap"):
            ui.label(f"Question {review.index}").classes(
                "text-subtitle2 col-grow"
            )
            if review.is_correct:
                ui.badge("CORRECT").props("color=positive")
            else:
                ui.badge("WRONG").props("color=negative")

        ui.markdown(question.text).classes("text-body1 q-mt-xs")

        _render_review_body(review)

        if question.explanation:
            ui.separator().classes("q-my-sm")
            ui.label("Explanation / reference").classes(
                "text-subtitle2"
            )
            ui.markdown(question.explanation).classes("text-body2")


def _render_review_body(review: QuestionReview) -> None:
    """Dispatch per-type rendering of a read-only, highlighted answer list."""

    question = review.question
    payload = review.given_payload

    if question.type == "single_choice":
        _render_choice_rows(
            question,
            picked_ids=_collect_single(payload),
        )
    elif question.type == "multi_choice":
        _render_choice_rows(
            question,
            picked_ids=_collect_multi(payload),
        )
    elif question.type == "hotspot":
        _render_hotspot_review(question, payload)
    else:  # pragma: no cover - defensive
        ui.label(f"Unknown type: {question.type}").classes("text-negative")


def _collect_single(payload: Optional[Any]) -> set[str]:
    """Return the picked choice id from a single_choice payload as a set."""

    if isinstance(payload, dict):
        cid = payload.get("choice_id")
        if isinstance(cid, str):
            return {cid}
    return set()


def _collect_multi(payload: Optional[Any]) -> set[str]:
    """Return the picked choice ids from a multi_choice payload."""

    if isinstance(payload, dict):
        raw = payload.get("choice_ids")
        if isinstance(raw, list):
            return {str(x) for x in raw}
    return set()


def _render_choice_rows(
    question: Question, *, picked_ids: set[str]
) -> None:
    """Render every choice as a read-only row with review highlighting.

    Same class palette as the Runner's training feedback rows so the
    detail view feels visually identical to what the candidate saw at
    answer time — correct rows green, wrong picks red, untouched rows
    neutral.
    """

    for idx, choice in enumerate(question.choices):
        picked = choice.id in picked_ids
        row_classes = "items-center q-gutter-sm no-wrap w-full " + choice_row_classes(
            is_correct=choice.is_correct,
            picked=picked,
            feedback_pending=True,
        )
        with ui.row().classes(row_classes):
            marker = "●" if picked else "○"
            ui.label(marker).classes("text-weight-medium q-mr-xs")
            ui.label(f"{letter_for(idx)}.").classes(
                "text-weight-medium q-mr-xs"
            )
            ui.markdown(choice.text).classes("col-grow")


def _render_hotspot_review(
    question: Question, payload: Optional[Any]
) -> None:
    """Render per-step rows for a hotspot question in review mode."""

    if question.hotspot is None:  # pragma: no cover - defensive
        ui.label("Invalid hotspot.").classes("text-negative")
        return

    picks: dict[str, str] = {}
    if isinstance(payload, dict):
        raw = payload.get("step_option_ids")
        if isinstance(raw, dict):
            picks = {str(k): str(v) for k, v in raw.items()}

    option_map = {opt.id: opt.text for opt in question.hotspot.options}
    for step in question.hotspot.steps:
        user_pick = picks.get(step.id)
        correct = step.correct_option_id
        row_classes = choice_row_classes(
            is_correct=(user_pick == correct),
            picked=bool(user_pick),
            feedback_pending=True,
        )
        with ui.column().classes(f"w-full q-mt-sm {row_classes}"):
            ui.label(step.prompt).classes("text-body2")
            user_text = option_map.get(user_pick, "(no answer)") if user_pick else "(no answer)"
            ui.label(f"Your answer: {user_text}").classes("text-caption")
            if user_pick != correct:
                ui.label(
                    f"Correct: {option_map.get(correct, correct)}"
                ).classes("text-caption text-positive")


def _render_back_button() -> None:
    """Clear the detail stash and refresh back to picker/history."""

    def _back() -> None:
        app.storage.user[RUNNER_DETAIL_STORAGE_KEY] = None
        # Lazy import keeps the graph acyclic — page.py owns the refreshable.
        from posrat.runner.page import _render_runner_body
        _render_runner_body.refresh()

    with ui.row().classes("justify-end q-mt-md"):
        ui.button("Back to history", on_click=_back).props(
            "color=secondary"
        )


__all__ = [
    "RUNNER_DETAIL_STORAGE_KEY",
    "render_session_detail",
]
