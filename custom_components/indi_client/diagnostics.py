"""Diagnostics support for INDI Client (Settings -> Devices -> Download diagnostics)."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_CLIENT, DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return the current known state of every device/property, plus recent logs."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]

    devices: dict[str, Any] = {}
    for device, props in client.devices.items():
        devices[device] = {
            prop_name: {
                "type": prop.ptype,
                "state": prop.state,
                "perm": prop.perm,
                "rule": prop.rule,
                "elements": {name: element.value for name, element in prop.elements.items()},
            }
            for prop_name, prop in props.items()
        }

    return {
        "connected": client.connected,
        "host": client.host,
        "port": client.port,
        "devices": devices,
        "recent_messages": client.messages,
    }
