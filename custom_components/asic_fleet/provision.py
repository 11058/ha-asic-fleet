"""Router side, done by the integration itself: no scripts on the router.

* The block rule: forward, src-address-list=<blocklist>, out to the WAN
  interface list, drop. Only towards the WAN: a rule for all traffic from a
  blocked miner would also drop its answers to Home Assistant, and a blocked
  miner would then look dead instead of idle.
* Pinned addresses: a miner's DHCP lease is made static before it is
  blocked (and, by default, as soon as it is found), so the blocked IP is
  the one it keeps. That replaces the lease-script older setups used; a
  router that still has one keeps working, the marker list is still kept.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field

from .mikrotik import MikrotikClient, MikrotikError

_LOGGER = logging.getLogger(__name__)

BLOCK_COMMENT = "asic-fleet-block"
USERNAME = "ha-asic"
GROUP = "ha-asic"
CERT = "asic-fleet-rest"
KEY_USAGE = "digital-signature,key-encipherment,tls-server,key-cert-sign"
_DENY = "!local,!telnet,!ssh,!ftp,!reboot,!policy,!test,!winbox,!password,!web,!sniff,!sensitive,!romon"  # noqa: E501


@dataclass
class ProvisionResult:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def new_password() -> str:
    return secrets.token_urlsafe(18)


def onboarding_script(*, ha_ip: str, password: str) -> str:
    """One paste into the router terminal; safe to repeat (RouterOS 7.x)."""
    ha = f"{ha_ip}/32"
    pol = f"read,write,api,rest-api,{_DENY}"
    return "\n".join(
        [
            f"# ASIC Fleet: access for Home Assistant {ha_ip}",
            f":if ([:len [/certificate find name={CERT}]] = 0) do={{ /certificate add name={CERT} common-name={CERT} days-valid=3650 key-usage={KEY_USAGE} }}",  # noqa: E501
            f":if ([:len [/certificate get [find name={CERT}] fingerprint]] = 0) do={{ /certificate set [find name={CERT}] key-usage={KEY_USAGE}; /certificate sign {CERT} }}",  # noqa: E501
            f':do {{ :if ([/ip service get www-ssl certificate] = "none") do={{ /ip service set www-ssl certificate={CERT} }} }} on-error={{ :put "asic-fleet: certificate not ready - wait a minute and paste again" }}',  # noqa: E501
            f':if ([/ip service get www-ssl disabled]) do={{ /ip service set www-ssl address={ha} disabled=no }} else={{ :local a [/ip service get www-ssl address]; :if ([:len $a] > 0 && [:typeof [:find $a {ha}]] = "nil") do={{ /ip service set www-ssl address=($a,"{ha}") }} }}',  # noqa: E501
            f':if ([:len [/user group find name={GROUP}]] = 0) do={{ /user group add name={GROUP} policy={pol} comment="ASIC Fleet (Home Assistant)" }} else={{ /user group set {GROUP} policy={pol} }}',  # noqa: E501
            f':if ([:len [/user find name={USERNAME}]] = 0) do={{ /user add name={USERNAME} group={GROUP} address={ha} password="{password}" }} else={{ /user set {USERNAME} group={GROUP} address={ha} password="{password}" }}',  # noqa: E501
            f':if ([:len [/ip firewall filter find comment="asic-fleet: HA REST"]] = 0) do={{ :local f [/ip firewall filter find chain=input]; :if ([:len $f] > 0) do={{ /ip firewall filter add chain=input action=accept protocol=tcp dst-port=443 src-address={ha_ip} comment="asic-fleet: HA REST" place-before=[:pick $f 0] }} else={{ /ip firewall filter add chain=input action=accept protocol=tcp dst-port=443 src-address={ha_ip} comment="asic-fleet: HA REST" }} }}',  # noqa: E501
        ]
    )


def wan_list_name(lists: list[dict]) -> str | None:
    names = [str(x.get("name", "")) for x in lists]
    for name in names:
        if name.upper() == "WAN":
            return name
    for name in names:
        if "wan" in name.lower():
            return name
    return None


async def async_ensure_block_rule(
    router: MikrotikClient, blocklist: str
) -> ProvisionResult:
    result = ProvisionResult()
    try:
        rules = await router.firewall_filters()
        if any(r.get("comment") == BLOCK_COMMENT for r in rules):
            return result
        wan = wan_list_name(await router.interface_lists())
        if wan is None:
            result.failed.append(
                "no WAN interface list on the router: add the block rule by hand"
            )
            return result
        rule = {
            "chain": "forward",
            "action": "drop",
            "src-address-list": blocklist,
            "out-interface-list": wan,
            "comment": BLOCK_COMMENT,
        }
        # Above everything, accept-established included: a block must stop
        # the pool session that is already running.
        first = next((r.get(".id") for r in rules if r.get("chain") == "forward"), None)
        if first:
            rule["place-before"] = first
        await router.add_firewall_filter(rule)
        result.done.append(f"block rule {blocklist} -> {wan}")
    except MikrotikError as err:
        result.failed.append(f"block rule: {err}")
    return result
