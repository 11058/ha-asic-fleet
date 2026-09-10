"""HTTP CGI client for Antminer-family miners running Promminer firmware.

The web UI speaks HTTP Digest, which `aiohttp` does not implement, so this
module rides on `httpx` (already a Home Assistant dependency) via
`homeassistant.helpers.httpx_client`.

Verified against Promminer_L7_7007 (bmminer 2.12). L9 units expose the same
CGI surface. Note the firmware quirk documented upstream: `set_miner_conf.cgi`
REPLACES the whole config and answers `M000 OK!` even for fields it ignores.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Everything is normalised to MH/s. An L7 reports MH/s and an L9 reports GH/s
# for numbers of the same order, so without this a fleet total would be the sum
# of two different units.
CANONICAL_RATE_UNIT = "MH/s"
RATE_FACTORS = {
    "h/s": 1e-6,
    "kh/s": 1e-3,
    "mh/s": 1.0,
    "gh/s": 1e3,
    "th/s": 1e6,
    "ph/s": 1e9,
}


class AsicError(Exception):
    """Miner unreachable or answered with garbage."""


class AsicAuthError(AsicError):
    """Digest auth rejected."""


@dataclass
class AsicTelemetry:
    """One poll cycle's worth of numbers for a single miner."""

    reachable: bool = False
    rate_5s: float | None = None
    rate_30m: float | None = None
    rate_avg: float | None = None
    rate_ideal: float | None = None
    rate_unit: str = CANONICAL_RATE_UNIT
    reported_unit: str | None = None
    power: float | None = None
    efficiency: float | None = None
    elapsed: int | None = None
    hw_all: int | None = None
    hw_error_pct: float | None = None
    chains: int | None = None
    fan_rpm: list[int] = field(default_factory=list)
    chip_ok: int | None = None
    chip_total: int | None = None
    temp_min: int | None = None
    temp_avg: int | None = None
    temp_max: int | None = None
    status_flags: dict[str, Any] = field(default_factory=dict)
    chain_sn: list[str] = field(default_factory=list)
    pool_url: str | None = None
    pool_user: str | None = None
    pool_status: str | None = None
    accepted: int | None = None
    rejected: int | None = None
    model: str | None = None
    firmware: str | None = None
    reported_hostname: str | None = None
    mac: str | None = None
    error: str | None = None

    @property
    def fan_min(self) -> int | None:
        return min(self.fan_rpm) if self.fan_rpm else None

    @property
    def fan_max(self) -> int | None:
        return max(self.fan_rpm) if self.fan_rpm else None

    @property
    def chip_health(self) -> float | None:
        if not self.chip_total:
            return None
        return round((self.chip_ok or 0) / self.chip_total, 4)

    @property
    def uptime_hours(self) -> float | None:
        if self.elapsed is None:
            return None
        return round(self.elapsed / 3600.0, 2)


class AsicClient:
    """Digest-auth CGI calls against one miner, addressed by IP."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        username: str,
        password: str,
        port: int = 80,
    ) -> None:
        self._client = client
        self._auth = httpx.DigestAuth(username, password)
        self._port = port

    def _url(self, ip: str, cgi: str) -> str:
        return f"http://{ip}:{self._port}/cgi-bin/{cgi}"

    async def _get(self, ip: str, cgi: str) -> Any:
        try:
            resp = await self._client.get(
                self._url(ip, cgi), auth=self._auth, timeout=HTTP_TIMEOUT
            )
        except httpx.HTTPError as err:
            raise AsicError(f"{ip} {cgi}: {err}") from err
        if resp.status_code == 401:
            raise AsicAuthError(f"{ip} {cgi}: digest auth rejected")
        if resp.status_code >= 400:
            raise AsicError(f"{ip} {cgi}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as err:
            raise AsicError(f"{ip} {cgi}: not JSON") from err

    async def _post(self, ip: str, cgi: str, payload: Any = None) -> str:
        try:
            resp = await self._client.post(
                self._url(ip, cgi), auth=self._auth, json=payload, timeout=HTTP_TIMEOUT
            )
        except httpx.HTTPError as err:
            raise AsicError(f"{ip} {cgi}: {err}") from err
        if resp.status_code == 401:
            raise AsicAuthError(f"{ip} {cgi}: digest auth rejected")
        if resp.status_code >= 400:
            raise AsicError(f"{ip} {cgi}: HTTP {resp.status_code}")
        return resp.text

    # --- identity ------------------------------------------------------------

    async def system_info(self, ip: str) -> dict[str, Any]:
        data = await self._get(ip, "get_system_info.cgi")
        return data if isinstance(data, dict) else {}

    async def probe(self, ip: str) -> dict[str, Any] | None:
        """Cheap 'is this an ASIC?' check for a lease with no usable hostname.

        Returns the miner's system info on success, `{"auth_failed": True}` when
        something answered a digest challenge but rejected our credentials, and
        None when the host is not a miner at all. The auth-failed case is kept
        distinct on purpose: plenty of non-miners (IP cameras, NAS boxes) use
        digest auth too, so it is reported rather than adopted.
        """
        try:
            return await self.system_info(ip)
        except AsicAuthError:
            return {"auth_failed": True}
        except AsicError:
            return None

    # --- telemetry -----------------------------------------------------------

    async def poll(self, ip: str, *, with_pools: bool = True) -> AsicTelemetry:
        """Fetch summary + stats (+ pools) concurrently and fold into one object."""
        tasks = [self._get(ip, "summary.cgi"), self._get(ip, "stats.cgi")]
        if with_pools:
            tasks.append(self._get(ip, "pools.cgi"))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        telemetry = AsicTelemetry()
        summary, stats = results[0], results[1]
        pools = results[2] if with_pools and len(results) > 2 else None

        if isinstance(summary, Exception) and isinstance(stats, Exception):
            telemetry.error = str(summary)
            if isinstance(summary, AsicAuthError):
                raise summary
            return telemetry

        telemetry.reachable = True
        if not isinstance(summary, Exception):
            _parse_summary(summary, telemetry)
        if not isinstance(stats, Exception):
            _parse_stats(stats, telemetry)
        if pools is not None and not isinstance(pools, Exception):
            _parse_pools(pools, telemetry)
        return telemetry

    # --- control -------------------------------------------------------------

    async def reboot(self, ip: str) -> str:
        return await self._post(ip, "reboot.cgi")

    async def blink(self, ip: str, on: bool = True) -> str:
        return await self._post(ip, "blink.cgi", {"blink": on})

    async def get_conf(self, ip: str) -> dict[str, Any]:
        data = await self._get(ip, "get_miner_conf.cgi")
        return data if isinstance(data, dict) else {}

    async def set_conf(self, ip: str, conf: dict[str, Any]) -> str:
        """Write the miner config.

        Caller must pass a COMPLETE config — this CGI replaces the file rather
        than merging. Always derive the payload from `get_conf()`.
        """
        return await self._post(ip, "set_miner_conf.cgi", conf)


# --- parsers ------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _scale(value: Any, factor: float) -> float | None:
    number = _num(value)
    if number is None:
        return None
    # Round away the float noise that scaling by 1000 introduces.
    return round(number * factor, 4)


def _parse_summary(payload: Any, out: AsicTelemetry) -> None:
    if not isinstance(payload, dict):
        return
    info = payload.get("INFO")
    if isinstance(info, dict):
        out.model = info.get("type") or out.model
        out.firmware = info.get("miner_version") or out.firmware
    section = payload.get("SUMMARY") or [{}]
    summ = section[0] if isinstance(section, list) and section else {}
    if not isinstance(summ, dict):
        return

    reported_unit = summ.get("rate_unit") or CANONICAL_RATE_UNIT
    factor = RATE_FACTORS.get(str(reported_unit).strip().lower(), 1.0)
    out.reported_unit = reported_unit
    out.rate_unit = CANONICAL_RATE_UNIT
    out.rate_5s = _scale(summ.get("rate_5s") or summ.get("GHS 5s"), factor)
    out.rate_30m = _scale(summ.get("rate_30m") or summ.get("GHS 30m"), factor)
    out.rate_avg = _scale(summ.get("rate_avg") or summ.get("GHS av"), factor)
    out.rate_ideal = _scale(summ.get("rate_ideal"), factor)
    elapsed = _num(summ.get("elapsed") or summ.get("Elapsed"))
    out.elapsed = int(elapsed) if elapsed is not None else None
    hw = _num(summ.get("hw_all") or summ.get("Hardware Errors"))
    out.hw_all = int(hw) if hw is not None else None

    if out.rate_5s is not None and out.rate_ideal:
        out.efficiency = round(out.rate_5s / out.rate_ideal, 4)

    for entry in summ.get("status") or []:
        if isinstance(entry, dict) and entry.get("type"):
            out.status_flags[entry["type"]] = entry.get("status") or entry.get("code")


def _parse_stats(payload: Any, out: AsicTelemetry) -> None:
    if not isinstance(payload, dict):
        return
    section = payload.get("STATS") or [{}]
    stats = section[0] if isinstance(section, list) and section else {}
    if not isinstance(stats, dict):
        return

    chains = stats.get("chain") or []
    chain_num = _num(stats.get("chain_num"))
    out.chains = int(chain_num) if chain_num is not None else len(chains)
    out.hw_error_pct = _num(stats.get("hwp_total"))
    # Only newer firmware (L9 and friends) reports wall power.
    out.power = _num(stats.get("power"))
    out.fan_rpm = [
        int(f) for f in (stats.get("fan") or []) if isinstance(f, (int, float))
    ]

    temps: list[int] = []
    chips_ok = chips_total = 0
    serials: list[str] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        for temp in chain.get("temp_chip") or []:
            if isinstance(temp, (int, float)) and temp > 0:
                temps.append(int(temp))
        # `asic` is a chip map like "oooooo oooooo xoooo": 'o' good, else bad.
        for char in (chain.get("asic") or "").replace(" ", ""):
            chips_total += 1
            if char == "o":
                chips_ok += 1
        if chain.get("sn"):
            serials.append(str(chain["sn"]))

    if temps:
        out.temp_min = min(temps)
        out.temp_max = max(temps)
        out.temp_avg = round(sum(temps) / len(temps))
    if chips_total:
        out.chip_ok = chips_ok
        out.chip_total = chips_total
    if serials:
        out.chain_sn = serials


def _parse_pools(payload: Any, out: AsicTelemetry) -> None:
    if not isinstance(payload, dict):
        return
    pools = payload.get("POOLS") or []
    if not isinstance(pools, list):
        return
    # Several pools are usually alive at once; the one actually being mined is
    # the alive pool with the lowest priority number.
    alive = [
        p
        for p in pools
        if isinstance(p, dict) and str(p.get("status", "")).lower() == "alive"
    ]
    if alive:
        active = min(alive, key=lambda p: _num(p.get("priority")) or 0)
    else:
        active = pools[0] if pools and isinstance(pools[0], dict) else None
    if not isinstance(active, dict):
        return
    out.pool_url = active.get("url") or active.get("URL")
    out.pool_user = active.get("user") or active.get("User")
    out.pool_status = active.get("status") or active.get("Status")
    accepted = _num(active.get("accepted") or active.get("Accepted"))
    rejected = _num(active.get("rejected") or active.get("Rejected"))
    out.accepted = int(accepted) if accepted is not None else None
    out.rejected = int(rejected) if rejected is not None else None
