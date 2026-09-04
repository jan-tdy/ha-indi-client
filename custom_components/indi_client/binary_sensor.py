"""Read-only boolean states: individual (AnyOfMany) switch elements, and a
diagnostic sensor for the indiserver TCP link itself."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_connection, signal_new_property
from .entity import INDIElementEntity, build_unique_id
from .indi.model import INDIProperty


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]

    server_uid = f"{entry.entry_id}_server_connected"
    if server_uid not in added:
        added.add(server_uid)
        async_add_entities([INDIServerConnectedBinarySensor(entry.entry_id, entry.data, client)])

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        if prop.ptype != "Switch" or prop.perm != "ro" or prop.rule != "AnyOfMany":
            return
        entities = []
        for element_name in prop.elements:
            uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
            if uid in added:
                continue
            added.add(uid)
            entities.append(INDIBinarySensor(client, entry.entry_id, prop.device, prop, element_name))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )


class INDIBinarySensor(INDIElementEntity, BinarySensorEntity):
    """A single read-only (AnyOfMany) INDI switch element."""

    @property
    def is_on(self) -> bool | None:
        element = self._current_element()
        if element is None:
            return None
        return element.value == "On"


class INDIServerConnectedBinarySensor(BinarySensorEntity):
    """Whether the TCP connection to indiserver itself is currently up."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Server connected"

    def __init__(self, entry_id: str, entry_data, client) -> None:
        self._entry_id = entry_id
        self._client = client
        self._attr_unique_id = f"{entry_id}_server_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"INDI Server ({entry_data[CONF_HOST]}:{entry_data[CONF_PORT]})",
            manufacturer="INDI",
            model="indiserver",
        )

    @property
    def is_on(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_connection(self._entry_id), self._handle_connection)
        )

    @callback
    def _handle_connection(self, connected: bool) -> None:
        self.async_write_ha_state()
