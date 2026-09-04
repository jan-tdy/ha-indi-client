"""Unit tests for INDIClient's element parsing (no real socket needed).

These feed synthetic INDI XML straight into the private parsing methods,
the same way the asyncio read loop would after ``split_first_element``
handed it a complete element - no Home Assistant or network required.
"""
from __future__ import annotations

import base64

from indi.client import INDIClient
from indi.protocol import split_first_element


def _feed(client: INDIClient, xml: bytes) -> None:
    buf = xml
    while True:
        elem, buf = split_first_element(buf)
        if elem is None:
            break
        client._handle_bytes(elem)  # noqa: SLF001 - intentional white-box test


def _blob_vector(tag: str, *, payload: bytes) -> bytes:
    return (
        f'<{tag} device="CCD Simulator" name="CCD1" state="Ok" timeout="60">'.encode()
        + b'<oneBLOB name="CCD1" format=".fits">'
        + payload
        + b"</oneBLOB></"
        + tag.encode()
        + b">"
    )


def test_blob_decodes_line_wrapped_base64():
    # indiserver commonly wraps BLOB base64 with embedded newlines - that
    # is normal formatting, not corruption, and must still decode cleanly.
    raw = b"hello indi blob" * 10
    encoded = base64.b64encode(raw)
    wrapped = b"\n".join(encoded[i : i + 16] for i in range(0, len(encoded), 16))

    client = INDIClient("localhost", 7624)
    updates = []
    client.on_property_updated = lambda prop: updates.append(prop)

    _feed(client, _blob_vector("setBLOBVector", payload=wrapped))

    assert len(updates) == 1
    element = client.devices["CCD Simulator"]["CCD1"].elements["CCD1"]
    assert element.value == raw
    assert element.format == ".fits"


def test_blob_malformed_data_does_not_update_or_fire_callback():
    client = INDIClient("localhost", 7624)
    updates = []
    client.on_property_updated = lambda prop: updates.append(prop)

    # Seed a known-good frame first.
    good = base64.b64encode(b"good frame")
    _feed(client, _blob_vector("setBLOBVector", payload=good))
    assert len(updates) == 1

    # Then send something that isn't valid base64 even after stripping
    # whitespace (a literal '!' is outside the base64 alphabet).
    _feed(client, _blob_vector("setBLOBVector", payload=b"not-valid-base64!!!"))

    assert len(updates) == 1  # no second callback for the bad update
    element = client.devices["CCD Simulator"]["CCD1"].elements["CCD1"]
    assert element.value == b"good frame"  # previous good frame preserved
