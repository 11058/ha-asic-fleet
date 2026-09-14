"""Fleet coordinator: discovery, polling, anomaly detection, control."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .asic_api import AsicClient, AsicError, AsicTelemetry
from .const import (
    CONF_ALIASES,
    CONF_BLOCKLIST,
    CONF_FAIL_THRESHOLD,
    CONF_FAN_MIN_RPM,
    CONF_HASHRATE_WARN_PCT,
    CONF_HOSTNAME_PATTERN,
    CONF_HW_ERROR_PCT,
    CONF_MARKER_LIST,
    CONF_OFFLINE_GRACE,
    CONF_PROBE_UNKNOWN,
    CONF_SCAN_INTERVAL,
    CONF_TEMP_CRIT,
    CONF_TEMP_WARN,
    DEFAULT_BLOCKLIST,
    DEFAULT_FAIL_THRESHOLD,
    DEFAULT_FAN_MIN_RPM,
    DEFAULT_HASHRATE_WARN_PCT,
    DEFAULT_HOSTNAME_PATTERN,
    DEFAULT_HW_ERROR_PCT,
    DEFAULT_MARKER_LIST,
    DEFAULT_OFFLINE_GRACE,
    DEFAULT_PROBE_UNKNOWN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_CRIT,
    DEFAULT_TEMP_WARN,
    DOMAIN,
    EVENT_CLEARED,
    EVENT_DISCOVERED,
    EVENT_PROBLEM,
    POLL_CONCURRENCY,
    PROBLEM_BLOCK_DESYNC,
    PROBLEM_CHIPS,
    PROBLEM_FAN,
    PROBLEM_HASHRATE_LOW,
    PROBLEM_HASHRATE_ZERO,
    PROBLEM_HW_ERRORS,
    PROBLEM_IP_CHANGED,
    PROBLEM_OFFLINE,
    PROBLEM_OVERHEAT,
    PROBLEM_OVERHEAT_CRITICAL,
    PROBLEM_UNNAMED,
    SEVERITY,
)
from .identity import (
    algorithm_for,
    duplicate_hostnames,
    is_generic,
    marker_address,
    parse_rack,
    rack_from_name,
    resolve_marker,
    resolve_name,
)
from .mikrotik import MikrotikAuthError, MikrotikClient, MikrotikError
from .power import estimate_power
from .store import MinerStore, StoredMiner

_LOGGER = logging.getLogger(__name__)

# How many polls between re-probes of leases that did not look like a miner.
PROBE_BACKOFF_CYCLES = 20
PROBE_CONCURRENCY = 6


@dataclass
class AsicRecord:
    """Everything known about one miner in the current cycle."""

    mac: str
    name: str
    marker: str
    ip: str | None = None
    rack: str | None = None
    index: int | None = None
    lease_hostname: str | None = None
    port: str | None = None
    named: bool = True
    blocked: bool = False
    block_ip_synced: bool = True
    lease_active: bool = False
    telemetry: AsicTelemetry = field(default_factory=AsicTelemetry)
    problems: dict[str, str] = field(default_factory=dict)
    last_seen: datetime | None = None
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    algorithm: str = "unknown"
    # Present in the inventory but not in any current DHCP lease.
    stale_lease: bool = False
    manual: bool = False
    # Filled in during evaluation, where the thresholds live.
    temp_status: str = "unknown"

    @property
    def online(self) -> bool:
        return self.telemetry.reachable

    @property
    def error(self) -> str | None:
        """Human-readable reason this miner is not reporting, if any."""
        if self.telemetry.error:
            return self.telemetry.error
        if not self.ip:
            return "нет адреса: майнер не появлялся в DHCP и не задан вручную"
        if not self.online:
            return f"нет ответа от {self.ip}"
        return None

    @property
    def power(self) -> float | None:
        """Wall power: measured where the firmware reports it, else estimated."""
        if self.telemetry.power is not None:
            return self.telemetry.power
        return estimate_power(self.model, self.telemetry)

    @property
    def power_is_measured(self) -> bool:
        return self.telemetry.power is not None

    @property
    def hashrate_status(self) -> str:
        """Coarse state of this miner's hashing, for colouring and automations."""
        if self.blocked:
            return "blocked"
        if not self.online:
            return "unreachable"
        if self.telemetry.error:
            return "error"
        rate = self.telemetry.rate_5s
        if rate is None:
            return "error"
        if rate <= 0:
            return "off"
        if self.telemetry.efficiency is not None and self.telemetry.efficiency < 0.85:
            return "low"
        return "ok"

    def temperature_status(self, warn: float, crit: float) -> str:
        temp = self.telemetry.temp_max
        if temp is None:
            return "unknown"
        if temp >= crit:
            return "critical"
        if temp >= warn:
            return "warning"
        return "ok"

    @property
    def status(self) -> str:
        # Offline outranks blocked: blocking only cuts WAN, so a blocked miner
        # is still expected to answer on the LAN. Silence means it is gone.
        if not self.online:
            return "offline"
        if self.blocked:
            return "blocked"
        if any(sev == "critical" for sev in self.problems.values()):
            return "critical"
        if self.problems:
            return "degraded"
        return "online"


@dataclass
class FleetData:
    """Coordinator payload."""

    asics: dict[str, AsicRecord] = field(default_factory=dict)
    router_online: bool = False
    router_identity: str | None = None
    router_error: str | None = None
    unknown_leases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def errors(self) -> dict[str, str]:
        """name -> concrete reason, for every miner currently in trouble."""
        return {r.name: r.error for r in self.asics.values() if r.error}

    @property
    def racks(self) -> set[str]:
        return {a.rack for a in self.asics.values() if a.rack}


class FleetCoordinator(DataUpdateCoordinator[FleetData]):
    """Single poll loop for the router and every miner behind it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        router: MikrotikClient,
        asic: AsicClient,
        store: MinerStore,
    ) -> None:
        self.entry = entry
        self.router = router
        self.asic = asic
        self.store = store
        self._probe_skip: dict[str, int] = {}
        # Mirror of the persisted probe payloads, so an offline miner is still
        # identified after a restart instead of having to answer first.
        self._confirmed: dict[str, dict[str, Any]] = {
            mac: miner.probe for mac, miner in store.miners.items() if miner.probe
        }
        self._fail_counts: dict[tuple[str, str], int] = {}
        self._active_problems: dict[str, dict[str, str]] = {}
        self._last_reachable: dict[str, datetime] = {}
        self._last_ip: dict[str, str] = {}
        self._announced: set[str] = set()
        # Set once the hub device exists; miner devices hang off it.
        self.hub_device_id: str | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    # --- options -------------------------------------------------------------

    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, default)

    @property
    def blocklist(self) -> str:
        return self._opt(CONF_BLOCKLIST, DEFAULT_BLOCKLIST)

    @property
    def marker_list(self) -> str:
        return self._opt(CONF_MARKER_LIST, DEFAULT_MARKER_LIST)

    @property
    def aliases(self) -> dict[str, dict[str, Any]]:
        return dict(self._opt(CONF_ALIASES, {}) or {})

    # --- main loop -----------------------------------------------------------

    async def _async_update_data(self) -> FleetData:
        try:
            leases, addr_lists, hosts, identity = await asyncio.gather(
                self.router.leases(),
                self.router.address_lists(),
                self.router.bridge_hosts(),
                self.router.identity(),
            )
        except MikrotikAuthError as err:
            raise UpdateFailed(str(err)) from err
        except MikrotikError as err:
            # Keep the previous picture rather than blanking 56 devices because
            # of one dropped packet; entities go unavailable only when HA's own
            # retry budget runs out.
            raise UpdateFailed(str(err)) from err

        data = FleetData(
            router_online=True,
            router_identity=(identity or {}).get("name"),
        )

        port_by_mac = {
            _norm_mac(h.get("mac-address", "")): h.get("interface")
            for h in hosts
            if h.get("mac-address")
        }
        markers, blocked_ips = self._read_lists(addr_lists)
        records = await self._discover(leases, port_by_mac, data)

        for record in records.values():
            record.blocked = record.marker in markers
            blocked_entry_ip = blocked_ips.get(record.marker)
            # Intent says blocked, but no matching live IP entry means the
            # firewall is not actually dropping this miner's traffic yet.
            record.block_ip_synced = not record.blocked or (
                blocked_entry_ip is not None and blocked_entry_ip == record.ip
            )

        await self._identify(records)
        await self._poll_all(records)
        self._evaluate(records)
        self._persist(records)
        data.asics = records
        return data

    def _read_lists(
        self, entries: list[dict[str, Any]]
    ) -> tuple[set[str], dict[str, str]]:
        markers: set[str] = set()
        blocked_ips: dict[str, str] = {}
        for entry in entries:
            comment = entry.get("comment")
            if not comment:
                continue
            if entry.get("list") == self.marker_list:
                markers.add(comment)
            elif entry.get("list") == self.blocklist:
                blocked_ips[comment] = entry.get("address", "")
        return markers, blocked_ips

    # --- discovery -----------------------------------------------------------

    async def _discover(
        self,
        leases: list[dict[str, Any]],
        port_by_mac: dict[str, str],
        data: FleetData,
    ) -> dict[str, AsicRecord]:
        """Build this cycle's records from the inventory plus current leases.

        Miners already in the inventory are always present, lease or no lease.
        A machine that is switched off long enough for its lease to expire must
        not disappear from Home Assistant — it is still a machine in a rack,
        and losing it would take its name, history and entities with it.
        """
        pattern = re.compile(self._opt(CONF_HOSTNAME_PATTERN, DEFAULT_HOSTNAME_PATTERN))
        aliases = self.aliases
        records: dict[str, AsicRecord] = {}
        candidates: list[tuple[str, str, dict[str, Any]]] = []

        lease_by_mac: dict[str, tuple[str, dict[str, Any]]] = {}
        for lease in leases:
            mac = _norm_mac(lease.get("mac-address", ""))
            ip = lease.get("active-address") or lease.get("address")
            if mac and ip:
                lease_by_mac[mac] = (ip, lease)

        # Stock firmware hands out "Antminer" as the DHCP hostname, so several
        # machines can claim the same one. Knowing which names are contested is
        # a precondition for naming anything.
        collisions = duplicate_hostnames(lease.get("host-name") for lease in leases)

        # 1. Everything already known.
        for mac, stored in self.store.miners.items():
            ip, lease = lease_by_mac.get(mac, (stored.ip, {}))
            hostname = (lease.get("host-name") or stored.lease_hostname or "").strip()
            records[mac] = self._build_record(
                mac,
                ip,
                hostname,
                pattern.match(hostname) if hostname else None,
                aliases.get(mac),
                port_by_mac.get(mac) or stored.port,
                lease,
                pattern,
                hostname.lower() in collisions,
                stored=stored,
                stale_lease=mac not in lease_by_mac,
            )

        # 2. Leases that are new to us.
        for mac, (ip, lease) in lease_by_mac.items():
            if mac in records:
                continue
            hostname = (lease.get("host-name") or "").strip()
            match = pattern.match(hostname) if hostname else None
            alias = aliases.get(mac)

            if match or alias:
                records[mac] = self._build_record(
                    mac,
                    ip,
                    hostname,
                    match,
                    alias,
                    port_by_mac.get(mac),
                    lease,
                    pattern,
                    hostname.lower() in collisions,
                )
            elif self._opt(CONF_PROBE_UNKNOWN, DEFAULT_PROBE_UNKNOWN):
                skip = self._probe_skip.get(mac, 0)
                if skip > 0:
                    self._probe_skip[mac] = skip - 1
                else:
                    candidates.append((mac, ip, lease))

        if candidates:
            await self._probe_candidates(
                candidates, port_by_mac, records, data, pattern, collisions
            )

        self._persist(records)
        return records

    def _persist(self, records: dict[str, AsicRecord]) -> None:
        """Fold this cycle's findings back into the inventory."""
        for record in records.values():
            self.store.upsert(
                StoredMiner(
                    mac=record.mac,
                    name=record.name if record.named else "",
                    rack=record.rack,
                    index=record.index,
                    marker=record.marker,
                    lease_hostname=record.lease_hostname,
                    ip=record.ip,
                    port=record.port,
                    model=record.model,
                    firmware=record.firmware,
                    serial=record.serial,
                    algorithm=record.algorithm,
                    named=record.named,
                    manual=record.manual,
                    last_seen=record.last_seen.isoformat()
                    if record.last_seen
                    else None,
                    probe=self._confirmed.get(record.mac, {}),
                )
            )

    def _build_record(
        self,
        mac: str,
        ip: str | None,
        lease_hostname: str,
        match: re.Match[str] | None,
        alias: dict[str, Any] | None,
        port: str | None,
        lease: dict[str, Any],
        pattern: re.Pattern[str],
        hostname_is_duplicate: bool = False,
        stored: StoredMiner | None = None,
        stale_lease: bool = False,
    ) -> AsicRecord:
        probe = self._confirmed.get(mac) or (stored.probe if stored else {})
        rack, index = parse_rack(match)

        probe_hostname = str(probe.get("hostname") or "").strip()
        name, named = resolve_name(
            mac,
            alias=str(alias["name"]) if alias and alias.get("name") else None,
            lease_hostname=lease_hostname,
            probe_hostname=probe_hostname,
            hostname_is_duplicate=hostname_is_duplicate,
        )
        # A name we already settled on outlives a lease going away: without
        # this, a miner that is merely switched off would revert to
        # "ASIC A1B2C3" and lose the name someone gave it.
        if not named and stored and stored.named and stored.name:
            name, named = stored.name, True

        if rack is None and named and not is_generic(name):
            # A miner named by its own firmware still belongs to a rack.
            rack = rack_from_name(name, pattern)
        if rack is None and stored:
            rack = stored.rack
        if index is None and stored:
            index = stored.index
        if alias and alias.get("rack"):
            rack = str(alias["rack"])

        marker = resolve_marker(mac, lease_hostname, match is not None)
        if stale_lease and stored and stored.marker:
            # Keep addressing the firewall by whatever comment this miner was
            # blocked under; recomputing it from a vanished lease would leave
            # a stale block nobody can lift.
            marker = stored.marker

        return AsicRecord(
            mac=mac,
            name=name,
            marker=marker,
            ip=ip,
            rack=rack,
            index=index,
            lease_hostname=lease_hostname
            or (stored.lease_hostname if stored else None),
            port=port,
            named=named,
            stale_lease=stale_lease,
            manual=bool(stored and stored.manual),
            lease_active=lease.get("status") == "bound"
            or bool(lease.get("active-address")),
            model=probe.get("minertype") or (stored.model if stored else None),
            algorithm=algorithm_for(
                probe.get("minertype") or (stored.model if stored else None),
                probe.get("Algorithm")
                or (
                    stored.algorithm
                    if stored and stored.algorithm != "unknown"
                    else None
                ),
            ),
            # `firmware_type` is the Promminer build string (e.g.
            # Promminer_L7_7007); the filesystem version is the fallback for
            # stock firmware that omits it.
            firmware=probe.get("firmware_type")
            or probe.get("system_filesystem_version")
            or (stored.firmware if stored else None),
            serial=probe.get("serinum")
            or probe.get("serial")
            or (stored.serial if stored else None),
        )

    async def _probe_candidates(
        self,
        candidates: list[tuple[str, str, dict[str, Any]]],
        port_by_mac: dict[str, str],
        records: dict[str, AsicRecord],
        data: FleetData,
        pattern: re.Pattern[str],
        collisions: set[str],
    ) -> None:
        """Ask unidentified leases whether they are miners.

        This is how the L9 units — which never publish a DHCP hostname — get
        found without anyone hand-maintaining an inventory file.
        """
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def probe(mac: str, ip: str) -> tuple[str, str, dict[str, Any] | None]:
            async with semaphore:
                return mac, ip, await self.asic.probe(ip)

        results = await asyncio.gather(
            *(probe(mac, ip) for mac, ip, _ in candidates), return_exceptions=True
        )
        lease_by_mac = {mac: lease for mac, _, lease in candidates}

        for result in results:
            if isinstance(result, BaseException):
                continue
            mac, ip, info = result
            lease = lease_by_mac.get(mac, {})

            if not info or info.get("auth_failed"):
                # Either not a miner, or a digest-auth device whose credentials
                # differ. Neither is safe to adopt as a miner; back off and let
                # the diagnostics dump show it.
                self._probe_skip[mac] = PROBE_BACKOFF_CYCLES
                data.unknown_leases.append(
                    {
                        "mac": mac,
                        "ip": ip,
                        "host-name": lease.get("host-name"),
                        "auth_failed": bool(info and info.get("auth_failed")),
                    }
                )
                continue

            self._confirmed[mac] = info
            lease_hostname = (lease.get("host-name") or "").strip()
            records[mac] = self._build_record(
                mac,
                ip,
                lease_hostname,
                pattern.match(lease_hostname) if lease_hostname else None,
                self.aliases.get(mac),
                port_by_mac.get(mac),
                lease,
                pattern,
                lease_hostname.lower() in collisions,
            )
            if mac not in self._announced:
                self._announced.add(mac)
                self.hass.bus.async_fire(
                    EVENT_DISCOVERED,
                    {
                        "mac": mac,
                        "ip": ip,
                        "name": records[mac].name,
                        "model": info.get("minertype"),
                        "port": port_by_mac.get(mac),
                    },
                )
                _LOGGER.info(
                    "Discovered miner %s at %s (model %s, switch port %s)",
                    mac,
                    ip,
                    info.get("minertype"),
                    port_by_mac.get(mac),
                )

    async def _identify(self, records: dict[str, AsicRecord]) -> None:
        """Fetch get_system_info once per miner, for model and algorithm.

        Miners found by hostname are never probed during discovery, so without
        this their algorithm would stay unknown — and the fleet total needs it
        to avoid adding SHA-256 hashrate to Scrypt hashrate.
        """
        pending = [
            record
            for record in records.values()
            if record.ip and record.mac not in self._confirmed
        ]
        if not pending:
            return

        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def identify(record: AsicRecord) -> None:
            async with semaphore:
                info = await self.asic.probe(record.ip or "")
            if not info or info.get("auth_failed"):
                return
            self._confirmed[record.mac] = info
            record.model = info.get("minertype") or record.model
            record.algorithm = algorithm_for(
                info.get("minertype"), info.get("Algorithm")
            )
            record.firmware = (
                info.get("firmware_type")
                or info.get("system_filesystem_version")
                or record.firmware
            )
            record.serial = info.get("serinum") or record.serial

        await asyncio.gather(*(identify(r) for r in pending))

    # --- polling -------------------------------------------------------------

    async def _poll_all(self, records: dict[str, AsicRecord]) -> None:
        semaphore = asyncio.Semaphore(POLL_CONCURRENCY)

        async def poll(record: AsicRecord) -> None:
            if not record.ip:
                return
            async with semaphore:
                try:
                    record.telemetry = await self.asic.poll(record.ip)
                except AsicError as err:
                    record.telemetry = AsicTelemetry(reachable=False, error=str(err))

            if record.telemetry.reachable:
                record.last_seen = dt_util.utcnow()
                self._last_reachable[record.mac] = record.last_seen
                # summary.cgi reports both on every poll, so a miner that was
                # never probed still ends up with a model in the device page.
                if record.telemetry.model:
                    record.model = record.telemetry.model
                if record.telemetry.firmware and not record.firmware:
                    record.firmware = record.telemetry.firmware
            else:
                record.last_seen = self._last_reachable.get(record.mac)

        await asyncio.gather(*(poll(r) for r in records.values()))

    # --- anomaly detection ---------------------------------------------------

    def _evaluate(self, records: dict[str, AsicRecord]) -> None:
        temp_warn = self._opt(CONF_TEMP_WARN, DEFAULT_TEMP_WARN)
        temp_crit = self._opt(CONF_TEMP_CRIT, DEFAULT_TEMP_CRIT)
        rate_pct = self._opt(CONF_HASHRATE_WARN_PCT, DEFAULT_HASHRATE_WARN_PCT)
        fan_min = self._opt(CONF_FAN_MIN_RPM, DEFAULT_FAN_MIN_RPM)
        hw_pct = self._opt(CONF_HW_ERROR_PCT, DEFAULT_HW_ERROR_PCT)
        grace = self._opt(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE)
        threshold = self._opt(CONF_FAIL_THRESHOLD, DEFAULT_FAIL_THRESHOLD)
        now = dt_util.utcnow()

        for record in records.values():
            raised: dict[str, Any] = {}
            tel = record.telemetry
            record.temp_status = record.temperature_status(temp_warn, temp_crit)

            if not record.online:
                # A miner we deliberately blocked stays reachable on the LAN, so
                # "offline" here really does mean "gone", block or no block.
                last = self._last_reachable.get(record.mac)
                if last is None or (now - last).total_seconds() > grace:
                    raised[PROBLEM_OFFLINE] = {
                        "ip": record.ip,
                        "last_seen": last.isoformat() if last else None,
                        "error": tel.error,
                    }
            else:
                if tel.temp_max is not None:
                    if tel.temp_max >= temp_crit:
                        raised[PROBLEM_OVERHEAT_CRITICAL] = {"temp_max": tel.temp_max}
                    elif tel.temp_max >= temp_warn:
                        raised[PROBLEM_OVERHEAT] = {"temp_max": tel.temp_max}

                # Hashrate checks only make sense when the miner is supposed to
                # be hashing — a blocked miner losing its pool is expected.
                if not record.blocked:
                    if tel.rate_5s is not None and tel.rate_5s <= 0:
                        raised[PROBLEM_HASHRATE_ZERO] = {"rate_5s": tel.rate_5s}
                    elif tel.efficiency is not None and tel.efficiency * 100 < rate_pct:
                        raised[PROBLEM_HASHRATE_LOW] = {
                            "efficiency_pct": round(tel.efficiency * 100, 1),
                            "rate_5s": tel.rate_5s,
                            "rate_ideal": tel.rate_ideal,
                        }

                if tel.fan_rpm and (tel.fan_min or 0) < fan_min:
                    raised[PROBLEM_FAN] = {"fan_rpm": tel.fan_rpm}

                if tel.chip_total and tel.chip_ok != tel.chip_total:
                    raised[PROBLEM_CHIPS] = {
                        "chip_ok": tel.chip_ok,
                        "chip_total": tel.chip_total,
                    }

                if tel.hw_error_pct is not None and tel.hw_error_pct > hw_pct:
                    raised[PROBLEM_HW_ERRORS] = {"hw_error_pct": tel.hw_error_pct}

            if not record.block_ip_synced:
                raised[PROBLEM_BLOCK_DESYNC] = {
                    "marker": record.marker,
                    "ip": record.ip,
                }

            previous_ip = self._last_ip.get(record.mac)
            if previous_ip and record.ip and previous_ip != record.ip:
                raised[PROBLEM_IP_CHANGED] = {"from": previous_ip, "to": record.ip}
            if record.ip:
                self._last_ip[record.mac] = record.ip

            if not record.named:
                raised[PROBLEM_UNNAMED] = {"mac": record.mac, "port": record.port}

            self._commit_problems(record, raised, threshold)

    def _commit_problems(
        self, record: AsicRecord, raised: dict[str, Any], threshold: int
    ) -> None:
        """Apply hysteresis, then fire events for edges only."""
        active = self._active_problems.setdefault(record.mac, {})

        for code, details in raised.items():
            # One-shot informational codes should not wait for N cycles.
            needed = 1 if SEVERITY.get(code) == "info" else threshold
            key = (record.mac, code)
            count = self._fail_counts.get(key, 0) + 1
            self._fail_counts[key] = count
            if count < needed:
                continue
            record.problems[code] = SEVERITY.get(code, "warning")
            if code not in active:
                active[code] = record.problems[code]
                self.hass.bus.async_fire(
                    EVENT_PROBLEM,
                    {
                        "mac": record.mac,
                        "name": record.name,
                        "rack": record.rack,
                        "ip": record.ip,
                        "port": record.port,
                        "problem": code,
                        "severity": record.problems[code],
                        "details": details,
                    },
                )

        for code in list(self._fail_counts):
            if code[0] == record.mac and code[1] not in raised:
                self._fail_counts.pop(code, None)

        for code in list(active):
            if code not in raised:
                active.pop(code, None)
                self.hass.bus.async_fire(
                    EVENT_CLEARED,
                    {
                        "mac": record.mac,
                        "name": record.name,
                        "rack": record.rack,
                        "problem": code,
                    },
                )

    # --- control -------------------------------------------------------------

    def record_for_mac(self, mac: str) -> AsicRecord | None:
        if not self.data:
            return None
        return self.data.asics.get(_norm_mac(mac))

    async def async_set_blocked(self, record: AsicRecord, blocked: bool) -> None:
        """Add or remove this miner's entries in both address lists."""
        entries = await self.router.address_lists()
        mine = [e for e in entries if e.get("comment") == record.marker]

        if blocked:
            if not any(e.get("list") == self.marker_list for e in mine):
                await self.router.add_address_list_entry(
                    self.marker_list, marker_address(record.marker), record.marker
                )
            if record.ip:
                stale = [
                    e
                    for e in mine
                    if e.get("list") == self.blocklist and e.get("address") != record.ip
                ]
                for entry in stale:
                    if entry.get(".id"):
                        await self.router.remove_address_list_entry(entry[".id"])
                if not any(
                    e.get("list") == self.blocklist and e.get("address") == record.ip
                    for e in mine
                ):
                    await self.router.add_address_list_entry(
                        self.blocklist, record.ip, record.marker
                    )
                await self.router.drop_connections_from(record.ip)
        else:
            for entry in mine:
                if entry.get(".id") and entry.get("list") in (
                    self.marker_list,
                    self.blocklist,
                ):
                    await self.router.remove_address_list_entry(entry[".id"])

        await self.async_request_refresh()

    async def async_reboot(self, record: AsicRecord) -> None:
        if not record.ip:
            raise AsicError(f"{record.name}: no IP known, cannot reboot")
        await self.asic.reboot(record.ip)

    # --- inventory management ------------------------------------------------

    async def async_discover_now(self) -> None:
        """Re-probe everything, including leases previously judged uninteresting."""
        self._probe_skip.clear()
        await self.async_refresh()

    async def async_add_manual(
        self,
        mac: str,
        *,
        ip: str | None = None,
        name: str | None = None,
        rack: str | None = None,
    ) -> StoredMiner:
        """Add a miner by hand, for machines discovery cannot reach.

        Useful for a static-IP miner on a segment with no DHCP, or one that is
        currently powered down but should still appear in the fleet.
        """
        mac = _norm_mac(mac)
        miner = StoredMiner(
            mac=mac,
            name=name or "",
            rack=rack,
            ip=ip,
            named=bool(name),
            manual=True,
            marker=f"mac:{mac}",
        )
        stored = self.store.upsert(miner)
        await self.store.async_save_now()
        await self.async_refresh()
        return stored

    async def async_forget(self, mac: str) -> bool:
        """Drop a miner from the inventory and remove its device."""
        mac = _norm_mac(mac)
        removed = self.store.forget(mac)
        await self.store.async_save_now()

        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, mac)})
        if device:
            registry.async_remove_device(device.id)

        self._confirmed.pop(mac, None)
        self._probe_skip.pop(mac, None)
        self._last_reachable.pop(mac, None)
        self._last_ip.pop(mac, None)
        self._active_problems.pop(mac, None)
        self._announced.discard(mac)
        await self.async_refresh()
        return removed

    async def async_reset(self, *, keep_manual: bool = True) -> int:
        """Forget every discovered miner and start discovery over.

        Hand-added miners are kept by default — they cannot be rediscovered
        from the network, so dropping them would be irreversible.
        """
        registry = dr.async_get(self.hass)
        for mac, miner in list(self.store.miners.items()):
            if keep_manual and miner.manual:
                continue
            device = registry.async_get_device(identifiers={(DOMAIN, mac)})
            if device:
                registry.async_remove_device(device.id)

        removed = self.store.clear(keep_manual=keep_manual)
        await self.store.async_save_now()
        self._confirmed = {
            mac: m.probe for mac, m in self.store.miners.items() if m.probe
        }
        self._probe_skip.clear()
        self._last_reachable.clear()
        self._last_ip.clear()
        self._active_problems.clear()
        self._announced.clear()
        await self.async_refresh()
        return removed

    async def async_blink(self, record: AsicRecord, on: bool = True) -> None:
        if not record.ip:
            raise AsicError(f"{record.name}: no IP known, cannot blink")
        await self.asic.blink(record.ip, on)


def _norm_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")
