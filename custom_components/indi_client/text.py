"""Settable text INDI elements."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_new_property
from .entity import INDIElementEntity, build_unique_id
from .indi.model import INDIProperty


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        if prop.ptype != "Text" or prop.perm not in ("rw", "wo"):
            return
        entities = []
        for element_name in prop.elements:
            uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
            if uid in added:
                continue
            added.add(uid)
            entities.append(INDIText(client, entry.entry_id, prop.device, prop, element_name))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )


class INDIText(INDIElementEntity, TextEntity):
    """A single writable INDI text element."""

    @property
    def native_value(self):
        element = self._current_element()
        return element.value if element else None

    async def async_set_value(self, value: str) -> None:
        prop = self._current_property()
        if prop is None:
            return
        await self._client.set_text(self._device, prop.name, {self._element_name: value})
