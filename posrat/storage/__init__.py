"""POSRAT storage layer (SQLite DAO + migrations).

Public API grows step-by-step during Phase 2. So far it exposes:

- the migration runner (step 2.0),
- :func:`open_db` to open/create a DB and auto-migrate it (step 2.4),
- :func:`create_exam` / :func:`get_exam` DAO functions (step 2.5),
- :func:`add_question` / :func:`update_question` / :func:`delete_question`
  question-level DAO (step 2.6),
- :func:`list_questions` ordered retrieval (step 2.7),
- :func:`start_session` / :func:`finish_session` / :func:`get_session` /
  :func:`list_sessions` / :func:`record_answer` session CRUD (step 2.5.3b).
"""

from posrat.storage.connection import open_db
from posrat.storage.exam_repo import create_exam, get_exam
from posrat.storage.migrations import CURRENT_SCHEMA_VERSION, apply_migrations
from posrat.storage.question_repo import (
    add_question,
    delete_question,
    list_questions,
    reorder_questions,
    update_question,
)

from posrat.storage.session_repo import (
    finish_session,
    get_session,
    list_sessions,
    record_answer,
    start_session,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "add_question",
    "apply_migrations",
    "create_exam",
    "delete_question",
    "finish_session",
    "get_exam",
    "get_session",
    "list_questions",
    "list_sessions",
    "open_db",
    "record_answer",
    "reorder_questions",
    "start_session",
    "update_question",
]

