"""Synthetic selection, cloud, flow and lifecycle tests; no real cloud access."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import rinnai_smart as integration
from custom_components.rinnai_smart import config_flow, filters
from custom_components.rinnai_smart.rinnai_client import (
    AuthenticationError,
    HTTPClient,
    RinnaiClient,
    RinnaiError,
)

DEVICES = [
    {"id": "heater-a", "name": "Kitchen", "mac": "020000000001", "deviceType": "E86"},
    {"id": "heater-b", "name": "Bathroom", "mac": "020000000002", "deviceType": "E86"},
]
CREDENTIALS = {"username": "example", "password": "synthetic-password"}


@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, [True, True]),
        ({filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["heater-a"]}, [True, False]),
        ({filters.DEVICE_MODE: "exclude", filters.DEVICE_IDS: ["heater-a"]}, [False, True]),
        ({filters.DEVICE_MODE: "include", filters.DEVICE_IDS: []}, [False, False]),
        ({filters.DEVICE_MODE: "exclude", filters.DEVICE_IDS: []}, [True, True]),
        ({filters.ENTITY_MODE: "include", filters.ENTITY_IDS: []}, [False, False]),
        (
            {filters.ENTITY_MODE: "include", filters.ENTITY_IDS: ["heater-b_burning_state"]},
            [False, True],
        ),
    ],
)
def test_device_and_entity_filters(options, expected):
    assert [filters.device_needed(options, d["id"]) for d in DEVICES] == expected


def test_entity_selection_is_per_device_and_uses_existing_unique_ids():
    options = {filters.ENTITY_MODE: "exclude", filters.ENTITY_IDS: ["heater-a_cycle_mode"]}
    assert not filters.entity_selected(options, "heater-a", "cycle_mode")
    assert filters.entity_selected(options, "heater-b", "cycle_mode")
    assert filters.entity_selected(options, "heater-a", "water_heater")
    for key in filters.ENTITY_TYPES:
        assert filters.split_unique_id(f"device_with_underscores_{key}") == (
            "device_with_underscores",
            key,
        )
    assert filters.split_unique_id("unknown") is None


async def test_http_filters_before_state_reads_and_on_refresh():
    client = RinnaiClient(**CREDENTIALS, device_filter=lambda key: key == "heater-a")
    http = client._http_client
    http._token = "synthetic-token"
    http._get_devices = AsyncMock(return_value={"success": True, "data": {"list": DEVICES}})
    http._get_device_information = AsyncMock(return_value={"success": True, "data": {}})
    for _ in range(2):
        assert list(await client.get_devices()) == ["heater-a"]
    assert [c.args for c in http._get_device_information.await_args_list] == [("heater-a",)] * 2
    client._mqtt_client.subscribe = AsyncMock()
    await client.subscribe("heater-a", AsyncMock())
    assert await client.subscribe("heater-b", AsyncMock()) is False
    client._mqtt_client.subscribe.assert_awaited_once_with("020000000001")


async def test_device_listing_does_not_fetch_state():
    client = HTTPClient(**CREDENTIALS)
    client._token = "synthetic-token"
    client._get_devices = AsyncMock(return_value={"success": True, "data": {"list": DEVICES}})
    client._get_device_information = AsyncMock()
    assert await client.get_device_list() == DEVICES
    client._get_device_information.assert_not_awaited()


@pytest.mark.parametrize(
    "response",
    [
        {"success": False},
        {"success": True},
        {"success": True, "data": {"list": None}},
        {"success": True, "data": {"list": [None]}},
    ],
)
async def test_invalid_cloud_response(response):
    client = HTTPClient(**CREDENTIALS)
    client._token = "synthetic-token"
    client.login = AsyncMock(return_value=True)
    client._get_devices = AsyncMock(return_value=response)
    with pytest.raises(RinnaiError):
        await client.get_device_list()


async def test_failed_login_stops_device_requests():
    client = HTTPClient(**CREDENTIALS)
    client.login = AsyncMock(return_value=False)
    client._get_devices = AsyncMock()
    with pytest.raises(AuthenticationError):
        await client.get_device_list()
    client._get_devices.assert_not_awaited()


@pytest.fixture
def flow(hass):
    flow = config_flow.ConfigFlow()
    flow.hass = hass
    return flow


async def test_login_devices_entities_flow(flow):
    with patch.object(HTTPClient, "get_device_list", AsyncMock(return_value=DEVICES)):
        result = await flow.async_step_user(CREDENTIALS)
    assert result["step_id"] == "devices"
    result["data_schema"]({filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["heater-a"]})
    result = await flow.async_step_devices(
        {filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["heater-a"]}
    )
    assert result["step_id"] == "entities"
    # The picker contains seven functions for A, and no functions for B.
    schema = result["data_schema"].schema
    picker = next(value for key, value in schema.items() if key.schema == filters.ENTITY_IDS)
    assert len(picker.config["options"]) == 7
    assert all(item["value"].startswith("heater-a_") for item in picker.config["options"])
    result = await flow.async_step_entities(
        {filters.ENTITY_MODE: "include", filters.ENTITY_IDS: ["heater-a_burning_state"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == CREDENTIALS
    assert result["options"][filters.DEVICE_IDS] == ["heater-a"]
    assert result["options"][filters.ENTITY_IDS] == ["heater-a_burning_state"]
    assert "password" not in result["options"]


@pytest.mark.parametrize(
    "exception,error",
    [
        (AuthenticationError(), "invalid_auth"),
        (RinnaiError(), "cannot_connect"),
        (aiohttp.ClientError(), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
    ],
)
async def test_login_failure(flow, exception, error):
    with patch.object(HTTPClient, "get_device_list", AsyncMock(side_effect=exception)):
        result = await flow.async_step_user(CREDENTIALS)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_empty_account(flow):
    with patch.object(HTTPClient, "get_device_list", AsyncMock(return_value=[])):
        result = await flow.async_step_user(CREDENTIALS)
    assert result["reason"] == "no_devices"


async def test_unknown_selection_rejected(flow):
    with patch.object(HTTPClient, "get_device_list", AsyncMock(return_value=DEVICES)):
        await flow.async_step_user(CREDENTIALS)
    result = await flow.async_step_devices(
        {filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["unknown"]}
    )
    assert result["errors"] == {"base": "invalid_selection"}
    await flow.async_step_devices(
        {filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["heater-a"]}
    )
    result = await flow.async_step_entities(
        {filters.ENTITY_MODE: "include", filters.ENTITY_IDS: ["heater-b_water_heater"]}
    )
    assert result["errors"] == {"base": "invalid_selection"}


async def test_options_preserve_missing_choices_and_other_options(hass):
    options = {
        filters.DEVICE_MODE: "include",
        filters.DEVICE_IDS: ["missing-device"],
        filters.ENTITY_MODE: "include",
        filters.ENTITY_IDS: ["missing-device_water_heater"],
        "other_option": True,
    }
    entry = MockConfigEntry(domain="rinnai", data=CREDENTIALS, options=options)
    entry.add_to_hass(hass)
    flow = config_flow.RinnaiOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    with patch.object(HTTPClient, "get_device_list", AsyncMock(return_value=DEVICES)):
        result = await flow.async_step_init()
    result["data_schema"]({filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["missing-device"]})
    await flow.async_step_devices(
        {filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["missing-device"]}
    )
    result = await flow.async_step_entities(
        {filters.ENTITY_MODE: "include", filters.ENTITY_IDS: ["missing-device_water_heater"]}
    )
    assert result["data"] == options
    assert entry.options == options


async def test_registry_disables_and_restores_without_overriding_user(hass):
    entry = MockConfigEntry(
        domain="rinnai",
        data=CREDENTIALS,
        options={filters.DEVICE_MODE: "include", filters.DEVICE_IDS: []},
    )
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain="rinnai", data=CREDENTIALS)
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    active = registry.async_get_or_create(
        "water_heater", "rinnai", "heater-a_water_heater", config_entry=entry
    )
    registry.async_update_entity(active.entity_id, name="My custom heater")
    disabled = registry.async_get_or_create(
        "select",
        "rinnai",
        "heater-a_cycle_mode",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    unrelated = registry.async_get_or_create(
        "water_heater", "rinnai", "heater-b_water_heater", config_entry=other
    )
    integration.async_update_entity_selection(hass, entry)
    assert registry.async_get(active.entity_id).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(unrelated.entity_id).disabled_by is None
    hass.config_entries.async_update_entry(entry, options={})
    integration.async_update_entity_selection(hass, entry)
    restored = registry.async_get(active.entity_id)
    assert restored.disabled_by is None
    assert restored.name == "My custom heater"
    assert registry.async_get(disabled.entity_id).disabled_by == er.RegistryEntryDisabler.USER


@pytest.mark.parametrize(
    "module,expected",
    [
        ("water_heater", 0),
        ("text", 0),
        ("select", 1),
        ("switch", 0),
        ("binary_sensor", 0),
    ],
)
async def test_platform_creation_respects_selection(hass, module, expected):
    from importlib import import_module

    platform = import_module(f"custom_components.rinnai_smart.{module}")
    entry = MockConfigEntry(
        domain="rinnai",
        data=CREDENTIALS,
        options={filters.ENTITY_MODE: "include", filters.ENTITY_IDS: ["heater-a_cycle_mode"]},
    )
    hass.data["rinnai"] = {
        entry.entry_id: {"devices": [SimpleNamespace(id=d["id"]) for d in DEVICES]}
    }
    add = Mock()
    await platform.async_setup_entry(hass, entry, add)
    assert len(add.call_args.args[0]) == expected
    if expected:
        assert add.call_args.args[0][0].unique_id == "heater-a_cycle_mode"


async def test_setup_unload_cancels_mqtt_before_reload(hass):
    entry = MockConfigEntry(domain="rinnai", data=CREDENTIALS)
    entry.add_to_hass(hass)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def run(*args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    client = Mock(get_devices=AsyncMock(return_value={"heater-a": {"device": DEVICES[0]}}), run=run)
    coordinator = Mock(_async_setup=AsyncMock())
    with (
        patch.object(integration, "RinnaiClient", return_value=client),
        patch.object(integration, "RinnaiDeviceDataUpdateCoordinator", return_value=coordinator),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)),
    ):
        assert await integration.async_setup_entry(hass, entry)
        await asyncio.wait_for(started.wait(), 1)
        task = hass.data["rinnai"][entry.entry_id]["task"]
        assert await integration.async_unload_entry(hass, entry)
        assert task.cancelled() and stopped.is_set()
        assert entry.entry_id not in hass.data["rinnai"]


async def test_no_selected_devices_does_not_start_mqtt(hass):
    entry = MockConfigEntry(
        domain="rinnai",
        data=CREDENTIALS,
        options={filters.DEVICE_MODE: "include", filters.DEVICE_IDS: []},
    )
    client = Mock(get_devices=AsyncMock(return_value={}), run=AsyncMock())
    with (
        patch.object(integration, "RinnaiClient", return_value=client) as factory,
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
    ):
        assert await integration.async_setup_entry(hass, entry)
    assert not factory.call_args.kwargs["device_filter"]("heater-a")
    client.run.assert_not_called()


async def test_ha_flow_manager_reload_and_restore(hass):
    """Exercise real HA flows, platform setup, registry and options reload."""
    from importlib import import_module
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import (
        MockModule,
        mock_integration,
        mock_platform,
    )
    from custom_components.rinnai_smart.rinnai_client import MQTTClient

    mock_integration(
        hass,
        MockModule(
            "rinnai",
            async_setup_entry=integration.async_setup_entry,
            async_unload_entry=integration.async_unload_entry,
            partial_manifest={"config_flow": True},
        ),
    )
    mock_platform(hass, "rinnai.config_flow", config_flow)
    for name in integration.PLATFORMS:
        mock_platform(
            hass, f"rinnai.{name}", import_module(f"custom_components.rinnai_smart.{name}")
        )
    await async_setup_component(hass, "homeassistant", {})
    info = {
        "hotWaterTempSetting": "2D",
        "operationMode": "A0",
        "burningState": "0",
        "cycleModeSetting": "0",
        "cycleReservationSetting": "0",
        "temporaryCycleInsulationSetting": "0",
        "cycleReservationTimeSetting": "00 00 00",
    }
    connections = []

    async def run(*args):
        task = asyncio.current_task()
        connections.append(task)
        await asyncio.Future()

    with (
        patch.object(HTTPClient, "get_device_list", AsyncMock(return_value=DEVICES)),
        patch.object(
            HTTPClient,
            "_get_device_information",
            AsyncMock(return_value={"success": True, "data": info}),
        ),
        patch.object(MQTTClient, "subscribe", AsyncMock()),
        patch.object(RinnaiClient, "run", run),
    ):
        result = await hass.config_entries.flow.async_init("rinnai", context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
        assert result["step_id"] == "devices"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                filters.DEVICE_MODE: "include",
                filters.DEVICE_IDS: ["heater-a"],
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                filters.ENTITY_MODE: "exclude",
                filters.ENTITY_IDS: [],
            },
        )
        assert result["type"] == "create_entry"
        entry = result["result"]
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert len(entities) == 7
        assert all(e.unique_id.startswith("heater-a_") for e in entities)
        heater = next(e for e in entities if e.domain == "water_heater")
        assert hass.states.get(heater.entity_id).attributes["temperature"] == 45
        registry.async_update_entity(heater.entity_id, name="Custom heater")
        first_connection = connections[-1]

        async def select_devices(device_ids):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    filters.DEVICE_MODE: "include",
                    filters.DEVICE_IDS: device_ids,
                },
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    filters.ENTITY_MODE: "exclude",
                    filters.ENTITY_IDS: [],
                },
            )
            assert result["type"] == "create_entry"
            await hass.async_block_till_done()

        await select_devices([])
        assert first_connection.cancelled()
        assert hass.states.get(heater.entity_id) is None
        assert all(
            e.disabled_by == er.RegistryEntryDisabler.INTEGRATION
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        )
        await select_devices(["heater-a"])
        assert registry.async_get(heater.entity_id).name == "Custom heater"
        assert hass.states.get(heater.entity_id).attributes["temperature"] == 45
        assert len(connections) == 2
        await hass.config_entries.async_unload(entry.entry_id)
        assert all(task.cancelled() for task in connections)


@pytest.mark.parametrize(
    "exception,reason",
    [
        (AuthenticationError(), "invalid_auth"),
        (RinnaiError(), "cannot_connect"),
    ],
)
async def test_options_failure_preserves_entry(hass, exception, reason):
    entry = MockConfigEntry(
        domain="rinnai",
        data=CREDENTIALS,
        options={filters.DEVICE_MODE: "include", filters.DEVICE_IDS: ["heater-a"]},
    )
    entry.add_to_hass(hass)
    flow = config_flow.RinnaiOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    before = dict(entry.options)
    with patch.object(HTTPClient, "get_device_list", AsyncMock(side_effect=exception)):
        result = await flow.async_step_init()
    assert result["reason"] == reason
    assert entry.options == before and entry.data == CREDENTIALS


async def test_legacy_options_and_canceled_selection(hass):
    entry = MockConfigEntry(domain="rinnai", data=CREDENTIALS)
    entry.add_to_hass(hass)
    flow = config_flow.RinnaiOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    with patch.object(HTTPClient, "get_device_list", AsyncMock(return_value=DEVICES)):
        result = await flow.async_step_init()
    defaults = result["data_schema"]({})
    assert defaults == {filters.DEVICE_MODE: "exclude", filters.DEVICE_IDS: []}
    await flow.async_step_devices({filters.DEVICE_MODE: "include", filters.DEVICE_IDS: []})
    # Leaving the entity step without saving must not alter the active entry.
    assert entry.options == {}


async def test_setup_failure_cancels_connection(hass):
    entry = MockConfigEntry(domain="rinnai", data=CREDENTIALS)
    tasks = []

    def run(*args):
        async def wait():
            await asyncio.Future()

        return wait()

    async def fail_setup():
        tasks.append(hass.data["rinnai"][entry.entry_id]["task"])
        raise RuntimeError("synthetic setup failure")

    client = Mock(get_devices=AsyncMock(return_value={"heater-a": {"device": DEVICES[0]}}), run=run)
    with (
        patch.object(integration, "RinnaiClient", return_value=client),
        patch.object(
            integration,
            "RinnaiDeviceDataUpdateCoordinator",
            return_value=Mock(_async_setup=fail_setup),
        ),
    ):
        with pytest.raises(RuntimeError, match="synthetic setup failure"):
            await integration.async_setup_entry(hass, entry)
    assert tasks[0].cancelled()
    assert entry.entry_id not in hass.data["rinnai"]


async def test_failed_platform_unload_retains_runtime(hass):
    entry = MockConfigEntry(domain="rinnai", data=CREDENTIALS)
    task = Mock()
    runtime = {"task": task}
    hass.data["rinnai"] = {entry.entry_id: runtime}
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert not await integration.async_unload_entry(hass, entry)
    task.cancel.assert_not_called()
    assert hass.data["rinnai"][entry.entry_id] is runtime
