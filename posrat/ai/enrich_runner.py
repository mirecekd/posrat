"""Async orchestrator for the bulk Auto-enrich CLI.

Wraps :func:`posrat.ai.agent.run_chat_turn` so a single call enriches
one whole exam: open the per-exam SQLite, iterate questions, stream
the LLM reply per question, parse the AI answer, persist the new
explanation (and optionally re-flag ``is_correct``) on disk.

Why a dedicated async runner (separate from the interactive
:func:`posrat.ai.chat_dialog.render_chat_dialog` path):

* No NiceGUI request context, no token streaming UI — collect the
  full reply in memory and only print a one-line status per
  question to stdout.
* Returns an :class:`EnrichSummary` with rolled-up counts +
  per-question :class:`~posrat.ai.enrich.EnrichResult` rows so the
  CLI layer can pretty-print the final report.

**MCP client lifecycle gotcha.** Strands' :class:`MCPClient` is a
strictly single-session context manager — once it has been
``with``-entered it cannot be re-entered until exited. The chat
agent's :func:`run_chat_turn` already enters every client it gets in
its own ``ExitStack`` for each turn. So this runner **must not** open
those clients itself; it just builds fresh client instances per
question (mirroring what
:func:`posrat.ai.chat_dialog._stream_assistant_reply` does for the
interactive dialog) and hands them to the agent. Reusing one client
across multiple turns triggers ``MCPClientInitializationError: the
client session is currently running`` because the second turn tries
to enter an already-entered context.

The persistence helpers (writing back to the exam SQLite) live in
:mod:`posrat.ai.enrich_persistence` so the runner stays focused on
the "talk to the LLM" bit and the CLI dispatcher's monkey-patches
can replace just one of the two for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence

from posrat.ai.agent import run_chat_turn
from posrat.ai.config import AISettings
from posrat.ai.context import build_question_context
from posrat.ai.enrich import (
    CommunityVote,
    EnrichResult,
    append_community_vote_summary,
    build_enrich_user_prompt,
    classify_verdict,
    derive_db_correct_letters,
    extract_community_vote,
    is_already_enriched,
    parse_correct_letters_from_reply,
    strip_community_vote,
)
from posrat.ai.mcp_client import build_mcp_clients, parse_mcp_config
from posrat.models import Question



#: Type of the "pretty-print one status line per question" callback
#: the CLI passes in. Receiving the full :class:`EnrichResult` lets
#: the CLI render whatever shape it likes (verbose / compact /
#: machine-readable) without coupling the runner to a specific
#: format.
ProgressCallback = Callable[[int, int, EnrichResult], None]


@dataclass(frozen=True)
class EnrichSummary:
    """Roll-up of all per-question outcomes after a bulk run.

    The CLI uses this to print the final summary block and to
    serialize the report to JSON when ``--report`` is supplied.
    """

    results: List[EnrichResult]
    matches: int
    mismatches: int
    unknown: int
    skipped_hotspot: int
    skipped_already_enriched: int
    errors: int

    @property
    def total(self) -> int:
        return len(self.results)


def _summarise(results: Sequence[EnrichResult]) -> EnrichSummary:
    """Build a final :class:`EnrichSummary` from per-question results."""

    counts: dict[str, int] = {
        "match": 0,
        "mismatch": 0,
        "unknown": 0,
        "skipped_hotspot": 0,
        "skipped_already_enriched": 0,
        "error": 0,
    }
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    return EnrichSummary(
        results=list(results),
        matches=counts["match"],
        mismatches=counts["mismatch"],
        unknown=counts["unknown"],
        skipped_hotspot=counts["skipped_hotspot"],
        skipped_already_enriched=counts["skipped_already_enriched"],
        errors=counts["error"],
    )


async def _collect_assistant_reply(
    settings: AISettings,
    user_prompt: str,
    *,
    question_context: str,
    mcp_clients: list,
) -> str:
    """Stream a single chat turn and return the concatenated reply text.

    This is the runner's equivalent of the chat dialog's
    ``_stream_assistant_reply``, except it doesn't update any UI —
    it just buffers ``data`` deltas into a string and falls back to
    the trailing assistant message when the stream had zero text
    deltas (tool-only turn).
    """

    buffer: list[str] = []
    final_messages: Optional[list] = None
    async for event in run_chat_turn(
        settings,
        user_prompt,
        question_context=question_context,
        mcp_clients=mcp_clients,
    ):
        if not isinstance(event, dict):
            continue
        delta = event.get("data")
        if isinstance(delta, str):
            buffer.append(delta)
            continue
        trace = event.get("complete_messages")
        if isinstance(trace, list):
            final_messages = trace

    text = "".join(buffer).strip()
    if text:
        return text

    # Fallback: extract the last assistant message text from the
    # trace. Strands message dicts wrap content as a list of
    # ``{"text": "..."}`` blocks, but legacy / mocked variants pass
    # plain strings; both are normalised here.
    if final_messages:
        for message in reversed(final_messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        chunk = block.get("text")
                        if isinstance(chunk, str):
                            parts.append(chunk)
                joined = "".join(parts).strip()
                if joined:
                    return joined
    return ""


async def enrich_question(
    settings: AISettings,
    question: Question,
    *,
    mcp_clients: list,
    overwrite_already_enriched: bool,
) -> EnrichResult:
    """Enrich one question and return the verdict + new explanation.

    Hotspot questions short-circuit to ``"skipped_hotspot"`` because
    the markdown template doesn't fit (multiple steps × options pool
    don't reduce to a single ``## Correct Answer:`` heading). Already
    enriched questions short-circuit to ``"skipped_already_enriched"``
    unless the caller passed ``overwrite_already_enriched=True``.
    """

    if question.type == "hotspot":
        return EnrichResult(
            question_id=question.id,
            verdict="skipped_hotspot",
        )
    if not overwrite_already_enriched and is_already_enriched(question.explanation):
        return EnrichResult(
            question_id=question.id,
            verdict="skipped_already_enriched",
        )

    db_letters = derive_db_correct_letters(question)
    community_vote: Optional[CommunityVote] = extract_community_vote(question.explanation)

    # The Designer Auto-enrich button uses ``include_answers=True``;
    # we keep that behaviour because the prompt addendum explicitly
    # tells the model to verify (or override) our marking. The model
    # sees both the choice texts and the [correct] markers — that's
    # the whole point of "agree or disagree".
    question_for_context = question
    if community_vote is not None:
        # Hide the raw "Community vote distribution" block from the
        # context render so the LLM doesn't echo it. The bulk runner
        # appends the structured one-sentence summary after the
        # streamed reply finishes.
        stripped = strip_community_vote(question.explanation)
        question_for_context = question.model_copy(
            update={"explanation": stripped or None}
        )

    question_context = build_question_context(
        question_for_context, include_answers=True
    )

    user_prompt = build_enrich_user_prompt(
        settings.effective_enrich_prompt,
        db_letters=db_letters,
        has_community_vote=community_vote is not None,
    )

    try:
        reply = await _collect_assistant_reply(
            settings,
            user_prompt,
            question_context=question_context,
            mcp_clients=mcp_clients,
        )
    except Exception as exc:  # noqa: BLE001 — surface anything to the report
        return EnrichResult(
            question_id=question.id,
            verdict="error",
            db_letters=db_letters,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    if not reply:
        return EnrichResult(
            question_id=question.id,
            verdict="error",
            db_letters=db_letters,
            error_message="empty reply",
        )

    ai_letters = parse_correct_letters_from_reply(reply)
    final_explanation = append_community_vote_summary(reply, community_vote)
    verdict = classify_verdict(db_letters, ai_letters)

    return EnrichResult(
        question_id=question.id,
        verdict=verdict,
        db_letters=db_letters,
        ai_letters=ai_letters,
        new_explanation=final_explanation,
    )


async def enrich_questions(
    settings: AISettings,
    questions: Sequence[Question],
    *,
    overwrite_already_enriched: bool = False,
    progress: Optional[ProgressCallback] = None,
    persist: Optional[Callable[[EnrichResult], Awaitable[None]]] = None,
) -> EnrichSummary:
    """Enrich every question in ``questions`` sequentially.

    Each question gets a **fresh batch of MCP clients** built from
    :attr:`AISettings.mcp_config_json`. Strands' :class:`MCPClient`
    is single-session — the agent enters every client it gets via
    its own ``ExitStack`` for the duration of the turn and exits it
    when the turn ends. Reusing a client for a later turn raises
    ``MCPClientInitializationError: the client session is currently
    running``. The trade-off is one HTTPS handshake per question for
    the aws-knowledge-mcp endpoint, which is cheap compared to the
    Bedrock turn itself.

    Each question is awaited one after another (no concurrency) —
    Bedrock has tight per-account RPS limits and bulk runs care
    about total cost more than wall-clock latency.

    Args:
        settings: Resolved AI settings (Bedrock model, region, MCP
            JSON, enrich prompt).
        questions: All questions to process. The caller is
            responsible for filtering / ordering as needed.
        overwrite_already_enriched: When ``True`` re-enrich
            questions whose ``explanation`` already starts with the
            template prefix. Default ``False`` makes runs idempotent.
        progress: Optional callback invoked after each result with
            ``(index_1based, total, result)``.
        persist: Optional async callback that writes the result
            back to disk (explanation update, optional auto-correct
            of ``is_correct`` flags). Kept as a callback so unit
            tests can run the full pipeline without touching SQLite.
    """

    servers = parse_mcp_config(settings.mcp_config_json)

    results: list[EnrichResult] = []
    total = len(questions)
    for idx, question in enumerate(questions, start=1):
        # Build a fresh batch per question — see module docstring.
        mcp_clients = build_mcp_clients(servers) if servers else []
        result = await enrich_question(
            settings,
            question,
            mcp_clients=mcp_clients,
            overwrite_already_enriched=overwrite_already_enriched,
        )
        results.append(result)
        if persist is not None:
            try:
                await persist(result)
            except Exception as exc:  # noqa: BLE001 — degrade to report row
                results[-1] = EnrichResult(
                    question_id=result.question_id,
                    verdict="error",
                    db_letters=result.db_letters,
                    ai_letters=result.ai_letters,
                    new_explanation=result.new_explanation,
                    error_message=f"persist: {type(exc).__name__}: {exc}",
                )
        if progress is not None:
            progress(idx, total, results[-1])

    return _summarise(results)



__all__ = [
    "EnrichSummary",
    "ProgressCallback",
    "enrich_question",
    "enrich_questions",
]
