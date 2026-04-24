
# tests/test_importers_rtf_utils.py
"""Unit tests for the hand-written RTF stripper in posrat.importers.rtf_utils.

These exercise each branch of strip_rtf_to_text with minimal synthetic RTF
snippets so the behaviour can be pinned down without depending on a bundled
real-world fixture file.
"""

from __future__ import annotations

from posrat.importers.rtf_utils import strip_rtf_to_text


def test_simple_text_is_preserved() -> None:
    text, images = strip_rtf_to_text(b"{\\rtf1\\ansi Hello World}")
    assert text == "Hello World"
    assert images == {}


def test_par_becomes_newline_and_hex_escape_decodes() -> None:
    # \'0d is a carriage return — the RTF dump uses it to separate "Q<N>" from
    # the question body on the same RTF paragraph.
    text, images = strip_rtf_to_text(
        b"{\\rtf1\\ansi Hello\\par World\\'0d end}"
    )
    assert text == "Hello\nWorld\r end"
    assert images == {}


def test_unicode_escape_with_fallback_character_is_skipped() -> None:
    # \u269? — the literal '?' is the single fallback character that \uc1
    # instructs the parser to skip after decoding the code point.
    text, _ = strip_rtf_to_text(b"{\\rtf1\\ansi\\uc1 \\u269?erven\\'e1 liska}")
    # 'č' (U+010D) decoded from \u269, followed by 'erven' + 'é' + ' liska'.
    assert text == "červená liska"


def test_unicode_escape_with_negative_code_point() -> None:
    # -3891 => 0x10000 - 3891 = 0xF0CD is a Private Use Area glyph in RTF,
    # but here we just want to prove signed decoding works.
    text, _ = strip_rtf_to_text(b"{\\rtf1\\ansi\\uc1 \\u-3891?X}")
    assert text == chr(0x10000 - 3891) + "X"


def test_single_pict_is_replaced_with_placeholder() -> None:
    rtf = b"{\\rtf1\\ansi Hi {\\pict\\pngblip deadbeef} bye}"
    text, images = strip_rtf_to_text(rtf)
    assert text == "Hi ⟨IMG:0⟩ bye"
    assert images == {0: (".png", b"\xde\xad\xbe\xef")}


def test_multiple_picts_get_sequential_ids() -> None:
    rtf = (
        b"{\\rtf1\\ansi a {\\pict\\pngblip cafe} b "
        b"{\\pict\\jpegblip 0102} c {\\pict\\wmetafile ff}}"
    )
    text, images = strip_rtf_to_text(rtf)
    assert text == "a ⟨IMG:0⟩ b ⟨IMG:1⟩ c ⟨IMG:2⟩"
    assert images[0] == (".png", b"\xca\xfe")
    assert images[1] == (".jpg", b"\x01\x02")
    assert images[2] == (".wmf", b"\xff")


def test_fonttbl_colortbl_stylesheet_are_stripped() -> None:
    rtf = (
        b"{\\rtf1\\ansi"
        b"{\\fonttbl{\\f0\\fnil\\fcharset1 Arial;}}"
        b"{\\colortbl;\\red0\\green0\\blue0;}"
        b"{\\stylesheet{\\s0 Normal;}}"
        b" Body text here}"
    )
    text, images = strip_rtf_to_text(rtf)
    assert text == " Body text here"
    assert images == {}


def test_literal_brace_and_backslash_escapes() -> None:
    text, _ = strip_rtf_to_text(b"{\\rtf1\\ansi a\\{b\\}c\\\\d}")
    assert text == "a{b}c\\d"


def test_pict_payload_tolerates_whitespace() -> None:
    # Real RTF wraps hex bytes across lines — the decoder must ignore the
    # embedded spaces and newlines.
    rtf = b"{\\rtf1\\ansi {\\pict\\pngblip de ad\r\nbe\nef}}"
    _, images = strip_rtf_to_text(rtf)
    assert images[0] == (".png", b"\xde\xad\xbe\xef")


def test_control_words_with_numeric_arguments_are_consumed() -> None:
    # \fs20, \li0 and friends must vanish without eating the surrounding text.
    # Each control word eats its single trailing space as its terminator per
    # the RTF spec, so the "hi" run is unprefixed despite the spaces between
    # control words in the source.
    text, _ = strip_rtf_to_text(
        b"{\\rtf1\\ansi\\pard\\fi0\\li0 \\plain \\f0\\fs20 hi}"
    )
    assert text == "hi"


def test_rtf_like_question_skeleton() -> None:
    # Mirrors the shape of a real RTF paragraph: a font table,
    # Q-label with embedded CR, then question body, then an answer letter
    # line. This guards against regressions when the stripper changes.
    rtf = (
        b"{\\rtf1\\ansi\\uc1"
        b"{\\fonttbl{\\f0 Arial;}}"
        b"\\pard\\plain \\f0\\fs20 Q1\\'0d\\plain \\f0\\fs20 "
        b"What is 2+2?\\par"
        b"\\pard\\plain \\f0\\fs20 A. \\plain \\f0\\fs20 Four}"
    )
    text, images = strip_rtf_to_text(rtf)
    assert "Q1\rWhat is 2+2?" in text
    assert "A. Four" in text
    assert images == {}
