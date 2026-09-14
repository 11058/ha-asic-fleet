"""Power figures for firmware that reports none.

The live fleet is the case that matters: 48 Promminer L7s report no power at
all, six L9s do, and a total built only on measurements said 20 kW where the
farm draws nearer 200 kW.
"""

from __future__ import annotations

import pytest
from conftest import load_module

power = load_module("power")
asic_api = load_module("asic_api")
AsicTelemetry = asic_api.AsicTelemetry


class TestNominal:
    def test_known_models(self) -> None:
        assert power.nominal_power("Antminer L7") == 3425
        assert power.nominal_power("Antminer L9") == 3450

    def test_longest_prefix_wins(self) -> None:
        # "Antminer S21+" must not be matched by the "Antminer S21" entry.
        assert power.nominal_power("Antminer S21+") == 3510
        assert power.nominal_power("Antminer S21") == 3500

    def test_unknown_model_is_not_guessed(self) -> None:
        assert power.nominal_power("Whatsminer M50") is None
        assert power.nominal_power(None) is None


class TestEstimate:
    def test_scales_with_how_hard_it_is_hashing(self) -> None:
        tel = AsicTelemetry(reachable=True, rate_5s=4809.0, rate_ideal=9618.11)
        assert power.estimate_power("Antminer L7", tel) == pytest.approx(1712, abs=2)

    def test_full_rate_draws_nominal(self) -> None:
        tel = AsicTelemetry(reachable=True, rate_5s=9618.11, rate_ideal=9618.11)
        assert power.estimate_power("Antminer L7", tel) == pytest.approx(3425, abs=2)

    def test_stopped_miner_is_not_zero(self) -> None:
        # Fans and control board keep running even with the chips idle.
        tel = AsicTelemetry(reachable=True, rate_5s=0.0, rate_ideal=9618.11)
        assert power.estimate_power("Antminer L7", tel) == pytest.approx(514, abs=2)

    def test_overclocked_is_capped(self) -> None:
        tel = AsicTelemetry(reachable=True, rate_5s=99999.0, rate_ideal=9618.11)
        assert power.estimate_power("Antminer L7", tel) == pytest.approx(3939, abs=2)

    def test_unreachable_miner_has_no_estimate(self) -> None:
        assert (
            power.estimate_power("Antminer L7", AsicTelemetry(reachable=False)) is None
        )

    def test_unknown_model_has_no_estimate(self) -> None:
        tel = AsicTelemetry(reachable=True, rate_5s=100.0, rate_ideal=100.0)
        assert power.estimate_power("Whatsminer M50", tel) is None

    def test_missing_ideal_rate_falls_back_to_nominal(self) -> None:
        tel = AsicTelemetry(reachable=True, rate_5s=9000.0, rate_ideal=None)
        assert power.estimate_power("Antminer L7", tel) == 3425.0


class TestFleetTotal:
    def test_mixed_fleet_reaches_a_realistic_total(self) -> None:
        """48 L7 + 6 L9 + 1 S21+, all hashing at nominal, is ~190 kW."""
        total = 0.0
        for model, count, ideal in (
            ("Antminer L7", 48, 9618.11),
            ("Antminer L9", 6, 16500.0),
            ("Antminer S21+", 1, 241012950.0),
        ):
            tel = AsicTelemetry(reachable=True, rate_5s=ideal, rate_ideal=ideal)
            total += (power.estimate_power(model, tel) or 0) * count
        assert 180_000 < total < 200_000
