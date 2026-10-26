"""Log in, then choose devices and entities to import."""

from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, TITLE
from .filters import (
    DEVICE_IDS,
    DEVICE_MODE,
    ENTITY_IDS,
    ENTITY_MODE,
    ENTITY_TYPES,
    EXCLUDE,
    INCLUDE,
    device_selected,
)
from .rinnai_client import AuthenticationError, HTTPClient, RinnaiError

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def selection_schema(mode_key, ids_key, choices, options):
    """Show named choices, retaining saved IDs temporarily absent from the cloud."""
    choices = dict(choices)
    for identifier in options.get(ids_key, []):
        choices.setdefault(identifier, identifier)
    return vol.Schema(
        {
            vol.Required(mode_key, default=options[mode_key]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[INCLUDE, EXCLUDE], translation_key="filter_mode"
                )
            ),
            vol.Required(ids_key, default=list(options.get(ids_key, []))): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": key, "label": label} for key, label in choices.items()],
                    multiple=True,
                )
            ),
        }
    )


def device_label(device):
    """Disambiguate identically named heaters without exposing authorization data."""
    return f"{device.get('name') or device['id']} ({device.get('mac', device['id'])})"


class SelectionFlow:
    """Shared selection steps for initial setup and subsequent options."""

    async def async_step_devices(self, user_input=None):
        choices = {device["id"]: device_label(device) for device in self._devices}
        errors = {}
        if user_input is not None:
            if set(user_input[DEVICE_IDS]) <= (
                set(choices) | set(self._selection.get(DEVICE_IDS, []))
            ):
                self._selection.update(user_input)
                return await self.async_step_entities()
            errors["base"] = "invalid_selection"
        return self.async_show_form(
            step_id="devices",
            data_schema=selection_schema(DEVICE_MODE, DEVICE_IDS, choices, self._selection),
            errors=errors,
        )

    async def async_step_entities(self, user_input=None):
        choices = {
            f"{device['id']}_{key}": f"{device_label(device)} · {name}"
            for device in self._devices
            if device_selected(self._selection, device["id"])
            for key, name in ENTITY_TYPES.items()
        }
        errors = {}
        if user_input is not None:
            if set(user_input[ENTITY_IDS]) <= (
                set(choices) | set(self._selection.get(ENTITY_IDS, []))
            ):
                self._selection.update(user_input)
                return self._finish_selection()
            errors["base"] = "invalid_selection"
        return self.async_show_form(
            step_id="entities",
            data_schema=selection_schema(ENTITY_MODE, ENTITY_IDS, choices, self._selection),
            errors=errors,
        )


class ConfigFlow(SelectionFlow, ConfigFlow, domain=DOMAIN):
    """Validate credentials before showing any selection steps."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return RinnaiOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = HTTPClient(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            try:
                self._devices = await client.get_device_list()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, TimeoutError, RinnaiError):
                errors["base"] = "cannot_connect"
            else:
                if not self._devices:
                    return self.async_abort(reason="no_devices")
                self._credentials = dict(user_input)
                self._selection = {
                    DEVICE_MODE: INCLUDE,
                    DEVICE_IDS: [device["id"] for device in self._devices],
                    ENTITY_MODE: EXCLUDE,
                    ENTITY_IDS: [],
                }
                return await self.async_step_devices()
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    def _finish_selection(self):
        return self.async_create_entry(title=TITLE, data=self._credentials, options=self._selection)


class RinnaiOptionsFlow(SelectionFlow, OptionsFlow):
    """Change selection without re-entering credentials or recreating the entry."""

    async def async_step_init(self, user_input=None):
        client = HTTPClient(
            self.config_entry.data[CONF_USERNAME], self.config_entry.data[CONF_PASSWORD]
        )
        try:
            self._devices = await client.get_device_list()
        except AuthenticationError:
            return self.async_abort(reason="invalid_auth")
        except (aiohttp.ClientError, TimeoutError, RinnaiError):
            return self.async_abort(reason="cannot_connect")
        self._selection = {
            DEVICE_MODE: EXCLUDE,
            DEVICE_IDS: [],
            ENTITY_MODE: EXCLUDE,
            ENTITY_IDS: [],
            **self.config_entry.options,
        }
        return await self.async_step_devices()

    def _finish_selection(self):
        return self.async_create_entry(title="", data=self._selection)
