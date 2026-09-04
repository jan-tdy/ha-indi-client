"""Unit tests for the pure-Python INDI protocol helpers (no Home Assistant required)."""
from __future__ import annotations

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
    elem, rest = split_first_element(data)
    assert elem == data
    assert rest == b""


def test_split_first_element_incomplete_returns_none():
    data = b'<defNumberVector device="Foo" name="BAR">'
    elem, rest = split_first_element(data)
    assert elem is None
    assert rest == data


def test_split_first_element_nested_vector_then_message():
    data = (
        b'<defNumberVector device="CCD" name="CCD_TEMPERATURE" state="Idle">'
        b'<defNumber name="CCD_TEMPERATURE_VALUE">-10.0</defNumber>'
        b"</defNumberVector>"
        b'<message device="CCD" message="hello"/>'
    )
    elem, rest = split_first_element(data)
    assert elem.startswith(b"<defNumberVector")
    assert elem.endswith(b"</defNumberVector>")

    elem2, rest2 = split_first_element(rest)
    assert elem2 == b'<message device="CCD" message="hello"/>'
    assert rest2 == b""


def test_split_first_element_angle_bracket_inside_attribute_value():
    # '>' is legal, unescaped, inside an XML attribute value - a naive
    # scanner that ignores quoting would cut the tag short here.
    data = b'<oneText name="A" label="1 > 2">hi</oneText>'
    elem, rest = split_first_element(data)
    assert elem == data
    assert rest == b""


def test_split_first_element_skips_leading_whitespace():
    elem, rest = split_first_element(b"   \n\t")
    assert elem is None
    assert rest == b""


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
