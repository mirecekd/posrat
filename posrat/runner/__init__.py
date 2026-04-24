"""POSRAT Runner — training/exam session player.

The Runner is the right-hand counterpart to the Designer. Where the
Designer lets you author and edit exams, the Runner lets a candidate
sit through one: pick an exam, choose a mode, answer questions and see
a results summary at the end.

Module layout (Phase 7 incremental split):

* :mod:`posrat.runner.picker` — exam list / metadata summaries shown on
  the ``/runner`` landing page.
* :mod:`posrat.runner.sampler` — pure helpers: random N-of-all question
  sampling, shuffle-respecting choice ordering.
* :mod:`posrat.runner.mode_dialog` — Visual CertExam-style "Exam Mode"
  dialog (candidate name, question count, mode, timer).
* :mod:`posrat.runner.session_state` — per-user ``app.storage.user``
  serialisation for an in-flight session.
* :mod:`posrat.runner.page` — ``/runner`` + ``/runner/<session_id>``
  route handlers and their assembled bodies.
* :mod:`posrat.runner.grading` — pure helpers to score an :class:`Answer`
  against a :class:`~posrat.models.Question` ``given_json`` payload.
* :mod:`posrat.runner.identity`` — multi-user username resolution
  (nginx / OIDC header → ``USER`` env var → ``local_dev`` fallback).

Each module stays narrow on purpose so individual steps can ship as
small, testable commits. The public entry point wired into
:mod:`posrat.app` is :func:`render_runner`.
"""


from __future__ import annotations

from posrat.runner.identity import (
    DEFAULT_LOCAL_USERNAME,
    USERNAME_ENV,
    resolve_username,
)
from posrat.runner.picker import (
    RunnerExamSummary,
    list_runnable_exams,
    summarise_runnable_exam,
)
from posrat.runner.grading import (
    decode_given_json,
    encode_answer_payload,
    grade_answer,
)
from posrat.runner.orchestrator import (
    SessionScore,
    StartedSession,
    compute_session_score,
    start_runner_session,
    submit_runner_answer,
)
from posrat.runner.history import (
    SessionResultSummary,
    list_session_results,
)
from posrat.runner.history_view import RUNNER_HISTORY_HEADING
from posrat.runner.page import render_runner
from posrat.runner.picker_view import RUNNER_PICKER_HEADING


from posrat.runner.sampler import sample_question_ids, shuffle_choices
from posrat.runner.session_state import (
    RUNNER_SESSION_STORAGE_KEY,
    advance_session_stash,
    build_runner_session_stash,
    is_session_stash_complete,
)


__all__ = [
    "DEFAULT_LOCAL_USERNAME",
    "RUNNER_HISTORY_HEADING",
    "RUNNER_PICKER_HEADING",
    "RUNNER_SESSION_STORAGE_KEY",
    "RunnerExamSummary",
    "SessionResultSummary",
    "SessionScore",
    "StartedSession",
    "USERNAME_ENV",
    "advance_session_stash",
    "build_runner_session_stash",
    "compute_session_score",
    "decode_given_json",
    "encode_answer_payload",
    "grade_answer",
    "is_session_stash_complete",
    "list_runnable_exams",
    "list_session_results",
    "render_runner",
    "resolve_username",
    "sample_question_ids",
    "shuffle_choices",
    "start_runner_session",
    "submit_runner_answer",
    "summarise_runnable_exam",
]




