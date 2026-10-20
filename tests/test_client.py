"""Unit tests for INDIClient's element parsing (no real socket needed).

These feed synthetic INDI XML straight into the private parsing methods,
the same way the asyncio read loop would after ``split_first_element``
handed it a complete element - no Home Assistant or network required.
"""
from __future__ import annotations

import asyncio
import base64

from indi.client import INDIClient, _is_blob_vector_bytes, _parse_blob_element
from indi.model import INDIElement, INDIProperty
from indi.protocol import split_first_element


def _feed(client: INDIClient, xml: bytes) -> None:
    buf = xml
    resume = None
    while True:
        elem, buf, resume = split_first_element(buf, resume)
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


def test_is_blob_vector_bytes_identifies_def_and_set():
    assert _is_blob_vector_bytes(b'<defBLOBVector device="CCD Simulator" name="CCD1">')
    assert _is_blob_vector_bytes(b'<setBLOBVector device="CCD Simulator" name="CCD1">')
    assert not _is_blob_vector_bytes(b'<setNumberVector device="CCD Simulator" name="CCD1">')


def test_parse_blob_element_decodes_off_loop_helper():
    # _parse_blob_element is the pure, side-effect-free function offloaded
    # to a thread executor for large frames (issue #9) - verify it alone
    # decodes correctly before trusting the async wrapper around it.
    raw = b"hello indi blob" * 10
    encoded = base64.b64encode(raw)
    elem, decoded = _parse_blob_element(_blob_vector("setBLOBVector", payload=encoded))

    assert elem.tag == "setBLOBVector"
    assert decoded == {"CCD1": raw}


def test_parse_blob_element_reports_invalid_payload_as_none():
    elem, decoded = _parse_blob_element(
        _blob_vector("setBLOBVector", payload=b"not-valid-base64!!!")
    )

    assert elem.tag == "setBLOBVector"
    assert decoded == {"CCD1": None}


def test_handle_blob_element_async_matches_sync_decoding():
    """The async, off-loop path used for real BLOB vectors (see
    INDIClient._read_until_disconnected) must produce the same result as
    the synchronous path exercised by test_blob_decodes_line_wrapped_base64.
    """

    async def scenario() -> None:
        raw = b"async indi blob" * 1000  # large enough to matter on the wire
        encoded = base64.b64encode(raw)
        wrapped = b"\n".join(encoded[i : i + 72] for i in range(0, len(encoded), 72))

        client = INDIClient("localhost", 7624)
        updates = []
        client.on_property_updated = lambda prop: updates.append(prop)

        await client._handle_blob_element(  # noqa: SLF001 - intentional white-box test
            _blob_vector("setBLOBVector", payload=wrapped)
        )

        assert len(updates) == 1
        element = client.devices["CCD Simulator"]["CCD1"].elements["CCD1"]
        assert element.value == raw
        assert element.format == ".fits"

    asyncio.run(scenario())


def test_handle_blob_element_async_invalid_payload_keeps_last_good_frame():
    async def scenario() -> None:
        client = INDIClient("localhost", 7624)
        updates = []
        client.on_property_updated = lambda prop: updates.append(prop)

        good = base64.b64encode(b"good frame")
        await client._handle_blob_element(_blob_vector("setBLOBVector", payload=good))  # noqa: SLF001
        assert len(updates) == 1

        await client._handle_blob_element(  # noqa: SLF001
            _blob_vector("setBLOBVector", payload=b"not-valid-base64!!!")
        )

        assert len(updates) == 1  # no second callback for the bad update
        element = client.devices["CCD Simulator"]["CCD1"].elements["CCD1"]
        assert element.value == b"good frame"

    asyncio.run(scenario())


def test_blob_vector_over_wire_is_decoded_via_read_loop():
    """End-to-end: a BLOB vector arriving over a real socket is routed
    through the async, off-loop parsing path by the read loop itself,
    not just when called directly in isolation."""

    async def scenario() -> None:
        raw = b"wire indi blob" * 2000
        encoded = base64.b64encode(raw)
        frame = _blob_vector("setBLOBVector", payload=encoded)

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(65536)  # consume the client's getProperties
            writer.write(frame)
            await writer.drain()
            try:
                while await reader.read(65536):
                    pass
            except (ConnectionError, OSError):
                pass

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        client = INDIClient(host, port)
        updates = []
        client.on_property_updated = lambda prop: updates.append(prop)

        try:
            await client.connect()
            await client.start()
            for _ in range(500):
                if updates:
                    break
                await asyncio.sleep(0.01)
            assert updates
            element = client.devices["CCD Simulator"]["CCD1"].elements["CCD1"]
            assert element.value == raw
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_large_blob_over_many_small_reads_completes_quickly():
    """A multi-megabyte BLOB spread across many small socket reads must
    not take quadratically longer to parse than a single big one - see
    issue #26. Before the fix, each new chunk re-scanned the *entire*
    buffered payload so far for the next '<', turning this into an O(n^2)
    operation; this test sends the frame in small enough pieces, and
    bounds the wall-clock time loosely enough to catch a regression back
    to that behaviour without being flaky on slow CI runners.
    """

    async def scenario() -> None:
        raw = b"x" * (2 * 1024 * 1024)  # 2 MiB raw -> ~2.7 MiB base64
        encoded = base64.b64encode(raw)
        frame = _blob_vector("setBLOBVector", payload=encoded)

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(65536)  # consume the client's getProperties
            # Trickle the frame out in small pieces so the client's read
            # loop has to re-enter its buffer-parsing path many times
            # while the element is still incomplete.
            chunk_size = 4096
            for pos in range(0, len(frame), chunk_size):
                writer.write(frame[pos : pos + chunk_size])
                await writer.drain()
            try:
                while await reader.read(65536):
                    pass
            except (ConnectionError, OSError):
                pass

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        client = INDIClient(host, port)
        updates = []
        client.on_property_updated = lambda prop: updates.append(prop)

        try:
            await client.connect()
            await client.start()
            start = asyncio.get_event_loop().time()
            for _ in range(1000):
                if updates:
                    break
                await asyncio.sleep(0.01)
            elapsed = asyncio.get_event_loop().time() - start
            assert updates
            element = client.devices["CCD Simulator"]["CCD1"].elements["CCD1"]
            assert element.value == raw
            # Generous bound: the O(n^2) pre-fix behaviour for a frame
            # this size took well over a second locally; a healthy
            # O(n) parse finishes in a few hundredths of a second.
            assert elapsed < 5.0, f"BLOB parsing took {elapsed:.2f}s - looks quadratic again"
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_oversized_unterminated_buffer_forces_reconnect():
    """A stream that never completes an element (corrupt peer, or a BLOB
    far larger than any real driver would ever send) must not grow
    ``self._buffer`` without bound - see issue #26. Once buffered data
    exceeds the safety cap, the client must force a reconnect instead of
    accumulating memory forever.
    """

    async def scenario() -> None:
        connection_count = 0
        got_second_connection = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal connection_count
            connection_count += 1
            await reader.read(65536)  # consume the client's getProperties
            if connection_count == 1:
                # An element that never closes - just an ever-growing
                # opening tag's worth of attribute-like filler, well past
                # the (lowered, for this test) buffer cap.
                writer.write(b'<setBLOBVector device="CCD" name="CCD1">')
                writer.write(b"<oneBLOB name=\"CCD1\" format=\".fits\">" + b"A" * 20_000)
                await writer.drain()
                try:
                    while await reader.read(65536):
                        pass
                except (ConnectionError, OSError):
                    pass
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
        client._MAX_BUFFER_SIZE = 10_000  # lowered so the test stays small/fast

        try:
            await client.connect()
            await client.start()

            await asyncio.wait_for(got_second_connection.wait(), timeout=5)
            await asyncio.sleep(0.05)

            assert client.connected is True
            # The oversized, never-completed element must not have
            # survived into the new connection's state.
            assert client.devices == {}
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_read_loop_resyncs_past_multibyte_garbage_without_extra_socket_read():
    """A burst of several leading non-'<' bytes in a single socket read
    must not stall the read loop waiting for more data that may never
    arrive - see issue #10. The server here sends garbage immediately
    followed by one valid element in the *same* write and then nothing
    else, so the test only passes if the client resyncs using bytes
    already in its buffer instead of blocking on another socket read.
    """

    async def scenario() -> None:
        garbage = b"\x00\x01\x02garbage-bytes"  # several leading non-'<' bytes
        valid = b'<message device="CCD" message="hello"/>'

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(65536)  # consume the client's getProperties
            writer.write(garbage + valid)
            await writer.drain()
            try:
                while await reader.read(65536):
                    pass
            except (ConnectionError, OSError):
                pass

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        client = INDIClient(host, port)
        messages: list[str] = []
        client.on_message = lambda device, timestamp, message: messages.append(message)

        try:
            await client.connect()
            await client.start()
            for _ in range(200):
                if messages:
                    break
                await asyncio.sleep(0.01)
            assert messages == ["hello"]
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


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


def test_read_loop_survives_unexpected_callback_exception():
    """A bug in a consumer callback (e.g. a Home Assistant entity update)
    must not kill the read-loop task for good - see issue #17. Before the
    fix, only (ConnectionError, OSError) was caught around element
    handling, so any other exception propagated out of the task and left
    it dead with nothing left to drive reconnects or future reads.
    """

    async def scenario() -> None:
        connection_count = 0
        got_second_connection = asyncio.Event()
        message = b'<message device="CCD" message="hello"/>'

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal connection_count
            connection_count += 1
            await reader.read(65536)  # consume the client's getProperties
            if connection_count == 1:
                writer.write(message)
                await writer.drain()
            else:
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

        def boom(device: str, timestamp: str, msg: str) -> None:
            raise RuntimeError("boom - simulated bug in a consumer callback")

        client.on_message = boom

        try:
            await client.connect()
            await client.start()

            await asyncio.wait_for(got_second_connection.wait(), timeout=5)
            await asyncio.sleep(0.05)

            # The read-loop task must still be alive and reconnected, not
            # dead from the unhandled exception.
            assert client._read_task is not None  # noqa: SLF001
            assert not client._read_task.done()  # noqa: SLF001
            assert client.connected is True
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_enable_blob_is_replayed_after_reconnect():
    """BLOB enablement is per-connection (issue #23): a reconnect must
    resend enableBLOB for every vector a caller (e.g. a camera entity)
    previously enabled, since the redefined defBLOBVector is deduplicated
    by the caller and never triggers a fresh enable_blob() call.
    """

    async def scenario() -> None:
        connection_count = 0
        second_connection_requests: list[bytes] = []
        got_second_enable_blob = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal connection_count
            connection_count += 1
            if connection_count == 1:
                # getProperties and enableBLOB may arrive coalesced into a
                # single read - one read is enough to drain them before
                # dropping the connection.
                await reader.read(65536)
                writer.close()
                await writer.wait_closed()
                return
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                second_connection_requests.append(chunk)
                if b"<enableBLOB" in chunk:
                    got_second_enable_blob.set()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        client = INDIClient(host, port)
        client._RECONNECT_INITIAL_DELAY = 0.01
        client._RECONNECT_MAX_DELAY = 0.01

        try:
            await client.connect()
            await client.start()
            await client.enable_blob("CCD Simulator", "CCD1")

            await asyncio.wait_for(got_second_enable_blob.wait(), timeout=5)
            assert b'device="CCD Simulator"' in b"".join(second_connection_requests)
            assert b'name="CCD1"' in b"".join(second_connection_requests)
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_devices_cleared_on_reconnect():
    """A reconnect must drop stale device/property state (issue #25):
    otherwise a device that never reappears (indiserver restarted with a
    different driver set, or just never comes back) keeps reporting
    "available" with stale values forever - a ghost entity. The client
    also notifies ``on_property_deleted`` for each device it drops, the
    same callback a real ``delProperty`` would trigger.
    """

    async def scenario() -> None:
        connection_count = 0
        got_second_connection = asyncio.Event()
        device_vector = (
            b'<defNumberVector device="CCD Simulator" name="CCD_TEMPERATURE" '
            b'label="Temperature" group="Main" state="Ok" perm="ro" timeout="0">'
            b'<defNumber name="CCD_TEMPERATURE_VALUE" label="Temperature" '
            b'format="%4.1f" min="-50" max="50" step="0">10.0</defNumber>'
            b"</defNumberVector>"
        )

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal connection_count
            connection_count += 1
            await reader.read(65536)  # consume the client's getProperties
            if connection_count == 1:
                writer.write(device_vector)
                await writer.drain()
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
        # Deliberately slow, so the assertion right after the defNumberVector
        # arrives can't race the reconnect that follows the server dropping
        # the first connection.
        client._RECONNECT_INITIAL_DELAY = 0.3
        client._RECONNECT_MAX_DELAY = 0.3

        deleted: list[tuple[str, str | None]] = []
        client.on_property_deleted = lambda device, name: deleted.append((device, name))
        got_property = asyncio.Event()
        client.on_property_defined = lambda prop: got_property.set()

        try:
            await client.connect()
            await client.start()

            await asyncio.wait_for(got_property.wait(), timeout=5)
            assert "CCD Simulator" in client.devices

            await asyncio.wait_for(got_second_connection.wait(), timeout=5)
            await asyncio.sleep(0.05)

            assert client.devices == {}
            assert deleted == [("CCD Simulator", None)]
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_watchdog_forces_reconnect_on_half_open_socket():
    """A half-open socket (peer vanished without a FIN/RST) must not hang
    the client forever waiting on a read that will never complete - see
    issue #25. The watchdog pings after a shorter idle window and, once
    truly dead, force-closes the socket so the normal reconnect path
    takes over.
    """

    async def scenario() -> None:
        connection_count = 0
        got_second_connection = asyncio.Event()
        got_ping = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal connection_count
            connection_count += 1
            await reader.read(65536)  # consume the client's getProperties
            if connection_count == 1:
                # Simulate a half-open socket: never respond and never
                # close - just stop reading/writing. The watchdog's ping
                # arrives here and is deliberately never answered, so the
                # watchdog must eventually force the socket closed itself.
                try:
                    while True:
                        chunk = await reader.read(65536)
                        if not chunk:
                            return
                        got_ping.set()
                except (ConnectionError, OSError):
                    pass
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
        client._WATCHDOG_CHECK_INTERVAL = 0.02
        client._WATCHDOG_PING_AFTER_IDLE = 0.05
        client._WATCHDOG_DEAD_AFTER_IDLE = 0.15

        try:
            await client.connect()
            await client.start()

            await asyncio.wait_for(got_ping.wait(), timeout=5)
            await asyncio.wait_for(got_second_connection.wait(), timeout=5)
            await asyncio.sleep(0.05)

            assert client.connected is True
        finally:
            await client.disconnect()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def _make_switch_prop(rule: str, elements: dict[str, str]) -> INDIProperty:
    prop = INDIProperty(
        device="Dev",
        name="OUTPUTS",
        label="Outputs",
        group="",
        ptype="Switch",
        state="Ok",
        perm="rw",
        rule=rule,
        timeout=0.0,
    )
    for name, value in elements.items():
        prop.elements[name] = INDIElement(name=name, label=name, value=value)
    return prop


def test_set_switch_sends_full_vector_for_any_of_many():
    """issue #29: a driver may require every element of the vector on
    each newSwitchVector, not just the one being changed. For an
    AnyOfMany vector, every current element is carried forward unchanged
    except the one(s) explicitly passed in."""

    async def scenario() -> None:
        sent: list[bytes] = []

        client = INDIClient("localhost", 7624)
        client.devices["Dev"] = {
            "OUTPUTS": _make_switch_prop(
                "AnyOfMany",
                {"Unconfigured": "Off", "COOLING_1": "Off", "BLOWING_1": "On"},
            )
        }
        client._writer = object()  # truthy sentinel so _send doesn't raise
        client._send = lambda data: sent.append(data) or asyncio.sleep(0)

        await client.set_switch("Dev", "OUTPUTS", {"COOLING_1": "On"})

        assert len(sent) == 1
        payload = sent[0]
        assert b'name="COOLING_1">On<' in payload
        assert b'name="BLOWING_1">On<' in payload  # untouched element preserved
        assert b'name="Unconfigured">Off<' in payload

    asyncio.run(scenario())


def test_set_switch_enforces_exclusivity_for_one_of_many():
    """For a OneOfMany/AtMostOne vector, turning one element 'On' must
    turn every other element 'Off' in the same request rather than
    carrying forward a stale 'On' from whichever element was previously
    selected (e.g. CONNECT vs. DISCONNECT on the CONNECTION vector)."""

    async def scenario() -> None:
        sent: list[bytes] = []

        client = INDIClient("localhost", 7624)
        client.devices["Dev"] = {
            "CONNECTION": _make_switch_prop(
                "OneOfMany", {"CONNECT": "Off", "DISCONNECT": "On"}
            )
        }
        client._writer = object()
        client._send = lambda data: sent.append(data) or asyncio.sleep(0)

        await client.set_switch("Dev", "CONNECTION", {"CONNECT": "On"})

        assert len(sent) == 1
        payload = sent[0]
        assert b'name="CONNECT">On<' in payload
        assert b'name="DISCONNECT">Off<' in payload  # not left stale "On"

    asyncio.run(scenario())
