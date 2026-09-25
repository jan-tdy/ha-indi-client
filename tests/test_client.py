"""Unit tests for INDIClient's element parsing (no real socket needed).

These feed synthetic INDI XML straight into the private parsing methods,
the same way the asyncio read loop would after ``split_first_element``
handed it a complete element - no Home Assistant or network required.
"""
from __future__ import annotations

import asyncio
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


def test_reconnects_after_connection_drop():
    """The read loop must survive a dropped TCP connection (server
    restart, network blip, ...) by retrying until it can reconnect -
    see issue #5. Uses a real loopback TCP server rather than mocks, so
    the actual asyncio streams are exercised end to end.
    """

    async def scenario() -> None:
        connection_count = 0
        got_second_connection = asyncio.Event()

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            nonlocal connection_count
            connection_count += 1
            if connection_count == 1:
                # Consume the client's getProperties before closing, so
                # the client's write doesn't race a socket that already
                # went away.
                await reader.read(65536)
                writer.close()
                await writer.wait_closed()
                return
            got_second_connection.set()
            try:
                while await reader.read(65536):
                    pass
            except (ConnectionError, OSError):
                pass

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        client = INDIClient(host, port)
        client._RECONNECT_INITIAL_DELAY = 0.01
        client._RECONNECT_MAX_DELAY = 0.01

        connection_states: list[bool] = []
        client.on_connection_changed = connection_states.append

        try:
            await client.connect()
            await client.start()

            await asyncio.wait_for(got_second_connection.wait(), timeout=5)
            # Give the read task a moment to process the reconnect and
            # flip `connected` back on before asserting.
            await asyncio.sleep(0.05)

            assert connection_states == [True, False, True]
            assert client.connected is True
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
