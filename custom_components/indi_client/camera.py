"""Camera entities for INDI BLOB (captured frame) properties.

BLOB data is opt-in per the INDI protocol, so as soon as a camera entity
is created for a BLOB vector, this platform sends ``enableBLOB ... Also``
for it - the driver only starts pushing image data from that point on.

FITS frames (the format virtually every INDI camera driver uses) are
decoded and stretched to an 8-bit preview with ``indi.fits`` + numpy, then
JPEG-encoded with Pillow. Raw JPEG/PNG BLOBs (some drivers offer a
lightweight preview format) are passed through unchanged when possible.
"""
from __future__ import annotations

import io
import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ADDED_ENTITIES, DATA_CLIENT, DOMAIN, signal_new_property
from .entity import INDIElementEntity, build_unique_id
from .indi.fits import FITSError, decode_grayscale, stretch_to_uint8
from .indi.model import INDIProperty

_LOGGER = logging.getLogger(__name__)

_FITS_EXTENSIONS = (".fits", ".fit", ".fts")
_JPEG_EXTENSIONS = (".jpg", ".jpeg")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[DATA_CLIENT]
    added: set[str] = data[DATA_ADDED_ENTITIES]

    @callback
    def _handle_new_property(prop: INDIProperty) -> None:
        if prop.ptype != "BLOB":
            return
        entities = []
        for element_name in prop.elements:
            uid = build_unique_id(entry.entry_id, prop.device, prop.name, element_name)
            if uid in added:
                continue
            added.add(uid)
            entities.append(INDICamera(client, entry.entry_id, prop.device, prop, element_name))
        if entities:
            async_add_entities(entities)
            hass.async_create_task(client.enable_blob(prop.device, prop.name))

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_property(entry.entry_id), _handle_new_property)
    )


class INDICamera(INDIElementEntity, Camera):
    """A single INDI BLOB element (e.g. a CCD's most recently captured frame)."""

    def __init__(self, client, entry_id, device, prop: INDIProperty, element_name: str) -> None:
        INDIElementEntity.__init__(self, client, entry_id, device, prop, element_name)
        Camera.__init__(self)
        self._attr_content_type = "image/jpeg"

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        element = self._current_element()
        if element is None or not element.value:
            return None
        raw: bytes = element.value
        fmt = (element.format or "").lower()

        if fmt in _JPEG_EXTENSIONS:
            return raw
        if fmt in _FITS_EXTENSIONS or (not fmt and raw[:6] == b"SIMPLE"):
            return await self.hass.async_add_executor_job(self._render_fits_preview, raw)

        _LOGGER.debug(
            "Unsupported BLOB format %r for %s, cannot render a preview", fmt, self.entity_id
        )
        return None

    def _render_fits_preview(self, raw: bytes) -> bytes | None:
        try:
            array = decode_grayscale(raw)
        except FITSError as err:
            _LOGGER.debug("Could not decode FITS preview for %s: %s", self.entity_id, err)
            return None

        from PIL import Image  # local import: only needed once a frame actually arrives

        pixels = stretch_to_uint8(array)
        image = Image.fromarray(pixels, mode="L")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
