"""Shared entity base classes for the INDI Client integration.

INDI is push-based (the server broadcasts changes to every connected
client, including changes made by other clients such as CCDciel or
KStars/EKOS), so entities here never poll - they subscribe to a
dispatcher signal for their specific property and re-render on push,
mirroring the pattern used by Home Assistant's MQTT integration.
"""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, signal_connection, signal_property_update
from .indi.client import INDIClient, INDIClientError
from .indi.model import INDIElement, INDIProperty


async def async_send(coro) -> None:
    """Await an ``INDIClient`` write call, as entities make it.

    A write made while a reconnect is in progress (``INDIClient._writer``
    is ``None``) raises ``INDIConnectionError`` straight out of
    ``INDIClient._send`` - surface that as a clean, actionable
    ``HomeAssistantError`` instead of an unhandled traceback out of a
    Home Assistant entity service call.
    """
    try:
        await coro
    except INDIClientError as err:
        raise HomeAssistantError(f"Not connected to indiserver: {err}") from err


def build_unique_id(entry_id: str, device: str, prop: str, element: str | None = None) -> str:
    """Build a stable unique_id for an INDI vector or one of its elements."""
    parts = [entry_id, device, prop]
    if element:
        parts.append(element)
    return "_".join(parts).lower().replace(" ", "_").replace(":", "_")


def device_info(entry_id: str, device: str) -> DeviceInfo:
    """Home Assistant device representing one INDI device (e.g. a mount, a CCD)."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{device}")},
        name=device,
        manufacturer="INDI",
        via_device=(DOMAIN, entry_id),
    )


class INDIBaseEntity(Entity):
    """Base entity tracking one INDI property (vector) as a whole."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client: INDIClient, entry_id: str, device: str, prop_name: str) -> None:
        self._client = client
        self._entry_id = entry_id
        self._device = device
        self._prop_name = prop_name
        self._attr_device_info = device_info(entry_id, device)

    @property
    def available(self) -> bool:
        return (
            self._client.connected
            and self._device in self._client.devices
            and self._device_connected()
        )

    def _device_connected(self) -> bool:
        """Whether the device's own CONNECTION property reports it as On.

        A device stays in ``self._client.devices`` (and keeps its last
        known property values) after its driver disconnects - indiserver
        does not delete the properties, it just flips CONNECTION.CONNECT
        to Off. Without this check every other entity for that device
        would keep reporting "available" with stale values. Drivers that
        don't expose CONNECTION at all (or haven't sent it yet) are
        treated as connected so they aren't penalized for something they
        never had.
        """
        prop = self._client.devices.get(self._device, {}).get("CONNECTION")
        if prop is None:
            return True
        element = prop.elements.get("CONNECT")
        if element is None:
            return True
        return element.value == "On"

    def _current_property(self) -> INDIProperty | None:
        return self._client.devices.get(self._device, {}).get(self._prop_name)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_property_update(self._entry_id, self._device, self._prop_name),
                self._handle_property_update,
            )
        )
        if self._prop_name != "CONNECTION":
            # `available` also depends on the device's CONNECTION
            # property (see _device_connected) - without this, an entity
            # would keep reporting available/stale until its own
            # property happened to update next, instead of the moment
            # the device actually disconnects.
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    signal_property_update(self._entry_id, self._device, "CONNECTION"),
                    self._handle_property_update,
                )
            )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_connection(self._entry_id), self._handle_connection_change
            )
        )

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_property_update(self, prop: INDIProperty) -> None:
        self.async_write_ha_state()


class INDIElementEntity(INDIBaseEntity):
    """Base entity tracking a single element inside an INDI vector."""

    def __init__(
        self,
        client: INDIClient,
        entry_id: str,
        device: str,
        prop: INDIProperty,
        element_name: str,
    ) -> None:
        super().__init__(client, entry_id, device, prop.name)
        self._element_name = element_name
        element = prop.elements[element_name]
        self._attr_unique_id = build_unique_id(entry_id, device, prop.name, element_name)
        self._attr_name = element.label or element_name

    def _current_element(self) -> INDIElement | None:
        prop = self._current_property()
        if prop is None:
            return None
        return prop.elements.get(self._element_name)
