"""Sensors for individual miners and for the fleet as a whole."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AsicRecord, FleetCoordinator, FleetData
from .entity import AsicEntity, FleetEntity

REVOLUTIONS_PER_MINUTE = "rpm"


@dataclass(frozen=True, kw_only=True)
class AsicSensorDescription(SensorEntityDescription):
    """Sensor backed by one miner's record."""

    value_fn: Callable[[AsicRecord], Any]
    attrs_fn: Callable[[AsicRecord], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class FleetSensorDescription(SensorEntityDescription):
    """Sensor aggregating the whole fleet."""

    value_fn: Callable[[FleetData], Any]
    attrs_fn: Callable[[FleetData], dict[str, Any]] | None = None


ASIC_SENSORS: tuple[AsicSensorDescription, ...] = (
    AsicSensorDescription(
        key="hashrate",
        translation_key="hashrate",
        native_unit_of_measurement="MH/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda r: r.telemetry.rate_5s,
        attrs_fn=lambda r: {
            "rate_30m": r.telemetry.rate_30m,
            "rate_avg": r.telemetry.rate_avg,
            "rate_ideal": r.telemetry.rate_ideal,
        },
    ),
    AsicSensorDescription(
        key="hashrate_avg",
        translation_key="hashrate_avg",
        native_unit_of_measurement="MH/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.telemetry.rate_avg,
    ),
    AsicSensorDescription(
        key="efficiency",
        translation_key="efficiency",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: (
            None
            if r.telemetry.efficiency is None
            else round(r.telemetry.efficiency * 100, 1)
        ),
    ),
    AsicSensorDescription(
        key="temp_max",
        translation_key="temp_max",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r: r.telemetry.temp_max,
        attrs_fn=lambda r: {
            "temp_min": r.telemetry.temp_min,
            "temp_avg": r.telemetry.temp_avg,
        },
    ),
    AsicSensorDescription(
        key="temp_avg",
        translation_key="temp_avg",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.telemetry.temp_avg,
    ),
    AsicSensorDescription(
        key="fan_min",
        translation_key="fan_min",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r: r.telemetry.fan_min,
        attrs_fn=lambda r: {"fans": r.telemetry.fan_rpm},
    ),
    AsicSensorDescription(
        key="fan_max",
        translation_key="fan_max",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.telemetry.fan_max,
    ),
    AsicSensorDescription(
        key="chip_health",
        translation_key="chip_health",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: (
            None
            if r.telemetry.chip_health is None
            else round(r.telemetry.chip_health * 100, 2)
        ),
        attrs_fn=lambda r: {
            "chip_ok": r.telemetry.chip_ok,
            "chip_total": r.telemetry.chip_total,
            "chains": r.telemetry.chains,
        },
    ),
    AsicSensorDescription(
        key="hw_error_pct",
        translation_key="hw_error_pct",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.telemetry.hw_error_pct,
        attrs_fn=lambda r: {"hw_errors": r.telemetry.hw_all},
    ),
    AsicSensorDescription(
        key="uptime",
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.telemetry.uptime_hours,
    ),
    AsicSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["online", "offline", "blocked", "degraded", "critical"],
        value_fn=lambda r: r.status,
        attrs_fn=lambda r: {"problems": sorted(r.problems)},
    ),
    AsicSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.ip,
        attrs_fn=lambda r: {
            "mac": r.mac,
            "dhcp_hostname": r.lease_hostname,
            "marker": r.marker,
        },
    ),
    AsicSensorDescription(
        key="switch_port",
        translation_key="switch_port",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.port,
    ),
    AsicSensorDescription(
        key="pool",
        translation_key="pool",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.telemetry.pool_url,
        attrs_fn=lambda r: {
            "user": r.telemetry.pool_user,
            "status": r.telemetry.pool_status,
            "accepted": r.telemetry.accepted,
            "rejected": r.telemetry.rejected,
        },
    ),
    AsicSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.last_seen,
    ),
)


def _hottest(data: FleetData) -> AsicRecord | None:
    candidates = [r for r in data.asics.values() if r.telemetry.temp_max is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.telemetry.temp_max or 0)


def _sum(data: FleetData, attr: str) -> float:
    total = 0.0
    for record in data.asics.values():
        value = getattr(record.telemetry, attr)
        if value:
            total += value
    return round(total, 1)


FLEET_SENSORS: tuple[FleetSensorDescription, ...] = (
    FleetSensorDescription(
        key="fleet_hashrate",
        translation_key="fleet_hashrate",
        native_unit_of_measurement="MH/s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _sum(d, "rate_5s"),
        attrs_fn=lambda d: {"ideal": _sum(d, "rate_ideal")},
    ),
    FleetSensorDescription(
        key="miners_total",
        translation_key="miners_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.asics),
        attrs_fn=lambda d: {"racks": sorted(d.racks)},
    ),
    FleetSensorDescription(
        key="miners_online",
        translation_key="miners_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for r in d.asics.values() if r.online),
    ),
    FleetSensorDescription(
        key="miners_offline",
        translation_key="miners_offline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for r in d.asics.values() if not r.online),
        attrs_fn=lambda d: {
            "names": sorted(r.name for r in d.asics.values() if not r.online)
        },
    ),
    FleetSensorDescription(
        key="miners_blocked",
        translation_key="miners_blocked",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for r in d.asics.values() if r.blocked),
        attrs_fn=lambda d: {
            "names": sorted(r.name for r in d.asics.values() if r.blocked)
        },
    ),
    FleetSensorDescription(
        key="miners_with_problems",
        translation_key="miners_with_problems",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for r in d.asics.values() if r.problems),
        attrs_fn=lambda d: {
            "detail": {
                r.name: sorted(r.problems) for r in d.asics.values() if r.problems
            }
        },
    ),
    FleetSensorDescription(
        key="fleet_temp_max",
        translation_key="fleet_temp_max",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _hottest(d).telemetry.temp_max if _hottest(d) else None,
        attrs_fn=lambda d: {"hottest": _hottest(d).name if _hottest(d) else None},
    ),
    FleetSensorDescription(
        key="unnamed_miners",
        translation_key="unnamed_miners",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: sum(1 for r in d.asics.values() if not r.named),
        attrs_fn=lambda d: {
            "devices": [
                {"mac": r.mac, "ip": r.ip, "port": r.port, "model": r.model}
                for r in d.asics.values()
                if not r.named
            ]
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors, adding new miners as discovery finds them."""
    coordinator: FleetCoordinator = entry.runtime_data
    async_add_entities(FleetSensor(coordinator, d) for d in FLEET_SENSORS)

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
            AsicSensor(coordinator, mac, description)
            for mac in new
            for description in ASIC_SENSORS
        )

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AsicSensor(AsicEntity, SensorEntity):
    """One metric of one miner."""

    entity_description: AsicSensorDescription

    def __init__(
        self,
        coordinator: FleetCoordinator,
        mac: str,
        description: AsicSensorDescription,
    ) -> None:
        super().__init__(coordinator, mac)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{mac}_{description.key}"

    @property
    def native_value(self) -> Any:
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


class FleetSensor(FleetEntity, SensorEntity):
    """One aggregate metric across every miner."""

    entity_description: FleetSensorDescription

    def __init__(
        self, coordinator: FleetCoordinator, description: FleetSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.entry.entry_id}_{description.key}"
        )

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
