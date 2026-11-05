"""Plain-language reasons for a miner's problems (pure, no HA imports).

The coordinator raises problem codes with a details dict; the panel and the
event log need to say what actually happened: which fan, how hot, how many
chips are gone, what the miner itself reports.
"""

from __future__ import annotations

from typing import Any

# Antminer summary.cgi "status": [{"type": "fans", "status": "e"}, ...]
FLAG_NAMES = {
    "rate": "хэшрейт",
    "network": "сеть",
    "fans": "вентиляторы",
    "temp": "температура",
    "pool": "пул",
}
FLAG_STATES = {"e": "ошибка", "w": "предупреждение", "f": "ошибка"}


def _fmt_rate(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{v:.1f}" if v < 100 else f"{v:.0f}"


def problem_text(code: str, details: dict[str, Any] | None) -> str:
    d = details or {}
    if code == "offline":
        why = d.get("error") or "майнер не отвечает"
        since = d.get("last_seen")
        tail = f"; последний ответ {since[:16].replace('T', ' ')}" if since else ""
        return f"Нет связи: {why}{tail}"
    if code == "overheat_critical":
        return f"Критический перегрев: {d.get('temp_max')} °C на чипах"
    if code == "overheat":
        return f"Перегрев: {d.get('temp_max')} °C на чипах"
    if code == "hashrate_zero":
        return "Хэшрейт 0: майнер не считает (пул, плата или питание)"
    if code == "hashrate_low":
        return (
            f"Хэшрейт {d.get('efficiency_pct')} % от номинала "
            f"({_fmt_rate(d.get('rate_5s'))} из {_fmt_rate(d.get('rate_ideal'))} TH/s)"
        )
    if code == "fan":
        rpm = d.get("fan_rpm") or []
        bad = [
            f"№{i + 1}: {r}" for i, r in enumerate(rpm) if r < (d.get("fan_min") or 1)
        ]
        listed = ", ".join(bad) if bad else ", ".join(str(r) for r in rpm)
        return f"Вентилятор: обороты {listed} об/мин"
    if code == "chips":
        ok, total = d.get("chip_ok"), d.get("chip_total")
        lost = (total or 0) - (ok or 0)
        chains = d.get("bad_chains") or []
        where = f" (плата {', '.join(str(c) for c in chains)})" if chains else ""
        return f"Не работают чипы: {lost} из {total}{where}"
    if code == "hw_errors":
        return f"Аппаратные ошибки: {d.get('hw_error_pct')} %"
    if code == "block_desync":
        return "Блокировка не применилась на роутере: адрес в списке не совпадает"
    if code == "ip_changed":
        return f"Сменил адрес: {d.get('from')} → {d.get('to')}"
    if code == "unnamed":
        return "Без имени: подпишите место и номер"
    return code


def miner_flags_text(flags: dict[str, Any]) -> list[str]:
    """What the miner itself reports as not fine."""
    out = []
    for key, state in (flags or {}).items():
        word = FLAG_STATES.get(str(state).lower())
        if word:
            out.append(f"{FLAG_NAMES.get(key, key)}: {word}")
    return out


def bad_chains(chain_maps: list[str]) -> list[int]:
    """1-based numbers of hash boards with a dead chip ('x' in the asic map)."""
    return [
        i + 1
        for i, chips in enumerate(chain_maps)
        if chips and any(c != "o" for c in chips.replace(" ", ""))
    ]
