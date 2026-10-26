from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN as RINNAI_DOMAIN, SWITCHES
from .device import RinnaiDeviceDataUpdateCoordinator
from .entity import RinnaiEntity
from .filters import entity_selected


async def async_setup_entry(hass, config_entry, async_add_entities):
    devices: list[RinnaiDeviceDataUpdateCoordinator] = hass.data[RINNAI_DOMAIN][
        config_entry.entry_id
    ]["devices"]
    entities = []
    for device in devices:
        entities.extend(
            RinnaiSwitch(switch, device) for switch in SWITCHES
            if entity_selected(config_entry.options, device.id, switch["entity_type"])
        )
    async_add_entities(entities)


class RinnaiSwitch(RinnaiEntity, SwitchEntity):
    def __init__(self, switch_dict, device):
        if switch_dict.get("icon"):
            self._attr_icon = switch_dict["icon"]
        self._switch_dict = switch_dict
        super().__init__(switch_dict["entity_type"], switch_dict["name"], device)

    @property
    def is_on(self):
        match self._switch_dict["entity_type"]:
            case "cycle_reservation":
                return self._device.is_cycle_reservation_on
            case "temporary_cycle_insulation":
                return self._device.is_temporary_cycle_insulation_on

    async def async_turn_on(self, **kwargs):
        match self._switch_dict["entity_type"]:
            case "cycle_reservation":
                await self._device.async_turn_on_cycle_reservation()
            case "temporary_cycle_insulation":
                await self._device.async_turn_on_temporary_cycle_insulation()

    async def async_turn_off(self, **kwargs):
        match self._switch_dict["entity_type"]:
            case "cycle_reservation":
                await self._device.async_turn_off_cycle_reservation()
            case "temporary_cycle_insulation":
                await self._device.async_turn_off_temporary_cycle_insulation()
