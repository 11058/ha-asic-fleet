"""Diagnostics dump for bug reports."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ASIC_PASSWORD, CONF_ROUTER_PASSWORD
from .coordinator import FleetCoordinator

REDACT = {CONF_ROUTER_PASSWORD, CONF_ASIC_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the fleet picture as the coordinator last saw it."""
    coordinator: FleetCoordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": dict(entry.options),
        },
        "router": {
            "host": coordinator.router.host,
            "identity": data.router_identity if data else None,
            "last_update_success": coordinator.last_update_success,
        },
        "counts": {
            "miners": len(data.asics) if data else 0,
            "online": sum(1 for r in data.asics.values() if r.online) if data else 0,
            "blocked": sum(1 for r in data.asics.values() if r.blocked) if data else 0,
            "unnamed": sum(1 for r in data.asics.values() if not r.named)
            if data
            else 0,
        },
        "miners": [
            {
                "mac": record.mac,
                "name": record.name,
                "rack": record.rack,
                "ip": record.ip,
                "port": record.port,
                "named": record.named,
                "lease_hostname": record.lease_hostname,
                "marker": record.marker,
                "blocked": record.blocked,
                "block_ip_synced": record.block_ip_synced,
                "status": record.status,
                "problems": record.problems,
                "model": record.model,
                "telemetry": asdict(record.telemetry),
            }
            for record in (data.asics.values() if data else [])
        ],
        "unknown_leases": data.unknown_leases if data else [],
    }
