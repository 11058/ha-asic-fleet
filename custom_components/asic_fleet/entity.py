"""Shared entity plumbing."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AsicRecord, FleetCoordinator


class AsicEntity(CoordinatorEntity[FleetCoordinator]):
    """Base for entities bound to one miner, addressed by MAC."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FleetCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac

    @property
    def record(self) -> AsicRecord | None:
        return self.coordinator.record_for_mac(self._mac)

    @property
    def available(self) -> bool:
        # The record itself disappearing means the DHCP lease is gone — that is
        # a real "unknown", distinct from a miner that is merely powered off
        # (which keeps its record and reports online=False).
        return super().available and self.record is not None

    @property
    def device_info(self) -> DeviceInfo:
        record = self.record
        name = record.name if record else self._mac
        info = DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            connections={(CONNECTION_NETWORK_MAC, self._mac)},
            name=name,
            manufacturer="Bitmain",
            model=(record.model if record else None) or "ASIC miner",
            sw_version=record.firmware if record else None,
            serial_number=record.serial if record else None,
            suggested_area=record.rack if record and record.rack else None,
        )
        if self.coordinator.hub_device_id:
            info["via_device_id"] = self.coordinator.hub_device_id
        return info


class FleetEntity(CoordinatorEntity[FleetCoordinator]):
    """Base for fleet-wide (hub) entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FleetCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name="ASIC Fleet",
            manufacturer="MikroTik",
            model="RouterOS",
            configuration_url=f"http://{self.coordinator.router.host}",
        )
