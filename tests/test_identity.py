"""Naming and firewall-marker rules.

The cases here are taken from the live fleet: 49 miners publish DHCP hostnames
like R1-ASIC7, three publish nothing at all, and four publish the stock
hostname "Antminer" — which, if trusted, would give four machines the same
firewall marker.
"""

from __future__ import annotations

import re

import pytest
from conftest import load_module

identity = load_module("identity")

PATTERN = re.compile(r"^R(?P<rack>\d+)-ASIC(?P<index>\d+)$")


def match(hostname: str):
    return PATTERN.match(hostname)


class TestMarker:
    def test_conforming_hostname_keeps_its_bare_marker(self) -> None:
        # Backward compatibility: this is what the existing lease-script and
        # the YAML package already wrote.
        assert (
            identity.resolve_marker("AA:BB:CC:DD:EE:FF", "R1-ASIC7", True) == "R1-ASIC7"
        )

    def test_missing_hostname_falls_back_to_mac(self) -> None:
        assert (
            identity.resolve_marker("AA:BB:CC:DD:EE:FF", "", False)
            == "mac:AA:BB:CC:DD:EE:FF"
        )

    def test_generic_hostname_never_becomes_a_marker(self) -> None:
        # Four machines answer to "Antminer"; sharing a marker would mean
        # blocking one blocks all four.
        macs = [
            "02:2E:48:DA:AF:69",
            "02:AD:EF:F0:7B:76",
            "02:8B:F7:28:EA:A7",
            "88:66:33:F9:EA:45",
        ]
        markers = {identity.resolve_marker(m, "Antminer", False) for m in macs}
        assert markers == {f"mac:{m}" for m in macs}
        assert len(markers) == 4

    def test_non_conforming_hostname_is_keyed_by_mac(self) -> None:
        assert (
            identity.resolve_marker("AA:BB:CC:DD:EE:FF", "shed-heater", False)
            == "mac:AA:BB:CC:DD:EE:FF"
        )


class TestMarkerAddress:
    def test_placeholder_is_in_reserved_space(self) -> None:
        # Never 0.0.0.0: RouterOS would read that as "everything" if the marker
        # list were ever referenced by a filter rule.
        for marker in ("R1-ASIC7", "mac:AA:BB:CC:DD:EE:FF", ""):
            address = identity.marker_address(marker)
            assert address.startswith("240.")
            octets = [int(o) for o in address.split(".")]
            assert len(octets) == 4
            assert all(0 <= o <= 255 for o in octets)

    def test_stable_and_distinct(self) -> None:
        first = identity.marker_address("R1-ASIC7")
        assert first == identity.marker_address("R1-ASIC7")
        assert first != identity.marker_address("R1-ASIC8")


class TestName:
    def test_alias_wins(self) -> None:
        assert identity.resolve_name(
            "AA:BB:CC:DD:EE:FF", alias="R1-ASIC57", lease_hostname="R1-ASIC7"
        ) == ("R1-ASIC57", True)

    def test_dhcp_hostname_is_used_when_unique(self) -> None:
        assert identity.resolve_name(
            "AA:BB:CC:DD:EE:FF", lease_hostname="R1-ASIC7"
        ) == ("R1-ASIC7", True)

    def test_firmware_hostname_rescues_a_nameless_lease(self) -> None:
        # This is the real L9 case: no DHCP hostname, but the miner knows it is
        # R1-ASIC48 and says so over HTTP.
        assert identity.resolve_name(
            "EA:5D:F8:34:05:51", lease_hostname="", probe_hostname="R1-ASIC48"
        ) == ("R1-ASIC48", True)

    def test_generic_dhcp_hostname_defers_to_firmware(self) -> None:
        assert identity.resolve_name(
            "C6:A8:B7:5A:32:58",
            lease_hostname="Antminer",
            probe_hostname="R1-ASIC46",
            hostname_is_duplicate=True,
        ) == ("R1-ASIC46", True)

    def test_duplicate_hostname_is_not_a_name(self) -> None:
        name, named = identity.resolve_name(
            "02:2E:48:DA:AF:69",
            lease_hostname="Antminer",
            probe_hostname="Antminer",
            hostname_is_duplicate=True,
        )
        assert name == "ASIC DAAF69"
        assert named is False

    def test_unnamed_fallback_is_stable_and_readable(self) -> None:
        assert identity.resolve_name("88:66:33:F9:EA:45") == ("ASIC F9EA45", False)


class TestHelpers:
    @pytest.mark.parametrize(
        "hostname", ["Antminer", "antminer", "localhost", "", None, "  UNKNOWN "]
    )
    def test_generic_hostnames(self, hostname: str | None) -> None:
        assert identity.is_generic(hostname)

    def test_real_hostname_is_not_generic(self) -> None:
        assert not identity.is_generic("R1-ASIC7")

    def test_duplicate_detection(self) -> None:
        hostnames = ["R1-ASIC1", "Antminer", "Antminer", None, "", "antminer"]
        assert identity.duplicate_hostnames(hostnames) == {"antminer"}

    def test_rack_parsing(self) -> None:
        assert identity.parse_rack(match("R1-ASIC7")) == ("R1", 7)
        assert identity.parse_rack(None) == (None, None)
        assert identity.rack_from_name("R2-ASIC13", PATTERN) == "R2"
        assert identity.rack_from_name("Antminer", PATTERN) is None
