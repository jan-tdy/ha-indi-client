"""The INDI Client integration.

Connects to a running ``indiserver`` (default port 7624) as an
additional, independent client - the same way CCDciel or KStars/EKOS
would - and mirrors the properties of every device it announces as Home
Assistant entities, bidirectionally.
"""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DEVICE,
    ATTR_ELEMENT,
    ATTR_PATH,
    ATTR_PROPERTY,
    ATTR_TYPE,
    ATTR_VALUES,
    DATA_ADDED_ENTITIES,
    DATA_CLIENT,
    DOMAIN,
    EVENT_FRAME,
    SERVICE_REFRESH,
    SERVICE_SAVE_FRAME,
    SERVICE_SET_PROPERTY,
    signal_connection,
    signal_message,
    signal_new_property,
    signal_property_removed,
    signal_property_update,
)
from .indi.client import INDIClient, INDIClientError
from .indi.model import INDIElement, INDIProperty

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
]

SERVICE_REFRESH_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_DEVICE): cv.string,
    }
)

SERVICE_SET_PROPERTY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_DEVICE): cv.string,
        vol.Required(ATTR_PROPERTY): cv.string,
        vol.Required(ATTR_TYPE): vol.In(["Text", "Number", "Switch"]),
        vol.Required(ATTR_VALUES): dict,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up INDI Client from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    client = INDIClient(host, port)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {DATA_CLIENT: client, DATA_ADDED_ENTITIES: set()}

    @callback
    def _on_property_defined(prop: INDIProperty) -> None:
        async_dispatcher_send(hass, signal_new_property(entry.entry_id), prop)
        async_dispatcher_send(hass, signal_property_update(entry.entry_id, prop.device, prop.name), prop)

    @callback
    def _on_property_updated(prop: INDIProperty) -> None:
        async_dispatcher_send(hass, signal_property_update(entry.entry_id, prop.device, prop.name), prop)

    @callback
    def _on_blob_received(prop: INDIProperty, element: INDIElement) -> None:
        hass.bus.async_fire(
            EVENT_FRAME,
            {
                ATTR_CONFIG_ENTRY_ID: entry.entry_id,
                ATTR_DEVICE: prop.device,
                ATTR_PROPERTY: prop.name,
                ATTR_ELEMENT: element.name,
                "format": element.format,
                "size": len(element.value),
            },
        )

    @callback
    def _on_property_deleted(device: str, name: str | None) -> None:
        async_dispatcher_send(hass, signal_property_removed(entry.entry_id), device, name)

    @callback
    def _on_message(device: str, timestamp: str, message: str) -> None:
        async_dispatcher_send(hass, signal_message(entry.entry_id), device, timestamp, message)

    @callback
    def _on_connection_changed(connected: bool) -> None:
        async_dispatcher_send(hass, signal_connection(entry.entry_id), connected)

    client.on_property_defined = _on_property_defined
    client.on_property_updated = _on_property_updated
    client.on_blob_received = _on_blob_received
    client.on_property_deleted = _on_property_deleted
    client.on_message = _on_message
    client.on_connection_changed = _on_connection_changed

    try:
        await client.connect()
    except (INDIClientError, OSError, TimeoutError) as err:
        hass.data[DOMAIN].pop(entry.entry_id)
        raise ConfigEntryNotReady(f"Cannot connect to indiserver at {host}:{port}: {err}") from err

    try:
        await _async_register_services(hass)

        # Platforms must subscribe to the client's dispatcher signals before any
        # data starts flowing, or a property def*Vector arriving in the window
        # between connecting and a platform's async_setup_entry could be missed
        # (client.devices would still have it, but no entity would ever be
        # created for it). So forward to platforms first, then start the client.
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await client.start()
    except Exception:
        await client.disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)
        raise

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and disconnect from indiserver."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data[DATA_CLIENT].disconnect()
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
            hass.services.async_remove(DOMAIN, SERVICE_SET_PROPERTY)
            hass.services.async_remove(DOMAIN, SERVICE_SAVE_FRAME)
    return unload_ok


def _get_client(hass: HomeAssistant, entry_id: str) -> INDIClient:
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if entry_data is None:
        raise ServiceValidationError(f"Unknown INDI Client config entry: {entry_id}")
    return entry_data[DATA_CLIENT]


async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _handle_refresh(call: ServiceCall) -> None:
        client = _get_client(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        try:
            await client.refresh(call.data.get(ATTR_DEVICE))
        except INDIClientError as err:
            raise ServiceValidationError(f"Not connected to indiserver: {err}") from err

    async def _handle_set_property(call: ServiceCall) -> None:
        client = _get_client(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        device = call.data[ATTR_DEVICE]
        prop_name = call.data[ATTR_PROPERTY]
        values = call.data[ATTR_VALUES]
        ptype = call.data[ATTR_TYPE]
        try:
            if ptype == "Number":
                try:
                    numeric_values = {k: float(v) for k, v in values.items()}
                except (TypeError, ValueError) as err:
                    raise ServiceValidationError(f"Invalid numeric value for {prop_name}: {err}") from err
                await client.set_number(device, prop_name, numeric_values)
            elif ptype == "Text":
                await client.set_text(device, prop_name, {k: str(v) for k, v in values.items()})
            else:
                await client.set_switch(device, prop_name, {k: str(v) for k, v in values.items()})
        except INDIClientError as err:
            raise ServiceValidationError(f"Not connected to indiserver: {err}") from err

    async def _handle_save_frame(call: ServiceCall) -> None:
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
        if entry_data is None:
            raise ServiceValidationError(f"Unknown INDI Client config entry: {entry_id}")
        device = call.data[ATTR_DEVICE]
        prop_name = call.data[ATTR_PROPERTY]
        element_name = call.data[ATTR_ELEMENT]
        prop = entry_data[DATA_CLIENT].devices.get(device, {}).get(prop_name)
        element = prop.elements.get(element_name) if prop and prop.ptype == "BLOB" else None
        if element is None or not isinstance(element.value, bytes) or not element.value:
            raise ServiceValidationError(
                f"No frame data is available for {device}.{prop_name}.{element_name}"
            )
        target = Path(call.data[ATTR_PATH]).expanduser()
        if not target.is_absolute():
            raise ServiceValidationError(
                "Frame path must be inside a Home Assistant allowed directory"
            )
        target = target.resolve()
        if not hass.config.is_allowed_path(str(target)):
            raise ServiceValidationError(
                "Frame path must be inside a Home Assistant allowed directory"
            )
        try:
            await hass.async_add_executor_job(target.write_bytes, element.value)
        except OSError as err:
            raise ServiceValidationError(f"Could not save frame to {target}: {err}") from err

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=SERVICE_REFRESH_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROPERTY, _handle_set_property, schema=SERVICE_SET_PROPERTY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_FRAME,
        _handle_save_frame,
        schema=vol.Schema(
            {
                vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
                vol.Required(ATTR_DEVICE): cv.string,
                vol.Required(ATTR_PROPERTY): cv.string,
                vol.Required(ATTR_ELEMENT): cv.string,
                vol.Required(ATTR_PATH): cv.string,
            }
        ),
    )
