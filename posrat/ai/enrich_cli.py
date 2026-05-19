"""CLI glue for ``python -m posrat enrich``.

Lifted out of :mod:`posrat.__main__` to keep the dispatcher tiny and
to make argument parsing, progress printing, and report writing
unit-testable on their own. The dispatcher just calls
:func:`run_enrich_command` after stripping the ``enrich`` token.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from posrat.ai import enrich_persistence, enrich_runner
from posrat.ai.config import load_ai_settings
from posrat.ai.enrich import EnrichResult
from posrat.ai.enrich_runner import EnrichSummary
from posrat.designer.browser import resolve_data_dir
from posrat.system.system_db import open_system_db, resolve_system_db_path


_VERDICT_LABELS: dict[str, str] = {
    "match": "match",
    "mismatch": "mismatch",
    "unknown": "unknown",
    "skipped_hotspot": "skip-hotspot",
    "skipped_already_enriched": "skip-done",
    "error": "error",
}


def _build_parser() -> argparse.ArgumentParser:
    """Argparse spec shared between the dispatcher and the help text."""

    parser = argparse.ArgumentParser(
        prog="python -m posrat enrich",
        description=(
            "Bulk-enrich every question in an exam .sqlite using the "
            "admin-configured Auto-enrich prompt. A timestamped backup "
            "is written next to the source before any changes."
        ),
    )
    parser.add_argument(
        "exam_path",
        type=Path,
        help="Path to the per-exam .sqlite file to enrich.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Re-enrich questions whose explanation already starts "
            "with the template heading (default: skip them)."
        ),
    )
    parser.add_argument(
        "--no-auto-correct",
        action="store_true",
        help=(
            "Do NOT flip is_correct flags when the AI disagrees with "
            "the DB. Default behaviour writes the AI's letters into "
            "the choices table on every mismatch (operator opt-in to "
            "less manual review work)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run the agent and print the report, but skip both the "
            "backup copy and any DB writes. Useful for previewing "
            "what would change."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write a JSON report of all results.",
    )
    return parser


def _format_progress_line(idx: int, total: int, result: EnrichResult) -> str:
    label = _VERDICT_LABELS.get(result.verdict, result.verdict)
    head = f"Q{idx}/{total} [{label}] {result.question_id}"
    if result.verdict in {"match", "mismatch", "unknown"}:
        db_letters = ",".join(result.db_letters) or "-"
        ai_letters = ",".join(result.ai_letters) or "-"
        head += f" AI={ai_letters} DB={db_letters}"
    if result.error_message:
        head += f" :: {result.error_message}"
    return head


def _format_summary(summary: EnrichSummary) -> str:
    lines = [
        "",
        "Summary:",
        f"  total          : {summary.total}",
        f"  match          : {summary.matches}",
        f"  mismatch       : {summary.mismatches}",
        f"  unknown        : {summary.unknown}",
        f"  skip-hotspot   : {summary.skipped_hotspot}",
        f"  skip-done      : {summary.skipped_already_enriched}",
        f"  error          : {summary.errors}",
    ]
    mismatches = [r for r in summary.results if r.verdict == "mismatch"]
    if mismatches:
        lines.append("")
        lines.append("Mismatches (review in Designer):")
        for result in mismatches:
            ai = ",".join(result.ai_letters) or "-"
            db = ",".join(result.db_letters) or "-"
            lines.append(f"  - {result.question_id}: AI={ai} DB={db}")
    errors = [r for r in summary.results if r.verdict == "error"]
    if errors:
        lines.append("")
        lines.append("Errors:")
        for result in errors:
            lines.append(
                f"  - {result.question_id}: "
                f"{result.error_message or 'unknown error'}"
            )
    return "\n".join(lines)


def _summary_to_json(summary: EnrichSummary) -> dict:
    """Convert an :class:`EnrichSummary` to a JSON-serialisable dict."""

    return {
        "total": summary.total,
        "match": summary.matches,
        "mismatch": summary.mismatches,
        "unknown": summary.unknown,
        "skipped_hotspot": summary.skipped_hotspot,
        "skipped_already_enriched": summary.skipped_already_enriched,
        "errors": summary.errors,
        "results": [asdict(r) for r in summary.results],
    }


def _load_settings():
    """Open the system DB and return the resolved AI settings."""

    data_dir = Path(resolve_data_dir())
    db = open_system_db(resolve_system_db_path(data_dir))
    try:
        return load_ai_settings(db)
    finally:
        db.close()


def run_enrich_command(argv: Sequence[str]) -> int:
    """Entry point invoked by ``python -m posrat enrich <args>``.

    Returns a Unix-style exit code:

    * ``0`` on a clean run (every question either enriched or
      explicitly skipped),
    * ``1`` when one or more questions errored out (``unknown`` does
      *not* count as an error — it just means the model went
      off-script for that one row),
    * ``2`` for argparse / CLI usage errors.
    """

    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:  # argparse already printed the usage
        return int(exc.code or 2)

    exam_path: Path = args.exam_path
    if not exam_path.exists():
        print(f"enrich: file not found: {exam_path}", file=sys.stderr)
        return 1

    settings = _load_settings()
    if not settings.enabled:
        print(
            "enrich: AI chat is disabled in /admin → AI chat. "
            "Enable it (and check the model id / region / MCP "
            "config) before running bulk enrichment.",
            file=sys.stderr,
        )
        return 1

    backup_path: Optional[Path] = None
    if not args.dry_run:
        try:
            backup_path = enrich_persistence.make_backup(exam_path)
        except (FileNotFoundError, FileExistsError) as exc:
            print(f"enrich: backup failed: {exc}", file=sys.stderr)
            return 1
        print(f"Backup written: {backup_path}")
    else:
        print("Dry-run mode: no backup, no DB writes.")

    try:
        questions = enrich_persistence.load_exam_questions(exam_path)
    except (sqlite3.DatabaseError, ValueError) as exc:
        print(f"enrich: cannot load questions: {exc}", file=sys.stderr)
        return 1

    if not questions:
        print("enrich: exam has no questions; nothing to do.")
        return 0

    auto_correct = not args.no_auto_correct

    async def _persist(result: EnrichResult) -> None:
        if args.dry_run:
            return
        enrich_persistence.persist_enrich_result(
            exam_path,
            result,
            auto_correct_mismatches=auto_correct,
        )

    def _progress(idx: int, total: int, result: EnrichResult) -> None:
        print(_format_progress_line(idx, total, result))

    summary = asyncio.run(
        enrich_runner.enrich_questions(
            settings,
            questions,
            overwrite_already_enriched=args.overwrite,
            progress=_progress,
            persist=_persist,
        )
    )

    print(_format_summary(summary))

    if args.report is not None:
        args.report.write_text(
            json.dumps(_summary_to_json(summary), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Report written: {args.report}")

    return 1 if summary.errors else 0


__all__ = [
    "run_enrich_command",
]
