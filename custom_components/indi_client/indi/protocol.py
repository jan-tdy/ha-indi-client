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

import re
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


def build_enable_blob(device: str, name: str | None = None, mode: str = "Also") -> bytes:
    """Build an ``<enableBLOB/>`` request.

    BLOB (image) data is opt-in per the INDI spec: a driver only sends it
    to clients that asked for it. ``mode`` is one of ``"Never"``,
    ``"Also"`` (BLOBs plus normal property updates) or ``"Only"``.
    ``name`` scopes the request to a single BLOB vector; omitted, it
    applies to every BLOB property of ``device``.
    """
    attrs = f" device={quoteattr(device)}"
    if name:
        attrs += f" name={quoteattr(name)}"
    return f"<enableBLOB{attrs}>{escape(mode)}</enableBLOB>".encode()


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


_SEXAGESIMAL_PRECISION_RE = re.compile(r"\.(\d+)m\s*$")

# Maps the printf precision digit from an INDI "%<w>.<f>m" format string to
# the number of decimal places shown on the seconds field. Precision 3 has
# no seconds field at all (handled separately, below) and is not listed
# here.
_SEXAGESIMAL_SECOND_DECIMALS = {5: 0, 6: 1, 8: 2, 9: 3}


def format_number(value: float, fmt: str | None) -> str:
    """Format a float back into the wire representation expected by INDI.

    ``fmt`` is the driver-supplied C ``printf``-style format string from
    the property definition. A trailing ``m`` means sexagesimal
    (``dd:mm[:ss[.s]]``) notation, which has no direct Python ``%``
    equivalent and is therefore handled separately.
    """
    if fmt and fmt.strip().endswith("m"):
        return _format_sexagesimal(value, fmt.strip())
    if fmt:
        try:
            return fmt % value
        except (TypeError, ValueError):
            pass
    return repr(value)


def _format_sexagesimal(value: float, fmt: str) -> str:
    """Format ``value`` as ``dd:mm[:ss[.s]]`` per the INDI ``%<w>.<f>m`` spec.

    The fractional part of the printf width/precision spec (the digit
    before the trailing ``m``) encodes the field layout: ``3`` -> ``dd:mm``
    (no seconds), ``5`` -> ``dd:mm:ss`` (integer seconds), ``6``/``8``/``9``
    -> ``dd:mm:ss`` with 1/2/3 decimal places on the seconds. An
    unrecognised or missing precision falls back to the ``.6m`` behaviour
    (1 decimal place) rather than raising.

    The total value is rounded to the target precision *before* being
    decomposed into degrees/minutes/seconds, with proper carry, so a value
    that rounds up (e.g. ``10.999999`` degrees) never prints an invalid
    field such as ``60.0`` seconds or ``60`` minutes.
    """
    match = _SEXAGESIMAL_PRECISION_RE.search(fmt)
    precision = int(match.group(1)) if match else 6

    sign = "-" if value < 0 else ""
    total_seconds = abs(value) * 3600.0

    if precision == 3:
        # dd:mm only - round to the nearest whole minute.
        total_minutes = round(total_seconds / 60.0)
        degrees, minutes = divmod(int(total_minutes), 60)
        return f"{sign}{degrees:d}:{minutes:02d}"

    decimals = _SEXAGESIMAL_SECOND_DECIMALS.get(precision, 1)
    scale = 10**decimals
    # Round the total seconds to the target precision as a single integer
    # (in units of 1/scale seconds) so the degrees/minutes/seconds carry
    # falls out of plain integer arithmetic, with no leftover float error.
    scaled_total = round(total_seconds * scale)
    whole_seconds, frac = divmod(scaled_total, scale)
    minutes, seconds = divmod(int(whole_seconds), 60)
    degrees, minutes = divmod(minutes, 60)

    if decimals == 0:
        return f"{sign}{degrees:d}:{minutes:02d}:{seconds:02d}"
    return f"{sign}{degrees:d}:{minutes:02d}:{seconds:02d}.{frac:0{decimals}d}"
