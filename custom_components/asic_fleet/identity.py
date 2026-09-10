"""Deciding what a miner is called and how the firewall refers to it.

Kept free of Home Assistant imports so the rules can be tested directly.

Two separate questions, deliberately answered by different rules:

* **The marker** is what RouterOS matches on, so it has to be unique and
  stable. Only a hostname that matches the site's naming pattern earns one;
  everything else is keyed by MAC. This matters in practice: several stock
  Bitmain units publish the DHCP hostname "Antminer", and letting them share a
  marker would mean blocking one blocks all of them.
* **The display name** can come from softer evidence — the miner's own
  internally configured hostname, for instance, which stock firmware reports
  over HTTP even when it never sends it via DHCP.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Hostnames the firmware ships with, which identify a model rather than a unit.
GENERIC_HOSTNAMES = frozenset(
    {"antminer", "localhost", "unknown", "miner", "bitmain", "none", "null"}
)

MAC_MARKER_PREFIX = "mac:"


def is_generic(hostname: str | None) -> bool:
    """True when a hostname names a product rather than a machine."""
    if not hostname:
        return True
    return hostname.strip().lower() in GENERIC_HOSTNAMES


def duplicate_hostnames(hostnames: Iterable[str | None]) -> set[str]:
    """Hostnames claimed by more than one lease, lowercased."""
    seen: dict[str, int] = {}
    for hostname in hostnames:
        if not hostname:
            continue
        key = hostname.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    return {key for key, count in seen.items() if count > 1}


def resolve_marker(mac: str, lease_hostname: str | None, matches_pattern: bool) -> str:
    """The address-list `comment` this miner is tracked under.

    A pattern-matching DHCP hostname keeps its bare-hostname marker, which is
    what the pre-existing lease-script and YAML package already write. Anything
    else — no hostname, a generic one, or one that does not fit the site's
    convention — is keyed by MAC.
    """
    if lease_hostname and matches_pattern and not is_generic(lease_hostname):
        return lease_hostname
    return f"{MAC_MARKER_PREFIX}{mac}"


def marker_address(marker: str) -> str:
    """A placeholder address for a marker entry.

    Marker entries exist for their comment; the address is never matched by a
    rule. It is drawn from 240.0.0.0/4 (reserved, unroutable) rather than
    0.0.0.0, which RouterOS would widen to "everything" if the list were ever
    referenced by a filter rule.
    """
    digest = 0
    for char in marker:
        digest = (digest * 131 + ord(char)) & 0xFFFFFF
    return f"240.{(digest >> 16) & 0xFF}.{(digest >> 8) & 0xFF}.{digest & 0xFF}"


def resolve_name(
    mac: str,
    *,
    alias: str | None = None,
    lease_hostname: str | None = None,
    probe_hostname: str | None = None,
    hostname_is_duplicate: bool = False,
) -> tuple[str, bool]:
    """Return (display name, whether it is a real name).

    A miner with no real name still gets something readable and stable, and is
    flagged so the integration can nag about it.
    """
    if alias and alias.strip():
        return alias.strip(), True

    lease = (lease_hostname or "").strip()
    if lease and not is_generic(lease) and not hostname_is_duplicate:
        return lease, True

    # Stock firmware keeps its configured hostname even when its DHCP client
    # never sends one — that is where the L9s' real names come from.
    probe = (probe_hostname or "").strip()
    if probe and not is_generic(probe):
        return probe, True

    return f"ASIC {mac.replace(':', '')[-6:]}", False


def parse_rack(match: re.Match[str] | None) -> tuple[str | None, int | None]:
    """Pull rack and index out of a hostname pattern match."""
    if not match:
        return None, None
    groups = match.groupdict()
    rack = groups.get("rack")
    raw_index = groups.get("index")
    index = int(raw_index) if raw_index and raw_index.isdigit() else None
    if rack and not rack.upper().startswith("R"):
        rack = f"R{rack}"
    return rack, index


def rack_from_name(name: str, pattern: re.Pattern[str]) -> str | None:
    """Best-effort rack for a miner named by its own firmware, not by DHCP."""
    rack, _ = parse_rack(pattern.match(name.strip()))
    return rack


# Mining algorithm, needed because a fleet total may not add hashrate across
# algorithms: an S21+ doing 241 TH/s of SHA-256 and an L9 doing 16 GH/s of
# Scrypt are not quantities that sum to anything meaningful.
#
# Promminer and current stock firmware report `Algorithm` in get_system_info;
# older stock builds (the S21+ here, for one) do not, so the model name is the
# fallback. Best effort by design — an unrecognised model stays "unknown"
# rather than being guessed into the wrong bucket.
_ALGORITHM_BY_MODEL_PREFIX: tuple[tuple[str, str], ...] = (
    ("KS", "kHeavyHash"),
    ("KA", "Kadena"),
    ("L", "Scrypt"),
    ("S", "SHA-256"),
    ("T", "SHA-256"),
    ("D", "X11"),
    ("E", "Ethash"),
    ("Z", "Equihash"),
)

UNKNOWN_ALGORITHM = "unknown"


def algorithm_for(model: str | None, reported: str | None = None) -> str:
    """Best-effort mining algorithm for one miner."""
    if reported and reported.strip():
        return reported.strip()
    if not model:
        return UNKNOWN_ALGORITHM
    # "Antminer L9" -> "L9", "Antminer S21+" -> "S21+"
    token = model.strip().split()[-1].upper()
    for prefix, algorithm in _ALGORITHM_BY_MODEL_PREFIX:
        if token.startswith(prefix):
            return algorithm
    return UNKNOWN_ALGORITHM
