"""Diagnostics support for INDI Client (Settings -> Devices -> Download diagnostics)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_CLIENT, DOMAIN

# Elements that can carry the installation's physical location or the
# observer's name. INDI mounts/domes/GPS devices publish site coordinates
# as the standard GEOGRAPHIC_COORD property (LAT/LONG/ELEV elements), and
# some drivers put an observer or site name into free-text elements such
# as FITS_HEADER's OBSERVER/SITE. Diagnostics are meant to be pasted into
# public issues, so these must never show up unredacted.
TO_REDACT = {
    "host",
    "LAT",
    "LONG",
    "ELEV",
    "OBSERVER",
    "SITE",
}


def _diagnostic_value(ptype: str, element: Any) -> Any:
    """Return a JSON-safe stand-in for a BLOB element's raw image bytes.

    A BLOB element's ``value`` is the decoded image payload (potentially
    a multi-megabyte camera frame) as raw ``bytes`` - not JSON
    serializable, and not something that belongs in a downloadable
    diagnostics dump in the first place. Every other property type's
    value is already a JSON-friendly scalar.
    """
    value = element.value
    if ptype == "BLOB" and isinstance(value, bytes):
        return f"<{len(value)} bytes, format={element.format}>"
    return value


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
                "elements": {
                    name: _diagnostic_value(prop.ptype, element) for name, element in prop.elements.items()
                },
            }
            for prop_name, prop in props.items()
        }

    return async_redact_data(
        {
            "connected": client.connected,
            "host": client.host,
            "port": client.port,
            "devices": devices,
            "recent_messages": client.messages,
        },
        TO_REDACT,
    )
