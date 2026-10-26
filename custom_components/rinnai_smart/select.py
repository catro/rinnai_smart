from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .const import DOMAIN, SELECTS, OPERATION_MAP
from .device import RinnaiDeviceDataUpdateCoordinator
from .entity import RinnaiEntity
from .filters import entity_selected


async def async_setup_entry(hass, config_entry, async_add_entities):
    devices: list[RinnaiDeviceDataUpdateCoordinator] = hass.data[DOMAIN][
        config_entry.entry_id
    ]["devices"]
    entities = []
    for device in devices:
        entities.extend(
            RinnaiSelect(select, device) for select in SELECTS
            if entity_selected(config_entry.options, device.id, select["entity_type"])
        )
    async_add_entities(entities)


class RinnaiSelect(RinnaiEntity, SelectEntity):
    def __init__(self, select_dict, device):
        if select_dict.get("icon"):
            self._attr_icon = select_dict["icon"]
        self._select_dict = select_dict
        super().__init__(select_dict["entity_type"], select_dict["name"], device)

    @property
    def current_option(self):
        if self._select_dict["entity_type"] == "cycle_mode":
            return self._device.cycle_mode
        elif self._select_dict["entity_type"] == "operation_mode":
            return self._device.operation_mode

    @property
    def options(self):
        return self._select_dict["options"]

    async def async_select_option(self, option: str) -> None:
        if self._select_dict["entity_type"] == "cycle_mode":
            await self._device.async_set_cycle_mode(option)
        elif self._select_dict["entity_type"] == "operation_mode":
            await self._device.async_set_operation_mode(option)
