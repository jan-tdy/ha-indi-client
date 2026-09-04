"""Config flow for the INDI Client integration."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DEFAULT_PORT, DOMAIN
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
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            try:
                await _test_connection(host, port)
            except (INDIClientError, OSError, asyncio.TimeoutError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating INDI connection")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(self.config_entry, data=user_input)
                return self.async_create_entry(title="", data={})
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current.get(CONF_HOST)): str,
                vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
