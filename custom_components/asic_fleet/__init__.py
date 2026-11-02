"""The ASIC Fleet integration."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.httpx_client import get_async_client

try:  # HA 2025.9+
    from homeassistant.helpers.target import (
        TargetSelection,
        async_extract_referenced_entity_ids,
    )
except ImportError:  # pragma: no cover - older cores
    from homeassistant.helpers.service import (  # type: ignore[no-redef]
        async_extract_referenced_entity_ids,
    )

    TargetSelection = None  # type: ignore[assignment]

from .asic_api import AsicClient, AsicError
from .const import (
    CONF_ALIASES,
    CONF_ALLOW_POOL_WRITE,
    CONF_ALLOW_REBOOT,
    CONF_ASIC_PASSWORD,
    CONF_ASIC_PORT,
    CONF_ASIC_USERNAME,
    CONF_AUTO_CONFIGURE,
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
    CONF_ROUTER_PORT,
    CONF_ROUTER_TLS,
    CONF_ROUTER_USERNAME,
    CONF_ROUTER_VERIFY_SSL,
    DEFAULT_ASIC_PORT,
    DEFAULT_AUTO_CONFIGURE,
    DOMAIN,
    SERVICE_ADD_DEVICE,
    SERVICE_ASSIGN_NAME,
    SERVICE_BLINK,
    SERVICE_BLOCK,
    SERVICE_BLOCK_RACK,
    SERVICE_DISCOVER,
    SERVICE_FORGET_DEVICE,
    SERVICE_REBOOT,
    SERVICE_REBOOT_RACK,
    SERVICE_REFRESH,
    SERVICE_RESET,
    SERVICE_SET_POOLS,
    SERVICE_UNBLOCK,
    SERVICE_UNBLOCK_RACK,
)
from .coordinator import AsicRecord, FleetCoordinator
from .mikrotik import MikrotikClient, MikrotikError
from .store import MinerStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

type AsicFleetConfigEntry = ConfigEntry[FleetCoordinator]

RACK_SCHEMA = vol.Schema(
    {
        vol.Required("rack"): cv.string,
        vol.Optional("stagger", default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=600)
        ),
    }
)

ASSIGN_NAME_SCHEMA = vol.Schema(
    {
        vol.Required("mac"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("rack"): cv.string,
    }
)

ADD_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required("mac"): cv.string,
        vol.Optional("ip"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("rack"): cv.string,
    }
)

FORGET_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("mac"): cv.string,
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional("area_id"): vol.All(cv.ensure_list, [cv.string]),
    }
)

RESET_SCHEMA = vol.Schema({vol.Optional("keep_manual", default=True): cv.boolean})

SET_POOLS_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional("area_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("pools"): vol.All(
            cv.ensure_list,
            vol.Length(min=1, max=3),
            [
                vol.Schema(
                    {
                        vol.Required("url"): cv.string,
                        vol.Required("user"): cv.string,
                        vol.Optional("pass", default="x"): cv.string,
                    }
                )
            ],
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: AsicFleetConfigEntry) -> bool:
    """Set up ASIC Fleet from a config entry."""
    router = MikrotikClient(
        async_get_clientsession(hass, verify_ssl=False),
        entry.data[CONF_ROUTER_HOST],
        entry.data[CONF_ROUTER_USERNAME],
        entry.data[CONF_ROUTER_PASSWORD],
        tls=entry.data.get(CONF_ROUTER_TLS, False),
        verify_ssl=entry.data.get(CONF_ROUTER_VERIFY_SSL, False),
        port=entry.data.get(CONF_ROUTER_PORT) or None,
    )
    asic = AsicClient(
        get_async_client(hass, verify_ssl=False),
        entry.data[CONF_ASIC_USERNAME],
        entry.data[CONF_ASIC_PASSWORD],
        entry.data.get(CONF_ASIC_PORT, DEFAULT_ASIC_PORT),
    )

    store = MinerStore(hass, entry.entry_id)
    await store.async_load()

    coordinator = FleetCoordinator(hass, entry, router, asic, store)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="ASIC Fleet",
        manufacturer="MikroTik",
        model="RouterOS",
        configuration_url=f"http://{router.host}",
    )
    coordinator.hub_device_id = hub_device.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _async_register_services(hass)
    if entry.options.get(CONF_AUTO_CONFIGURE, DEFAULT_AUTO_CONFIGURE):
        entry.async_create_background_task(
            hass, _async_provision(hass, entry, coordinator), f"{DOMAIN} provision"
        )
    from .panel import async_setup_panel

    await async_setup_panel(hass)
    return True


async def _async_provision(
    hass: HomeAssistant, entry: AsicFleetConfigEntry, coordinator
) -> None:
    """Put the block rule on the router if it is missing; report what changed."""
    from homeassistant.components import persistent_notification

    from .provision import async_ensure_block_rule

    result = await async_ensure_block_rule(coordinator.router, coordinator.blocklist)
    if not result.done and not result.failed:
        return
    lines = [f"- added: {x}" for x in result.done]
    lines += [f"- **failed**: {x}" for x in result.failed]
    persistent_notification.async_create(
        hass,
        f"ASIC Fleet configured the router {entry.data.get(CONF_ROUTER_HOST)}:\n\n"
        + "\n".join(lines),
        title="ASIC Fleet",
        notification_id=f"{DOMAIN}_{entry.entry_id}_provision",
    )


async def async_unload_entry(hass: HomeAssistant, entry: AsicFleetConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        for service in (
            SERVICE_BLOCK,
            SERVICE_UNBLOCK,
            SERVICE_REBOOT,
            SERVICE_BLINK,
            SERVICE_BLOCK_RACK,
            SERVICE_UNBLOCK_RACK,
            SERVICE_REBOOT_RACK,
            SERVICE_ASSIGN_NAME,
            SERVICE_SET_POOLS,
            SERVICE_REFRESH,
            SERVICE_DISCOVER,
            SERVICE_ADD_DEVICE,
            SERVICE_FORGET_DEVICE,
            SERVICE_RESET,
        ):
            hass.services.async_remove(DOMAIN, service)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# --- services ----------------------------------------------------------------


def _coordinators(hass: HomeAssistant) -> list[FleetCoordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None)
    ]


def _targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[FleetCoordinator, AsicRecord]]:
    """Resolve a service target into (coordinator, record) pairs.

    Accepts anything HA's target selector produces — entity, device or area —
    and maps back to the miner MAC through the device registry.
    """
    if TargetSelection is not None:
        referenced = async_extract_referenced_entity_ids(
            hass, TargetSelection(call.data)
        )
    else:  # pragma: no cover - older cores take the service-call form
        referenced = async_extract_referenced_entity_ids(hass, call)
    entity_registry = er.async_get(hass)
    device_ids: set[str] = set(referenced.referenced_devices)

    for entity_id in referenced.referenced | referenced.indirectly_referenced:
        entry = entity_registry.async_get(entity_id)
        if entry and entry.device_id:
            device_ids.add(entry.device_id)

    device_registry = dr.async_get(hass)
    macs: set[str] = set()
    for device_id in device_ids:
        device = device_registry.async_get(device_id)
        if not device:
            continue
        for domain, value in device.identifiers:
            if domain == DOMAIN:
                macs.add(value)

    out: list[tuple[FleetCoordinator, AsicRecord]] = []
    for coordinator in _coordinators(hass):
        for mac in macs:
            record = coordinator.record_for_mac(mac)
            if record:
                out.append((coordinator, record))
    if not out:
        raise ServiceValidationError(
            "No ASIC Fleet miners matched this target. Target a miner device or "
            "one of its entities."
        )
    return out


def _rack_targets(
    hass: HomeAssistant, rack: str
) -> list[tuple[FleetCoordinator, AsicRecord]]:
    wanted = rack.strip().upper()
    out = [
        (coordinator, record)
        for coordinator in _coordinators(hass)
        if coordinator.data
        for record in coordinator.data.asics.values()
        if (record.rack or "").upper() == wanted
    ]
    if not out:
        raise ServiceValidationError(f"No miners found in rack {rack}")
    return out


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_BLOCK):
        return

    async def handle_block(call: ServiceCall) -> None:
        for coordinator, record in _targets(hass, call):
            await coordinator.async_set_blocked(record, True)

    async def handle_unblock(call: ServiceCall) -> None:
        for coordinator, record in _targets(hass, call):
            await coordinator.async_set_blocked(record, False)

    async def handle_reboot(call: ServiceCall) -> None:
        for coordinator, record in _targets(hass, call):
            _assert_reboot_allowed(coordinator)
            await _guarded(coordinator.async_reboot(record), record)

    async def handle_blink(call: ServiceCall) -> None:
        on = bool(call.data.get("on", True))
        for coordinator, record in _targets(hass, call):
            await _guarded(coordinator.async_blink(record, on), record)

    async def handle_block_rack(call: ServiceCall) -> None:
        for coordinator, record in _rack_targets(hass, call.data["rack"]):
            await coordinator.async_set_blocked(record, True)

    async def handle_unblock_rack(call: ServiceCall) -> None:
        for coordinator, record in _rack_targets(hass, call.data["rack"]):
            await coordinator.async_set_blocked(record, False)

    async def handle_reboot_rack(call: ServiceCall) -> None:
        stagger = call.data.get("stagger", 0)
        targets = _rack_targets(hass, call.data["rack"])
        for index, (coordinator, record) in enumerate(targets):
            _assert_reboot_allowed(coordinator)
            if index and stagger:
                # Rebooting a whole rack at once drops the entire load in one
                # step; the genset and the pool both prefer a ramp.
                await asyncio.sleep(stagger)
            await _guarded(coordinator.async_reboot(record), record)

    async def handle_assign_name(call: ServiceCall) -> None:
        mac = call.data["mac"].strip().upper().replace("-", ":")
        name = call.data["name"]
        rack = call.data.get("rack")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            aliases = dict(entry.options.get(CONF_ALIASES, {}) or {})
            aliases[mac] = {"name": name, **({"rack": rack} if rack else {})}
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_ALIASES: aliases}
            )

    async def handle_set_pools(call: ServiceCall) -> None:
        pools = call.data["pools"]
        for coordinator, record in _targets(hass, call):
            if not coordinator.entry.options.get(CONF_ALLOW_POOL_WRITE, False):
                raise ServiceValidationError(
                    "Pool writes are disabled. Enable 'Allow pool configuration "
                    "writes' in the integration options first."
                )
            if not record.ip:
                raise ServiceValidationError(f"{record.name}: no IP known")
            # set_miner_conf.cgi replaces the whole config, so start from what
            # the miner currently has and change only the pool block.
            conf = await coordinator.asic.get_conf(record.ip)
            if not conf:
                raise HomeAssistantError(
                    f"{record.name}: could not read current config"
                )
            conf["pools"] = [
                {"url": p["url"], "user": p["user"], "pass": p.get("pass", "x")}
                for p in pools
            ]
            _LOGGER.info("Writing pool config to %s (%s)", record.name, record.ip)
            await coordinator.asic.set_conf(record.ip, conf)

    async def handle_refresh(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    async def handle_discover(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_discover_now()

    async def handle_add_device(call: ServiceCall) -> None:
        coordinators = _coordinators(hass)
        if not coordinators:
            raise ServiceValidationError("ASIC Fleet is not set up")
        added = await coordinators[0].async_add_manual(
            call.data["mac"],
            ip=call.data.get("ip"),
            name=call.data.get("name"),
            rack=call.data.get("rack"),
        )
        _LOGGER.info("Added miner %s by hand (%s)", added.mac, added.name or "unnamed")

    async def handle_forget_device(call: ServiceCall) -> None:
        macs: set[str] = set()
        if call.data.get("mac"):
            macs.add(str(call.data["mac"]))
        if any(call.data.get(k) for k in ("device_id", "entity_id", "area_id")):
            macs.update(record.mac for _, record in _targets(hass, call))
        if not macs:
            raise ServiceValidationError(
                "Target a miner device, or pass its MAC address."
            )
        for coordinator in _coordinators(hass):
            for mac in macs:
                await coordinator.async_forget(mac)

    async def handle_reset(call: ServiceCall) -> None:
        keep_manual = bool(call.data.get("keep_manual", True))
        for coordinator in _coordinators(hass):
            removed = await coordinator.async_reset(keep_manual=keep_manual)
            _LOGGER.warning(
                "ASIC Fleet inventory reset: %d miners dropped, rediscovering",
                removed,
            )

    hass.services.async_register(DOMAIN, SERVICE_BLOCK, handle_block)
    hass.services.async_register(DOMAIN, SERVICE_UNBLOCK, handle_unblock)
    hass.services.async_register(DOMAIN, SERVICE_REBOOT, handle_reboot)
    hass.services.async_register(DOMAIN, SERVICE_BLINK, handle_blink)
    hass.services.async_register(
        DOMAIN, SERVICE_BLOCK_RACK, handle_block_rack, RACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNBLOCK_RACK, handle_unblock_rack, RACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REBOOT_RACK, handle_reboot_rack, RACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ASSIGN_NAME, handle_assign_name, ASSIGN_NAME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_POOLS, handle_set_pools, SET_POOLS_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
    hass.services.async_register(DOMAIN, SERVICE_DISCOVER, handle_discover)
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_DEVICE, handle_add_device, ADD_DEVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORGET_DEVICE, handle_forget_device, FORGET_DEVICE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_RESET, handle_reset, RESET_SCHEMA)


def _assert_reboot_allowed(coordinator: FleetCoordinator) -> None:
    if not coordinator.entry.options.get(CONF_ALLOW_REBOOT, True):
        raise ServiceValidationError(
            "Reboot is disabled in this integration's options."
        )


async def _guarded(coro, record: AsicRecord) -> None:
    try:
        await coro
    except (AsicError, MikrotikError) as err:
        raise HomeAssistantError(f"{record.name}: {err}") from err
