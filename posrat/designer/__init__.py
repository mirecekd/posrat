"""POSRAT Designer UI package.

The Designer lets the user browse, create and edit exam questions. It is
intentionally split into narrow modules so each concern can evolve
independently:

Since the Phase-R refactor the Designer is a 3-panel layout
(Visual CertExam-style):

* :mod:`posrat.designer.browser` — pure DAO helpers + legacy modal
  dialog handlers (still reachable via the inline per-field editors in
  the Exam Explorer row context menu, and by the 3-panel Editor's
  "Open hotspot editor" / "Image…" buttons).
* :mod:`posrat.designer.state` — per-user "selected question id"
  storage + helpers.
* :mod:`posrat.designer.explorer` — top-left Exam Explorer panel.
* :mod:`posrat.designer.properties` — bottom-left Properties grid.
* :mod:`posrat.designer.editor` — right-hand Question / Choices /
  Explanation editor.
* :mod:`posrat.designer.layout` — 3-panel splitter assembler.

The AI chat panel (Phase 6) will live under the Explanation section in
:mod:`posrat.designer.editor` once it's implemented.
"""


from __future__ import annotations

from posrat.designer.browser import (
    ALLOWED_QUESTION_TYPES,
    ASSETS_DIRNAME,
    BLANK_QUESTION_ID_PREFIX,
    BLANK_QUESTION_TEXT,
    DATA_DIR_ENV,
    DEFAULT_DATA_DIR,
    DEFAULT_MULTI_CHOICE_COUNT,
    DEFAULT_SINGLE_CHOICE_COUNT,
    DIRTY_LABEL_TEXT,

    EXAM_FILE_SUFFIX,
    EXPORTS_DIRNAME,
    MAX_IMAGE_SIZE_BYTES,
    MOVE_DOWN,
    MOVE_UP,
    OPEN_EXAM_STORAGE_KEY,
    QUESTION_LIST_TEXT_PREVIEW,
    SAVED_LABEL_TEXT,
    SEARCH_QUERY_STORAGE_KEY,
    add_blank_question_to_file,
    add_blank_question_to_open_exam,
    attach_image_to_question_in_file,
    attach_image_to_question_in_open_exam,
    change_question_type_in_file,
    change_question_type_in_open_exam,
    clear_question_image_in_file,
    clear_question_image_in_open_exam,
    create_exam_file,
    delete_question_from_file,

    delete_question_from_open_exam,
    export_exam_to_json_in_file,
    export_open_exam_to_json,
    filter_questions,
    format_question_label,

    is_open_exam_dirty,
    list_exam_files,
    load_questions_for_open_exam,
    load_questions_from_file,
    move_question_in_file,
    move_question_in_open_exam,
    open_exam_from_file,
    render_designer,
    reorder_questions_in_file,
    replace_question_choices_in_file,
    replace_question_choices_in_open_exam,
    replace_question_hotspot_in_file,
    replace_question_hotspot_in_open_exam,
    resolve_assets_dir,
    resolve_data_dir,
    resolve_exports_dir,
    resolve_question_image_path,
    update_exam_default_question_count_in_file,
    update_exam_default_question_count_in_open_exam,
    update_exam_passing_score_in_file,
    update_exam_passing_score_in_open_exam,
    update_exam_target_score_in_file,
    update_exam_target_score_in_open_exam,
    update_exam_time_limit_minutes_in_file,
    update_exam_time_limit_minutes_in_open_exam,
    update_question_allow_shuffle_in_file,
    update_question_allow_shuffle_in_open_exam,
    update_question_complexity_in_file,
    update_question_complexity_in_open_exam,
    update_question_explanation_in_file,
    update_question_explanation_in_open_exam,
    update_question_section_in_file,
    update_question_section_in_open_exam,
    update_question_text_in_file,
    update_question_text_in_open_exam,
)






__all__ = [
    "ALLOWED_QUESTION_TYPES",
    "ASSETS_DIRNAME",
    "BLANK_QUESTION_ID_PREFIX",
    "BLANK_QUESTION_TEXT",
    "DATA_DIR_ENV",
    "DEFAULT_DATA_DIR",
    "DEFAULT_MULTI_CHOICE_COUNT",
    "DEFAULT_SINGLE_CHOICE_COUNT",
    "DIRTY_LABEL_TEXT",

    "EXAM_FILE_SUFFIX",
    "EXPORTS_DIRNAME",
    "MAX_IMAGE_SIZE_BYTES",
    "MOVE_DOWN",
    "MOVE_UP",
    "OPEN_EXAM_STORAGE_KEY",
    "QUESTION_LIST_TEXT_PREVIEW",
    "SAVED_LABEL_TEXT",
    "SEARCH_QUERY_STORAGE_KEY",
    "add_blank_question_to_file",
    "add_blank_question_to_open_exam",
    "attach_image_to_question_in_file",
    "attach_image_to_question_in_open_exam",
    "change_question_type_in_file",
    "change_question_type_in_open_exam",
    "clear_question_image_in_file",
    "clear_question_image_in_open_exam",
    "create_exam_file",
    "delete_question_from_file",
    "delete_question_from_open_exam",
    "export_exam_to_json_in_file",
    "export_open_exam_to_json",
    "filter_questions",
    "format_question_label",
    "is_open_exam_dirty",

    "list_exam_files",
    "load_questions_for_open_exam",
    "load_questions_from_file",
    "move_question_in_file",
    "move_question_in_open_exam",
    "open_exam_from_file",
    "render_designer",
    "reorder_questions_in_file",
    "replace_question_choices_in_file",
    "replace_question_choices_in_open_exam",
    "replace_question_hotspot_in_file",
    "replace_question_hotspot_in_open_exam",
    "resolve_assets_dir",
    "resolve_data_dir",
    "resolve_exports_dir",
    "resolve_question_image_path",
    "update_exam_default_question_count_in_file",
    "update_exam_default_question_count_in_open_exam",
    "update_exam_passing_score_in_file",
    "update_exam_passing_score_in_open_exam",
    "update_exam_target_score_in_file",
    "update_exam_target_score_in_open_exam",
    "update_exam_time_limit_minutes_in_file",
    "update_exam_time_limit_minutes_in_open_exam",
    "update_question_allow_shuffle_in_file",
    "update_question_allow_shuffle_in_open_exam",
    "update_question_complexity_in_file",
    "update_question_complexity_in_open_exam",

    "update_question_explanation_in_file",

    "update_question_explanation_in_open_exam",
    "update_question_section_in_file",
    "update_question_section_in_open_exam",
    "update_question_text_in_file",
    "update_question_text_in_open_exam",
]




