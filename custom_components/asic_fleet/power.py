"""Wall-power figures for miners whose firmware does not report any.

Only newer Bitmain firmware exposes `power` in stats.cgi. An L7 on Promminer
never does, so a fleet total built purely on measurements counts a handful of
machines and reports a fraction of the real draw — 20 kW where the farm pulls
nearer 200 kW.

Where the firmware is silent the draw is estimated from the model's nominal
consumption, scaled by how hard the machine is actually hashing. An estimate is
labelled as one everywhere it surfaces: it is a planning number, not a meter.
"""

from __future__ import annotations

# Nominal wall power at stock settings, in watts, by model as the miner
# reports it in `minertype`. Matching is by prefix, longest first, so
# "Antminer S21+" wins over "Antminer S21".
NOMINAL_POWER_W: dict[str, int] = {
    "Antminer L3": 800,
    "Antminer L7": 3425,
    "Antminer L9": 3450,
    "Antminer D9": 2839,
    "Antminer E9": 2556,
    "Antminer K7": 3080,
    "Antminer KS3": 3188,
    "Antminer KS5": 3150,
    "Antminer S19 XP": 3010,
    "Antminer S19j": 3050,
    "Antminer S19": 3250,
    "Antminer S21+": 3510,
    "Antminer S21": 3500,
    "Antminer T21": 3610,
}

# A miner hashing above its nominal rate still draws roughly nominal power, and
# one that has stopped is not at zero either — the fans and control board keep
# running. These bounds keep a wild hashrate reading from producing a wild
# power figure.
MIN_RATIO = 0.15
MAX_RATIO = 1.15


def nominal_power(model: str | None) -> int | None:
    """Catalogue power for a model, or None when the model is unknown."""
    if not model:
        return None
    name = model.strip()
    for candidate in sorted(NOMINAL_POWER_W, key=len, reverse=True):
        if name.lower().startswith(candidate.lower()):
            return NOMINAL_POWER_W[candidate]
    return None


def estimate_power(model: str | None, telemetry: object) -> float | None:
    """Estimated wall power, or None when there is nothing to base it on.

    Returns None rather than a guess for an unrecognised model: a fleet total
    that silently invents watts is worse than one that says how much it could
    not account for.
    """
    nominal = nominal_power(model)
    if nominal is None:
        return None
    if not getattr(telemetry, "reachable", False):
        return None

    rate = getattr(telemetry, "rate_5s", None)
    ideal = getattr(telemetry, "rate_ideal", None)
    if rate is None or not ideal:
        return float(nominal)

    ratio = min(max(rate / ideal, MIN_RATIO), MAX_RATIO)
    return round(nominal * ratio, 1)
