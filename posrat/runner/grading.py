"""Pure helpers for grading a Runner :class:`Answer` payload.

The Runner stores a user's response as an opaque ``given_json`` string
on :class:`posrat.models.Answer` so the persistence layer does not need
to know the per-question-type schema. Grading happens in this module:

* :func:`encode_answer_payload` — canonical dict → JSON string
  (sorted keys, no whitespace) so the same input always produces the
  same :class:`Answer.given_json`. Makes results deterministic for
  export / comparison.
* :func:`grade_answer` — compares a candidate's answer payload against
  a :class:`Question` and returns ``(is_correct, given_json)``.

Supported payload shapes (all JSON-encoded):

* ``single_choice``:  ``{"choice_id": "<choice.id>"}``
* ``multi_choice``:   ``{"choice_ids": ["<id>", ...]}`` (unordered set)
* ``hotspot``:        ``{"step_option_ids": {"<step.id>": "<option.id>"}}``

All three shapes are validated defensively so grading a malformed
payload raises a clear :class:`ValueError` instead of silently
returning the wrong score.
"""

from __future__ import annotations

import json
from typing import Any

from posrat.models import Question


def encode_answer_payload(payload: Any) -> str:
    """Serialise ``payload`` into a canonical JSON string.

    ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` gives a
    deterministic string representation — two logically-equal payloads
    always produce identical bytes, which the export layer relies on
    for reproducible session bundles.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _decode_or_raise(given_json: str) -> Any:
    """Parse ``given_json`` or raise ``ValueError`` with a friendly message."""

    try:
        return json.loads(given_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"given_json is not valid JSON: {exc}") from exc


def _grade_single_choice(question: Question, payload: Any) -> bool:
    """Return ``True`` when ``payload['choice_id']`` matches the correct choice."""

    if not isinstance(payload, dict) or "choice_id" not in payload:
        raise ValueError(
            "single_choice payload must be a dict with 'choice_id'"
        )
    chosen = payload["choice_id"]
    if chosen is None or chosen == "":
        # Unanswered single_choice is always wrong.
        return False

    correct_ids = {c.id for c in question.choices if c.is_correct}
    return chosen in correct_ids


def _grade_multi_choice(question: Question, payload: Any) -> bool:
    """Return ``True`` when ``payload['choice_ids']`` matches the correct set.

    Multi_choice grading is strict set equality — picking fewer than
    all correct answers (or picking any incorrect answer) fails. That
    matches the Visual CertExam rule and keeps the scoring unambiguous.
    """

    if not isinstance(payload, dict) or "choice_ids" not in payload:
        raise ValueError(
            "multi_choice payload must be a dict with 'choice_ids'"
        )
    chosen = payload["choice_ids"]
    if not isinstance(chosen, list):
        raise ValueError(
            "multi_choice payload 'choice_ids' must be a list"
        )
    chosen_set = {str(item) for item in chosen}
    correct_set = {c.id for c in question.choices if c.is_correct}
    return chosen_set == correct_set


def _grade_hotspot(question: Question, payload: Any) -> bool:
    """Return ``True`` when every hotspot step's option matches the correct one.

    A hotspot question passes only when **every** step is answered
    correctly — a single wrong dropdown pick fails the whole question.
    This mirrors the original Visual CertExam behaviour and is the
    most straightforward rule that still supports partial-credit in
    future without breaking the binary ``is_correct`` column.
    """

    if question.hotspot is None:
        raise ValueError(
            "hotspot grading invoked but question.hotspot is None"
        )
    if not isinstance(payload, dict) or "step_option_ids" not in payload:
        raise ValueError(
            "hotspot payload must be a dict with 'step_option_ids'"
        )
    picks = payload["step_option_ids"]
    if not isinstance(picks, dict):
        raise ValueError(
            "hotspot payload 'step_option_ids' must be a dict"
        )

    for step in question.hotspot.steps:
        if picks.get(step.id) != step.correct_option_id:
            return False
    return True


def grade_answer(
    question: Question, payload: Any
) -> tuple[bool, str]:
    """Grade ``payload`` against ``question``.

    Returns a ``(is_correct, given_json)`` tuple the Runner can hand
    straight to :func:`posrat.storage.record_answer`. ``given_json`` is
    produced via :func:`encode_answer_payload` so it stays canonical
    regardless of Python dict ordering.

    Raises :class:`ValueError` for unknown question types or malformed
    payloads so the Runner surfaces the bug instead of silently
    marking a wrong question "correct".
    """

    dispatch = {
        "single_choice": _grade_single_choice,
        "multi_choice": _grade_multi_choice,
        "hotspot": _grade_hotspot,
    }
    grader = dispatch.get(question.type)
    if grader is None:
        raise ValueError(f"cannot grade unknown question type: {question.type!r}")

    is_correct = grader(question, payload)
    return is_correct, encode_answer_payload(payload)


def decode_given_json(given_json: str) -> Any:
    """Parse a persisted ``given_json`` string back into its payload dict.

    Thin wrapper so Runner views can render "your answer was …" without
    re-importing :mod:`json`. Raises :class:`ValueError` on malformed
    input — the storage layer already refuses garbage via the
    :class:`posrat.models.Answer` validator, but double-checking here
    keeps the Runner robust against hand-edited DBs.
    """

    return _decode_or_raise(given_json)
