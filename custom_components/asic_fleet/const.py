"""Constants for the ASIC Fleet integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "asic_fleet"

# --- Config entry keys -------------------------------------------------------
CONF_ROUTER_HOST: Final = "router_host"
CONF_ROUTER_USERNAME: Final = "router_username"
CONF_ROUTER_PASSWORD: Final = "router_password"
CONF_ROUTER_TLS: Final = "router_tls"
CONF_ROUTER_VERIFY_SSL: Final = "router_verify_ssl"
CONF_ASIC_USERNAME: Final = "asic_username"
CONF_ASIC_PASSWORD: Final = "asic_password"
CONF_ASIC_PORT: Final = "asic_port"

# --- Option keys -------------------------------------------------------------
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_HOSTNAME_PATTERN: Final = "hostname_pattern"
CONF_PROBE_UNKNOWN: Final = "probe_unknown"
CONF_PROBE_SUBNETS: Final = "probe_subnets"
CONF_ALIASES: Final = "aliases"
CONF_BLOCKLIST: Final = "blocklist"
CONF_MARKER_LIST: Final = "marker_list"
CONF_ALLOW_REBOOT: Final = "allow_reboot"
CONF_ALLOW_POOL_WRITE: Final = "allow_pool_write"
CONF_TEMP_WARN: Final = "temp_warn"
CONF_TEMP_CRIT: Final = "temp_crit"
CONF_HASHRATE_WARN_PCT: Final = "hashrate_warn_pct"
CONF_FAN_MIN_RPM: Final = "fan_min_rpm"
CONF_HW_ERROR_PCT: Final = "hw_error_pct"
CONF_OFFLINE_GRACE: Final = "offline_grace"
CONF_FAIL_THRESHOLD: Final = "fail_threshold"

# --- Defaults ----------------------------------------------------------------
DEFAULT_SCAN_INTERVAL: Final = 30
DEFAULT_ASIC_PORT: Final = 80
DEFAULT_ASIC_USERNAME: Final = "root"
DEFAULT_ROUTER_USERNAME: Final = "ha-asic"
DEFAULT_HOSTNAME_PATTERN: Final = r"^R(?P<rack>\d+)-ASIC(?P<index>\d+)$"
DEFAULT_BLOCKLIST: Final = "asic_blocked"
DEFAULT_MARKER_LIST: Final = "asic_blocked_hosts"
DEFAULT_TEMP_WARN: Final = 75
DEFAULT_TEMP_CRIT: Final = 85
DEFAULT_HASHRATE_WARN_PCT: Final = 85
DEFAULT_FAN_MIN_RPM: Final = 1000
DEFAULT_HW_ERROR_PCT: Final = 5.0
DEFAULT_OFFLINE_GRACE: Final = 180
DEFAULT_FAIL_THRESHOLD: Final = 3
DEFAULT_PROBE_UNKNOWN: Final = True

# Concurrency ceiling for per-ASIC HTTP polling. Keeps a 56-machine farm from
# opening 56 sockets at once on the HA box.
POLL_CONCURRENCY: Final = 12
HTTP_TIMEOUT: Final = 6.0

# --- Problem codes -----------------------------------------------------------
PROBLEM_OFFLINE: Final = "offline"
PROBLEM_OVERHEAT: Final = "overheat"
PROBLEM_OVERHEAT_CRITICAL: Final = "overheat_critical"
PROBLEM_HASHRATE_LOW: Final = "hashrate_low"
PROBLEM_HASHRATE_ZERO: Final = "hashrate_zero"
PROBLEM_FAN: Final = "fan"
PROBLEM_CHIPS: Final = "chips"
PROBLEM_HW_ERRORS: Final = "hw_errors"
PROBLEM_BLOCK_DESYNC: Final = "block_desync"
PROBLEM_IP_CHANGED: Final = "ip_changed"
PROBLEM_UNNAMED: Final = "unnamed"

SEVERITY: Final = {
    PROBLEM_OFFLINE: "critical",
    PROBLEM_OVERHEAT_CRITICAL: "critical",
    PROBLEM_HASHRATE_ZERO: "critical",
    PROBLEM_OVERHEAT: "warning",
    PROBLEM_HASHRATE_LOW: "warning",
    PROBLEM_FAN: "warning",
    PROBLEM_CHIPS: "warning",
    PROBLEM_HW_ERRORS: "warning",
    PROBLEM_BLOCK_DESYNC: "warning",
    PROBLEM_IP_CHANGED: "info",
    PROBLEM_UNNAMED: "info",
}

# --- Events ------------------------------------------------------------------
EVENT_PROBLEM: Final = "asic_fleet_problem"
EVENT_CLEARED: Final = "asic_fleet_cleared"
EVENT_DISCOVERED: Final = "asic_fleet_discovered"

# --- Services ----------------------------------------------------------------
SERVICE_BLOCK: Final = "block"
SERVICE_UNBLOCK: Final = "unblock"
SERVICE_REBOOT: Final = "reboot"
SERVICE_BLINK: Final = "blink"
SERVICE_BLOCK_RACK: Final = "block_rack"
SERVICE_UNBLOCK_RACK: Final = "unblock_rack"
SERVICE_REBOOT_RACK: Final = "reboot_rack"
SERVICE_ASSIGN_NAME: Final = "assign_name"
SERVICE_SET_POOLS: Final = "set_pools"
SERVICE_REFRESH: Final = "refresh"
