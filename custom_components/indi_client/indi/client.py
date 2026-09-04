"""Asyncio client for the INDI protocol.

This client connects to ``indiserver`` as *just another client*, the same
way CCDciel, KStars/EKOS or ``indi_getprop`` would. It does not take
exclusive ownership of any device: it reads whatever properties the
server broadcasts (which already reflects changes made by other clients)
and can send its own ``new*Vector`` commands to change them.

BLOB (image) data is opt-in per the INDI spec: a driver only starts
sending it once a client asks via ``enableBLOB`` (see ``enable_blob``
below). Until then, no large binary payloads reach this client.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Callable
from xml.etree import ElementTree as ET

from .model import INDIElement, INDIProperty
from .protocol import (
    build_enable_blob,
    build_get_properties,
    build_new_vector,
    format_number,
    parse_number,
    split_first_element,
)

_LOGGER = logging.getLogger(__name__)

MAX_MESSAGES = 25

_VECTOR_TAGS = {
    "defTextVector": "Text",
    "defNumberVector": "Number",
    "defSwitchVector": "Switch",
    "defLightVector": "Light",
    "defBLOBVector": "BLOB",
    "setTextVector": "Text",
    "setNumberVector": "Number",
    "setSwitchVector": "Switch",
    "setLightVector": "Light",
    "setBLOBVector": "BLOB",
}


class INDIClientError(Exception):
    """Base error for the INDI client."""


class INDIConnectionError(INDIClientError):
    """Raised when the connection to indiserver could not be established."""


class INDIClient:
    """Minimal, event-driven INDI client."""

    def __init__(self, host: str, port: int, *, connect_timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.devices: dict[str, dict[str, INDIProperty]] = {}
        self.messages: dict[str, list[tuple[str, str]]] = {}

        self.on_property_defined: Callable[[INDIProperty], None] | None = None
        self.on_property_updated: Callable[[INDIProperty], None] | None = None
        self.on_property_deleted: Callable[[str, str | None], None] | None = None
        self.on_message: Callable[[str, str, str], None] | None = None
        self.on_connection_changed: Callable[[bool], None] | None = None

        self._connect_timeout = connect_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = b""
        self._read_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Open the TCP connection and start listening for updates."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self._connect_timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise INDIConnectionError(str(err)) from err

        self.connected = True
        self._read_task = asyncio.ensure_future(self._read_loop())
        await self._send(build_get_properties())
        if self.on_connection_changed:
            self.on_connection_changed(True)

    async def disconnect(self) -> None:
        """Close the connection and stop the read loop."""
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = None
        self._writer = None
        if self.connected:
            self.connected = False
            if self.on_connection_changed:
                self.on_connection_changed(False)

    async def refresh(self, device: str | None = None) -> None:
        """Re-request property definitions, optionally for one device."""
        await self._send(build_get_properties(device=device))

    async def set_number(self, device: str, name: str, values: dict[str, float]) -> None:
        """Send a ``newNumberVector`` for one or more elements."""
        prop = self.devices.get(device, {}).get(name)
        payload: dict[str, str] = {}
        for element_name, value in values.items():
            fmt = None
            if prop is not None and element_name in prop.elements:
                fmt = prop.elements[element_name].format
            payload[element_name] = format_number(value, fmt)
        await self._send(build_new_vector("Number", device, name, payload))

    async def set_text(self, device: str, name: str, values: dict[str, str]) -> None:
        """Send a ``newTextVector`` for one or more elements."""
        await self._send(build_new_vector("Text", device, name, values))

    async def set_switch(self, device: str, name: str, values: dict[str, str]) -> None:
        """Send a ``newSwitchVector`` for one or more elements (values: 'On'/'Off')."""
        await self._send(build_new_vector("Switch", device, name, values))

    async def enable_blob(self, device: str, name: str | None = None, mode: str = "Also") -> None:
        """Ask the driver to start sending BLOB (image) data for a property.

        Must be called (once per device/vector) before any image data for
        it will actually arrive - see the module docstring.
        """
        await self._send(build_enable_blob(device, name, mode))

    async def _send(self, data: bytes) -> None:
        if self._writer is None:
            raise INDIConnectionError("Not connected to indiserver")
        self._writer.write(data)
        await self._writer.drain()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                chunk = await self._reader.read(65536)
                if not chunk:
                    break
                self._buffer += chunk
                while True:
                    element_bytes, self._buffer = split_first_element(self._buffer)
                    if element_bytes is None:
                        break
                    self._handle_bytes(element_bytes)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as err:
            _LOGGER.debug("INDI connection to %s:%s lost: %s", self.host, self.port, err)
        finally:
            if self.connected:
                self.connected = False
                if self.on_connection_changed:
                    self.on_connection_changed(False)

    def _handle_bytes(self, raw: bytes) -> None:
        try:
            elem = ET.fromstring(raw)
        except ET.ParseError as err:
            _LOGGER.debug("Ignoring malformed INDI element (%s): %.200r", err, raw)
            return
        self._handle_element(elem)

    def _handle_element(self, elem: ET.Element) -> None:
        tag = elem.tag
        if tag in _VECTOR_TAGS:
            self._handle_vector(elem, is_def=tag.startswith("def"))
        elif tag == "delProperty":
            self._handle_del_property(elem)
        elif tag == "message":
            self._handle_message(elem)
        else:
            _LOGGER.debug("Unhandled INDI element: %s", tag)

    def _handle_vector(self, elem: ET.Element, *, is_def: bool) -> None:
        ptype = _VECTOR_TAGS[elem.tag]
        device = elem.get("device", "")
        name = elem.get("name", "")
        if not device or not name:
            return

        props = self.devices.setdefault(device, {})
        prop = props.get(name)
        if prop is None:
            prop = INDIProperty(
                device=device,
                name=name,
                label=elem.get("label", name),
                group=elem.get("group", ""),
                ptype=ptype,
                state=elem.get("state", "Idle"),
                perm=elem.get("perm", "ro"),
                rule=elem.get("rule"),
                timeout=_safe_float(elem.get("timeout")) or 0.0,
            )
            props[name] = prop
        else:
            if elem.get("state"):
                prop.state = elem.get("state")
            if elem.get("label"):
                prop.label = elem.get("label")

        for child in elem:
            if not child.tag.startswith(("def", "one")):
                continue
            element_name = child.get("name")
            if not element_name:
                continue
            value_text = (child.text or "").strip()
            existing = prop.elements.get(element_name)
            element_obj = existing or INDIElement(name=element_name, label=child.get("label", element_name))

            if ptype == "Number":
                try:
                    element_obj.value = parse_number(value_text)
                except ValueError:
                    _LOGGER.debug("Could not parse INDI number %r for %s.%s", value_text, name, element_name)
                if is_def:
                    element_obj.format = child.get("format")
                    element_obj.min = _safe_float(child.get("min"))
                    element_obj.max = _safe_float(child.get("max"))
                    element_obj.step = _safe_float(child.get("step"))
            elif ptype == "BLOB":
                # defBLOB declares the property but rarely carries data;
                # the actual image arrives later via setBLOBVector, and
                # only once enable_blob() has been called for it.
                if child.get("format"):
                    element_obj.format = child.get("format")
                if value_text:
                    try:
                        element_obj.value = base64.b64decode(value_text)
                    except (binascii.Error, ValueError) as err:
                        _LOGGER.debug(
                            "Could not base64-decode BLOB %s.%s: %s", name, element_name, err
                        )
            else:
                element_obj.value = value_text

            if is_def and child.get("label"):
                element_obj.label = child.get("label")

            prop.elements[element_name] = element_obj

        callback = self.on_property_defined if is_def else self.on_property_updated
        if callback:
            callback(prop)

        message = elem.get("message")
        if message:
            self._store_message(device, elem.get("timestamp", ""), message)

    def _handle_del_property(self, elem: ET.Element) -> None:
        device = elem.get("device", "")
        name = elem.get("name")
        if not device:
            return
        if name:
            props = self.devices.get(device)
            if props is not None:
                props.pop(name, None)
        else:
            self.devices.pop(device, None)
        if self.on_property_deleted:
            self.on_property_deleted(device, name)
        message = elem.get("message")
        if message:
            self._store_message(device, elem.get("timestamp", ""), message)

    def _handle_message(self, elem: ET.Element) -> None:
        device = elem.get("device", "")
        message = elem.get("message", "")
        self._store_message(device, elem.get("timestamp", ""), message)

    def _store_message(self, device: str, timestamp: str, message: str) -> None:
        bucket = self.messages.setdefault(device or "_server", [])
        bucket.append((timestamp, message))
        del bucket[:-MAX_MESSAGES]
        if self.on_message:
            self.on_message(device, timestamp, message)


def _safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None
