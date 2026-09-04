"""Read-only sensors: numbers, text, lights, switch-vector state and logs."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_message, signal_new_property
from .entity import INDIBaseEntity, INDIElementEntity, build_unique_id, device_info
from .indi.model import INDIProperty

MAX_STORED_MESSAGES = 25
_TEMPERATURE_HINTS = ("TEMPERATURE",)
_HUMIDITY_HINTS = ("HUMIDITY",)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]
    message_sensors: dict[str, INDIMessageSensor] = {}

    def _ensure_message_sensor(device: str) -> INDIMessageSensor | None:
        if not device or device in message_sensors:
            return None
        uid = build_unique_id(entry.entry_id, device, "messages")
        if uid in added:
            return None
        added.add(uid)
        sensor = INDIMessageSensor(entry.entry_id, device)
        message_sensors[device] = sensor
        return sensor

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        entities: list[SensorEntity] = []

        if prop.ptype == "Number" and prop.perm == "ro":
            for element_name in prop.elements:
                uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
                if uid in added:
                    continue
                added.add(uid)
                entities.append(INDINumberSensor(client, entry.entry_id, prop.device, prop, element_name))
        elif prop.ptype == "Text" and prop.perm == "ro":
            for element_name in prop.elements:
                uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
                if uid in added:
                    continue
                added.add(uid)
                entities.append(INDITextSensor(client, entry.entry_id, prop.device, prop, element_name))
        elif prop.ptype == "Light":
            for element_name in prop.elements:
                uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
                if uid in added:
                    continue
                added.add(uid)
                entities.append(INDILightSensor(client, entry.entry_id, prop.device, prop, element_name))
        elif prop.ptype == "Switch" and prop.perm == "ro" and prop.rule in ("OneOfMany", "AtMostOne"):
            uid = build_unique_id(entry.entry_id, prop.device, prop.name)
            if uid not in added:
                added.add(uid)
                entities.append(INDISwitchStateSensor(client, entry.entry_id, prop.device, prop))

        message_sensor = _ensure_message_sensor(prop.device)
        if message_sensor is not None:
            entities.append(message_sensor)

        if entities:
            async_add_entities(entities)

    @callback
    def _handle_message(device: str, timestamp: str, message: str) -> None:
        sensor = _ensure_message_sensor(device)
        if sensor is not None:
            async_add_entities([sensor])

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )
    entry.async_on_unload(async_dispatcher_connect(hass, signal_message(entry.entry_id), _handle_message))


class INDINumberSensor(INDIElementEntity, SensorEntity):
    """A read-only INDI number element, e.g. a temperature or humidity readout."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, client, entry_id, device, prop, element_name) -> None:
        super().__init__(client, entry_id, device, prop, element_name)
        name_upper = element_name.upper()
        if any(hint in name_upper for hint in _TEMPERATURE_HINTS):
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = "°C"
        elif any(hint in name_upper for hint in _HUMIDITY_HINTS):
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self):
        element = self._current_element()
        return element.value if element else None


class INDITextSensor(INDIElementEntity, SensorEntity):
    """A read-only INDI text element."""

    @property
    def native_value(self):
        element = self._current_element()
        return element.value if element else None


class INDILightSensor(INDIElementEntity, SensorEntity):
    """An INDI light element (status indicator: Idle/Ok/Busy/Alert)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:traffic-light"

    @property
    def native_value(self):
        element = self._current_element()
        return element.value if element else None


class INDISwitchStateSensor(INDIBaseEntity, SensorEntity):
    """The currently selected option of a read-only single-choice switch vector."""

    def __init__(self, client, entry_id, device, prop: INDIProperty) -> None:
        super().__init__(client, entry_id, device, prop.name)
        self._attr_unique_id = build_unique_id(entry_id, device, prop.name)
        self._attr_name = prop.label or prop.name

    @property
    def native_value(self):
        prop = self._current_property()
        if prop is None:
            return None
        for element in prop.elements.values():
            if element.value == "On":
                return element.label or element.name
        return None


class INDIMessageSensor(SensorEntity):
    """Latest log message of one INDI device, with recent history as an attribute."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:text-box-outline"

    def __init__(self, entry_id: str, device: str) -> None:
        self._entry_id = entry_id
        self._device = device
        self._attr_unique_id = build_unique_id(entry_id, device, "messages")
        self._attr_name = "Last message"
        self._attr_device_info = device_info(entry_id, device)
        self._history: list[tuple[str, str]] = []
        self._latest: str | None = None

    @property
    def native_value(self):
        return self._latest

    @property
    def extra_state_attributes(self):
        return {"history": [f"{ts}: {msg}" for ts, msg in self._history]}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_message(self._entry_id), self._handle_message)
        )

    @callback
    def _handle_message(self, device: str, timestamp: str, message: str) -> None:
        if device != self._device:
            return
        self._latest = message
        self._history.append((timestamp, message))
        del self._history[:-MAX_STORED_MESSAGES]
        self.async_write_ha_state()
