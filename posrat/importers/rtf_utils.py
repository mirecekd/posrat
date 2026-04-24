
# posrat/importers/rtf_utils.py
"""Minimal, dependency-free RTF text extractor tailored to ExamTopics dumps.

The ExamTopics bulk import flow (Fáze 8) feeds us plain RTF files. A real
RTF parser would be overkill — ExamTopics' output uses only a tiny subset of
the spec (plain paragraphs, ``\\par``/``\\line`` breaks, hex-escaped CR bytes
as Q-label separators, occasional ``{\\pict ...}`` image groups). Pulling in
a third-party library for this would add deployment weight and a new attack
surface for essentially one-shot parsing.

This module therefore implements a focused hand-written stripper with one
public helper:

* :func:`strip_rtf_to_text` turns raw RTF bytes into a ``(text, images)`` pair.

Why return the images alongside the text instead of inline:

* The downstream ExamTopics parser (step 8.2) needs clean text to regex on.
  Keeping the image bytes out of the text stream means it can do that with
  simple ``Q<N>`` tokenization without worrying about binary pollution.
* The conversion step (step 8.4) persists images to ``assets/`` *only* for
  questions the user actually imports. Deferring the write means we avoid
  cluttering disk with hundreds of picture files for a preview that may be
  cancelled.

The stripper is deliberately lenient: unknown control words are silently
consumed, malformed ``\\'XX`` escapes degrade to the literal byte ``0xFF`` as
a last-resort replacement, and any ``{\\pict ...}`` group whose hex payload
fails to decode still produces a placeholder (with empty bytes) so the text
stream stays structurally aligned with what the user sees in Word.
"""

from __future__ import annotations

import re
from typing import Final


_HEX_RE: Final[re.Pattern[str]] = re.compile(r"[0-9A-Fa-f]")
"""Cached matcher for a single hex digit. Used when decoding ``{\\pict ...}``
payloads, where we must tolerate embedded whitespace/newlines between nibbles
but reject anything else."""


def _decode_pict_payload(raw: str) -> bytes:
    """Decode the hex body of a ``{\\pict ...}`` group to raw image bytes.

    ExamTopics wraps the image bytes in ASCII hex with arbitrary newlines and
    spaces inserted for line-wrapping. We strip every non-hex character (the
    RTF spec guarantees the hex stream itself contains only ``0-9a-fA-F``)
    and decode the remaining even-length run.

    If the stripped string has an odd length (corrupt payload) the trailing
    nibble is dropped rather than raising — the caller still needs a
    placeholder in the text stream even if the bytes are unusable.
    """
    hex_only = "".join(ch for ch in raw if _HEX_RE.match(ch))
    if len(hex_only) % 2 == 1:
        hex_only = hex_only[:-1]
    try:
        return bytes.fromhex(hex_only)
    except ValueError:
        return b""


_PICT_FORMAT_TO_SUFFIX: Final[dict[str, str]] = {
    "pngblip": ".png",
    "jpegblip": ".jpg",
    "wmetafile": ".wmf",
    "emfblip": ".emf",
}
"""Mapping from RTF picture format control words to file suffixes used on
disk. Anything not listed here falls back to ``.bin`` so the bytes can still
be inspected after import."""


def _parse_pict(body: str) -> tuple[str, bytes]:
    """Turn the *inside* of a ``{\\pict ...}`` group into ``(suffix, bytes)``.

    ``body`` is the text between the opening ``{\\pict`` and the matching
    closing brace, **including** subsequent control words like ``\\pngblip``
    and any picture size hints (``\\picw1234``). Size hints are ignored; we
    only care about the format flag and the hex payload.

    The suffix returned always starts with a dot so it can be concatenated
    directly onto a base filename (``f"img_0{suffix}"``).
    """
    suffix = ".bin"
    for fmt, ext in _PICT_FORMAT_TO_SUFFIX.items():
        # Use word boundary via regex to avoid matching 'pngblipfoo'.
        if re.search(rf"\\{fmt}\b", body):
            suffix = ext
            break

    # The hex payload starts after the last control word and runs to the end
    # of the group. Strip all control words (``\word`` optionally followed by
    # digits) and braces; what's left is hex + whitespace.
    payload = re.sub(r"\\[A-Za-z]+-?\d*", " ", body)
    payload = payload.replace("{", " ").replace("}", " ")
    return suffix, _decode_pict_payload(payload)


def _find_group_end(data: str, open_brace: int) -> int:
    """Return the index of the ``}`` that closes the group opened at ``open_brace``.

    Respects RTF's ``\\{`` / ``\\}`` escapes (which are *not* group delimiters)
    and ``\\\\`` (a literal backslash that must not be mistaken for the start
    of such an escape).

    If the RTF is truncated and no matching brace is found, returns
    ``len(data)`` so the caller treats the rest of the stream as the group
    body rather than looping forever.
    """
    assert data[open_brace] == "{"
    depth = 0
    i = open_brace
    n = len(data)
    while i < n:
        ch = data[i]
        if ch == "\\" and i + 1 < n:
            # Skip the escaped next character so \{ \} \\ don't affect depth.
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


_SKIP_DESTINATION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "header",
        "footer",
        "headerl",
        "headerr",
        "footerl",
        "footerr",
        "pgdsctbl",
        "listtable",
        "listoverridetable",
        "rsidtbl",
        "generator",
        "themedata",
        "datastore",
        "latentstyles",
        "revtbl",
        "filetbl",
    }
)
"""Destination control words whose entire containing group should be dropped
from the output. These carry metadata (font definitions, stylesheet, revision
marks, ...) that is not user-visible text in Word and would pollute the
stripped output if left in."""


def strip_rtf_to_text(
    rtf_bytes: bytes,
) -> tuple[str, dict[int, tuple[str, bytes]]]:
    """Convert raw RTF bytes to plain text plus an image sidecar dict.

    Parameters
    ----------
    rtf_bytes:
        The full contents of an ``.rtf`` file. Must be ASCII-compatible in its
        non-escaped portions (RTF itself is 7-bit; non-ASCII characters are
        represented via ``\\'XX`` hex escapes and ``\\uNNNN`` unicode escapes).

    Returns
    -------
    tuple
        ``(text, images)`` where ``text`` is a Python ``str`` with every RTF
        control stripped or translated, and ``images`` maps
        ``placeholder_id -> (suffix, raw_bytes)``. Each embedded picture
        group has been replaced in ``text`` by a placeholder token of exactly
        the form ``⟨IMG:{id}⟩`` (U+27E8 / U+27E9), with ``id`` an integer
        starting at 0 and incremented for every picture encountered in
        document order.

    Notes
    -----
    Key handling rules, matching the RTF 1.9 spec as far as ExamTopics exercises it:

    * ``\\par`` and ``\\line`` expand to ``\\n``. Other control words
      (``\\pard``, ``\\f0``, ``\\fs20``, ``\\li0``, ...) are silently consumed
      together with their optional signed digit argument and the single
      trailing space (the RTF "destination space").
    * ``\\'XX`` decodes a single byte in Windows-1252. For the common ASCII
      range this is an identity mapping; higher bytes are decoded
      best-effort and fall back to U+FFFD on decode failure so the text
      remains valid Unicode.
    * ``\\uNNNN`` decodes a signed 16-bit unicode code point (negative values
      are wrapped into the 0x8000..0xFFFF range per the spec). It is followed
      by one fallback character that must be *skipped* (``\\uc1`` default);
      we honour ``\\ucN`` to override that skip count.
    * ``\\\\``, ``\\{``, and ``\\}`` produce the literal characters.
    * ``{\\pict ...}`` groups are extracted wholesale: format and hex payload
      are parsed via :func:`_parse_pict` and stored in the returned
      ``images`` dict under a fresh sequential ID, while the group is
      replaced by ``⟨IMG:{id}⟩`` in ``text``.
    * Groups whose first control word is in
      :data:`_SKIP_DESTINATION_WORDS` (font table, stylesheet, ...) are
      dropped entirely — their contents are format metadata, not user text.

    The function never raises on malformed input: any unknown construct
    degrades to silent skipping so that a partially broken RTF still yields
    as much recoverable text as possible.
    """

    # RTF is 7-bit ASCII with \'XX for high bytes, so we can safely decode as
    # latin-1 (which is a 1:1 byte->code-point mapping) and operate on a str.
    # All "real" text bytes live in \'XX / \uNNNN escapes which we handle
    # explicitly below — this initial decode just lets us index by character.
    data = rtf_bytes.decode("latin-1", errors="replace")
    n = len(data)

    out: list[str] = []
    images: dict[int, tuple[str, bytes]] = {}
    next_image_id = 0

    # Stack of Unicode-character skip counts (``\ucN``). RTF scopes \uc to
    # the current group, inheriting the parent's value on group entry and
    # popping on group exit. ExamTopics emits \uc1 at the top of the file.
    uc_stack: list[int] = [1]
    # Parallel stack of "are we inside a skipped destination group?" flags.
    # When the top is True, every character (including nested groups) is
    # dropped until we pop back above that group.
    skip_stack: list[bool] = [False]

    def _emit(s: str) -> None:
        if not skip_stack[-1]:
            out.append(s)

    i = 0
    while i < n:
        ch = data[i]

        # --- Group open ---------------------------------------------------
        if ch == "{":
            # Peek to see whether this is a {\pict ...} or a skip-destination
            # group (fonttbl/colortbl/...). We only treat \* destinations and
            # the first control word inside the group.
            j = i + 1
            # Skip optional \* ignorable destination marker.
            if j < n and data[j] == "\\" and j + 1 < n and data[j + 1] == "*":
                j += 2
                # Then there must be another backslash for the real control word.
                while j < n and data[j] in " \r\n\t":
                    j += 1
            # Inspect the first control word inside the group, if any.
            first_word = ""
            if j < n and data[j] == "\\" and j + 1 < n and data[j + 1].isalpha():
                k = j + 1
                while k < n and data[k].isalpha():
                    k += 1
                first_word = data[j + 1 : k]

            if first_word == "pict":
                end = _find_group_end(data, i)
                body = data[i + 1 : end]
                suffix, img_bytes = _parse_pict(body)
                if not skip_stack[-1]:
                    images[next_image_id] = (suffix, img_bytes)
                    out.append(f"⟨IMG:{next_image_id}⟩")
                    next_image_id += 1
                i = end + 1
                continue

            if first_word in _SKIP_DESTINATION_WORDS:
                # Drop the whole group unconditionally — format metadata only.
                end = _find_group_end(data, i)
                i = end + 1
                continue

            # Normal group: push scope and advance.
            uc_stack.append(uc_stack[-1])
            skip_stack.append(skip_stack[-1])
            i += 1
            continue

        # --- Group close --------------------------------------------------
        if ch == "}":
            if len(uc_stack) > 1:
                uc_stack.pop()
                skip_stack.pop()
            i += 1
            continue

        # --- Backslash escape / control word ------------------------------
        if ch == "\\":
            if i + 1 >= n:
                i += 1
                continue
            nxt = data[i + 1]

            # Literal punctuation escapes.
            if nxt in ("\\", "{", "}"):
                _emit(nxt)
                i += 2
                continue

            # \'XX hex byte escape.
            if nxt == "'":
                if i + 3 < n:
                    hex_pair = data[i + 2 : i + 4]
                    try:
                        byte_val = int(hex_pair, 16)
                        _emit(
                            bytes([byte_val]).decode("cp1252", errors="replace")
                        )
                    except ValueError:
                        _emit("�")
                    i += 4
                    continue
                i += 2
                continue

            # \uNNNN unicode escape (optionally signed). Followed by \ucN
            # fallback characters that we must skip.
            if nxt == "u" and i + 2 < n and (
                data[i + 2].isdigit() or data[i + 2] == "-"
            ):
                k = i + 2
                if data[k] == "-":
                    k += 1
                while k < n and data[k].isdigit():
                    k += 1
                try:
                    code = int(data[i + 2 : k])
                except ValueError:
                    code = 0
                if code < 0:
                    code += 0x10000
                # Clamp to the valid Unicode range; invalid surrogate halves
                # collapse to U+FFFD so chr() never raises.
                if 0 <= code <= 0x10FFFF and not (0xD800 <= code <= 0xDFFF):
                    _emit(chr(code))
                else:
                    _emit("�")
                # Consume optional terminating space after the digits.
                if k < n and data[k] == " ":
                    k += 1
                # Skip ``uc`` fallback characters. Each ``\'XX`` or ``\uNNNN``
                # or a whole balanced ``{...}`` group counts as ONE char.
                to_skip = uc_stack[-1]
                while to_skip > 0 and k < n:
                    if data[k] == "\\" and k + 1 < n and data[k + 1] == "'":
                        k += 4
                    elif data[k] == "{":
                        k = _find_group_end(data, k) + 1
                    elif data[k] == "\\" and k + 1 < n and data[k + 1].isalpha():
                        m = k + 1
                        while m < n and data[m].isalpha():
                            m += 1
                        if m < n and (data[m] == "-" or data[m].isdigit()):
                            if data[m] == "-":
                                m += 1
                            while m < n and data[m].isdigit():
                                m += 1
                        if m < n and data[m] == " ":
                            m += 1
                        k = m
                    else:
                        k += 1
                    to_skip -= 1
                i = k
                continue

            # Control word: \ + letters + optional signed digits + optional space.
            if nxt.isalpha():
                k = i + 1
                while k < n and data[k].isalpha():
                    k += 1
                word = data[i + 1 : k]
                # Optional numeric parameter (signed).
                param_start = k
                if k < n and data[k] == "-":
                    k += 1
                while k < n and data[k].isdigit():
                    k += 1
                param_str = data[param_start:k]
                # A single trailing space is a control-word terminator and is
                # NOT part of the following text run.
                if k < n and data[k] == " ":
                    k += 1

                if word in ("par", "line"):
                    _emit("\n")
                elif word == "tab":
                    _emit("\t")
                elif word == "uc" and param_str:
                    try:
                        uc_stack[-1] = int(param_str)
                    except ValueError:
                        pass
                # Every other control word is silently consumed.
                i = k
                continue

            # \<non-alpha, non-special> — consume the backslash alone.
            i += 1
            continue

        # --- Raw literal text. RTF strips bare CR/LF from the stream. -----
        if ch in ("\r", "\n"):
            i += 1
            continue
        _emit(ch)
        i += 1

    return "".join(out), images
