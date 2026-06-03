#!/usr/bin/env python3
"""Generate a static HTML question-histogram report from POSRAT exam DBs.

Standalone helper (no app imports, stdlib only). For each per-exam
``*.sqlite`` file in the data directory it computes, per question:

* how many times the question was answered (across all sessions),
* how many of those were correct / wrong,
* whether it was never answered,

plus an exam-level summary (coverage, accuracy, an "answered N times"
histogram, and a per-section breakdown). The result is one
self-contained, offline HTML page (pure CSS bar charts, no external
JS/CDN) that you can open directly in a browser.

Usage
-----
    python scripts/exam_stats.py
        # all exams in ./data, output -> data/stats/index.html

    python scripts/exam_stats.py data/AIF-C01.sqlite
        # a single exam file

    python scripts/exam_stats.py --user mirecek --out /tmp/stats.html
        # only sessions started by user "mirecek"

This script is intentionally outside the ``posrat`` package and is
listed in .dockerignore so it never ships in the container image.
"""

from __future__ import annotations

import argparse
import html
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DATA_DIR = Path("data")
SYSTEM_DB_FILENAME = "system.sqlite"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class QuestionStat:
    question_id: str
    text: str
    qtype: str
    section: Optional[str]
    order_index: int
    times_answered: int = 0
    times_correct: int = 0
    times_wrong: int = 0
    last_result: Optional[bool] = None  # True correct / False wrong / None unanswered

    @property
    def accuracy(self) -> Optional[float]:
        if self.times_answered == 0:
            return None
        return self.times_correct / self.times_answered

    @property
    def status(self) -> str:
        if self.times_answered == 0:
            return "unanswered"
        if self.times_wrong == 0:
            return "mastered"
        return "struggling"


@dataclass
class ExamStat:
    file: Path
    exam_id: str
    name: str
    questions: list[QuestionStat] = field(default_factory=list)
    session_count: int = 0

    @property
    def total(self) -> int:
        return len(self.questions)

    @property
    def answered(self) -> int:
        return sum(1 for q in self.questions if q.times_answered > 0)

    @property
    def unanswered(self) -> int:
        return self.total - self.answered

    @property
    def mastered(self) -> int:
        return sum(1 for q in self.questions if q.status == "mastered")

    @property
    def struggling(self) -> int:
        return sum(1 for q in self.questions if q.status == "struggling")

    @property
    def total_attempts(self) -> int:
        return sum(q.times_answered for q in self.questions)

    @property
    def total_correct(self) -> int:
        return sum(q.times_correct for q in self.questions)

    @property
    def coverage(self) -> float:
        return (self.answered / self.total) if self.total else 0.0

    @property
    def overall_accuracy(self) -> Optional[float]:
        if self.total_attempts == 0:
            return None
        return self.total_correct / self.total_attempts

    def answer_count_histogram(self) -> dict[str, int]:
        """Buckets: how many questions were answered 0 / 1 / 2 / 3+ times."""
        buckets = {"0": 0, "1": 0, "2": 0, "3+": 0}
        for q in self.questions:
            n = q.times_answered
            if n == 0:
                buckets["0"] += 1
            elif n == 1:
                buckets["1"] += 1
            elif n == 2:
                buckets["2"] += 1
            else:
                buckets["3+"] += 1
        return buckets

    def section_breakdown(self) -> list[tuple[str, int, int, int]]:
        """Per-section (section, total, answered, correct_attempts)."""
        agg: dict[str, list[int]] = {}
        for q in self.questions:
            key = q.section or "(no section)"
            slot = agg.setdefault(key, [0, 0, 0])
            slot[0] += 1
            if q.times_answered > 0:
                slot[1] += 1
            slot[2] += q.times_correct
        return sorted((k, v[0], v[1], v[2]) for k, v in agg.items())


# --------------------------------------------------------------------------- #
# DB extraction
# --------------------------------------------------------------------------- #
def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def collect_exam_stat(path: Path, user: Optional[str]) -> Optional[ExamStat]:
    """Open a single exam DB and compute its statistics, or None if not an exam."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not (_table_exists(conn, "exams") and _table_exists(conn, "questions")):
            return None
        exam_row = conn.execute(
            "SELECT id, name FROM exams ORDER BY rowid ASC LIMIT 1"
        ).fetchone()
        if exam_row is None:
            return None

        exam = ExamStat(
            file=path,
            exam_id=exam_row["id"],
            name=exam_row["name"] or path.stem,
        )

        q_rows = conn.execute(
            "SELECT id, text, type, section, order_index FROM questions"
            " ORDER BY order_index ASC, rowid ASC"
        ).fetchall()
        qmap: dict[str, QuestionStat] = {}
        for r in q_rows:
            qs = QuestionStat(
                question_id=r["id"],
                text=r["text"] or "",
                qtype=r["type"],
                section=r["section"],
                order_index=r["order_index"],
            )
            exam.questions.append(qs)
            qmap[r["id"]] = qs

        # Session filter (optional per-user). sessions.username may not
        # exist on very old schemas, so guard the column.
        has_username = False
        if _table_exists(conn, "sessions"):
            cols = {c["name"] for c in conn.execute("PRAGMA table_info(sessions)")}
            has_username = "username" in cols

        if user is not None and has_username:
            sess_rows = conn.execute(
                "SELECT id FROM sessions WHERE username = ?", (user,)
            ).fetchall()
        elif _table_exists(conn, "sessions"):
            sess_rows = conn.execute("SELECT id FROM sessions").fetchall()
        else:
            sess_rows = []

        session_ids = [s["id"] for s in sess_rows]
        exam.session_count = len(session_ids)
        if not session_ids:
            return exam

        # Walk answers newest-first so we can capture last_result.
        placeholders = ",".join("?" * len(session_ids))
        ans_rows = conn.execute(
            "SELECT a.question_id, a.is_correct, s.started_at"
            " FROM answers a JOIN sessions s ON a.session_id = s.id"
            f" WHERE a.session_id IN ({placeholders})"
            " ORDER BY s.started_at ASC, a.rowid ASC",
            session_ids,
        ).fetchall()

        for a in ans_rows:
            qs = qmap.get(a["question_id"])
            if qs is None:
                continue  # answer to a question that no longer exists
            qs.times_answered += 1
            if a["is_correct"]:
                qs.times_correct += 1
                qs.last_result = True
            else:
                qs.times_wrong += 1
                qs.last_result = False

        return exam
    finally:
        conn.close()


def discover_exam_files(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(data_dir.iterdir()):
        if not p.is_file() or p.suffix != ".sqlite":
            continue
        if p.name == SYSTEM_DB_FILENAME:
            continue
        if ".bak-" in p.name:  # enrich/backups
            continue
        out.append(p)
    return out


# --------------------------------------------------------------------------- #
# HTML rendering (pure CSS, offline)
# --------------------------------------------------------------------------- #
def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _truncate(s: str, n: int = 110) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def _bar(label: str, value: int, total: int, css_class: str) -> str:
    width = 0 if total == 0 else round(value / total * 100)
    return (
        '<div class="barrow">'
        f'<span class="barlabel">{_esc(label)}</span>'
        '<span class="bartrack">'
        f'<span class="barfill {css_class}" style="width:{width}%"></span>'
        "</span>"
        f'<span class="barval">{value}</span>'
        "</div>"
    )


def render_exam_section(exam: ExamStat) -> str:
    hist = exam.answer_count_histogram()
    hist_max = max(hist.values()) or 1
    parts: list[str] = []
    parts.append('<section class="exam">')
    parts.append(f"<h2>{_esc(exam.name)} <span class='muted'>({_esc(exam.exam_id)})</span></h2>")
    parts.append(f"<p class='muted'>{_esc(exam.file.name)}</p>")

    # Summary tiles
    parts.append('<div class="tiles">')
    tiles = [
        ("Questions", str(exam.total)),
        ("Answered", f"{exam.answered} ({_pct(exam.coverage)})"),
        ("Unanswered", str(exam.unanswered)),
        ("Mastered", str(exam.mastered)),
        ("Struggling", str(exam.struggling)),
        ("Sessions", str(exam.session_count)),
        ("Total attempts", str(exam.total_attempts)),
        ("Overall accuracy", _pct(exam.overall_accuracy)),
    ]
    for label, val in tiles:
        parts.append(f'<div class="tile"><div class="tileval">{_esc(val)}</div>'
                     f'<div class="tilelabel">{_esc(label)}</div></div>')
    parts.append("</div>")

    # Histogram: answered N times
    parts.append('<h3>Coverage histogram (questions answered N times)</h3>')
    parts.append('<div class="bars">')
    css_for = {"0": "warn", "1": "ok", "2": "ok", "3+": "ok"}
    for bucket in ("0", "1", "2", "3+"):
        parts.append(_bar(f"{bucket}x", hist[bucket], hist_max, css_for[bucket]))
    parts.append("</div>")

    # Section breakdown
    sections = exam.section_breakdown()
    if len(sections) > 1 or (sections and sections[0][0] != "(no section)"):
        parts.append("<h3>By section</h3>")
        parts.append('<table class="sectbl"><thead><tr>'
                     "<th>Section</th><th>Questions</th><th>Answered</th>"
                     "<th>Correct attempts</th></tr></thead><tbody>")
        for name, tot, ans, corr in sections:
            parts.append(f"<tr><td>{_esc(name)}</td><td>{tot}</td>"
                         f"<td>{ans}</td><td>{corr}</td></tr>")
        parts.append("</tbody></table>")

    # Per-question table
    parts.append("<h3>Questions</h3>")
    parts.append('<table class="qtbl"><thead><tr>'
                 "<th>#</th><th>Question</th><th>Type</th><th>Section</th>"
                 "<th>Answered</th><th>Correct</th><th>Wrong</th>"
                 "<th>Accuracy</th><th>Last</th><th>Status</th>"
                 "</tr></thead><tbody>")
    for i, q in enumerate(exam.questions, start=1):
        last = "-" if q.last_result is None else ("correct" if q.last_result else "wrong")
        parts.append(
            f'<tr class="st-{q.status}">'
            f"<td>{i}</td>"
            f'<td class="qtext">{_esc(_truncate(q.text))}</td>'
            f"<td>{_esc(q.qtype)}</td>"
            f"<td>{_esc(q.section or '-')}</td>"
            f"<td>{q.times_answered}</td>"
            f"<td>{q.times_correct}</td>"
            f"<td>{q.times_wrong}</td>"
            f"<td>{_pct(q.accuracy)}</td>"
            f"<td>{last}</td>"
            f'<td><span class="badge b-{q.status}">{q.status}</span></td>'
            "</tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</section>")
    return "\n".join(parts)


CSS = """
:root { --bg:#0f1419; --panel:#1b2430; --line:#2c3a4a; --txt:#e6edf3;
        --muted:#8b98a5; --ok:#2ea043; --warn:#d29922; --bad:#cf222e;
        --accent:#388bfd; }
* { box-sizing: border-box; }
body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       background:var(--bg); color:var(--txt); line-height:1.45; }
header { padding:24px 32px; border-bottom:1px solid var(--line); }
header h1 { margin:0 0 4px; font-size:22px; }
.muted { color:var(--muted); }
main { padding:24px 32px; max-width:1200px; margin:0 auto; }
.toc { margin:0 0 24px; padding:0; list-style:none; display:flex; flex-wrap:wrap; gap:8px; }
.toc a { background:var(--panel); border:1px solid var(--line); color:var(--txt);
         padding:6px 12px; border-radius:6px; text-decoration:none; font-size:14px; }
.toc a:hover { border-color:var(--accent); }
section.exam { background:var(--panel); border:1px solid var(--line);
               border-radius:10px; padding:20px 24px; margin:0 0 28px; }
section.exam h2 { margin:0 0 2px; font-size:20px; }
section.exam h3 { margin:22px 0 10px; font-size:15px; color:var(--muted);
                  text-transform:uppercase; letter-spacing:.04em; }
.tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
         gap:12px; margin-top:12px; }
.tile { background:var(--bg); border:1px solid var(--line); border-radius:8px;
        padding:12px 14px; }
.tileval { font-size:22px; font-weight:600; }
.tilelabel { font-size:12px; color:var(--muted); margin-top:2px; }
.bars { display:flex; flex-direction:column; gap:8px; max-width:560px; }
.barrow { display:flex; align-items:center; gap:10px; }
.barlabel { width:42px; font-size:13px; color:var(--muted); text-align:right; }
.bartrack { flex:1; background:var(--bg); border:1px solid var(--line);
            border-radius:5px; height:18px; overflow:hidden; }
.barfill { display:block; height:100%; }
.barfill.ok { background:var(--accent); }
.barfill.warn { background:var(--warn); }
.barval { width:48px; font-size:13px; }
table { width:100%; border-collapse:collapse; font-size:13px; margin-top:6px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
         vertical-align:top; }
th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--panel); }
.qtext { max-width:520px; }
tr.st-unanswered { opacity:.72; }
tr.st-struggling td { background:rgba(207,34,46,.08); }
.badge { font-size:11px; padding:2px 8px; border-radius:10px; }
.b-unanswered { background:#30363d; color:var(--muted); }
.b-mastered { background:rgba(46,160,67,.2); color:#3fb950; }
.b-struggling { background:rgba(207,34,46,.2); color:#ff7b72; }
footer { padding:20px 32px; color:var(--muted); font-size:12px;
         border-top:1px solid var(--line); }
"""


def render_html(exams: list[ExamStat], user: Optional[str]) -> str:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    scope = f"user: {user}" if user else "all users"

    toc = "".join(
        f'<a href="#exam-{i}">{_esc(e.exam_id)}</a>' for i, e in enumerate(exams)
    )
    body = []
    for i, e in enumerate(exams):
        body.append(f'<div id="exam-{i}"></div>')
        body.append(render_exam_section(e))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POSRAT - Question Statistics</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>POSRAT - Question Statistics</h1>
  <div class="muted">Scope: {_esc(scope)} &middot; {len(exams)} exam(s) &middot; generated {generated} UTC</div>
</header>
<main>
  <nav><ul class="toc">{toc}</ul></nav>
  {''.join(body)}
</main>
<footer>Generated by scripts/exam_stats.py - static offline report, no external resources.</footer>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "files", nargs="*", type=Path,
        help="Exam .sqlite file(s). Default: every exam in the data dir.",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR,
        help="Directory to scan when no files are given (default: ./data).",
    )
    parser.add_argument(
        "--user", default=None,
        help="Limit stats to sessions started by this POSRAT username.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output HTML path (default: <data-dir>/stats/index.html).",
    )
    args = parser.parse_args(argv)

    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = discover_exam_files(args.data_dir)

    if not files:
        print("No exam .sqlite files found.", file=sys.stderr)
        return 1

    exams: list[ExamStat] = []
    for f in files:
        if not f.exists():
            print(f"skip (not found): {f}", file=sys.stderr)
            continue
        try:
            stat = collect_exam_stat(f, args.user)
        except sqlite3.DatabaseError as exc:
            print(f"skip (not a valid DB): {f} ({exc})", file=sys.stderr)
            continue
        if stat is None:
            print(f"skip (not an exam DB): {f}", file=sys.stderr)
            continue
        exams.append(stat)

    if not exams:
        print("No valid exam databases to report on.", file=sys.stderr)
        return 1

    out = args.out or (args.data_dir / "stats" / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(exams, args.user), encoding="utf-8")

    print(f"Wrote report for {len(exams)} exam(s) -> {out}")
    for e in exams:
        print(f"  {e.exam_id}: {e.answered}/{e.total} answered, "
              f"{e.unanswered} unanswered, {e.session_count} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
