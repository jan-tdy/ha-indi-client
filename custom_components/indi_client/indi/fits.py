"""Minimal FITS reading + display stretch for INDI camera BLOB previews.

Virtually every INDI camera driver sends captured frames as a standard
FITS image (INDI's ``CCD1``-style BLOB property). This is not a
general-purpose FITS library - just enough to turn a simple 2-D
grayscale image HDU into an array suitable for a quick preview:

- No multi-extension / table HDU support.
- No debayering: a one-shot-color (OSC) camera's raw Bayer frame is
  decoded as plain grayscale, so previews from those cameras show the
  Bayer mosaic pattern rather than a demosaiced color image.

Pure numpy, no Home Assistant dependency, so it stays unit testable on
its own.
"""
from __future__ import annotations

import numpy as np

_BLOCK_SIZE = 2880
_CARD_SIZE = 80

_BITPIX_DTYPE = {
    8: ">u1",
    16: ">i2",
    32: ">i4",
    -32: ">f4",
    -64: ">f8",
}


class FITSError(ValueError):
    """Raised when data cannot be parsed as a simple 2-D FITS image."""


def _parse_value(raw: bytes) -> object:
    text = raw.decode("ascii", "replace").strip()
    if text.startswith("'"):
        return text.strip("'").strip()
    if text in ("T", "F"):
        return text == "T"
    try:
        if any(c in text for c in ".eE") and text not in ("T", "F"):
            return float(text)
        return int(text)
    except ValueError:
        return text


def _parse_header(data: bytes) -> tuple[dict[str, object], int]:
    header: dict[str, object] = {}
    pos = 0
    while True:
        if pos + _BLOCK_SIZE > len(data):
            raise FITSError("truncated FITS header")
        block = data[pos : pos + _BLOCK_SIZE]
        pos += _BLOCK_SIZE
        ended = False
        for i in range(0, _BLOCK_SIZE, _CARD_SIZE):
            card = block[i : i + _CARD_SIZE]
            keyword = card[:8].decode("ascii", "replace").strip()
            if keyword == "END":
                ended = True
                break
            if keyword and card[8:10] == b"= ":
                raw_value = card[10:].split(b"/", 1)[0].strip()
                header[keyword] = _parse_value(raw_value)
        if ended:
            break
    return header, pos


def decode_grayscale(data: bytes) -> np.ndarray:
    """Decode a simple 2-D FITS image HDU into a ``float64`` array."""
    if data[:6] != b"SIMPLE":
        raise FITSError("not a FITS file (missing SIMPLE header)")

    header, data_start = _parse_header(data)
    naxis = int(header.get("NAXIS", 0))
    if naxis < 2:
        raise FITSError(f"unsupported NAXIS={naxis} (need a 2-D image)")

    bitpix = int(header.get("BITPIX", 0))
    dtype = _BITPIX_DTYPE.get(bitpix)
    if dtype is None:
        raise FITSError(f"unsupported BITPIX={bitpix}")

    try:
        width = int(header["NAXIS1"])
        height = int(header["NAXIS2"])
    except KeyError as err:
        raise FITSError(f"missing {err} in FITS header") from err

    itemsize = abs(bitpix) // 8
    count = width * height
    raw = data[data_start : data_start + count * itemsize]
    if len(raw) < count * itemsize:
        raise FITSError("truncated FITS pixel data")

    array = np.frombuffer(raw, dtype=dtype, count=count).reshape(height, width).astype(np.float64)

    bzero = float(header.get("BZERO", 0.0))
    bscale = float(header.get("BSCALE", 1.0))
    if bzero or bscale != 1.0:
        array = array * bscale + bzero
    return array


def stretch_to_uint8(array: np.ndarray, *, low_pct: float = 1.0, high_pct: float = 99.5) -> np.ndarray:
    """Percentile-clip linear stretch into an 8-bit array for a quick preview."""
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return np.zeros(array.shape, dtype=np.uint8)
    lo, hi = np.percentile(finite, [low_pct, high_pct])
    if hi <= lo:
        hi = lo + 1.0
    clipped = np.clip((array - lo) / (hi - lo), 0.0, 1.0)
    return (clipped * 255).astype(np.uint8)
