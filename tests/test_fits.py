"""Unit tests for the minimal FITS decoder/stretch used for camera previews."""
from __future__ import annotations

import numpy as np
import pytest

from indi.fits import FITSError, decode_grayscale, stretch_to_uint8

_DTYPE_BY_BITPIX = {8: ">u1", 16: ">i2", 32: ">i4", -32: ">f4", -64: ">f8"}


def _card(keyword: str, value) -> bytes:
    if isinstance(value, bool):
        text = "T" if value else "F"
    elif isinstance(value, int):
        text = str(value)
    else:
        text = repr(value)
    line = f"{keyword:<8}= {text:>20}"
    return line.ljust(80).encode("ascii")[:80]


def _make_fits(width: int, height: int, bitpix: int, pixel_value: float, *, extra_cards=()) -> bytes:
    cards = [
        _card("SIMPLE", True),
        _card("BITPIX", bitpix),
        _card("NAXIS", 2),
        _card("NAXIS1", width),
        _card("NAXIS2", height),
        *[_card(k, v) for k, v in extra_cards],
    ]
    header = b"".join(cards) + b"END".ljust(80)
    header += b" " * ((-len(header)) % 2880)

    dtype = _DTYPE_BY_BITPIX[bitpix]
    data = np.full((height, width), pixel_value, dtype=dtype)
    data_bytes = data.tobytes()
    data_bytes += b"\x00" * ((-len(data_bytes)) % 2880)
    return header + data_bytes


def test_decode_grayscale_uniform_frame():
    array = decode_grayscale(_make_fits(4, 3, 16, 1000))
    assert array.shape == (3, 4)
    assert np.all(array == 1000)


def test_decode_grayscale_applies_bscale_bzero():
    raw = _make_fits(2, 2, 16, 10, extra_cards=[("BZERO", 32768), ("BSCALE", 2)])
    array = decode_grayscale(raw)
    assert np.all(array == 10 * 2 + 32768)


def test_decode_grayscale_rejects_naxis_three():
    # A 3-D cube (e.g. multiple planes/frames) must be rejected rather than
    # silently decoded as a single 2-D plane.
    cards = [
        _card("SIMPLE", True),
        _card("BITPIX", 16),
        _card("NAXIS", 3),
        _card("NAXIS1", 4),
        _card("NAXIS2", 4),
        _card("NAXIS3", 3),
    ]
    header = b"".join(cards) + b"END".ljust(80)
    header += b" " * ((-len(header)) % 2880)
    with pytest.raises(FITSError):
        decode_grayscale(header)


def test_decode_grayscale_rejects_non_fits():
    with pytest.raises(FITSError):
        decode_grayscale(b"not a fits file" + b" " * 100)


def test_decode_grayscale_rejects_unsupported_bitpix():
    raw = _make_fits(2, 2, 16, 0)
    raw = raw.replace(_card("BITPIX", 16), _card("BITPIX", 7))
    with pytest.raises(FITSError):
        decode_grayscale(raw)


def test_decode_grayscale_rejects_truncated_data():
    raw = _make_fits(4, 4, 16, 5)
    truncated = raw[:-2880]  # drop the whole (padded) data block
    with pytest.raises(FITSError):
        decode_grayscale(truncated)


def test_stretch_to_uint8_full_range():
    array = np.array([[0.0, 500.0], [1000.0, 1500.0]])
    result = stretch_to_uint8(array, low_pct=0, high_pct=100)
    assert result.dtype == np.uint8
    assert result.min() == 0
    assert result.max() == 255


def test_stretch_to_uint8_constant_frame_does_not_divide_by_zero():
    array = np.full((3, 3), 42.0)
    result = stretch_to_uint8(array)
    assert result.shape == (3, 3)
    assert result.dtype == np.uint8
