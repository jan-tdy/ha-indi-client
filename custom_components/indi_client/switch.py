"""Multi-choice INDI switch vectors (rule AnyOfMany) plus a dedicated
Connected toggle for the standard CONNECTION property."""
from __future__ import annotations

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_new_property
from .entity import INDIBaseEntity, INDIElementEntity, build_unique_id
from .indi.model import INDIProperty

CONNECTION_VECTOR = "CONNECTION"
CONNECT_ELEMENT = "CONNECT"
DISCONNECT_ELEMENT = "DISCONNECT"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        if prop.ptype != "Switch" or prop.perm not in ("rw", "wo"):
            return
        entities = []
        if prop.name == CONNECTION_VECTOR and CONNECT_ELEMENT in prop.elements:
            uid = build_unique_id(entry.entry_id, prop.device, prop.name)
            if uid not in added:
                added.add(uid)
                entities.append(INDIConnectionSwitch(client, entry.entry_id, prop.device, prop))
        elif prop.rule == "AnyOfMany":
            for element_name in prop.elements:
                uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
                if uid in added:
                    continue
                added.add(uid)
                entities.append(INDISwitch(client, entry.entry_id, prop.device, prop, element_name))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )


class INDISwitch(INDIElementEntity, SwitchEntity):
    """A single independent (AnyOfMany) INDI switch element."""

    @property
    def is_on(self) -> bool | None:
        element = self._current_element()
        if element is None:
            return None
        return element.value == "On"

    async def async_turn_on(self, **kwargs) -> None:
        prop = self._current_property()
        if prop is None:
            return
        await self._client.set_switch(self._device, prop.name, {self._element_name: "On"})

    async def async_turn_off(self, **kwargs) -> None:
        prop = self._current_property()
        if prop is None:
            return
        await self._client.set_switch(self._device, prop.name, {self._element_name: "Off"})


class INDIConnectionSwitch(INDIBaseEntity, SwitchEntity):
    """Connect/disconnect the INDI driver for a device (its CONNECTION property)."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:connection"

    def __init__(self, client, entry_id, device, prop: INDIProperty) -> None:
        super().__init__(client, entry_id, device, prop.name)
        self._attr_unique_id = build_unique_id(entry_id, device, prop.name)
        self._attr_name = "Connected"

    @property
    def is_on(self) -> bool | None:
        prop = self._current_property()
        if prop is None:
            return None
        element = prop.elements.get(CONNECT_ELEMENT)
        return element.value == "On" if element else None

    async def async_turn_on(self, **kwargs) -> None:
        await self._client.set_switch(self._device, CONNECTION_VECTOR, {CONNECT_ELEMENT: "On"})

    async def async_turn_off(self, **kwargs) -> None:
        await self._client.set_switch(self._device, CONNECTION_VECTOR, {DISCONNECT_ELEMENT: "On"})
