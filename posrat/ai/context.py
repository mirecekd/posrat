"""Build the per-question system message fed into the AI chat.

The chat assistant receives two system messages:

1. The global one from admin settings
   (:data:`posrat.ai.config.DEFAULT_SYSTEM_PROMPT` or the configured
   override).
2. A dynamic **question context** built here — rendered as a plain
   text block listing the current question, its choices with correct
   flags, and the explanation.

Why plain text? Strands / Bedrock accept any text in the system
prompt, and the LLM handles "formatted markdown-like blocks" just
fine. Keeping the renderer to ``str`` output means the module has no
LLM dependencies whatsoever — it's pure data-in → string-out and the
unit tests can exercise every branch without mocking anything.

**Exam-mode gating.** ``include_answers=False`` omits the
``[correct]`` flags and the explanation so, on the off chance the FAB
ever leaks into exam mode, the assistant doesn't accidentally become
a cheat tool. The Runner call site also guards by only rendering the
FAB in training mode — this is belt-and-suspenders.
"""

from __future__ import annotations

from typing import Optional

from posrat.models import Question


def build_question_context(
    question: Optional[Question],
    *,
    include_answers: bool = True,
) -> str:
    """Render a human-readable context block for ``question``.

    Returns an empty string when ``question`` is ``None`` — used by the
    header-button call site ("generic AWS chat, no specific
    question"). The empty string signals to the agent factory that no
    extra system message should be appended.

    Args:
        question: The question currently shown in Designer / Runner,
            or ``None`` for generic chat.
        include_answers: When ``True`` (Training mode / Designer) the
            block carries correct-answer flags and the explanation.
            When ``False`` (hypothetical Exam mode), only the prompt
            and the raw choice texts are rendered.
    """

    if question is None:
        return ""

    lines: list[str] = []
    lines.append("CURRENT QUESTION CONTEXT")
    lines.append("")
    lines.append(f"Type: {question.type}")
    if question.section:
        lines.append(f"Section: {question.section}")
    if question.complexity is not None:
        lines.append(f"Complexity: {question.complexity}/5")
    lines.append("")
    lines.append(f"Q: {question.text}")

    if question.type == "hotspot":
        _append_hotspot_block(lines, question, include_answers)
    else:
        _append_choice_block(lines, question, include_answers)

    if include_answers and question.explanation:
        lines.append("")
        lines.append("Explanation / reference:")
        lines.append(question.explanation)

    return "\n".join(lines).strip()


def _append_choice_block(
    lines: list[str],
    question: Question,
    include_answers: bool,
) -> None:
    """Render single/multi_choice answers into ``lines``."""

    if not question.choices:
        return

    lines.append("")
    lines.append("Choices:")
    for idx, choice in enumerate(question.choices):
        letter = chr(ord("A") + idx) if idx < 26 else f"[{idx}]"
        marker = ""
        if include_answers and choice.is_correct:
            marker = " [correct]"
        lines.append(f"  {letter}. {choice.text}{marker}")


def _append_hotspot_block(
    lines: list[str],
    question: Question,
    include_answers: bool,
) -> None:
    """Render the hotspot options-pool + steps into ``lines``."""

    hotspot = question.hotspot
    if hotspot is None:  # pragma: no cover - defensive; invariant
        return

    option_map = {opt.id: opt.text for opt in hotspot.options}

    lines.append("")
    lines.append("Options pool:")
    for opt in hotspot.options:
        lines.append(f"  - {opt.text}")

    lines.append("")
    lines.append("Steps:")
    for idx, step in enumerate(hotspot.steps, start=1):
        lines.append(f"  {idx}. {step.prompt}")
        if include_answers and step.correct_option_id:
            correct_text = option_map.get(
                step.correct_option_id, step.correct_option_id
            )
            lines.append(f"     correct: {correct_text}")


__all__ = ["build_question_context"]
