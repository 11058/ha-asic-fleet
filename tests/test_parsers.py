"""Parser tests against payloads captured from a live Antminer L7.

Fixtures came off a production machine running Promminer_L7_7007 (worker name
and MAC scrubbed). If the firmware ever changes shape, these fail loudly rather
than quietly reporting None for every metric.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import load_module

asic_api = load_module("asic_api")
AsicTelemetry = asic_api.AsicTelemetry
_parse_pools = asic_api._parse_pools
_parse_stats = asic_api._parse_stats
_parse_summary = asic_api._parse_summary

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def telemetry() -> AsicTelemetry:
    out = AsicTelemetry()
    _parse_summary(load("summary_l7.json"), out)
    _parse_stats(load("stats_l7.json"), out)
    _parse_pools(load("pools_l7.json"), out)
    return out


def test_hashrate(telemetry: AsicTelemetry) -> None:
    assert telemetry.rate_5s == 9261.02
    assert telemetry.rate_30m == 9731.66
    assert telemetry.rate_avg == 9690.48
    assert telemetry.rate_ideal == 9618.11
    assert telemetry.rate_unit == "MH/s"
    assert telemetry.efficiency == pytest.approx(0.9629, abs=1e-4)


def test_identity_comes_from_summary_info(telemetry: AsicTelemetry) -> None:
    assert telemetry.model == "Antminer L7"
    assert telemetry.firmware == "81.0-1.0.0"


def test_uptime_and_errors(telemetry: AsicTelemetry) -> None:
    assert telemetry.elapsed == 455471
    assert telemetry.uptime_hours == pytest.approx(126.52, abs=0.01)
    assert telemetry.hw_all == 6596
    assert telemetry.hw_error_pct == 0.02
    assert telemetry.status_flags == {
        "rate": "s",
        "network": "s",
        "fans": "s",
        "temp": "s",
    }


def test_thermals_and_fans(telemetry: AsicTelemetry) -> None:
    assert telemetry.fan_rpm == [5880, 6000, 5760, 5760]
    assert telemetry.fan_min == 5760
    assert telemetry.fan_max == 6000
    assert telemetry.temp_min == 54
    assert telemetry.temp_max == 79
    # Mean over every chip temperature on every chain, not a mean of maxima.
    assert telemetry.temp_avg == 67


def test_chip_map(telemetry: AsicTelemetry) -> None:
    assert telemetry.chains == 3
    assert telemetry.chip_total == 360
    assert telemetry.chip_ok == 360
    assert telemetry.chip_health == 1.0
    assert len(telemetry.chain_sn) == 3


def test_dead_chip_is_counted() -> None:
    stats = load("stats_l7.json")
    chain = stats["STATS"][0]["chain"][1]
    chain["asic"] = chain["asic"].replace("o", "x", 1)
    out = AsicTelemetry()
    _parse_stats(stats, out)
    assert out.chip_ok == 359
    assert out.chip_total == 360
    assert out.chip_health == pytest.approx(0.9972, abs=1e-4)


def test_active_pool_is_the_lowest_priority_alive_one(
    telemetry: AsicTelemetry,
) -> None:
    assert telemetry.pool_url == "stratum+tcp://ltc.trustpool.cc:3333"
    assert telemetry.pool_status == "Alive"
    assert telemetry.accepted == 64467
    assert telemetry.rejected == 48


def test_pool_priority_beats_list_order() -> None:
    pools = load("pools_l7.json")
    pools["POOLS"][0]["status"] = "Dead"
    out = AsicTelemetry()
    _parse_pools(pools, out)
    assert out.pool_url == "stratum+tcp://ltc.trustpool.cc:443"


def test_garbage_payloads_do_not_raise() -> None:
    out = AsicTelemetry()
    for payload in ({}, {"SUMMARY": []}, {"STATS": [{}]}, [], None, "nope"):
        _parse_summary(payload, out)
        _parse_stats(payload, out)
        _parse_pools(payload, out)
    assert out.rate_5s is None


class TestL9:
    """An Antminer L9 on stock firmware — a different unit and extra fields."""

    @pytest.fixture
    def telemetry(self) -> AsicTelemetry:
        out = AsicTelemetry()
        _parse_summary(load("summary_l9.json"), out)
        _parse_stats(load("stats_l9.json"), out)
        _parse_pools(load("pools_l9.json"), out)
        return out

    def test_ghs_is_normalised_to_mhs(self, telemetry: AsicTelemetry) -> None:
        # The miner reports 17.82 GH/s; a fleet total may not add that to an
        # L7's MH/s figure without converting first.
        assert telemetry.reported_unit == "GH/s"
        assert telemetry.rate_unit == "MH/s"
        assert telemetry.rate_5s == 17820.0
        assert telemetry.rate_30m == 16200.0
        assert telemetry.rate_avg == 16320.0
        assert telemetry.rate_ideal == 16500.0
        assert telemetry.efficiency == pytest.approx(1.08, abs=0.01)

    def test_power_is_read(self, telemetry: AsicTelemetry) -> None:
        assert telemetry.power == 3236

    def test_model_and_thermals(self, telemetry: AsicTelemetry) -> None:
        assert telemetry.model == "Antminer L9"
        assert telemetry.chains == 3
        assert telemetry.chip_total == 330
        assert telemetry.chip_ok == 330
        assert telemetry.fan_rpm == [3830, 3840, 3760, 3840]
        assert telemetry.temp_max == 82
        assert telemetry.hw_error_pct == 0.8841

    def test_l7_power_is_absent_not_zero(self) -> None:
        out = AsicTelemetry()
        _parse_stats(load("stats_l7.json"), out)
        assert out.power is None
