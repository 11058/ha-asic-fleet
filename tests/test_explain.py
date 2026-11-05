"""Problem codes become sentences that say what actually broke."""

from __future__ import annotations

from conftest import load_module

explain = load_module("explain")
asic_api = load_module("asic_api")


def test_problem_texts() -> None:
    t = explain.problem_text
    assert t("fan", {"fan_rpm": [5400, 0, 5300, 5350], "fan_min": 1000}) == (
        "Вентилятор: обороты №2: 0 об/мин"
    )
    assert t("chips", {"chip_ok": 210, "chip_total": 228, "bad_chains": [2]}) == (
        "Не работают чипы: 18 из 228 (плата 2)"
    )
    assert "92" in t("overheat_critical", {"temp_max": 92})
    assert "таймаут" in t("offline", {"error": "10.5.64.5: нет ответа (таймаут)"})
    assert t(
        "hashrate_low", {"efficiency_pct": 61.0, "rate_5s": 5.5, "rate_ideal": 9.0}
    ) == ("Хэшрейт 61.0 % от номинала (5.5 из 9.0 TH/s)")
    assert t("something_new", {}) == "something_new"


def test_bad_chains_and_flags() -> None:
    assert explain.bad_chains(["oooo oooo", "ooxo oooo", "", "oooo"]) == [2]
    assert explain.miner_flags_text({"rate": "s", "fans": "e", "temp": "w"}) == [
        "вентиляторы: ошибка",
        "температура: предупреждение",
    ]


def test_stats_keep_chain_maps() -> None:
    tel = asic_api.AsicTelemetry()
    asic_api._parse_stats(
        {"STATS": [{"chain": [{"asic": "oo oo"}, {"asic": "ox oo"}], "fan": [100]}]},
        tel,
    )
    assert tel.chain_maps == ["oo oo", "ox oo"] and tel.chip_ok == 7
