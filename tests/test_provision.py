"""Router-side setup done by the integration (no scripts on the router)."""

from __future__ import annotations

from conftest import load_module

provision = load_module("provision")
models = load_module("models")


def test_wan_list_choice():
    assert provision.wan_list_name([{"name": "LAN"}, {"name": "WAN"}]) == "WAN"
    assert provision.wan_list_name([{"name": "lan"}, {"name": "isp-wan"}]) == "isp-wan"
    assert provision.wan_list_name([{"name": "LAN"}]) is None


def test_onboarding_script():
    s = provision.onboarding_script(ha_ip="10.5.20.5", password="pw")
    assert 'password="pw"' in s and "address=10.5.20.5/32" in s
    assert "key-cert-sign" in s  # RouterOS 7.25 will not self-sign without it
    assert "/user add name=ha-asic" in s
    for line in s.splitlines()[1:]:
        assert line.startswith((":if (", ":do {")), line


def test_plug_survives_quiet_merge():
    stored = models.StoredMiner(mac="AA", plug="switch.rozetka_1")
    assert not stored.merge_from(models.StoredMiner(mac="AA"))
    assert stored.plug == "switch.rozetka_1"
