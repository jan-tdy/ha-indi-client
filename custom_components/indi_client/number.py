"""Settable numeric INDI elements (e.g. CCD_TEMPERATURE, focuser position)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_new_property
from .entity import INDIElementEntity, build_unique_id
from .indi.model import INDIProperty

_TEMPERATURE_HINTS = ("TEMPERATURE",)
_DEFAULT_STEP = 0.01
_FALLBACK_RANGE = 1_000_000


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        if prop.ptype != "Number" or prop.perm not in ("rw", "wo"):
            return
        entities = []
        for element_name in prop.elements:
            uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
            if uid in added:
                continue
            added.add(uid)
            entities.append(INDINumber(client, entry.entry_id, prop.device, prop, element_name))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )


class INDINumber(INDIElementEntity, NumberEntity):
    """A single writable INDI number element."""

    _attr_mode = NumberMode.BOX

    def __init__(self, client, entry_id, device, prop, element_name) -> None:
        super().__init__(client, entry_id, device, prop, element_name)
        element = prop.elements[element_name]
        if element.min is not None and element.max is not None and element.max > element.min:
            self._attr_native_min_value = element.min
            self._attr_native_max_value = element.max
        else:
            # INDI convention: min == max == 0 means "unbounded".
            self._attr_native_min_value = -_FALLBACK_RANGE
            self._attr_native_max_value = _FALLBACK_RANGE
        self._attr_native_step = element.step or _DEFAULT_STEP
        name_upper = element_name.upper()
        if any(hint in name_upper for hint in _TEMPERATURE_HINTS):
            self._attr_native_unit_of_measurement = "°C"

    @property
    def native_value(self):
        element = self._current_element()
        return element.value if element else None

    async def async_set_native_value(self, value: float) -> None:
        prop = self._current_property()
        if prop is None:
            return
        await self._client.set_number(self._device, prop.name, {self._element_name: value})
