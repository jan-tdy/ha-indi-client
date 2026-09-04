"""Wire-format helpers for the INDI protocol.

INDI (https://indilib.org) exchanges small XML elements over a plain TCP
socket. The stream is *not* a single well-formed XML document (there is
no common root element), so a standard one-shot XML parser cannot be
pointed at it directly. ``split_first_element`` implements a small,
quote-aware tag scanner that extracts exactly one complete top-level
element (e.g. one ``defNumberVector`` including its children) from a
byte buffer, so the caller can hand that single, well-formed fragment to
``xml.etree.ElementTree`` for real parsing.

Everything in this module is pure/stdlib-only so it can be unit tested
without Home Assistant installed.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

_NEW_VECTOR_CHILD_TAG = {
    "Text": "oneText",
    "Number": "oneNumber",
    "Switch": "oneSwitch",
}

_WHITESPACE = b" \t\r\n"


def split_first_element(buf: bytes) -> tuple[bytes | None, bytes]:
    """Split off the first complete top-level XML element from ``buf``.

    Returns ``(element_bytes, remainder)``. ``element_bytes`` is ``None``
    when ``buf`` does not (yet) contain a complete element, in which case
    ``remainder`` is what should be kept buffered (leading whitespace is
    trimmed so the buffer does not grow unbounded while idle).
    """
    n = len(buf)
    i = 0
    while i < n and buf[i : i + 1] in (b" ", b"\t", b"\r", b"\n"):
        i += 1
    if i >= n:
        return None, buf[i:]
    if buf[i : i + 1] != b"<":
        # Unexpected byte outside of any tag; drop it and try to resync.
        return None, buf[i + 1 :]

    start = i
    depth = 0
    pos = i
    while True:
        pos = buf.find(b"<", pos)
        if pos == -1:
            return None, buf
        tag_start = pos
        j = pos + 1
        in_quote = b""
        while j < n:
            c = buf[j : j + 1]
            if in_quote:
                if c == in_quote:
                    in_quote = b""
            elif c in (b'"', b"'"):
                in_quote = c
            elif c == b">":
                break
            j += 1
        if j >= n:
            return None, buf  # tag not fully received yet
        tag_end = j
        content = buf[tag_start + 1 : tag_end]
        pos = tag_end + 1

        if content.startswith(b"?") or content.startswith(b"!"):
            continue  # processing instruction / comment / doctype
        if content.endswith(b"/"):
            if depth == 0:
                return buf[start:pos], buf[pos:]
            continue
        if content.startswith(b"/"):
            depth -= 1
            if depth == 0:
                return buf[start:pos], buf[pos:]
            continue
        depth += 1


def build_get_properties(device: str | None = None, name: str | None = None) -> bytes:
    """Build a ``<getProperties/>`` request."""
    attrs = ' version="1.7"'
    if device:
        attrs += f" device={quoteattr(device)}"
    if name:
        attrs += f" name={quoteattr(name)}"
    return f"<getProperties{attrs}/>".encode()


def build_new_vector(ptype: str, device: str, name: str, values: dict[str, str]) -> bytes:
    """Build a ``new<Type>Vector`` command setting one or more elements."""
    child_tag = _NEW_VECTOR_CHILD_TAG[ptype]
    tag = f"new{ptype}Vector"
    parts = [f"<{tag} device={quoteattr(device)} name={quoteattr(name)}>"]
    for element_name, value in values.items():
        parts.append(
            f"<{child_tag} name={quoteattr(element_name)}>{escape(str(value))}</{child_tag}>"
        )
    parts.append(f"</{tag}>")
    return "".join(parts).encode()


def parse_number(value: str) -> float:
    """Parse an INDI number, including sexagesimal ``dd:mm:ss.s`` values."""
    value = value.strip()
    if ":" in value:
        negative = value.startswith("-")
        core = value[1:] if value[:1] in ("-", "+") else value
        pieces = [float(p) for p in core.split(":")]
        result = 0.0
        for index, piece in enumerate(pieces):
            result += piece / (60**index)
        return -result if negative else result
    return float(value)


def format_number(value: float, fmt: str | None) -> str:
    """Format a float back into the wire representation expected by INDI.

    ``fmt`` is the driver-supplied C ``printf``-style format string from
    the property definition. A trailing ``m`` means sexagesimal
    (``dd:mm:ss.s``) notation, which has no direct Python ``%`` equivalent
    and is therefore handled separately.
    """
    if fmt and fmt.strip().endswith("m"):
        sign = "-" if value < 0 else ""
        value = abs(value)
        degrees = int(value)
        minutes_full = (value - degrees) * 60
        minutes = int(minutes_full)
        seconds = (minutes_full - minutes) * 60
        return f"{sign}{degrees:d}:{minutes:02d}:{seconds:04.1f}"
    if fmt:
        try:
            return fmt % value
        except (TypeError, ValueError):
            pass
    return repr(value)
