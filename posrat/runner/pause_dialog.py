"""Training-mode pause dialog for the Runner.

Renders a persistent centered modal that freezes the countdown timer
while the candidate takes a break. Lives in its own module so both
the footer (where the Pause button is) and the timer widget (which
re-opens the modal automatically after a page refresh) can import it
without a circular dependency through :mod:`posrat.runner.choice_inputs`.

State shape on the session stash:

* ``paused_at`` — ISO-8601 UTC timestamp pinned the moment the
  candidate hits Pause. ``None`` while the timer is running.
* ``paused_seconds`` — total wall-clock seconds spent in past pauses.
  The countdown widget adds this back to ``remaining_seconds`` so the
  pause does not eat into the candidate's exam budget.

The dialog is non-dismissible (``persistent``) — the only way out is
the explicit Continue button, mirroring the timeout modal in
:mod:`posrat.runner.timer_widget`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from nicegui import app, ui

from posrat.runner.countdown import _parse_iso_utc
from posrat.runner.session_state import RUNNER_SESSION_STORAGE_KEY
from posrat.runner.view_helpers import utc_now_iso


def open_pause_dialog(stash: dict) -> None:
    """Pin ``paused_at`` (if not already set) and open the Paused modal.

    Idempotent on ``paused_at``: when called from a page-refresh
    auto-reopen path the field is already populated, so we leave it
    alone — otherwise we'd silently extend the pause every reload.
    """

    if not stash.get("paused_at"):
        stash["paused_at"] = utc_now_iso()
        app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash

    with ui.dialog() as dlg, ui.card().classes("q-pa-lg"):
        ui.label("Paused").classes("text-h5")
        ui.label(
            "The timer is paused. Click Continue to resume."
        ).classes("text-body2 q-mt-sm")

        with ui.row().classes("justify-end q-mt-md"):
            def _continue() -> None:
                started = _parse_iso_utc(stash.get("paused_at") or "")
                if started is not None:
                    elapsed = int(
                        (datetime.now(timezone.utc) - started).total_seconds()
                    )
                    stash["paused_seconds"] = (
                        int(stash.get("paused_seconds") or 0)
                        + max(elapsed, 0)
                    )
                stash["paused_at"] = None
                app.storage.user[RUNNER_SESSION_STORAGE_KEY] = stash
                dlg.close()

            ui.button("Continue", on_click=_continue).props("color=primary")

    dlg.props("persistent")
    dlg.open()


__all__ = ["open_pause_dialog"]
