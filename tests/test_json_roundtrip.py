"""JSON round-trip tests for the data model.

Goal of step 1.7: prove that ``Exam -> JSON -> Exam`` is lossless
across all three question types and that the reconstructed objects
are *equal* (Pydantic structural equality), not merely "similar".

We use ``model_dump_json()`` / ``model_validate_json()`` — the same
path the future JSON bundle importer will take.
"""

from __future__ import annotations

from posrat.models import (
    Choice,
    Exam,
    Hotspot,
    HotspotOption,
    HotspotStep,
    Question,
)


def _build_sample_exam() -> Exam:
    """Build one small exam that exercises every question type and
    every optional field (explanation, image_path, hotspot)."""

    single = Question(
        id="q-single",
        type="single_choice",
        text="What is object storage on AWS?",
        explanation="S3 is the object store.",
        image_path="q-single.png",
        choices=[
            Choice(id="a", text="S3", is_correct=True),
            Choice(id="b", text="EBS", is_correct=False),
            Choice(id="c", text="EFS", is_correct=False),
        ],
    )

    multi = Question(
        id="q-multi",
        type="multi_choice",
        text="Which are AWS compute services?",
        explanation="EC2 and Lambda are compute; S3 is storage.",
        choices=[
            Choice(id="a", text="EC2", is_correct=True),
            Choice(id="b", text="Lambda", is_correct=True),
            Choice(id="c", text="S3", is_correct=False),
        ],
    )

    hotspot_payload = Hotspot(
        options=[
            HotspotOption(id="o-s3", text="S3"),
            HotspotOption(id="o-ec2", text="EC2"),
            HotspotOption(id="o-lambda", text="Lambda"),
        ],
        steps=[
            HotspotStep(
                id="step-storage",
                prompt="Durable object storage",
                correct_option_id="o-s3",
            ),
            HotspotStep(
                id="step-vm",
                prompt="Long-running virtual machine",
                correct_option_id="o-ec2",
            ),
            HotspotStep(
                id="step-fn",
                prompt="Event-driven function",
                correct_option_id="o-lambda",
            ),
        ],
    )
    hotspot = Question(
        id="q-hotspot",
        type="hotspot",
        text="Match each capability to the right AWS service.",
        explanation="See the AWS services overview.",
        hotspot=hotspot_payload,
    )

    return Exam(
        id="sample-exam-1",
        name="POSRAT sample exam",
        description="Mixed exam used by the round-trip test.",
        questions=[single, multi, hotspot],
    )


def test_exam_json_roundtrip_is_lossless() -> None:
    original = _build_sample_exam()
    dumped = original.model_dump_json()
    restored = Exam.model_validate_json(dumped)

    # Pydantic's __eq__ compares fields recursively — this is the
    # strongest "nothing got lost or coerced" assertion we can make
    # at the model layer.
    assert restored == original

    # And the second dump must be byte-for-byte identical to the first,
    # which guarantees no hidden drift via defaults/coercion.
    assert restored.model_dump_json() == dumped


def test_question_types_survive_roundtrip() -> None:
    original = _build_sample_exam()
    restored = Exam.model_validate_json(original.model_dump_json())

    types = [q.type for q in restored.questions]
    assert types == ["single_choice", "multi_choice", "hotspot"]

    hs_q = next(q for q in restored.questions if q.type == "hotspot")
    assert hs_q.hotspot is not None
    assert len(hs_q.hotspot.options) == 3
    assert len(hs_q.hotspot.steps) == 3


def test_roundtrip_preserves_complexity_and_section_through_json(
    tmp_path,
) -> None:
    """Step 8.5 fields survive the SQLite → JSON → SQLite cycle.

    Guard against future regressions where someone adds ``exclude`` /
    ``exclude_none`` to the exporter and silently drops the new metadata.
    Also covers the "all None" case via a bare legacy-shaped question to
    prove None stays None through the full loop.
    """

    from posrat.io import dump_exam_to_json, load_exam_from_json_str
    from posrat.models import Choice, Exam, Question
    from posrat.storage import create_exam, open_db

    rated = Question(
        id="q-rated",
        type="single_choice",
        text="Rated",
        choices=[
            Choice(id="q-rated-a", text="A", is_correct=True),
            Choice(id="q-rated-b", text="B", is_correct=False),
        ],
        complexity=5,
        section="Compute",
    )
    bare = Question(
        id="q-bare",
        type="single_choice",
        text="Bare",
        choices=[
            Choice(id="q-bare-a", text="A", is_correct=True),
            Choice(id="q-bare-b", text="B", is_correct=False),
        ],
    )

    db = open_db(tmp_path / "src.sqlite")
    try:
        original = Exam(
            id="meta-rt",
            name="Meta round-trip",
            questions=[rated, bare],
        )
        create_exam(db, original)

        json_str = dump_exam_to_json(db, "meta-rt")
    finally:
        db.close()

    assert '"complexity": 5' in json_str
    assert '"section": "Compute"' in json_str

    reloaded = load_exam_from_json_str(json_str)
    by_id = {q.id: q for q in reloaded.questions}
    assert by_id["q-rated"].complexity == 5
    assert by_id["q-rated"].section == "Compute"
    assert by_id["q-bare"].complexity is None
    assert by_id["q-bare"].section is None


def test_load_exam_from_json_accepts_legacy_file_without_metadata_fields() -> None:
    """Pre-8.5 JSON files must still import (defaults: complexity+section None).

    The importer hits ``Exam.model_validate_json`` which falls back to the
    Pydantic field defaults for missing keys. This test pins that
    behaviour so we do not accidentally flip either field to
    ``required`` later.
    """

    from posrat.io import load_exam_from_json_str

    legacy = (
        '{"id":"e","name":"Legacy","description":null,"questions":['
        '{"id":"q1","type":"single_choice","text":"Legacy question",'
        '"explanation":null,"image_path":null,"hotspot":null,"choices":['
        '{"id":"a","text":"A","is_correct":true},'
        '{"id":"b","text":"B","is_correct":false}'
        "]}]}"
    )

    exam = load_exam_from_json_str(legacy)
    assert exam.questions[0].complexity is None
    assert exam.questions[0].section is None


# --------------------------------------------------------------------------- #
# Phase 7A.4 — Runner-facing exam metadata JSON round-trip                    #
# --------------------------------------------------------------------------- #


def test_dump_and_reload_exam_json_preserves_runner_metadata(tmp_path) -> None:
    """Exam-level Runner metadata (7A.1) must survive JSON dump + reload.

    Relies purely on Pydantic default serialization — no special handling
    in ``posrat.io`` because the four new fields are regular
    ``Optional[int]`` attributes on :class:`Exam`. This test pins that
    contract so a future custom serializer does not accidentally drop
    them.
    """

    from posrat.io import dump_exam_to_json, load_exam_from_json_str
    from posrat.storage import create_exam, open_db

    db = open_db(tmp_path / "meta.sqlite")
    try:
        original = Exam(
            id="aif-c01",
            name="AIF-C01",
            description="AWS AI Practitioner",
            default_question_count=65,
            time_limit_minutes=90,
            passing_score=700,
            target_score=1000,
            questions=[],
        )
        create_exam(db, original)

        json_str = dump_exam_to_json(db, "aif-c01")
    finally:
        db.close()

    # All four keys appear in the serialized JSON payload.
    assert '"default_question_count": 65' in json_str
    assert '"time_limit_minutes": 90' in json_str
    assert '"passing_score": 700' in json_str
    assert '"target_score": 1000' in json_str

    reloaded = load_exam_from_json_str(json_str)
    assert reloaded.default_question_count == 65
    assert reloaded.time_limit_minutes == 90
    assert reloaded.passing_score == 700
    assert reloaded.target_score == 1000


def test_load_exam_from_json_accepts_legacy_file_without_runner_metadata() -> None:
    """Pre-Phase-7A JSON bundles must still import with ``None`` metadata.

    Pins the legacy-compat promise: old exam exports in the wild have
    only the pre-7A fields. The importer must fall back to the field
    defaults (``None``) for every missing metadata key.
    """

    from posrat.io import load_exam_from_json_str

    legacy = (
        '{"id":"e","name":"Legacy","description":null,"questions":['
        '{"id":"q1","type":"single_choice","text":"Legacy question",'
        '"explanation":null,"image_path":null,"hotspot":null,"choices":['
        '{"id":"a","text":"A","is_correct":true},'
        '{"id":"b","text":"B","is_correct":false}'
        "]}]}"
    )

    exam = load_exam_from_json_str(legacy)
    assert exam.default_question_count is None
    assert exam.time_limit_minutes is None
    assert exam.passing_score is None
    assert exam.target_score is None
