"""Designer — right-hand Main editor panel.

Renders three stacked sections for the currently selected question:

1. **Question** — the question text (``on_blur`` auto-save).
2. **Available Choices** — radio / checkbox list for single / multi
   choice questions, or a "Open hotspot editor" stub for hotspot.
3. **Explanation/Reference** — the rationale textarea
   (``on_blur`` auto-save). Placeholder for the Phase 6 AI chat panel
   sits underneath as a commented separator.

Auto-save on blur mirrors the Visual CertExam flow — no "Save" button
is needed because every field commits straight to the DB via the pure
helpers in :mod:`posrat.designer.browser`. Errors surface as negative
notifications; success is silent (the refresh keeps everything in
sync).
"""

from __future__ import annotations

import sqlite3
import uuid

from nicegui import events, ui

from posrat.designer.browser import (
    MAX_IMAGE_SIZE_BYTES,
    _handle_edit_hotspot_click,
    _render_designer_body,
    delete_open_exam_asset,
    list_open_exam_assets,
    replace_question_choices_in_open_exam,
    update_question_explanation_in_open_exam,
    update_question_text_in_open_exam,
    upload_asset_to_open_exam,
)


from posrat.designer.state import get_selected_question
from posrat.models import Choice, Question


#: Pixel width of the letter prefix ("A.", "B.", …) column in the
#: Choices section. Wide enough for "AA." which we never produce today
#: but leaves headroom for exotic 27+ choice questions.
CHOICE_LETTER_WIDTH_PX = 32


def _letter_for_index(index: int) -> str:
    """Return the uppercase letter (``A``, ``B``, …) for ``index``.

    Zero-based. Wraps around at 26 (past ``Z`` the letters become
    ``AA`` / ``BB`` — unlikely in practice but cheap to handle).
    """

    if index < 0:
        raise ValueError(f"letter index must be non-negative, got {index}")
    if index < 26:
        return chr(ord("A") + index)
    # Very defensive fallback; no MVP question ever has 27+ choices.
    first = chr(ord("A") + (index // 26) - 1)
    second = chr(ord("A") + (index % 26))
    return f"{first}{second}"


def _persist_question_text(question: Question, new_text: str) -> None:
    """Persist the question text on blur.

    Uses the standard notify matrix: success is silent except for a
    refresh, ``False`` race → warning + refresh, ``None`` no-exam →
    warning, ``ValueError`` / ``sqlite3.DatabaseError`` → negative.
    Silent on empty input (we just clamp it to the original text so
    the on-disk value never becomes invalid) — the user sees their
    edit snap back and can try again.
    """

    cleaned = new_text.strip()
    if not cleaned:
        ui.notify(
            "Question text must not be empty.", type="warning"
        )
        _render_designer_body.refresh()
        return

    if cleaned == question.text:
        return  # no-op; skip the DB hit

    try:
        updated = update_question_text_in_open_exam(question.id, cleaned)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot save text: {exc}", type="negative")
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return
    if not updated:
        ui.notify(f"Question {question.id} no longer exists.", type="warning")
        _render_designer_body.refresh()
        return

    # Silent success — just keep downstream panels in sync.
    _render_designer_body.refresh()


def _persist_question_explanation(
    question: Question, new_explanation: str
) -> None:
    """Persist the explanation on blur.

    ``explanation`` is ``Optional[str]`` on the model; whitespace-only
    input maps to ``None`` (SQL NULL) so Runner's Training mode
    doesn't render a blank rationale bubble. Notification matrix
    matches :func:`_persist_question_text`.
    """

    raw = new_explanation.strip()
    target: str | None = raw or None
    if target == question.explanation:
        return  # no-op

    try:
        updated = update_question_explanation_in_open_exam(
            question.id, target
        )
    except sqlite3.DatabaseError as exc:
        ui.notify(f"Cannot save explanation: {exc}", type="negative")
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return
    if not updated:
        ui.notify(f"Question {question.id} no longer exists.", type="warning")
        _render_designer_body.refresh()
        return

    _render_designer_body.refresh()


def _rebuild_choices_from_draft(
    question: Question, draft: list[dict[str, object]]
) -> list[Choice]:
    """Normalise and construct :class:`Choice` list from the editor draft.

    Whitespace-only texts are kept (so the user can edit them inline
    without losing the row); Pydantic will refuse truly empty strings
    at the :class:`Question` constructor level. ``is_correct`` is
    carried verbatim — single_choice exactly-one / multi_choice ≥1
    invariants are enforced downstream by :func:`Question` validation.
    """

    result: list[Choice] = []
    for entry in draft:
        result.append(
            Choice(
                id=str(entry["id"]),
                text=str(entry.get("text") or ""),
                is_correct=bool(entry.get("is_correct")),
            )
        )
    return result


def _persist_choices(question: Question, new_choices: list[Choice]) -> None:
    """Persist the choices list via the standard DAO.

    Centralises the notify matrix so Question / Choice / Correctness
    edits all share the same error handling. ``ValueError`` from
    Pydantic invariants (exactly-one-correct, ≥1 correct, min length)
    surfaces as a negative notify and a refresh — the refresh
    re-reads from disk, so an invalid edit silently reverts the UI.
    """

    try:
        updated = replace_question_choices_in_open_exam(
            question.id, new_choices
        )
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot save answers: {exc}", type="negative")
        _render_designer_body.refresh()
        return

    if updated is None:
        ui.notify("No exam is open.", type="warning")
        return
    if not updated:
        ui.notify(f"Question {question.id} no longer exists.", type="warning")
        _render_designer_body.refresh()
        return

    _render_designer_body.refresh()


def _next_choice_id(question_id: str, draft: list[dict[str, object]]) -> str:
    """Mint a fresh choice id anchored to ``question_id``."""

    existing = {str(c["id"]) for c in draft}
    while True:
        candidate = f"{question_id}-{uuid.uuid4().hex[:6]}"
        if candidate not in existing:
            return candidate


def _render_question_section(question: Question) -> None:
    """Render the top 'Question' section with an autosaving textarea.

    Below the editable textarea we render a live :func:`ui.markdown`
    preview that mirrors the textarea value — bind via
    :meth:`nicegui.elements.markdown.Markdown.bind_content_from` so the
    user sees the rendered ``![](/media/...)`` image as soon as they
    paste the snippet, not only after running the Runner. The preview
    is wrapped in a bordered card on a light grey background to make
    the "input" vs "rendered output" distinction obvious without extra
    chrome.
    """

    # Header combines the Visual CertExam-style "Question" caption with
    # the Qn label + id of the question currently under edit so the user
    # never has to scroll back to the Explorer to check which row they're
    # working on (was easy to lose track on 181-question imports).
    from posrat.designer.browser import (
        format_question_label,
        load_questions_for_open_exam,
    )

    questions = load_questions_for_open_exam()
    try:
        idx = next(
            i for i, q in enumerate(questions) if q.id == question.id
        )
        q_label = format_question_label(idx)
    except StopIteration:
        q_label = "Question"

    with ui.card().classes("w-full q-mb-sm").props("bordered"):
        with ui.row().classes(
            "items-center q-gutter-sm bg-grey-3 q-pa-xs no-wrap"
        ):
            ui.label(q_label).classes(
                "text-caption text-weight-bold"
            )
            ui.label("·").classes("text-caption text-grey")
            ui.label(question.id).classes("text-caption text-grey")
            ui.space()
            ui.label("Question").classes("text-caption text-grey")
        text_area = ui.textarea(value=question.text).props(
            "autogrow borderless input-class=text-body1"
        ).classes("w-full q-px-sm")

        text_area.on(
            "blur",
            lambda _evt=None, q=question, ta=text_area: _persist_question_text(
                q, ta.value or ""
            ),
        )

        # Live markdown preview — updates as the user types, so the
        # pasted ``![](/media/...)`` snippet becomes a rendered image
        # immediately instead of staying a literal string until they
        # run the Runner.
        ui.separator().classes("q-my-xs")
        ui.label("Preview:").classes(
            "text-caption text-grey q-px-sm q-mt-xs"
        )
        preview = ui.markdown(content=question.text).classes(
            "q-px-sm q-pb-sm"
        )
        preview.bind_content_from(text_area, "value")



def _render_hotspot_placeholder(question: Question) -> None:
    """Render a stub + 'Edit hotspot' button for hotspot questions.

    Inline hotspot editing in the 3-panel layout is out of scope for
    the R refactor; the existing modal dialog (step 5.5) still owns
    the full options-pool + steps editor and stays one click away.
    """

    ui.label(
        "The hotspot editor opens in a dialog."
    ).classes("text-caption text-grey q-px-sm q-mt-sm")
    with ui.row().classes("justify-start q-px-sm q-mb-sm"):
        ui.button(
            "Edit hotspot",
            on_click=lambda _evt=None, q=question: _handle_edit_hotspot_click(q),
        ).props("size=sm color=primary")


def _render_choices_section(question: Question) -> None:
    """Render the 'Available Choices' section for single / multi choice.

    Each row shows the letter prefix (A/B/C/…), a radio / checkbox
    (depending on type) and an inline text input. Editing the text
    triggers auto-save on blur; toggling the correct state saves
    immediately. ``+`` adds a fresh row, ``×`` removes one — both
    persist instantly.

    Hotspot questions render the placeholder from
    :func:`_render_hotspot_placeholder` (inline hotspot editing is a
    dialog-only flow for MVP).
    """

    header_text = (
        "Available Choices (select all choices that are correct)"
        if question.type == "multi_choice"
        else "Available Choices (select the correct choice)"
    )

    with ui.card().classes("w-full q-mb-sm").props("bordered"):
        ui.label(header_text).classes(
            "text-caption text-grey bg-grey-3 q-pa-xs"
        )

        if question.type == "hotspot":
            _render_hotspot_placeholder(question)
            return

        # Working copy of the choices so text-input blurs can pick up
        # the latest edits without racing the radio / checkbox saves.
        draft: list[dict[str, object]] = [
            {"id": c.id, "text": c.text, "is_correct": c.is_correct}
            for c in question.choices
        ]

        # --- Correct-state toggle handlers ------------------------
        def _on_single_correct_change(evt, draft=draft, q=question) -> None:
            """Radio group change → flip which choice is correct."""
            picked = str(evt.value or "")
            for row in draft:
                row["is_correct"] = str(row["id"]) == picked
            _persist_choices(q, _rebuild_choices_from_draft(q, draft))

        def _on_multi_correct_toggle(
            evt, row=None, q=question, draft=draft
        ) -> None:
            row["is_correct"] = bool(evt.value)
            _persist_choices(q, _rebuild_choices_from_draft(q, draft))

        # --- Row renderer ----------------------------------------
        radio_value: str | None = next(
            (str(c["id"]) for c in draft if c["is_correct"]), None
        )

        radio: ui.radio | None = None
        if question.type == "single_choice":
            # One radio widget for the whole section, positioned
            # inside each row below. We build it once here so the
            # group state can propagate across rows.
            radio = ui.radio(
                options={str(c["id"]): "" for c in draft},
                value=radio_value,
            ).props("dense")
            radio.on_value_change(_on_single_correct_change)
            radio.classes("hidden")  # actual toggles render per-row

        with ui.column().classes("w-full q-px-sm q-gutter-xs"):
            for idx, row in enumerate(draft):
                choice_id = str(row["id"])
                letter = _letter_for_index(idx)

                with ui.row().classes(
                    "items-center q-gutter-sm no-wrap w-full"
                ):
                    ui.label(f"{letter}.").classes(
                        "text-body2 text-weight-medium"
                    ).style(
                        f"min-width: {CHOICE_LETTER_WIDTH_PX}px;"
                    )

                    if question.type == "single_choice":
                        # Click-through radio dot.
                        def _on_radio_click(
                            _evt=None, cid=choice_id, r=radio
                        ) -> None:
                            if r is not None:
                                r.set_value(cid)

                        dot = ui.icon(
                            "radio_button_checked"
                            if row["is_correct"]
                            else "radio_button_unchecked",
                            color=(
                                "primary"
                                if row["is_correct"]
                                else "grey-6"
                            ),
                        ).classes("cursor-pointer")
                        dot.on("click", _on_radio_click)
                    else:
                        checkbox = ui.checkbox(
                            value=bool(row["is_correct"])
                        ).props("dense")
                        checkbox.on_value_change(
                            lambda evt, r=row: _on_multi_correct_toggle(
                                evt, row=r
                            )
                        )

                    # Multi-line textarea s autogrow aby uživatel mohl
                    # odřádkovat delší odpovědi nebo vložit text s
                    # newlines. ``autogrow`` drží řádek kompaktní pro
                    # krátké odpovědi a rozšíří se pro víceřádkové.
                    # Na blur persistujeme — stejný pattern jako u
                    # legacy browser.py editoru (8.4 fix).
                    text_input = ui.textarea(value=str(row["text"])).props(
                        "dense borderless autogrow"
                    ).classes("col-grow")

                    def _on_text_blur(
                        _evt=None, r=row, inp=text_input, q=question, d=draft
                    ) -> None:
                        r["text"] = inp.value or ""
                        _persist_choices(q, _rebuild_choices_from_draft(q, d))

                    text_input.on("blur", _on_text_blur)


                    def _on_remove_row(
                        _evt=None, cid=choice_id, q=question, d=draft
                    ) -> None:
                        d[:] = [x for x in d if str(x["id"]) != cid]
                        _persist_choices(q, _rebuild_choices_from_draft(q, d))

                    ui.button(icon="close").props(
                        "flat dense size=sm color=negative"
                    ).on("click", _on_remove_row).tooltip("Remove choice")

        # --- Add row ------------------------------------------------
        def _on_add_row(
            _evt=None, q=question, d=draft
        ) -> None:
            # Seed the new row's text with the next unused A/B/C/…
            # letter so the Pydantic ``Choice.text = Field(...,
            # min_length=1)`` invariant holds on the immediate
            # persist-after-add. An empty placeholder text would
            # fail validation and surface a scary ValueError the
            # first time the user clicks "+"; the letter is also
            # what ``_seed_default_choices`` uses so the row blends
            # in with the A/B/C/D seed style until the user edits it.
            next_letter = _letter_for_index(len(d))
            d.append(
                {
                    "id": _next_choice_id(q.id, d),
                    "text": next_letter,
                    "is_correct": False,
                }
            )
            _persist_choices(q, _rebuild_choices_from_draft(q, d))


        with ui.row().classes("justify-start q-px-sm q-mb-sm"):
            ui.button("Add choice", icon="add", on_click=_on_add_row).props(
                "size=sm flat color=primary"
            )


def _render_explanation_section(question: Question) -> None:
    """Render the 'Explanation/Reference' section with an autosaving textarea.

    The AI chat panel (Phase 6) will live underneath this section; for
    now we render only a muted divider comment so the structure is
    visible in the UI even before the chat lands.
    """

    with ui.card().classes("w-full q-mb-sm").props("bordered"):
        ui.label("Explanation/Reference").classes(
            "text-caption text-grey bg-grey-3 q-pa-xs"
        )
        expl_area = ui.textarea(value=question.explanation or "").props(
            "autogrow borderless input-class=text-body2"
        ).classes("w-full q-px-sm")

        expl_area.on(
            "blur",
            lambda _evt=None, q=question, ta=expl_area: _persist_question_explanation(
                q, ta.value or ""
            ),
        )

        # Live markdown preview — same pattern as the Question section;
        # explanation text is where markdown images are most useful
        # (Training-mode Runner shows the rationale after a wrong
        # answer, often with an annotated screenshot).
        ui.separator().classes("q-my-xs")
        ui.label("Preview:").classes(
            "text-caption text-grey q-px-sm q-mt-xs"
        )
        preview = ui.markdown(content=question.explanation or "").classes(
            "q-px-sm q-pb-sm"
        )
        preview.bind_content_from(expl_area, "value")

        # Placeholder for Phase 6 AI chat panel — rendered as a muted
        # caption so users can see where the chat will live.
        ui.separator().classes("q-my-sm")
        ui.label("AI chat (Phase 6) — not yet implemented.").classes(
            "text-caption text-grey q-pa-xs"
        )



async def _persist_asset_upload(
    event: events.UploadEventArguments,
) -> None:
    """Store an uploaded file in the exam-wide asset pool.

    Wrapper around :func:`upload_asset_to_open_exam` that reads NiceGUI 3's
    ``event.file`` (async ``read()``), persists the bytes into
    ``<data_dir>/assets/<exam_id>/`` and refreshes the editor so the
    gallery row picks up the new thumbnail. The markdown snippet for
    embedding is not auto-copied — the user drags-drops or clicks
    "Copy markdown" on the gallery row to do that explicitly
    (keeps the clipboard out of the upload side-effect).
    """

    raw = await event.file.read()
    filename = event.file.name

    try:
        asset_path = upload_asset_to_open_exam(raw, filename)
    except (ValueError, sqlite3.DatabaseError) as exc:
        ui.notify(f"Cannot upload image: {exc}", type="negative")
        return

    if asset_path is None:
        ui.notify("No exam is open.", type="warning")
        return

    ui.notify(f"Image uploaded: {asset_path}", type="positive")
    _render_designer_body.refresh()


def _handle_delete_asset(asset_path: str) -> None:
    """Delete ``asset_path`` from the exam pool and refresh the UI.

    Does **not** scrub remaining ``![](...)`` references from question
    or choice texts — that's an explicit Fáze 9 polish task (orphan
    cleanup mirrors :func:`clear_question_image_in_file` behavior).
    The user will see a broken image in the Runner preview if they
    delete an asset still referenced somewhere.
    """

    try:
        removed = delete_open_exam_asset(asset_path)
    except OSError as exc:
        ui.notify(f"Cannot delete image: {exc}", type="negative")
        return

    if removed is None:
        ui.notify("No exam is open.", type="warning")
        return
    if not removed:
        ui.notify("File no longer exists.", type="warning")
        _render_designer_body.refresh()
        return

    ui.notify(f"Image deleted: {asset_path}", type="positive")
    _render_designer_body.refresh()


def _render_asset_gallery() -> None:
    """Render the exam-wide asset gallery at the top of the editor.

    Upload widget + grid of thumbnails for every ``image/*`` file in
    ``data/assets/<exam_id>/``. Each thumbnail has:

    * **Copy markdown** button — writes ``![](/media/assets/<exam_id>/<filename>)``
      to the browser clipboard via :func:`ui.clipboard.write`. The
      user can then ``Ctrl+V`` into any of the three textareas
      (Question, any Choice, Explanation) to embed the image.
    * **Delete** button — removes the file from disk via
      :func:`delete_open_exam_asset`.

    The reason we split image upload from per-question ``image_path``
    is that one image often needs to appear in multiple places (e.g.
    a policy JSON screenshot shown in both the question text and a
    choice text), which the single ``Question.image_path`` slot cannot
    model. Using markdown syntax keeps the storage layer completely
    unchanged — the text columns already hold strings, any markdown
    renderer picks up ``![alt](url)`` for free.
    """

    from posrat.app import ASSETS_URL_PREFIX

    max_mb = MAX_IMAGE_SIZE_BYTES // 1_000_000
    asset_paths = list_open_exam_assets()

    # The gallery lives inside a collapsible ``ui.expansion`` because it
    # takes a lot of vertical space once a dozen+ screenshots are
    # uploaded — keeping it collapsed by default means the user only
    # opens it when they actually want to grab a markdown snippet. The
    # label on the header shows the current asset count so it's obvious
    # whether something is in there to open.
    header = (
        f"Exam images (shared pool) — {len(asset_paths)}"
        if asset_paths
        else "Exam images (shared pool)"
    )
    with ui.expansion(header, icon="image").classes(
        "w-full q-mb-sm"
    ).props("header-class=bg-grey-3 dense"):
        ui.label(
            "Upload one or more images and then use 'Copy markdown' "
            "to paste the ![](/media/...) snippet with Ctrl+V into "
            "the question or answer text."
        ).classes("text-caption text-grey q-px-sm q-mt-xs")

        with ui.row().classes("q-pa-sm"):
            ui.upload(

                on_upload=_persist_asset_upload,
                on_rejected=lambda _evt=None: ui.notify(
                    f"File exceeded the {max_mb} MB limit.",
                    type="negative",
                ),
                max_file_size=MAX_IMAGE_SIZE_BYTES,
                auto_upload=True,
                multiple=True,
                label=(
                    f"Upload image(s) (max {max_mb} MB, "
                    "PNG/JPG/GIF/WebP/SVG)"
                ),
            ).props("accept=image/* color=primary flat dense").classes(
                "w-full"
            )

        if not asset_paths:
            ui.label("No images yet.").classes(
                "text-caption text-grey q-px-sm q-pb-sm"
            )
            return

        # Grid of thumbnails (3 columns — fits nicely in the default
        # editor width without growing the page too tall).
        with ui.grid(columns=3).classes("q-pa-sm q-gutter-sm w-full"):
            for asset_path in asset_paths:
                markdown_snippet = f"![]({ASSETS_URL_PREFIX}/{asset_path})"
                image_url = f"{ASSETS_URL_PREFIX}/{asset_path}"

                with ui.card().classes("q-pa-xs").props("bordered"):
                    ui.image(image_url).style(
                        "max-width: 220px; max-height: 150px;"
                    )
                    ui.label(asset_path.rsplit("/", 1)[-1]).classes(
                        "text-caption text-grey ellipsis"
                    )
                    with ui.row().classes("q-gutter-xs no-wrap"):
                        ui.button(
                            "Copy markdown",
                            icon="content_copy",
                            on_click=lambda _evt=None, snippet=markdown_snippet: (
                                ui.clipboard.write(snippet),
                                ui.notify(
                                    "Markdown copied to clipboard — Ctrl+V into the question text.",
                                    type="positive",
                                ),
                            ),
                        ).props("size=xs flat color=primary")
                        ui.button(
                            icon="delete",
                            on_click=lambda _evt=None, ap=asset_path: _handle_delete_asset(
                                ap
                            ),
                        ).props("size=xs flat color=negative").tooltip(
                            "Delete file"
                        )



def render_editor_panel() -> None:
    """Render the main right-hand editor for the selected question.

    Empty-state placeholder when no question is selected. Otherwise
    stacks the three per-field sections (Question / Choices /
    Explanation + AI chat scaffold) plus a small image toolbar row on
    top so attachments stay one click away.
    """

    question = get_selected_question()
    if question is None:
        ui.label(
            "Select a question in the Exam Explorer to open the editor."
        ).classes("text-caption text-grey q-pa-md")
        return

    # Order: Question → Choices → Explanation(+AI) → Asset gallery.
    # The user wants the main editing surface (question text, answers,
    # rationale) at the top and the "shared asset pool" at the bottom
    # so it never pushes the primary edit targets below the fold.
    _render_question_section(question)
    _render_choices_section(question)
    _render_explanation_section(question)
    _render_asset_gallery()



__all__ = [
    "CHOICE_LETTER_WIDTH_PX",
    "render_editor_panel",
]
