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
# Entity-id form (r1_asic7) and display form (R1-ASIC7) of the same name.
NAMED = re.compile(r"^r(\d+)_asic(\d+)$")
NAMED_DISPLAY = re.compile(r"^R(\d+)-ASIC(\d+)$", re.IGNORECASE)


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


# Status -> colour. Deliberately readable in both HA themes: these are the
# Material palette's 700 shades, which keep white text legible.
HASHRATE_COLORS = {
    "ok": "#2e7d32",
    "low": "#f9a825",
    "off": "#5d4037",
    "blocked": "#1565c0",
    "unreachable": "#546e7a",
    "error": "#c62828",
}
TEMPERATURE_COLORS = {
    "ok": "#2e7d32",
    "warning": "#f9a825",
    "critical": "#c62828",
    "unknown": "#546e7a",
}

# Shelves hold eight machines, so the readouts are laid out eight to a row and
# the grid maps onto what someone sees when they walk down the aisle.
PER_ROW = 8


def _bubble_cell(
    value_entity: str, status_entity: str, label: str, colors: dict[str, str]
) -> dict:
    """One coloured cell: the number, tinted by its status entity."""
    mapping = ", ".join(f"'{k}': '{v}'" for k, v in colors.items())
    return {
        "type": "custom:bubble-card",
        "card_type": "button",
        "button_type": "state",
        "entity": value_entity,
        "name": label,
        "show_state": True,
        "show_icon": False,
        "card_layout": "large",
        "tap_action": {"action": "more-info"},
        # bubble-card evaluates ${...} inside `styles` as JavaScript. Keeping
        # this to plain CSS with a single interpolation is the form least
        # likely to break across card versions.
        "styles": (
            ".bubble-button-background {\n"
            "  opacity: 1 !important;\n"
            "  background: ${"
            f"({{{mapping}}})[(hass.states['{status_entity}'] || {{}}).state]"
            " || '#546e7a'} !important;\n"
            "}\n"
            ".bubble-name, .bubble-state {\n"
            "  color: #fff !important;\n"
            "  font-weight: 600;\n"
            "}\n"
        ),
    }


def _stock_cell(
    value_entity: str, status_entity: str, label: str, colors: dict[str, str]
) -> dict:
    """Fallback with built-in cards only: no colour, just the number."""
    return {
        "type": "tile",
        "entity": value_entity,
        "name": label,
        "hide_state": False,
        "vertical": True,
        "grid_options": {"columns": 3, "rows": 1},
    }


def readout_grid(
    entities: list[tuple[str, str, str]], colors: dict[str, str], title: str, cards: str
) -> list[dict]:
    """A titled grid of coloured cells, eight to a row."""
    build = _bubble_cell if cards == "bubble" else _stock_cell
    cells = [build(value, status, label, colors) for value, status, label in entities]
    return [
        heading(title, "subtitle", "mdi:view-grid-outline"),
        {"type": "grid", "columns": PER_ROW, "square": False, "cards": cells},
    ]


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


def _from_slug(slug: str) -> str:
    """Readable name for a miner we only know by its entity id."""
    m = NAMED.match(slug)
    return f"R{m[1]}-ASIC{m[2]}" if m else f"ASIC {slug.replace('asic_', '').upper()}"


def index_miners(registry: list[dict]) -> dict[str, dict[str, str]]:
    """mac -> {sensor key: entity_id}, read from unique ids.

    Entity ids get renamed and miners get renamed; unique ids do not. They are
    shaped `asic_fleet_<MAC>_<key>`, and a MAC contains no underscores, so the
    split is unambiguous. Deriving one entity's id from another's — the obvious
    shortcut — breaks the moment two of a miner's entities were created while
    it had different names.
    """
    miners: dict[str, dict[str, str]] = {}
    for entry in registry:
        unique = entry.get("unique_id") or ""
        if not unique.startswith(f"{DOMAIN}_"):
            continue
        mac, _, key = unique[len(DOMAIN) + 1 :].partition("_")
        if ":" not in mac or not key:
            continue  # fleet-level entity, keyed by config entry id
        miners.setdefault(mac.upper(), {})[key] = entry["entity_id"]
    return miners


def build_view(
    entity_ids: set[str],
    rack: str = "R1",
    lang: str = "ru",
    cards: str = "bubble",
    miners: dict[str, dict[str, str]] | None = None,
    names: dict[str, str] | None = None,
) -> dict:
    t = TEXT[lang]
    slugs = sorted(
        e.split(".", 1)[1][: -len("_internet")]
        for e in entity_ids
        if e.startswith("switch.") and e.endswith("_internet")
    )

    # Entity ids and display names both drift as miners get renamed, so every
    # lookup below goes through the registry map keyed by MAC instead.
    by_slug: dict[str, dict[str, str]] = {}
    name_by_slug: dict[str, str] = {}
    for mac, entities in (miners or {}).items():
        switch = entities.get("internet")
        if not switch:
            continue
        slug = switch.split(".", 1)[1][: -len("_internet")]
        by_slug[slug] = entities
        if names and names.get(mac):
            name_by_slug[slug] = names[mac]

    def display(slug: str) -> str:
        return name_by_slug.get(slug) or _from_slug(slug)

    def _match(slug: str):
        # A miner renamed after its entities were created keeps an entity id
        # from its old name, so judge by what it is called now — and the two
        # forms are spelled differently: r1_asic7 versus R1-ASIC7.
        return NAMED_DISPLAY.match(display(slug)) or NAMED.match(slug)

    named = sorted(
        (s for s in slugs if _match(s)),
        key=lambda s: (int(_match(s)[1]), int(_match(s)[2])),
    )
    unnamed = [s for s in slugs if s not in named]
    ordered = named + unnamed

    def has(eid: str) -> bool:
        return eid in entity_ids

    def short(slug: str) -> str:
        m = _match(slug)
        return m[2] if m else slug.replace("asic_", "").upper()

    def collect(domain: str, key: str) -> list[str]:
        found = []
        for slug in ordered:
            eid = by_slug.get(slug, {}).get(key)
            if eid and eid.startswith(f"{domain}."):
                found.append(eid)
            elif has(f"{domain}.{slug}_{key}"):
                found.append(f"{domain}.{slug}_{key}")
        return found

    summary = [heading(t["fleet"], "title", "mdi:server-network")]
    for suffix, name, color in (
        ("fleet_hashrate", t["hashrate"], "blue"),
        ("fleet_power", t["power"], "purple"),
        ("miners_online", t["online"], "green"),
        ("miners_blocked", t["blocked"], "red"),
        ("miners_offline", t["no_link"], "orange"),
        ("miners_with_problems", t["problems"], "amber"),
        ("miners_with_errors", t["errors"], "red"),
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
            collect("sensor", "temperature_status"),
            [{"condition": "state", "state": ["warning", "critical"]}],
            "mdi:thermometer-alert",
        ),
        entity_filter(
            t["not_hashing"],
            collect("sensor", "hashrate_status"),
            [{"condition": "state", "state": ["off", "error", "low"]}],
            "mdi:speedometer-slow",
        ),
        entity_filter(
            t["desync"],
            collect("binary_sensor", "block_desync"),
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
        action_button(t["discover"], "mdi:radar", f"{DOMAIN}.discover", {}, None),
        action_button(
            t["reset"],
            "mdi:delete-sweep",
            f"{DOMAIN}.reset",
            {"keep_manual": True},
            t["confirm_reset"],
        ),
    ]

    def cells(key: str, status_key: str) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for slug in ordered:
            entities = by_slug.get(slug, {})
            value, status = entities.get(key), entities.get(status_key)
            if value and status:
                out.append((value, status, short(slug)))
        return out

    readouts = readout_grid(
        cells("hashrate", "hashrate_status"), HASHRATE_COLORS, t["hashrate_mhs"], cards
    )
    temperatures = readout_grid(
        cells("temp_max", "temperature_status"),
        TEMPERATURE_COLORS,
        t["temp_c"],
        cards,
    )

    switches = [heading(t["per_miner"], "subtitle", "mdi:toggle-switch-outline")]
    switches += [
        {
            "type": "tile",
            "entity": by_slug.get(s, {}).get("internet", f"switch.{s}_internet"),
            "name": display(s),
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
        {"type": "grid", "cards": readouts, "column_span": 2},
        {"type": "grid", "cards": temperatures, "column_span": 2},
        {"type": "grid", "cards": switches, "column_span": 2},
    ]

    if unnamed:
        cards = [heading(t["unnamed"], "subtitle", "mdi:help-network-outline")]
        for s in unnamed:
            cards.append(
                {
                    "type": "entities",
                    "title": display(s),
                    "state_color": True,
                    "entities": [
                        e
                        for e in (
                            by_slug.get(s, {}).get(key)
                            for key in (
                                "internet",
                                "ip_address",
                                "switch_port",
                                "status",
                                "hashrate",
                                "temp_max",
                            )
                        )
                        if e
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
        "errors": "Ошибки",
        "not_hashing": "Не хешируют",
        "discover": "Найти майнеры",
        "reset": "Сбросить список",
        "confirm_reset": (
            "Забыть все найденные майнеры и искать заново? "
            "Добавленные вручную сохранятся."
        ),
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
        "errors": "Errors",
        "not_hashing": "Not hashing",
        "discover": "Find miners",
        "reset": "Reset inventory",
        "confirm_reset": (
            "Forget every discovered miner and search again? Hand-added ones are kept."
        ),
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
        "--cards",
        default="bubble",
        choices=("bubble", "stock"),
        help="bubble: coloured cells via custom:bubble-card (HACS). "
        "stock: built-in cards only, no colour.",
    )
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
        mine = [e for e in registry if e.get("config_entry_id") in entry_ids]
        entity_ids = {e["entity_id"] for e in mine}
        miners = index_miners(mine)

        devices = await client.call({"type": "config/device_registry/list"})
        device_name = {
            d["id"]: (d.get("name_by_user") or d.get("name") or "") for d in devices
        }
        names: dict[str, str] = {}
        for entry in mine:
            unique = entry.get("unique_id") or ""
            mac, _, _key = unique[len(DOMAIN) + 1 :].partition("_")
            if ":" in mac and entry.get("device_id"):
                names.setdefault(mac.upper(), device_name.get(entry["device_id"], ""))

        view = build_view(
            entity_ids,
            rack=args.rack,
            lang=args.lang,
            cards=args.cards,
            miners=miners,
            names=names,
        )

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
