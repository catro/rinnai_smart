from __future__ import annotations

from homeassistant.components.water_heater import (
    PRECISION_WHOLE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    ATTR_TEMPERATURE,
)
from homeassistant.const import UnitOfTemperature

from .const import DOMAIN, OPERATION_MAP, WATER_HEATER, MIN_TEMP, MAX_TEMP, LOGGER
from .device import RinnaiDeviceDataUpdateCoordinator
from .entity import RinnaiEntity
from .filters import entity_selected


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Rinnai Water heater from config entry."""
    devices: list[RinnaiDeviceDataUpdateCoordinator] = hass.data[DOMAIN][
        config_entry.entry_id
    ]["devices"]
    entities = []
    for device in devices:
        if entity_selected(config_entry.options, device.id, "water_heater"):
            entities.append(RinnaiWaterHeater(device))
    async_add_entities(entities)


class RinnaiWaterHeater(RinnaiEntity, WaterHeaterEntity):
    """Water Heater entity for a Rinnai Device"""

    _attr_operation_list = list(dict.fromkeys(OPERATION_MAP.values()))
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_supported_features = (
        WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.TARGET_TEMPERATURE
    )

    def __init__(self, device: RinnaiDeviceDataUpdateCoordinator) -> None:
        """Initialize the water heater."""
        super().__init__("water_heater", WATER_HEATER, device)

    @property
    def current_operation(self):
        """Return current operation"""
        return self._device.operation_mode

    @property
    def icon(self):
        """Return the icon to use for the valve."""
        return "mdi:water-boiler"

    @property
    def target_temperature(self):
        """Return the temperature we try to reach"""
        return self._device.target_temperature

    @property
    def extra_state_attributes(self) -> dict:
        """Return the optional device state attributes."""
        return {"target_temp_step": 1}

    async def async_set_temperature(self, **kwargs):
        target_temp = kwargs.get(ATTR_TEMPERATURE)
        if target_temp is not None:
            await self._device.async_set_temperature(int(target_temp))
            LOGGER.debug("Updated temperature to: %s", target_temp)
        else:
            LOGGER.error("A target temperature must be provided")

    async def async_set_operation_mode(self, operation_mode):
        await self._device.async_set_operation_mode(operation_mode)

    async def async_turn_on(self):
        await self._device.async_turn_on()

    async def async_turn_off(self):
        await self._device.async_turn_off()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
