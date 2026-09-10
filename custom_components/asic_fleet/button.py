"""Buttons: reboot, locate, and a manual fleet refresh."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .asic_api import AsicError
from .const import CONF_ALLOW_REBOOT, DOMAIN
from .coordinator import FleetCoordinator
from .entity import AsicEntity, FleetEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: Any, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up buttons."""
    coordinator: FleetCoordinator = entry.runtime_data
    async_add_entities([RefreshButton(coordinator)])

    known: set[str] = set()

    @callback
    def _add_new() -> None:
        if not coordinator.data:
            return
        new = [mac for mac in coordinator.data.asics if mac not in known]
        if not new:
            return
        known.update(new)
        entities: list[ButtonEntity] = []
        for mac in new:
            entities.append(RebootButton(coordinator, mac))
            entities.append(BlinkButton(coordinator, mac))
        async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class RebootButton(AsicEntity, ButtonEntity):
    """Reboot one miner via reboot.cgi."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_translation_key = "reboot"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FleetCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac)
        self._attr_unique_id = f"{DOMAIN}_{mac}_reboot"

    async def async_press(self) -> None:
        record = self.record
        if record is None:
            raise HomeAssistantError("Miner is no longer known to the router")
        if not self.coordinator.entry.options.get(CONF_ALLOW_REBOOT, True):
            raise ServiceValidationError(
                "Reboot is disabled in this integration's options."
            )
        try:
            await self.coordinator.async_reboot(record)
        except AsicError as err:
            raise HomeAssistantError(f"{record.name}: {err}") from err


class BlinkButton(AsicEntity, ButtonEntity):
    """Flash the miner's locate LED — how you find a machine in a loud rack."""

    _attr_translation_key = "blink"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FleetCoordinator, mac: str) -> None:
        super().__init__(coordinator, mac)
        self._attr_unique_id = f"{DOMAIN}_{mac}_blink"

    async def async_press(self) -> None:
        record = self.record
        if record is None:
            raise HomeAssistantError("Miner is no longer known to the router")
        try:
            await self.coordinator.async_blink(record, True)
        except AsicError as err:
            raise HomeAssistantError(f"{record.name}: {err}") from err


class RefreshButton(FleetEntity, ButtonEntity):
    """Force an immediate poll instead of waiting for the interval."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FleetCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
