"""Constants for the INDI Client integration."""
from __future__ import annotations

DOMAIN = "indi_client"

DEFAULT_PORT = 7624
DEFAULT_CONNECT_TIMEOUT = 5

DATA_CLIENT = "client"
DATA_ADDED_ENTITIES = "added_entities"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_DEVICE = "device"
ATTR_PROPERTY = "property"
ATTR_TYPE = "type"
ATTR_VALUES = "values"
ATTR_ELEMENT = "element"
ATTR_PATH = "path"

SERVICE_REFRESH = "refresh"
SERVICE_SET_PROPERTY = "set_property"
SERVICE_SAVE_FRAME = "save_frame"

CONF_BLOB_DEVICES = "blob_devices"
CONF_BLOB_MODE = "blob_mode"
DEFAULT_BLOB_MODE = "Also"
DEFAULT_BLOB_DEVICES: list[str] = []

EVENT_FRAME = "indi_client_frame"


def signal_new_property(entry_id: str) -> str:
    """Dispatcher signal fired when a device/property is (re)defined."""
    return f"{DOMAIN}_{entry_id}_new_property"


def signal_property_update(entry_id: str, device: str, prop: str) -> str:
    """Dispatcher signal fired when one specific property changes value."""
    return f"{DOMAIN}_{entry_id}_{device}_{prop}_update"


def signal_property_removed(entry_id: str) -> str:
    """Dispatcher signal fired when a device or property is removed."""
    return f"{DOMAIN}_{entry_id}_property_removed"


def signal_connection(entry_id: str) -> str:
    """Dispatcher signal fired when the indiserver TCP link connects/drops."""
    return f"{DOMAIN}_{entry_id}_connection"


def signal_message(entry_id: str) -> str:
    """Dispatcher signal fired when a device (or server) log message arrives."""
    return f"{DOMAIN}_{entry_id}_message"


def blob_mode_for_device(options: dict, device: str) -> str:
    """Return the configured BLOB policy for a device, defaulting to opt-in."""
    if device not in options.get(CONF_BLOB_DEVICES, DEFAULT_BLOB_DEVICES):
        return "Never"
    return options.get(CONF_BLOB_MODE, DEFAULT_BLOB_MODE)
