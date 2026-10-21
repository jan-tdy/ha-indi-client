"""Config flow for the INDI Client integration."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import selector

from .const import (
    CONF_BLOB_DEVICES,
    CONF_BLOB_MODE,
    DEFAULT_BLOB_DEVICES,
    DEFAULT_BLOB_MODE,
    DEFAULT_PORT,
    DOMAIN,
)
from .indi.client import INDIClient, INDIClientError

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
)


async def _test_connection(host: str, port: int) -> None:
    """Try to open (and immediately close) a connection to indiserver."""
    client = INDIClient(host, port, connect_timeout=5)
    await client.connect()
    await client.disconnect()


class INDIClientConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for INDI Client."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            try:
                await _test_connection(host, port)
            except (INDIClientError, OSError, asyncio.TimeoutError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating INDI connection")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=f"INDI ({host}:{port})", data=user_input)
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> INDIClientOptionsFlow:
        return INDIClientOptionsFlow()


class INDIClientOptionsFlow(config_entries.OptionsFlow):
    """Allow changing the host/port of an existing entry."""

    async def async_step_init(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        current = self.config_entry.data
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            unique_id = f"{host}:{port}"
            if any(
                entry.unique_id == unique_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.entry_id != self.config_entry.entry_id
            ):
                errors["base"] = "already_configured"
            else:
                try:
                    await _test_connection(host, port)
                except (INDIClientError, OSError, asyncio.TimeoutError, TimeoutError):
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error validating INDI connection")
                    errors["base"] = "unknown"
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={CONF_HOST: host, CONF_PORT: port},
                        unique_id=unique_id,
                        title=f"INDI ({unique_id})",
                    )
                    options = {
                        **self.config_entry.options,
                        CONF_BLOB_DEVICES: user_input.get(CONF_BLOB_DEVICES, DEFAULT_BLOB_DEVICES),
                        CONF_BLOB_MODE: user_input.get(CONF_BLOB_MODE, DEFAULT_BLOB_MODE),
                    }
                    return self.async_create_entry(title="", data=options)
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        client = entry_data.get("client")
        devices = sorted(client.devices) if client else []
        selected_devices = self.config_entry.options.get(CONF_BLOB_DEVICES, DEFAULT_BLOB_DEVICES)
        devices = sorted(set(devices) | set(selected_devices))
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current.get(CONF_HOST)): str,
                vol.Required(
                    CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(CONF_BLOB_DEVICES, default=selected_devices): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=devices,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_BLOB_MODE,
                    default=self.config_entry.options.get(CONF_BLOB_MODE, DEFAULT_BLOB_MODE),
                ): vol.In(["Never", "Also", "Only"]),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
