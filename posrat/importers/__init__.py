# posrat/importers/__init__.py
"""Bulk import of exam questions from external sources (ExamTopics, ...).

This package provides pluggable parsers that convert external formats into
intermediate :class:`~posrat.importers.base.ParsedQuestion` objects. The UI /
higher-level code then lets the user preview the parsed questions before they
are materialised into Pydantic :class:`~posrat.models.question.Question`
instances and persisted via the regular storage layer.

See :mod:`posrat.importers.base` for the ``ImportSource`` protocol and the
registry helpers that discover concrete implementations.
"""

from __future__ import annotations

from posrat.importers.base import (
    IMPORT_SOURCES,
    ImportSource,
    ParseError,
    ParsedChoice,
    ParsedImage,
    ParsedQuestion,
    ParseResult,
    get_import_source,
    list_import_sources,
    register_import_source,
)
from posrat.importers.conversion import ImportReport, convert_parsed_to_question, persist_parsed_questions
from posrat.importers.examtopics import ExamTopicsParser
from posrat.importers.certexam_pdf import CertExamPdfParser

__all__ = [
    "IMPORT_SOURCES",
    "ImportReport",
    "ImportSource",
    "ParseError",
    "ParsedChoice",
    "ParsedImage",
    "ParsedQuestion",
    "ParseResult",
    "convert_parsed_to_question",
    "get_import_source",
    "list_import_sources",
    "persist_parsed_questions",
    "register_import_source",
    "ExamTopicsParser",
    "CertExamPdfParser",
]
