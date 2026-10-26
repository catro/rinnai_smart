from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode

from .const import DOMAIN as RINNAI_DOMAIN, TEXTS
from .device import RinnaiDeviceDataUpdateCoordinator
from .entity import RinnaiEntity
from .filters import entity_selected


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Rinnai sensors from config entry."""
    devices: list[RinnaiDeviceDataUpdateCoordinator] = hass.data[RINNAI_DOMAIN][
        config_entry.entry_id
    ]["devices"]
    entities = []
    for device in devices:
        entities.extend(
            RinnaiText(text, device) for text in TEXTS
            if entity_selected(config_entry.options, device.id, text["entity_type"])
        )
    async_add_entities(entities)


class RinnaiText(RinnaiEntity, TextEntity):
    _attr_mode = TextMode.TEXT
    def __init__(self, text_dict, device):
        self._text_dict = text_dict
        if text_dict.get("icon"):
            self._attr_icon = text_dict["icon"]
        super().__init__(text_dict["entity_type"], text_dict["name"], device)

    @property
    def pattern(self):
        return self._text_dict["pattern"]
    
    @property
    def native_value(self):
        if self._text_dict["entity_type"] == "cycle_reservation_time":
            return self._device.cycle_reservation_time

    async def async_set_value(self, value: str):
        if self._text_dict["entity_type"] == "cycle_reservation_time":
            await self._device.async_set_cycle_reservation_time(value)