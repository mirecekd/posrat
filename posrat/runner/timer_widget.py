"""Live countdown widget for the Runner question view header.

Drops a ``MM:SS`` label + a 1-second ``ui.timer`` that re-reads the
remaining time from the pinned stash every tick. The label colour
shifts through three states based on pure-helper output:

* green-ish default while plenty of time left.
* warning (amber) once ≤ 5 minutes remain.
* negative (red) once ≤ 60 seconds remain.

When the timer expires (``is_expired`` flips true) we open a modal
with a single "End exam" button that routes through
:func:`posrat.runner.submit_flow.force_finish_session` → results view.

All timekeeping stays in :mod:`posrat.runner.countdown` — this
module is pure presentation glue.
"""

from __future__ import annotations

from nicegui import ui

from posrat.runner.countdown import (
    format_mm_ss,
    is_expired,
    remaining_seconds,
)
from posrat.runner.submit_flow import force_finish_session


#: How often (seconds) the countdown widget re-renders. 1 keeps the
#: MM:SS display in sync with wall-clock ticks without hammering the
#: server — every refresh is a cheap pure-function call.
COUNTDOWN_INTERVAL_SECONDS = 1.0


def render_countdown(stash: dict) -> None:
    """Render the countdown label + expire-handling modal for ``stash``.

    No-ops when the session has no timer (``time_limit_minutes`` is
    ``None`` or zero): timer-less exams continue to show just the
    candidate name in the header, exactly as before the timer feature
    landed.
    """

    started_at = stash.get("started_at") or ""
    limit = stash.get("time_limit_minutes")
    if limit is None or limit <= 0:
        return
    if not started_at:
        # Defensive: a stash without ``started_at`` is malformed; the
        # view already routes back to the picker via
        # :func:`posrat.runner.session_state.is_session_stash_complete`,
        # but we still guard so a half-populated legacy stash does not
        # crash the header.
        return

    # ``expired_handled`` is a one-shot latch: the modal may open
    # only once per session view, even though ``is_expired`` keeps
    # returning True on every tick. Using a mutable dict instead of
    # a plain bool lets the inner closures flip the flag.
    state: dict[str, object] = {"modal_shown": False}

    label = ui.label("").classes("text-caption text-grey")

    def _tick() -> None:
        secs = remaining_seconds(
            started_at=started_at, time_limit_minutes=limit
        )
        text = format_mm_ss(secs)
        if text is None:
            label.set_text("")
            return
        label.set_text(f"Remaining: {text}")

        # Recolour: default → warning → negative as time shrinks.
        if secs is not None and secs <= 60:
            label.classes(
                replace="text-caption text-negative text-weight-bold"
            )
        elif secs is not None and secs <= 5 * 60:
            label.classes(replace="text-caption text-warning")
        else:
            label.classes(replace="text-caption text-grey")

        if is_expired(
            started_at=started_at, time_limit_minutes=limit
        ) and not state["modal_shown"]:
            state["modal_shown"] = True
            _open_timeout_dialog(stash)

    # First render happens immediately so the candidate does not wait
    # one full interval to see the initial MM:SS value.
    _tick()
    ui.timer(COUNTDOWN_INTERVAL_SECONDS, _tick)


def _open_timeout_dialog(stash: dict) -> None:
    """Show a modal announcing the timeout with a single exit button.

    The dialog is non-dismissible (``persistent``) so the candidate
    cannot keep answering after the clock hit zero; the only way out
    is the "End exam" button which force-finishes the session
    and routes to the results screen.
    """

    with ui.dialog() as dlg, ui.card():
        ui.label("Time's up").classes("text-h5 text-negative")
        ui.label(
            "The exam time limit has expired. The session will end"
            " and the result will be shown."
        ).classes("text-body2 q-mt-sm")

        with ui.row().classes("justify-end q-mt-md"):
            def _end() -> None:
                dlg.close()
                force_finish_session(stash)

            ui.button("End exam", on_click=_end).props(
                "color=primary"
            )

    dlg.props("persistent")
    dlg.open()


__all__ = [
    "COUNTDOWN_INTERVAL_SECONDS",
    "render_countdown",
]
