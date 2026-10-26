from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import DOMAIN as RINNAI_DOMAIN, BINARY_SENSORS
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
            RinnaiBinarySensor(sensor, device) for sensor in BINARY_SENSORS
            if entity_selected(config_entry.options, device.id, sensor["entity_type"])
        )
    async_add_entities(entities)


class RinnaiBinarySensor(RinnaiEntity, BinarySensorEntity):
    def __init__(self, sensor_dict, device):
        if sensor_dict.get("icon"):
            self._attr_icon = sensor_dict["icon"]
        self._sensor_dict = sensor_dict
        super().__init__(sensor_dict["entity_type"], sensor_dict["name"], device)

    @property
    def is_on(self):
        if self._sensor_dict["entity_type"] == "burning_state":
            return self._device.is_burn_state_on
