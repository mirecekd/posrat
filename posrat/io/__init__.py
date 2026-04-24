"""POSRAT JSON / bundle import-export layer.

- :func:`load_exam_from_json_str` — parse + Pydantic-validate a JSON string.
- :func:`load_exam_from_json_file` — same, but from a filesystem path.
- :func:`import_exam_from_json_file` — load + :func:`create_exam` the exam
  into an open SQLite connection (fail-fast on validation). [step 2.8]
- :func:`dump_exam_to_json` — reconstruct an exam from SQLite into a
  JSON string. [step 2.9]
- :func:`export_exam_to_json_file` — same, but write directly to disk.
"""

from posrat.io.exporter import dump_exam_to_json, export_exam_to_json_file
from posrat.io.validator import (
    import_exam_from_json_file,
    load_exam_from_json_file,
    load_exam_from_json_str,
)

__all__ = [
    "dump_exam_to_json",
    "export_exam_to_json_file",
    "import_exam_from_json_file",
    "load_exam_from_json_file",
    "load_exam_from_json_str",
]
