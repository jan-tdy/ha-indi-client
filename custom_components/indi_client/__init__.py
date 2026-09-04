"""The INDI Client integration.

Connects to a running ``indiserver`` (default port 7624) as an
additional, independent client - the same way CCDciel or KStars/EKOS
would - and mirrors the properties of every device it announces as Home
Assistant entities, bidirectionally.
"""
from __future__ import annotations

import logging

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
    ATTR_PROPERTY,
    ATTR_TYPE,
    ATTR_VALUES,
    DATA_ADDED_ENTITIES,
    DATA_CLIENT,
    DOMAIN,
    SERVICE_REFRESH,
    SERVICE_SET_PROPERTY,
    signal_connection,
    signal_message,
    signal_new_property,
    signal_property_removed,
    signal_property_update,
)
from .indi.client import INDIClient, INDIClientError
from .indi.model import INDIProperty

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.TEXT,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
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
    client.on_property_deleted = _on_property_deleted
    client.on_message = _on_message
    client.on_connection_changed = _on_connection_changed

    try:
        await client.connect()
    except (INDIClientError, OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot connect to indiserver at {host}:{port}: {err}") from err

    await _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
        await client.refresh(call.data.get(ATTR_DEVICE))

    async def _handle_set_property(call: ServiceCall) -> None:
        client = _get_client(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        device = call.data[ATTR_DEVICE]
        prop_name = call.data[ATTR_PROPERTY]
        values = call.data[ATTR_VALUES]
        ptype = call.data[ATTR_TYPE]
        if ptype == "Number":
            await client.set_number(device, prop_name, {k: float(v) for k, v in values.items()})
        elif ptype == "Text":
            await client.set_text(device, prop_name, {k: str(v) for k, v in values.items()})
        else:
            await client.set_switch(device, prop_name, {k: str(v) for k, v in values.items()})

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=SERVICE_REFRESH_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROPERTY, _handle_set_property, schema=SERVICE_SET_PROPERTY_SCHEMA
    )
