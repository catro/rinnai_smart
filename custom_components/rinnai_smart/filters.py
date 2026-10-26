"""Device and entity selection, independent of Home Assistant registries."""

from .const import BINARY_SENSORS, SELECTS, SWITCHES, TEXTS, WATER_HEATER

DEVICE_MODE = "device_filter_mode"
DEVICE_IDS = "device_ids"
ENTITY_MODE = "entity_filter_mode"
ENTITY_IDS = "entity_ids"
INCLUDE = "include"
EXCLUDE = "exclude"

ENTITY_TYPES = {
    "water_heater": WATER_HEATER,
    **{
        item["entity_type"]: item["name"]
        for items in (TEXTS, SELECTS, SWITCHES, BINARY_SENSORS)
        for item in items
    },
}


def matches(options, mode_key, ids_key, identifier):
    """Missing settings preserve the original import-all behavior."""
    selected = identifier in options.get(ids_key, [])
    if options.get(mode_key, EXCLUDE) == INCLUDE:
        return selected
    return not selected


def device_selected(options, device_id):
    """Return whether the device passes the device filter."""
    return matches(options, DEVICE_MODE, DEVICE_IDS, device_id)


def entity_selected(options, device_id, entity_type):
    """Use the existing unique ID so names and entity IDs do not affect selection."""
    return device_selected(options, device_id) and matches(
        options, ENTITY_MODE, ENTITY_IDS, f"{device_id}_{entity_type}"
    )


def device_needed(options, device_id):
    """Only fetch state and subscribe for devices with selected entities."""
    return any(entity_selected(options, device_id, key) for key in ENTITY_TYPES)


def split_unique_id(unique_id):
    """Recognize only IDs created by this integration."""
    for entity_type in ENTITY_TYPES:
        suffix = f"_{entity_type}"
        if unique_id.endswith(suffix):
            return unique_id[: -len(suffix)], entity_type
    return None
