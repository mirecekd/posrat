# posrat/importers/conversion.py
"""Conversion of ParsedQuestion intermediate objects to Pydantic Question models
and persistence into SQLite with optional image asset writing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from posrat.importers.base import ParsedQuestion
from posrat.models import Choice, Question


@dataclass
class ImportReport:
    imported: int
    skipped: list[tuple[ParsedQuestion, str]]
    image_paths: list[Path]


def convert_parsed_to_question(
    parsed: ParsedQuestion,
    id_prefix: str = "q-",
) -> Question:
    """Return a valid Pydantic Question. Raise ValueError on Pydantic failure.

    ⟨IMG:n⟩ placeholders in text are preserved as-is; image replacement
    is handled by persist_parsed_questions. image_path is always None here.
    """
    qid = f"{id_prefix}{uuid4().hex[:8]}"
    choices = [
        Choice(
            id=f"{qid}-{c.letter.lower()}",
            text=c.text,
            is_correct=c.is_correct,
        )
        for c in parsed.choices
    ]
    try:
        return Question(
            id=qid,
            type=parsed.question_type,
            text=parsed.text,
            explanation=parsed.explanation,
            choices=choices,
            image_path=None,
        )
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def persist_parsed_questions(
    selected: list[ParsedQuestion],
    *,
    db_path: Path,
    data_dir: Path,
    exam_id: str,
) -> ImportReport:
    """Persist a list of ParsedQuestion objects to SQLite with image asset writing.

    For each item:
    1. Pydantic validate via convert_parsed_to_question; on failure → skipped.
    2. Write image bytes to data_dir/assets/<exam_id>/ (only after validation).
    3. Replace ⟨IMG:n⟩ placeholders with Markdown image tags in question text.
    4. Insert into DB (retry up to 5 times on id collision before skipping).
    """
    import re

    from posrat.storage import add_question, open_db

    _img_re = re.compile("⟨IMG:(\\d+)⟩")

    imported = 0
    skipped: list[tuple[ParsedQuestion, str]] = []
    image_paths: list[Path] = []

    db = open_db(db_path)
    try:
        for parsed in selected:
            # 1. Pydantic validation — no side effects before this succeeds
            try:
                question = convert_parsed_to_question(parsed)
            except ValueError as exc:
                skipped.append((parsed, str(exc)))
                continue

            # 2+3. Write images and replace placeholders in text
            written: list[Path] = []
            if parsed.images:
                images_by_id = {img.placeholder_id: img for img in parsed.images}
                assets_dir = data_dir / "assets" / exam_id
                assets_dir.mkdir(parents=True, exist_ok=True)

                def _replace(
                    match: re.Match,
                    _ibi: dict = images_by_id,
                    _ad: Path = assets_dir,
                    _eid: str = exam_id,
                    _written: list = written,
                ) -> str:
                    pid = int(match.group(1))
                    if pid not in _ibi:
                        return match.group(0)
                    img = _ibi[pid]
                    filename = f"{uuid4().hex}{img.suffix}"
                    fpath = _ad / filename
                    fpath.write_bytes(img.data)
                    _written.append(fpath)
                    return f"![]({_eid}/{filename})"

                new_text = _img_re.sub(_replace, question.text)
                question = question.model_copy(update={"text": new_text})

            # 4. DB write with retry on id collision
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    add_question(db, exam_id, question)
                    imported += 1
                    image_paths.extend(written)
                    break
                except Exception as exc:
                    if attempt < max_retries - 1:
                        new_qid = f"q-{uuid4().hex[:8]}"
                        new_choices = [
                            Choice(
                                id=f"{new_qid}-{c.id.rsplit('-', 1)[-1]}",
                                text=c.text,
                                is_correct=c.is_correct,
                            )
                            for c in question.choices
                        ]
                        question = question.model_copy(
                            update={"id": new_qid, "choices": new_choices}
                        )
                    else:
                        for f in written:
                            f.unlink(missing_ok=True)
                        skipped.append((parsed, str(exc)))
    finally:
        db.close()

    return ImportReport(
        imported=imported,
        skipped=skipped,
        image_paths=image_paths,
    )
