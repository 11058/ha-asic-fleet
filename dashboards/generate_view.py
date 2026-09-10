#!/usr/bin/env python3
"""Build (and optionally install) a Lovelace view for the ASIC Fleet integration.

Reads the entity registry over Home Assistant's websocket API, so the view is
generated from what actually exists rather than from a hand-maintained list —
add a miner and re-run.

    pip install websockets
    export HA_URL=http://homeassistant.local:8123
    export HA_TOKEN=<long-lived access token>

    python generate_view.py                       # print the view as JSON
    python generate_view.py --dashboard lovelace  # install it as a new tab

The view uses only built-in cards and the `sections` layout, which reflows from
four columns on a laptop to one on a phone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

try:
    import websockets
except ImportError:  # pragma: no cover
    sys.exit("pip install websockets")

DOMAIN = "asic_fleet"
VIEW_PATH = "asic-fleet"
NAMED = re.compile(r"^r(\d+)_asic(\d+)$")


# --- Home Assistant websocket ------------------------------------------------


class Client:
    def __init__(self, url: str, token: str) -> None:
        self._url = (
            url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
            + "/api/websocket"
        )
        self._token = token
        self._id = 0

    async def __aenter__(self) -> Client:
        self._ws = await websockets.connect(self._url, max_size=40_000_000)
        await self._ws.recv()
        await self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        reply = json.loads(await self._ws.recv())
        if reply.get("type") != "auth_ok":
            raise SystemExit(f"authentication failed: {reply}")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._ws.close()

    async def call(self, payload: dict) -> dict:
        self._id += 1
        payload["id"] = self._id
        await self._ws.send(json.dumps(payload))
        while True:
            message = json.loads(await self._ws.recv())
            if message.get("id") == self._id and message["type"] == "result":
                if not message.get("success"):
                    raise SystemExit(
                        f"{payload['type']} failed: {message.get('error')}"
                    )
                return message["result"]


# --- view ---------------------------------------------------------------------


def heading(text: str, style: str = "subtitle", icon: str | None = None) -> dict:
    card = {"type": "heading", "heading": text, "heading_style": style}
    if icon:
        card["icon"] = icon
    return card


def tile(entity: str, name: str, color: str) -> dict:
    return {
        "type": "tile",
        "entity": entity,
        "name": name,
        "color": color,
        "vertical": True,
        "grid_options": {"columns": 3, "rows": 1},
    }


def entity_filter(
    title: str, entities: list[str], conditions: list[dict], icon: str
) -> dict:
    return {
        "type": "entity-filter",
        "entities": entities,
        "conditions": conditions,
        "show_empty": False,
        "card": {"type": "entities", "title": title, "icon": icon, "state_color": True},
    }


def action_button(
    name: str, icon: str, action: str, data: dict, confirm: str | None
) -> dict:
    tap: dict = {"action": "perform-action", "perform_action": action, "data": data}
    if confirm:
        tap["confirmation"] = {"text": confirm}
    return {
        "type": "button",
        "name": name,
        "icon": icon,
        "show_state": False,
        "grid_options": {"columns": 6, "rows": 1},
        "tap_action": tap,
    }


def build_view(entity_ids: set[str], rack: str = "R1", lang: str = "ru") -> dict:
    t = TEXT[lang]
    slugs = sorted(
        e.split(".", 1)[1][: -len("_internet")]
        for e in entity_ids
        if e.startswith("switch.") and e.endswith("_internet")
    )
    named = sorted(
        (s for s in slugs if NAMED.match(s)),
        key=lambda s: (int(NAMED.match(s)[1]), int(NAMED.match(s)[2])),
    )
    unnamed = [s for s in slugs if s not in named]
    ordered = named + unnamed

    def has(eid: str) -> bool:
        return eid in entity_ids

    def full_name(slug: str) -> str:
        m = NAMED.match(slug)
        return (
            f"R{m[1]}-ASIC{m[2]}" if m else f"ASIC {slug.replace('asic_', '').upper()}"
        )

    def short(slug: str) -> str:
        m = NAMED.match(slug)
        return m[2] if m else slug.replace("asic_", "").upper()

    def collect(domain: str, suffix: str) -> list[str]:
        return [
            f"{domain}.{s}_{suffix}" for s in ordered if has(f"{domain}.{s}_{suffix}")
        ]

    summary = [heading(t["fleet"], "title", "mdi:server-network")]
    for suffix, name, color in (
        ("fleet_hashrate", t["hashrate"], "blue"),
        ("fleet_power", t["power"], "purple"),
        ("miners_online", t["online"], "green"),
        ("miners_blocked", t["blocked"], "red"),
        ("miners_offline", t["no_link"], "orange"),
        ("miners_with_problems", t["problems"], "amber"),
        ("fleet_temperature_max", t["temp_max"], "deep-orange"),
    ):
        eid = f"sensor.{DOMAIN}_{suffix}"
        if has(eid):
            summary.append(tile(eid, name, color))
    router = f"binary_sensor.{DOMAIN}_router_online"
    if has(router):
        summary.append(tile(router, t["router"], "teal"))

    attention = [
        heading(t["attention"], "subtitle", "mdi:alert-decagram"),
        entity_filter(
            t["no_link"],
            collect("binary_sensor", "online"),
            [{"condition": "state", "state": "off"}],
            "mdi:lan-disconnect",
        ),
        entity_filter(
            t["problems"],
            collect("binary_sensor", "problem"),
            [{"condition": "state", "state": "on"}],
            "mdi:alert",
        ),
        entity_filter(
            t["hot"],
            collect("sensor", "temperature_max"),
            [{"condition": "numeric_state", "above": 88}],
            "mdi:thermometer-alert",
        ),
        entity_filter(
            t["desync"],
            collect("binary_sensor", "block_out_of_sync"),
            [{"condition": "state", "state": "on"}],
            "mdi:shield-alert",
        ),
    ]

    control = [
        heading(t["control"].format(rack=rack), "subtitle", "mdi:tune-vertical"),
        action_button(
            t["block_rack"].format(rack=rack),
            "mdi:lan-disconnect",
            f"{DOMAIN}.block_rack",
            {"rack": rack},
            t["confirm_block"].format(rack=rack),
        ),
        action_button(
            t["unblock_rack"].format(rack=rack),
            "mdi:lan-connect",
            f"{DOMAIN}.unblock_rack",
            {"rack": rack},
            t["confirm_unblock"].format(rack=rack),
        ),
        action_button(
            t["reboot_rack"].format(rack=rack),
            "mdi:restart-alert",
            f"{DOMAIN}.reboot_rack",
            {"rack": rack, "stagger": 5},
            t["confirm_reboot"].format(rack=rack),
        ),
        action_button(t["refresh"], "mdi:refresh", f"{DOMAIN}.refresh", {}, None),
    ]

    def glance(title: str, suffix: str) -> dict:
        return {
            "type": "glance",
            "title": title,
            "columns": 4,
            "show_name": True,
            "show_state": True,
            "show_icon": False,
            "entities": [
                {"entity": f"sensor.{s}_{suffix}", "name": short(s)}
                for s in ordered
                if has(f"sensor.{s}_{suffix}")
            ],
        }

    readouts = [
        heading(t["readouts"], "subtitle", "mdi:chart-box-outline"),
        glance(t["hashrate_mhs"], "hashrate"),
        glance(t["temp_c"], "temperature_max"),
    ]

    switches = [heading(t["per_miner"], "subtitle", "mdi:toggle-switch-outline")]
    switches += [
        {
            "type": "tile",
            "entity": f"switch.{s}_internet",
            "name": full_name(s),
            "grid_options": {"columns": 6, "rows": 1},
            # Tapping the card opens details; only the icon toggles, so a
            # mis-tap on a phone cannot silently stop a miner.
            "tap_action": {"action": "more-info"},
            "icon_tap_action": {"action": "toggle"},
        }
        for s in ordered
    ]

    sections = [
        {"type": "grid", "cards": summary},
        {"type": "grid", "cards": attention},
        {"type": "grid", "cards": control},
        {"type": "grid", "cards": readouts},
        {"type": "grid", "cards": switches, "column_span": 2},
    ]

    if unnamed:
        cards = [heading(t["unnamed"], "subtitle", "mdi:help-network-outline")]
        for s in unnamed:
            cards.append(
                {
                    "type": "entities",
                    "title": full_name(s),
                    "state_color": True,
                    "entities": [
                        e
                        for e in (
                            f"switch.{s}_internet",
                            f"sensor.{s}_ip_address",
                            f"sensor.{s}_switch_port",
                            f"sensor.{s}_status",
                            f"sensor.{s}_hashrate",
                            f"sensor.{s}_temperature_max",
                        )
                        if has(e)
                    ],
                }
            )
        sections.append({"type": "grid", "cards": cards})

    return {
        "type": "sections",
        "max_columns": 4,
        "title": t["fleet"],
        "path": VIEW_PATH,
        "icon": "mdi:pickaxe",
        "sections": sections,
    }


TEXT = {
    "ru": {
        "fleet": "ASIC Fleet",
        "hashrate": "Хешрейт",
        "power": "Мощность",
        "online": "В работе",
        "blocked": "Блок",
        "no_link": "Нет связи",
        "problems": "Проблемы",
        "temp_max": "Макс °C",
        "router": "Роутер",
        "attention": "Требуют внимания",
        "hot": "Горячее 88 °C",
        "desync": "Блокировка не применилась",
        "control": "Управление стойкой {rack}",
        "block_rack": "Заблокировать {rack}",
        "unblock_rack": "Разблокировать {rack}",
        "reboot_rack": "Перезагрузить {rack}",
        "refresh": "Обновить данные",
        "confirm_block": "Отрезать интернет ВСЕМ майнерам стойки {rack}?",
        "confirm_unblock": "Вернуть интернет всем майнерам стойки {rack}?",
        "confirm_reboot": "Перезагрузить ВСЕ майнеры стойки {rack} с паузой 5 с?",
        "readouts": "Хешрейт и температура",
        "hashrate_mhs": "Хешрейт, MH/s",
        "temp_c": "Температура, °C",
        "per_miner": "Интернет по майнерам",
        "unnamed": "Без имени — задать через asic_fleet.assign_name",
    },
    "en": {
        "fleet": "ASIC Fleet",
        "hashrate": "Hashrate",
        "power": "Power",
        "online": "Online",
        "blocked": "Blocked",
        "no_link": "Offline",
        "problems": "Problems",
        "temp_max": "Max °C",
        "router": "Router",
        "attention": "Needs attention",
        "hot": "Above 88 °C",
        "desync": "Block not applied",
        "control": "Rack {rack}",
        "block_rack": "Block {rack}",
        "unblock_rack": "Unblock {rack}",
        "reboot_rack": "Reboot {rack}",
        "refresh": "Refresh",
        "confirm_block": "Cut internet for EVERY miner in rack {rack}?",
        "confirm_unblock": "Restore internet for every miner in rack {rack}?",
        "confirm_reboot": "Reboot EVERY miner in rack {rack}, 5s apart?",
        "readouts": "Hashrate and temperature",
        "hashrate_mhs": "Hashrate, MH/s",
        "temp_c": "Temperature, °C",
        "per_miner": "Internet per miner",
        "unnamed": "Unnamed — set with asic_fleet.assign_name",
    },
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", help="url_path of the dashboard to install into")
    parser.add_argument("--rack", default="R1")
    parser.add_argument("--lang", default="ru", choices=sorted(TEXT))
    parser.add_argument(
        "--url", default=os.environ.get("HA_URL", "http://homeassistant.local:8123")
    )
    parser.add_argument("--token", default=os.environ.get("HA_TOKEN", ""))
    args = parser.parse_args()
    if not args.token:
        sys.exit("set HA_TOKEN (a long-lived access token) or pass --token")

    async with Client(args.url, args.token) as client:
        entries = await client.call({"type": "config_entries/get", "domain": DOMAIN})
        entry_ids = {e["entry_id"] for e in entries if e.get("domain") == DOMAIN}
        if not entry_ids:
            sys.exit(f"no {DOMAIN} config entry found")
        registry = await client.call({"type": "config/entity_registry/list"})
        entity_ids = {
            e["entity_id"] for e in registry if e.get("config_entry_id") in entry_ids
        }
        view = build_view(entity_ids, rack=args.rack, lang=args.lang)

        if not args.dashboard:
            print(json.dumps(view, ensure_ascii=False, indent=2))
            return

        config = await client.call(
            {"type": "lovelace/config", "url_path": args.dashboard}
        )
        views = [v for v in config.get("views", []) if v.get("path") != VIEW_PATH]
        replaced = len(config.get("views", [])) != len(views)
        config["views"] = [*views, view]
        await client.call(
            {
                "type": "lovelace/config/save",
                "url_path": args.dashboard,
                "config": config,
            }
        )
        cards = sum(len(s["cards"]) for s in view["sections"])
        print(
            f"{'replaced' if replaced else 'added'} view '{VIEW_PATH}' on "
            f"{args.dashboard}: {len(view['sections'])} sections, {cards} cards"
        )


if __name__ == "__main__":
    asyncio.run(main())
