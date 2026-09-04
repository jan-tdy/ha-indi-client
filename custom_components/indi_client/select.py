"""Single-choice INDI switch vectors (rule OneOfMany / AtMostOne) as `select`.

This is what makes e.g. parking a mount possible: a TELESCOPE_PARK switch
vector with a PARK/UNPARK choice becomes a dropdown; selecting "PARK"
sends `<newSwitchVector ...><oneSwitch name="PARK">On</oneSwitch></...>`.

The CONNECTION vector is deliberately excluded here - it gets a nicer
dedicated `switch.*_connected` entity instead, see switch.py.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_new_property
from .entity import INDIBaseEntity, build_unique_id
from .indi.model import INDIProperty

NONE_OPTION = "(none)"
CONNECTION_VECTOR = "CONNECTION"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        if prop.ptype != "Switch" or prop.perm not in ("rw", "wo"):
            return
        if prop.rule not in ("OneOfMany", "AtMostOne"):
            return
        if prop.name == CONNECTION_VECTOR:
            return
        uid = build_unique_id(entry.entry_id, prop.device, prop.name)
        if uid in added:
            return
        added.add(uid)
        async_add_entities([INDISelect(client, entry.entry_id, prop.device, prop)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )


class INDISelect(INDIBaseEntity, SelectEntity):
    """A single-choice INDI switch vector."""

    def __init__(self, client, entry_id, device, prop: INDIProperty) -> None:
        super().__init__(client, entry_id, device, prop.name)
        self._attr_unique_id = build_unique_id(entry_id, device, prop.name)
        self._attr_name = prop.label or prop.name
        self._allow_none = prop.rule == "AtMostOne"

    @property
    def options(self) -> list[str]:
        prop = self._current_property()
        if prop is None:
            return []
        options = [element.label or element.name for element in prop.elements.values()]
        if self._allow_none:
            options.append(NONE_OPTION)
        return options

    @property
    def current_option(self):
        prop = self._current_property()
        if prop is None:
            return None
        for element in prop.elements.values():
            if element.value == "On":
                return element.label or element.name
        return NONE_OPTION if self._allow_none else None

    async def async_select_option(self, option: str) -> None:
        if option == NONE_OPTION:
            return
        prop = self._current_property()
        if prop is None:
            return
        for element_name, element in prop.elements.items():
            if (element.label or element.name) == option:
                await self._client.set_switch(self._device, prop.name, {element_name: "On"})
                return
