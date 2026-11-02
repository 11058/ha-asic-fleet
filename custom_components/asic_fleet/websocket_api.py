"""Websocket commands for the ASIC sidebar panel."""

from __future__ import annotations

import contextlib
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_ALIASES, CONF_ALLOW_REBOOT, DOMAIN


def _coordinators(hass: HomeAssistant):
    return [
        (e, e.runtime_data)
        for e in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(e, "runtime_data", None) is not None
    ]


def _plug_view(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """State of a linked plug and, if its device meters it, the power."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    view: dict[str, Any] = {
        "entity_id": entity_id,
        "state": state.state if state else "missing",
        "name": state.attributes.get("friendly_name", entity_id)
        if state
        else entity_id,
        "power": None,
    }
    ent = er.async_get(hass).async_get(entity_id)
    if ent and ent.device_id:
        for other in er.async_entries_for_device(er.async_get(hass), ent.device_id):
            if other.domain != "sensor":
                continue
            st = hass.states.get(other.entity_id)
            if st and st.attributes.get("device_class") == "power":
                with contextlib.suppress(ValueError):
                    view["power"] = float(st.state)
                break
    return view


def _miner(hass: HomeAssistant, r) -> dict[str, Any]:
    t = r.telemetry
    return {
        "mac": r.mac,
        "name": r.name,
        "named": r.named,
        "place": r.rack,
        "index": r.index,
        "ip": r.ip,
        "model": r.model,
        "algorithm": r.algorithm,
        "online": r.online,
        "blocked": r.blocked,
        "block_synced": r.block_ip_synced,
        "status": r.hashrate_status,
        "temp_status": r.temp_status,
        "problems": r.problems,
        "error": r.error,
        "rate": t.rate_30m if t.rate_30m is not None else t.rate_5s,
        "rate_ideal": t.rate_ideal,
        "rate_unit": t.rate_unit,
        "temp_max": t.temp_max,
        "fan_min": t.fan_min,
        "fan_max": t.fan_max,
        "chips": [t.chip_ok, t.chip_total],
        "power": r.power,
        "power_measured": r.power_is_measured,
        "uptime_h": t.uptime_hours,
        "pool": t.pool_url,
        "pool_status": t.pool_status,
        "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        "plug": _plug_view(hass, r.plug),
    }


def _switch_entities(hass: HomeAssistant) -> list[dict[str, str]]:
    """Plugs one can link: switch entities, with their device name."""
    devs = dr.async_get(hass)
    out = []
    for ent in er.async_get(hass).entities.values():
        if ent.domain != "switch" or ent.platform == DOMAIN or ent.disabled:
            continue
        dev = devs.async_get(ent.device_id) if ent.device_id else None
        st = hass.states.get(ent.entity_id)
        name = (st.attributes.get("friendly_name") if st else None) or ent.entity_id
        out.append(
            {
                "entity_id": ent.entity_id,
                "name": name,
                "device": (dev.name_by_user or dev.name) if dev else "",
            }
        )
    return sorted(out, key=lambda x: x["name"])


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/overview"})
@callback
def ws_overview(hass: HomeAssistant, connection, msg: dict) -> None:
    fleets = []
    for entry, coord in _coordinators(hass):
        data = coord.data
        fleets.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "router": {
                    "online": bool(data and data.router_online),
                    "identity": data.router_identity if data else None,
                    "error": data.router_error if data else None,
                    "host": coord.router.host,
                },
                "allow_reboot": bool(entry.options.get(CONF_ALLOW_REBOOT, False)),
                "miners": [_miner(hass, r) for r in data.asics.values()]
                if data
                else [],
            }
        )
    connection.send_result(
        msg["id"], {"fleets": fleets, "plugs": _switch_entities(hass)}
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict) -> None:
    unsubs = []

    @callback
    def _changed() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], {"changed": True})
        )

    for _entry, coord in _coordinators(hass):
        unsubs.append(coord.async_add_listener(_changed))

    @callback
    def _unsub() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = _unsub
    connection.send_result(msg["id"])


def _find(hass: HomeAssistant, entry_id: str, mac: str):
    for entry, coord in _coordinators(hass):
        if entry.entry_id != entry_id:
            continue
        record = coord.record_for_mac(mac)
        if record is None:
            raise HomeAssistantError(f"{mac}: no such miner")
        return entry, coord, record
    raise HomeAssistantError("fleet not loaded")


ACTIONS = ["block", "unblock", "reboot", "blink", "power_cycle", "plug_on", "plug_off"]


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/action",
        vol.Required("entry_id"): str,
        vol.Required("mac"): str,
        vol.Required("action"): vol.In(ACTIONS),
    }
)
@websocket_api.async_response
async def ws_action(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        entry, coord, record = _find(hass, msg["entry_id"], msg["mac"])
        act = msg["action"]
        if act in ("block", "unblock"):
            await coord.async_set_blocked(record, act == "block")
        elif act == "reboot":
            if not entry.options.get(CONF_ALLOW_REBOOT, False):
                raise HomeAssistantError(
                    "Reboot is disabled: enable it in the integration options"
                )
            await coord.async_reboot(record)
        elif act == "blink":
            await coord.async_blink(record, True)
        elif act == "power_cycle":
            hass.async_create_background_task(
                coord.async_power_cycle(record), f"{DOMAIN} power cycle"
            )
        else:
            if not record.plug:
                raise HomeAssistantError(f"{record.name}: no smart plug linked")
            await hass.services.async_call(
                "homeassistant",
                "turn_on" if act == "plug_on" else "turn_off",
                {"entity_id": record.plug},
                blocking=True,
            )
    except Exception as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_plug",
        vol.Required("entry_id"): str,
        vol.Required("mac"): str,
        vol.Optional("plug", default=""): str,
    }
)
@websocket_api.async_response
async def ws_set_plug(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        _entry, coord, record = _find(hass, msg["entry_id"], msg["mac"])
        await coord.async_set_plug(record.mac, msg["plug"] or None)
    except Exception as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rename",
        vol.Required("entry_id"): str,
        vol.Required("mac"): str,
        vol.Required("name"): str,
        vol.Optional("place", default=""): str,
    }
)
@websocket_api.async_response
async def ws_rename(hass: HomeAssistant, connection, msg: dict) -> None:
    try:
        entry, _coord, record = _find(hass, msg["entry_id"], msg["mac"])
        aliases = dict(entry.options.get(CONF_ALIASES, {}) or {})
        alias: dict[str, str] = {"name": msg["name"].strip()}
        if msg["place"].strip():
            alias["rack"] = msg["place"].strip()
        aliases[record.mac] = alias
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ALIASES: aliases}
        )
    except Exception as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    for handler in (ws_overview, ws_subscribe, ws_action, ws_set_plug, ws_rename):
        websocket_api.async_register_command(hass, handler)
