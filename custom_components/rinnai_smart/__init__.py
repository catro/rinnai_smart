"""The rinnai_smart integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.util import ssl as ssl_util

from .const import CLIENT, DOMAIN
from .device import RinnaiDeviceDataUpdateCoordinator
from .filters import device_needed, entity_selected, split_unique_id
from .rinnai_client import AuthenticationError, RinnaiClient, RinnaiError

PLATFORMS = ["water_heater", "text", "select", "switch", "binary_sensor"]


def async_update_entity_selection(hass, entry):
    """Retain registry IDs and user settings while changing the import selection."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue
        identity = split_unique_id(entity.unique_id)
        if identity is None:
            continue
        selected = entity_selected(entry.options, *identity)
        if not selected and entity.disabled_by is None:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
        elif selected and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity.entity_id, disabled_by=None)
        # Unloading writes an unavailable placeholder before the new filter is
        # applied. Registry disabling alone does not remove that restored state.
        if not selected:
            state = hass.states.get(entity.entity_id)
            if state is not None and state.attributes.get("restored"):
                hass.states.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up only the selected devices and entities."""
    client = RinnaiClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        device_filter=lambda device_id: device_needed(entry.options, device_id),
    )
    try:
        devices = await client.get_devices()
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed("Rinnai authentication failed") from err
    except (aiohttp.ClientError, TimeoutError, RinnaiError) as err:
        raise ConfigEntryNotReady("Unable to retrieve Rinnai devices") from err

    coordinators = [
        RinnaiDeviceDataUpdateCoordinator(hass, client, value["device"], entry.options)
        for value in devices.values()
    ]
    runtime = {CLIENT: client, "devices": coordinators, "task": None}
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    try:
        if coordinators:
            runtime["task"] = hass.loop.create_task(client.run(ssl_util.client_context()))
        for device in coordinators:
            await device._async_setup()
        async_update_entity_selection(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await _async_stop_client(runtime)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_stop_client(runtime):
    """Close the old MQTT connection before a reload starts a new one."""
    if task := runtime["task"]:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
    """Apply selection changes with a clean connection lifecycle."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and stop their MQTT task."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    await _async_stop_client(runtime)
    return True
