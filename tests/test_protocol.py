"""Unit tests for the pure-Python INDI protocol helpers (no Home Assistant required)."""
from __future__ import annotations

import pytest

from indi.protocol import (
    build_enable_blob,
    build_get_properties,
    build_new_vector,
    format_number,
    parse_number,
    split_first_element,
)


def test_split_first_element_self_closing():
    data = b'<getProperties version="1.7"/>'
    elem, rest, resume = split_first_element(data)
    assert elem == data
    assert rest == b""
    assert resume is None


def test_split_first_element_incomplete_returns_none():
    data = b'<defNumberVector device="Foo" name="BAR">'
    elem, rest, resume = split_first_element(data)
    assert elem is None
    assert rest == data
    assert resume is not None


def test_split_first_element_nested_vector_then_message():
    data = (
        b'<defNumberVector device="CCD" name="CCD_TEMPERATURE" state="Idle">'
        b'<defNumber name="CCD_TEMPERATURE_VALUE">-10.0</defNumber>'
        b"</defNumberVector>"
        b'<message device="CCD" message="hello"/>'
    )
    elem, rest, resume = split_first_element(data)
    assert elem.startswith(b"<defNumberVector")
    assert elem.endswith(b"</defNumberVector>")
    assert resume is None

    elem2, rest2, resume2 = split_first_element(rest)
    assert elem2 == b'<message device="CCD" message="hello"/>'
    assert rest2 == b""
    assert resume2 is None


def test_split_first_element_angle_bracket_inside_attribute_value():
    # '>' is legal, unescaped, inside an XML attribute value - a naive
    # scanner that ignores quoting would cut the tag short here.
    data = b'<oneText name="A" label="1 > 2">hi</oneText>'
    elem, rest, resume = split_first_element(data)
    assert elem == data
    assert rest == b""
    assert resume is None


def test_split_first_element_skips_leading_whitespace():
    elem, rest, resume = split_first_element(b"   \n\t")
    assert elem is None
    assert rest == b""
    assert resume is None


def test_split_first_element_resumes_incomplete_scan_without_rescanning():
    """A ``resume`` cursor handed back in must let the next call skip
    straight past content it already confirmed has no further ``<`` -
    the fix for the O(n^2) BLOB-parsing cost in issue #26. Simulated here
    as a large payload arriving in two pieces, the same way a multi-
    megabyte BLOB spans many socket reads.
    """
    opening = b'<setBLOBVector device="CCD Simulator" name="CCD1"><oneBLOB name="CCD1" format=".fits">'
    payload = b"A" * 10_000
    closing = b"</oneBLOB></setBLOBVector>"

    elem, rest, resume = split_first_element(opening + payload[: len(payload) // 2])
    assert elem is None
    assert resume is not None
    # The resumed scan must not need to re-walk the payload bytes already
    # confirmed tag-free: the cached position is at or past where the
    # first half of the payload ends.
    assert resume[1] >= len(opening)

    full = opening + payload + closing
    elem2, rest2, resume2 = split_first_element(full, resume)
    assert elem2 == full
    assert rest2 == b""
    assert resume2 is None


def test_split_first_element_drops_multibyte_garbage_in_one_slice():
    """A run of several non-'<' bytes must resync in a single slice, not
    one byte at a time (issue #26) - otherwise a large garbage burst
    costs O(n) calls each doing an O(n) copy.
    """
    garbage = b"\x00\x01\x02garbage-bytes"
    valid = b'<message device="CCD" message="hello"/>'

    elem, rest, resume = split_first_element(garbage + valid)
    assert elem is None
    assert resume is None
    assert rest == valid

    elem2, rest2, resume2 = split_first_element(rest)
    assert elem2 == valid
    assert rest2 == b""
    assert resume2 is None


def test_split_first_element_all_garbage_clears_buffer():
    elem, rest, resume = split_first_element(b"\x00\x01\x02garbage-with-no-tag")
    assert elem is None
    assert rest == b""
    assert resume is None


def test_parse_number_plain_float():
    assert parse_number("12.5") == 12.5


def test_parse_number_sexagesimal():
    assert abs(parse_number("10:30:00") - 10.5) < 1e-9


def test_parse_number_negative_sexagesimal():
    assert abs(parse_number("-10:30:00") - (-10.5)) < 1e-9


def test_format_number_plain():
    assert format_number(12.5, "%5.2f") == "12.50"


def test_format_number_sexagesimal_roundtrip():
    formatted = format_number(10.5, "%10.6m")
    assert formatted.startswith("10:30:00")
    assert abs(parse_number(formatted) - 10.5) < 1e-3


def test_format_number_no_format_falls_back_to_repr():
    assert format_number(12.5, None) == "12.5"


@pytest.mark.parametrize(
    ("value", "fmt", "expected"),
    [
        # Rounding up to the next whole minute must carry into the degrees,
        # never print a 60-seconds (or 60-minutes) field.
        (10.999999, "%9.6m", "11:00:00.0"),
        (59.99999, "%9.6m", "60:00:00.0"),
        (0.99999999, "%9.6m", "1:00:00.0"),
        # Precision-digit field layouts per the INDI "%<w>.<f>m" spec.
        (10.5, "%9.3m", "10:30"),
        (10.5, "%9.5m", "10:30:00"),
        (10.5, "%9.6m", "10:30:00.0"),
        (10.5, "%9.8m", "10:30:00.00"),
        (10.5, "%9.9m", "10:30:00.000"),
        # .3m rounds to the nearest minute, carrying into degrees too.
        (10.99999, "%9.3m", "11:00"),
        # Unrecognised/missing precision digit falls back to .6m (1 decimal).
        (10.5, "%9.7m", "10:30:00.0"),
        (10.5, "%9m", "10:30:00.0"),
        # Negative values near zero keep their sign through the carry.
        (-0.00001, "%9.6m", "-0:00:00.0"),
        (-10.5, "%9.6m", "-10:30:00.0"),
    ],
)
def test_format_number_sexagesimal_variants(value, fmt, expected):
    assert format_number(value, fmt) == expected


@pytest.mark.parametrize(
    ("value", "fmt"),
    [
        (10.5, "%9.3m"),
        (10.5, "%9.5m"),
        (10.5, "%9.6m"),
        (10.5, "%9.8m"),
        (10.5, "%9.9m"),
        (-10.5, "%9.6m"),
        (-0.00001, "%9.6m"),
        (10.999999, "%9.6m"),
    ],
)
def test_format_number_sexagesimal_roundtrips_for_each_precision(value, fmt):
    formatted = format_number(value, fmt)
    assert abs(parse_number(formatted) - value) < 1e-3


def test_build_new_vector_switch():
    xml = build_new_vector("Switch", "Telescope Simulator", "TELESCOPE_PARK", {"PARK": "On"})
    assert b'device="Telescope Simulator"' in xml
    assert b'name="TELESCOPE_PARK"' in xml
    assert b'<oneSwitch name="PARK">On</oneSwitch>' in xml
    assert xml.endswith(b"</newSwitchVector>")


def test_build_get_properties_default():
    assert build_get_properties() == b'<getProperties version="1.7"/>'


def test_build_enable_blob_device_only():
    xml = build_enable_blob("CCD Simulator")
    assert xml == b'<enableBLOB device="CCD Simulator">Also</enableBLOB>'


def test_build_enable_blob_scoped_to_property():
    xml = build_enable_blob("CCD Simulator", "CCD1", mode="Only")
    assert xml == b'<enableBLOB device="CCD Simulator" name="CCD1">Only</enableBLOB>'


def test_build_get_properties_scoped_to_device():
    xml = build_get_properties(device="CCD Simulator")
    assert xml == b'<getProperties version="1.7" device="CCD Simulator"/>'
