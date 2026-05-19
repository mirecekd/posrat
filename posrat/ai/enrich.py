"""Pure helpers for the bulk Auto-enrich CLI (``python -m posrat enrich``).

Splits the reusable string- and model-level work away from the async
Bedrock agent driver in :mod:`posrat.ai.enrich_runner` and the CLI
glue in :mod:`posrat.__main__`. Everything in this module is
deterministic and unit-testable without a network round trip.

The CLI flow per question is:

1. Detect whether the question's current ``explanation`` is already
   in the new template (starts with ``## Correct Answer:``). If so,
   skip unless ``--overwrite`` was passed.
2. Pull any legacy "Community vote distribution" block out of the
   raw importer-supplied explanation (RTF / PDF / HTML harvested it
   verbatim) so the LLM can see it as context but won't carry the
   raw header into its reply.
3. Build the user prompt = the admin-configured enrichment template
   plus an optional appendix that asks the model to:

   * verify the answer marked correct in our DB and explicitly call
     out a mismatch in its reply,
   * append a one-sentence "Community vote summary" block at the
     very end of the markdown reply (the operator wants the
     primary signal to be MCP/AWS-docs-driven, with community vote
     used only as a tie-breaker / sanity check).
4. After streaming completes, parse the AI letters from the
   ``## Correct Answer: <X>`` heading and compare against the
   letters derived from the per-choice ``is_correct`` flags.
5. Return :class:`EnrichResult` so the caller can persist the new
   explanation and (optionally) auto-correct the ``is_correct``
   bits.

All Bedrock / Strands / NiceGUI imports stay out of this module —
the runner module wires those in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from posrat.models import Question


#: Heading prefix our enrichment template guarantees as the very first
#: characters of the AI reply (see :data:`posrat.ai.config.DEFAULT_ENRICH_PROMPT`).
#: Used both to detect "this question is already in the new format"
#: and to anchor the regex that extracts the AI's letter pick.
ENRICHED_EXPLANATION_PREFIX: str = "## Correct Answer:"


#: Legal options letters our enrichment template uses (A..Z). Anything
#: outside the alphabet drops on the floor; ``Question`` choice limits
#: to 26 already so this is safe in practice.
_LETTER_RE: re.Pattern[str] = re.compile(r"[A-Z]")


#: Detect the legacy "Community vote distribution" header anywhere in
#: the explanation. The RTF / PDF / HTML importers each put the
#: header on its own line, but the HTML importer also occasionally
#: joins votes with `` | `` separators on the same line as the
#: header. Anchoring on the phrase (not the whole line) handles both
#: shapes; vote tokens are then extracted from everything that
#: follows the header position.
_COMMUNITY_HEADER_RE: re.Pattern[str] = re.compile(
    r"(?i)Community vote distribution"
)



#: Pull "B (98%)" / "B(98 %)" / "B 98%" tokens out of a community
#: vote block. Tolerates the various whitespace / parenthesis flavours
#: produced by the importers.
_VOTE_LINE_RE: re.Pattern[str] = re.compile(
    r"(?P<letter>[A-Z])\s*\(?\s*(?P<percent>\d{1,3})\s*%\s*\)?"
)


@dataclass(frozen=True)
class CommunityVote:
    """Snapshot of a parsed community vote distribution.

    ``distribution`` keeps the raw letter -> percent map (in the order
    the parser saw them). ``top_letter`` and ``top_percent`` cache the
    dominant pick for the summary sentence — extracted once so the
    summarizer doesn't have to re-iterate the dict.
    """

    distribution: dict[str, int]
    top_letter: str
    top_percent: int


def extract_community_vote(explanation: Optional[str]) -> Optional[CommunityVote]:
    """Return the :class:`CommunityVote` parsed from ``explanation`` or ``None``.

    Looks for the ``Community vote distribution`` header (case-insensitive,
    flexible whitespace), then collects every ``LETTER (NN%)`` token in
    the lines below. Lines after a blank line that doesn't carry any
    further vote tokens are *not* considered (importers always put the
    block at the very end of the explanation, so this is safe).

    Returns ``None`` when:

    * ``explanation`` is ``None`` / empty,
    * the header is missing,
    * the header is present but no recognisable ``LETTER (NN%)``
      tokens follow.
    """

    if not explanation:
        return None
    match = _COMMUNITY_HEADER_RE.search(explanation)
    if match is None:
        return None
    tail = explanation[match.end():]
    distribution: dict[str, int] = {}
    for vote_match in _VOTE_LINE_RE.finditer(tail):
        letter = vote_match.group("letter")
        percent = int(vote_match.group("percent"))
        # Keep the first occurrence per letter — pathological inputs
        # with duplicates pick the leading vote (importers don't
        # produce dupes today, but be conservative).
        distribution.setdefault(letter, percent)
    if not distribution:
        return None
    top_letter = max(distribution, key=lambda key: distribution[key])
    return CommunityVote(
        distribution=dict(distribution),
        top_letter=top_letter,
        top_percent=distribution[top_letter],
    )


def strip_community_vote(explanation: Optional[str]) -> Optional[str]:
    """Return ``explanation`` with the ``Community vote distribution`` block removed.

    Keeps the prose that *precedes* the header intact (rare today —
    importers usually have the community block stand alone — but the
    Designer might have prepended an AI reply before the block exists).
    Returns ``None`` when the input is ``None`` and an empty string
    when stripping leaves nothing behind, so the caller can normalise
    via ``stripped or None`` if they want SQL ``NULL`` semantics.
    """

    if explanation is None:
        return None
    match = _COMMUNITY_HEADER_RE.search(explanation)
    if match is None:
        return explanation
    head = explanation[: match.start()]
    return head.rstrip()


def summarize_community_vote(vote: CommunityVote) -> str:
    """Render a one-sentence summary of ``vote`` for the AI reply tail.

    The format is fixed so the appended sentence is visually
    consistent across the whole exam:

    * ``"Community vote: B (98%) overwhelmingly preferred."`` when the
      top pick is >= 80%.
    * ``"Community vote: B (62%) leading; A trails at 38%."`` when the
      top pick is in the 50..79% range and there's a runner-up.
    * ``"Community vote split: B (45%), A (40%), C (15%)."`` when no
      single option crosses 50%.

    The phrasing avoids any en-dash / em-dash / Unicode icon to stay
    aligned with the operator's house style for Explanation/Reference.
    """

    distribution = vote.distribution
    if not distribution:  # pragma: no cover - constructed via extract_community_vote
        return ""
    sorted_items = sorted(
        distribution.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    top_letter, top_percent = sorted_items[0]

    if top_percent >= 80:
        return (
            f"Community vote: {top_letter} ({top_percent}%) "
            "overwhelmingly preferred."
        )
    if top_percent >= 50 and len(sorted_items) >= 2:
        runner_letter, runner_percent = sorted_items[1]
        return (
            f"Community vote: {top_letter} ({top_percent}%) leading; "
            f"{runner_letter} trails at {runner_percent}%."
        )
    if top_percent >= 50:
        return f"Community vote: {top_letter} ({top_percent}%) leading."
    parts = ", ".join(f"{ltr} ({pct}%)" for ltr, pct in sorted_items)
    return f"Community vote split: {parts}."


def is_already_enriched(explanation: Optional[str]) -> bool:
    """Return ``True`` when ``explanation`` is already in the new template.

    Used by the CLI to skip questions that have been enriched in a
    previous run (idempotent re-runs without ``--overwrite``). The
    check is intentionally narrow — looks at the very first
    non-whitespace characters — so a question whose author hand-typed
    a similar heading is *not* mistaken for an AI-generated reply
    unless they exactly match the prefix our prompt mandates.
    """

    if not explanation:
        return False
    return explanation.lstrip().startswith(ENRICHED_EXPLANATION_PREFIX)


def derive_db_correct_letters(question: Question) -> list[str]:
    """Return choice letters (``["A","C"]``) that are flagged correct.

    Letters are derived from the choice index (A/B/C/...) using the
    same ordering the chat context renderer uses
    (:func:`posrat.ai.context._append_choice_block`). Hotspot
    questions return ``[]`` because the concept doesn't apply — the
    CLI dispatcher skips hotspot rows before this is called, but
    return ``[]`` defensively rather than raise.
    """

    if question.type == "hotspot":
        return []
    letters: list[str] = []
    for idx, choice in enumerate(question.choices):
        if choice.is_correct:
            if idx >= 26:  # pragma: no cover - Question caps choices well below
                continue
            letters.append(chr(ord("A") + idx))
    return sorted(letters)


def parse_correct_letters_from_reply(reply: Optional[str]) -> list[str]:
    """Parse the ``## Correct Answer: <letters>`` heading from ``reply``.

    Tolerates the formats the model is most likely to emit:

    * ``## Correct Answer: D``         → ``["D"]``
    * ``## Correct Answer: BC``        → ``["B","C"]``
    * ``## Correct Answer: B, C``      → ``["B","C"]``
    * ``## Correct Answer: B and C``   → ``["B","C"]``
    * ``## Correct Answer: B, D, E``   → ``["B","D","E"]``

    Returns ``[]`` when the heading is absent or carries no letters
    in ``A..Z`` (the operator can flag those manually). Letters are
    deduplicated and sorted so equality checks against
    :func:`derive_db_correct_letters` are order-insensitive.
    """

    if not reply:
        return []
    # Anchor on the heading regardless of leading whitespace; the new
    # prompt instructs the model to start the reply with `## Correct
    # Answer:` as the very first non-whitespace token.
    heading_re = re.compile(
        r"(?im)^\s*##\s*Correct\s*Answer\s*:\s*(?P<rest>[^\n]+)$"
    )
    match = heading_re.search(reply)
    if match is None:
        return []
    raw = match.group("rest")
    letters = sorted({m.group(0) for m in _LETTER_RE.finditer(raw)})
    return letters


@dataclass(frozen=True)
class EnrichResult:
    """One question's outcome from a bulk enrichment run.

    Attributes:
        question_id: ID of the question that was processed.
        verdict: ``"match"``, ``"mismatch"``, ``"unknown"``,
            ``"skipped_hotspot"``, ``"skipped_already_enriched"``,
            ``"error"``. Drives the CLI summary buckets.
        db_letters: Letters our DB had marked correct before the run
            (sorted, deduped). Empty list for hotspot / non-choice
            types.
        ai_letters: Letters parsed out of the AI reply (sorted,
            deduped). Empty list when the model didn't emit a
            recognisable heading or the run errored.
        new_explanation: The full markdown reply, already with the
            community-vote summary appended (when the source had
            one). ``None`` for skipped / errored questions.
        error_message: Populated only for ``verdict == "error"``.
    """

    question_id: str
    verdict: str
    db_letters: list[str] = field(default_factory=list)
    ai_letters: list[str] = field(default_factory=list)
    new_explanation: Optional[str] = None
    error_message: Optional[str] = None


def classify_verdict(
    db_letters: list[str],
    ai_letters: list[str],
) -> str:
    """Return ``"match"`` / ``"mismatch"`` / ``"unknown"``.

    ``"unknown"`` covers the case where the AI reply didn't carry a
    parseable letter (model went off-script). ``"match"`` requires
    set equality; multi-choice picks are sorted before comparison so
    ``["B","C"] == ["C","B"]``.
    """

    if not ai_letters:
        return "unknown"
    if sorted(ai_letters) == sorted(db_letters):
        return "match"
    return "mismatch"


def append_community_vote_summary(
    reply: str,
    vote: Optional[CommunityVote],
) -> str:
    """Append a community-vote summary line at the bottom of ``reply``.

    No-op when ``vote`` is ``None``. Otherwise inserts a blank line
    plus the summary sentence after the existing content. Operator
    decision (2026-05-19): community vote belongs at the very end of
    the reply so the primary MCP/AWS-docs-driven justification reads
    first; vote is a tie-breaker / sanity check, not the headline.
    """

    if vote is None:
        return reply
    summary = summarize_community_vote(vote)
    body = reply.rstrip()
    return f"{body}\n\n{summary}\n"


def build_enrich_user_prompt(
    base_prompt: str,
    *,
    db_letters: list[str],
    has_community_vote: bool,
) -> str:
    """Assemble the user prompt sent to the agent for one question.

    ``base_prompt`` is the admin-configured template
    (``settings.effective_enrich_prompt``). The bulk runner appends a
    short addendum with two extra hints:

    * the letters our DB currently has marked correct, asking the
      model to verify them against AWS docs and **explicitly call
      out a mismatch** in the reply,
    * a reminder to keep the community-vote summary as the very last
      block of the reply when the question carries vote data
      (otherwise the importer-derived block would be lost).

    Keeping the addendum out of :data:`DEFAULT_ENRICH_PROMPT` itself
    means the interactive Designer "Auto-enrich" button (which has
    no DB-letter context) doesn't get this paragraph either.
    """

    addendum_parts: list[str] = [
        "",
        "Bulk-enrichment context (do not echo this header in the reply):",
        "",
    ]
    if db_letters:
        joined = ", ".join(db_letters)
        addendum_parts.append(
            f"- Our database currently marks {joined} as the correct "
            f"answer{'s' if len(db_letters) > 1 else ''}. Verify "
            "this against AWS documentation. If you disagree, your "
            "`## Correct Answer:` heading must reflect your "
            "verdict (not ours), and the Justification must explain "
            "why our marking is wrong."
        )
    else:
        addendum_parts.append(
            "- Our database does not have a correct answer marked yet. "
            "Pick the right one(s) based on AWS documentation."
        )

    if has_community_vote:
        addendum_parts.append(
            "- The question carries a community-vote distribution from "
            "the original exam dump. Treat it as a weak hint only "
            "(MCP / AWS docs win over crowd consensus). The bulk "
            "runner will append a one-sentence vote summary at the "
            "end of your reply automatically; do not include it "
            "yourself."
        )

    return base_prompt.rstrip() + "\n" + "\n".join(addendum_parts)


__all__ = [
    "CommunityVote",
    "ENRICHED_EXPLANATION_PREFIX",
    "EnrichResult",
    "append_community_vote_summary",
    "build_enrich_user_prompt",
    "classify_verdict",
    "derive_db_correct_letters",
    "extract_community_vote",
    "is_already_enriched",
    "parse_correct_letters_from_reply",
    "strip_community_vote",
    "summarize_community_vote",
]
