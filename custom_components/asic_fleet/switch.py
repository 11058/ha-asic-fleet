"""Internet-access switch — the fleet's only real load-management lever."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import FleetCoordinator
from .entity import AsicEntity
from .mikrotik import MikrotikError


async def async_setup_entry(
    hass: HomeAssistant, entry: Any, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one switch per miner."""
    coordinator: FleetCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        if not coordinator.data:
            return
        new = [mac for mac in coordinator.data.asics if mac not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(AsicInternetSwitch(coordinator, mac) for mac in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AsicInternetSwitch(AsicEntity, SwitchEntity):
    """On = miner may reach the internet; off = firewall drops its WAN traffic.

    The Promminer firmware ignores its own sleep mode, so cutting the pool
    connection at the router is the only way to stop a machine hashing without
    pulling power.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_translation_key = "internet"

    def __init__(self, coordinator: FleetCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac)
        self._attr_unique_id = f"{DOMAIN}_{mac}_internet"

    @property
    def is_on(self) -> bool | None:
        record = self.record
        if record is None:
            return None
        return not record.blocked

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        record = self.record
        if record is None:
            return None
        return {
            "marker": record.marker,
            "ip": record.ip,
            "firewall_in_sync": record.block_ip_synced,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(True)

    async def _set(self, blocked: bool) -> None:
        record = self.record
        if record is None:
            raise HomeAssistantError("Miner is no longer known to the router")
        try:
            await self.coordinator.async_set_blocked(record, blocked)
        except MikrotikError as err:
            raise HomeAssistantError(f"{record.name}: {err}") from err
