"""Problem and connectivity indicators."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PROBLEM_BLOCK_DESYNC,
    PROBLEM_CHIPS,
    PROBLEM_FAN,
    PROBLEM_HASHRATE_LOW,
    PROBLEM_HASHRATE_ZERO,
    PROBLEM_OVERHEAT,
    PROBLEM_OVERHEAT_CRITICAL,
)
from .coordinator import AsicRecord, FleetCoordinator, FleetData
from .entity import AsicEntity, FleetEntity


@dataclass(frozen=True, kw_only=True)
class AsicBinaryDescription(BinarySensorEntityDescription):
    """Binary state derived from one miner's record."""

    value_fn: Callable[[AsicRecord], bool]
    attrs_fn: Callable[[AsicRecord], dict[str, Any]] | None = None


ASIC_BINARY_SENSORS: tuple[AsicBinaryDescription, ...] = (
    AsicBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda r: r.online,
        attrs_fn=lambda r: {"ip": r.ip, "error": r.telemetry.error},
    ),
    AsicBinaryDescription(
        key="problem",
        translation_key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda r: bool(r.problems),
        attrs_fn=lambda r: {
            "problems": sorted(r.problems),
            "severity": _worst(r),
        },
    ),
    AsicBinaryDescription(
        key="overheating",
        translation_key="overheating",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda r: (
            PROBLEM_OVERHEAT in r.problems or PROBLEM_OVERHEAT_CRITICAL in r.problems
        ),
        attrs_fn=lambda r: {"temp_max": r.telemetry.temp_max},
    ),
    AsicBinaryDescription(
        key="hashrate_problem",
        translation_key="hashrate_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda r: (
            PROBLEM_HASHRATE_LOW in r.problems or PROBLEM_HASHRATE_ZERO in r.problems
        ),
        attrs_fn=lambda r: {
            "rate_5s": r.telemetry.rate_5s,
            "rate_ideal": r.telemetry.rate_ideal,
        },
    ),
    AsicBinaryDescription(
        key="hardware_problem",
        translation_key="hardware_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda r: PROBLEM_FAN in r.problems or PROBLEM_CHIPS in r.problems,
        attrs_fn=lambda r: {
            "fans": r.telemetry.fan_rpm,
            "chip_ok": r.telemetry.chip_ok,
            "chip_total": r.telemetry.chip_total,
        },
    ),
    AsicBinaryDescription(
        key="block_desync",
        translation_key="block_desync",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: PROBLEM_BLOCK_DESYNC in r.problems,
        attrs_fn=lambda r: {"marker": r.marker, "ip": r.ip},
    ),
)


def _worst(record: AsicRecord) -> str | None:
    for level in ("critical", "warning", "info"):
        if level in record.problems.values():
            return level
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: Any, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensors."""
    coordinator: FleetCoordinator = entry.runtime_data
    async_add_entities([RouterOnline(coordinator), FleetProblem(coordinator)])

    known: set[str] = set()

    @callback
    def _add_new() -> None:
        if not coordinator.data:
            return
        new = [mac for mac in coordinator.data.asics if mac not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(
            AsicBinarySensor(coordinator, mac, description)
            for mac in new
            for description in ASIC_BINARY_SENSORS
        )

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AsicBinarySensor(AsicEntity, BinarySensorEntity):
    """One boolean condition of one miner."""

    entity_description: AsicBinaryDescription

    def __init__(
        self,
        coordinator: FleetCoordinator,
        mac: str,
        description: AsicBinaryDescription,
    ) -> None:
        super().__init__(coordinator, mac)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{mac}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        record = self.record
        if record is None:
            return None
        return self.entity_description.value_fn(record)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        record = self.record
        if record is None or self.entity_description.attrs_fn is None:
            return None
        return {
            k: v
            for k, v in self.entity_description.attrs_fn(record).items()
            if v is not None
        }


class RouterOnline(FleetEntity, BinarySensorEntity):
    """Whether the RouterOS REST API answered on the last cycle."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "router_online"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FleetCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_router_online"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success and self.coordinator.data)

    @property
    def available(self) -> bool:
        # Deliberately always available: its whole job is to report the router
        # being unreachable, which is exactly when the coordinator fails.
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data: FleetData | None = self.coordinator.data
        return {
            "host": self.coordinator.router.host,
            "identity": data.router_identity if data else None,
        }


class FleetProblem(FleetEntity, BinarySensorEntity):
    """True when any miner has an active problem."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "fleet_problem"

    def __init__(self, coordinator: FleetCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry.entry_id}_fleet_problem"

    @property
    def is_on(self) -> bool:
        data: FleetData | None = self.coordinator.data
        if not data:
            return False
        return any(r.problems for r in data.asics.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data: FleetData | None = self.coordinator.data
        if not data:
            return {}
        critical = [
            r.name for r in data.asics.values() if "critical" in r.problems.values()
        ]
        return {
            "critical": sorted(critical),
            "affected": sorted(r.name for r in data.asics.values() if r.problems),
        }
