# ASIC Fleet

Home Assistant integration for running a mining farm of Antminer-class ASICs
behind a MikroTik router: live telemetry per machine, one switch per machine to
cut its internet access, rack-level bulk operations, and problem detection that
fires events you can route anywhere.

Built for **Promminer** firmware (verified on `Promminer_L7_7007`), which
accepts but silently ignores its own sleep mode — so the router's firewall is
the only reliable way to stop a machine hashing without pulling power.

## What it gives you

**Per miner** (one HA device each, keyed by MAC address):

| Entity | Notes |
|---|---|
| `switch` Internet | off = firewall drops this miner's WAN traffic |
| `sensor` Hashrate, Hashrate average, Efficiency | 5 s / 30 m / lifetime, % of nominal, normalised to MH/s |
| `sensor` Power | wall watts, on firmware that reports it (L9 does, L7 does not) |
| `sensor` Temperature max / average | hottest chip across all chains |
| `sensor` Fan min / max | rpm |
| `sensor` Chip health | working chips ÷ total, from the chain chip map |
| `sensor` Hardware errors, Uptime | diagnostics |
| `sensor` Status | online / offline / blocked / degraded / critical |
| `sensor` IP address, Switch port, Pool, Last seen | diagnostics |
| `binary_sensor` Online, Problem, Overheating, Hashrate problem, Hardware problem, Block out of sync | |
| `button` Reboot, Locate | Locate flashes the miner's LED |

**Fleet-wide**: total hashrate, total power, miners total / online / offline /
blocked / with problems, hottest miner, unnamed miners, router reachability.

Hashrate is normalised to **MH/s** whatever the miner reports — an L7 reports
MH/s and an L9 reports GH/s for numbers of the same magnitude, so a fleet total
that did not convert would be meaningless. The miner's own unit is kept as the
`reported_unit` attribute.

## Identity: MAC first, hostname second

Most miners publish a DHCP hostname like `R1-ASIC7`, and the integration parses
the rack and index straight out of it. Some — notably the Antminer L9 — never
send one at all.

Rather than depend on a hostname that may not exist, every device is keyed by
**MAC address**, which a DHCP lease always carries and which never changes.
On top of that:

- **Auto-discovery of nameless miners.** Any DHCP lease that is not already a
  known miner gets asked, once, whether it answers `get_system_info.cgi`. If it
  does, it is a miner: the integration adopts it, reads its model, and fires an
  `asic_fleet_discovered` event. Leases that do not answer are re-checked only
  every 20 cycles, so the probe costs nothing in steady state.
- **Switch port as a location hint.** The bridge host table maps MAC to bridge
  port, exposed as the `Switch port` diagnostic sensor — that is how you find
  the physical machine, but it is deliberately *not* the identity key: a port
  tells you where a cable goes, and says nothing useful if there is an
  unmanaged switch behind it.
- **Naming.** A DHCP hostname is used when it is unique and not one of the
  stock placeholders (`Antminer`, `localhost`, …). Failing that, the miner's
  own configured hostname — which stock firmware reports over HTTP even when
  its DHCP client never sends it — is used. Failing that too, the miner shows
  up as `ASIC AABBCC` and raises an `unnamed` info-level problem so it does not
  get forgotten; `asic_fleet.assign_name` gives it a permanent name and rack,
  keyed by MAC.

## How blocking works

Two RouterOS address-lists, one firewall rule:

- `asic_blocked` — the IP entries the `forward` drop rule actually matches.
- `asic_blocked_hosts` — *markers*. The `comment` carries the miner's DHCP
  hostname when that hostname matches the site's naming pattern, and
  `mac:<MAC>` otherwise. This list expresses **intent** and survives DHCP lease
  churn. The address is a placeholder drawn from the reserved 240.0.0.0/4
  range — deliberately not `0.0.0.0`, which RouterOS would widen to "every
  address" if the list were ever referenced by a filter rule.

  Requiring the hostname to match the pattern is not pedantry: stock Bitmain
  units all announce themselves as `Antminer`, and letting them share a marker
  would mean blocking one blocks every one of them.

A DHCP lease-script on the router mirrors intent into reality on every lease
event, so a miner that renews into a new IP stays blocked. Home Assistant
writes both lists directly when you flip a switch and drops the miner's live
connections, so a block takes effect immediately rather than when the pool
notices.

If the marker is present but no matching IP entry is, the `Block out of sync`
binary sensor goes on — that is the failure mode where a machine looks blocked
in the UI but is quietly still hashing.

## Alerts

The integration detects and de-bounces:

| Problem | Severity | Raised when |
|---|---|---|
| `offline` | critical | no answer for longer than the grace period |
| `hashrate_zero` | critical | reported hashrate is 0 while not blocked |
| `overheat_critical` | critical | hottest chip ≥ critical threshold |
| `overheat` | warning | hottest chip ≥ warning threshold |
| `hashrate_low` | warning | below *n* % of nominal while not blocked |
| `fan` | warning | slowest fan below the rpm floor |
| `chips` | warning | chain chip map shows a dead chip |
| `hw_errors` | warning | hardware error rate above threshold |
| `block_desync` | warning | block intent not reflected in the IP list |
| `ip_changed` | info | miner's lease moved to a different address |
| `unnamed` | info | miner has no name yet |

Each raise fires `asic_fleet_problem`, each recovery fires
`asic_fleet_cleared`, both carrying `mac`, `name`, `rack`, `ip`, `port`,
`problem`, `severity` and a `details` payload. A ready-made blueprint
(`blueprints/automation/asic_fleet/asic_fleet_alerts.yaml`) turns those into
notifications wherever you want them, filtered by severity and rack.

Problems must persist for *n* consecutive polls (default 3) before they are
raised, so a single dropped packet does not page anyone at 3 a.m.

## Services

| Service | Does |
|---|---|
| `asic_fleet.block` / `unblock` | cut or restore internet for the targeted miners |
| `asic_fleet.reboot` | reboot via `reboot.cgi` |
| `asic_fleet.blink` | flash the locate LED |
| `asic_fleet.block_rack` / `unblock_rack` | same, for every miner in a rack |
| `asic_fleet.reboot_rack` | rack reboot with a `stagger` delay so load ramps |
| `asic_fleet.assign_name` | name a miner by MAC |
| `asic_fleet.set_pools` | rewrite the pool list (off by default, see below) |
| `asic_fleet.refresh` | poll immediately |

`set_pools` is disabled until you turn on **Allow pool configuration writes** in
the integration options. The firmware's `set_miner_conf.cgi` replaces the whole
config rather than merging, so the integration reads the current config first
and changes only the pool block — but a bad write still lands on the miner, so
it stays opt-in.

## Install

### HACS

1. HACS → ⋮ → Custom repositories → add `https://github.com/11058/ha-asic-fleet`, category *Integration*.
2. Install **ASIC Fleet**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **ASIC Fleet**.

### Manual

Copy `custom_components/asic_fleet` into your `config/custom_components/` and
restart.

### Router side

Edit the tunables at the top of `mikrotik/setup.rsc` (HA's IP, WAN interface
list, DHCP server name, a strong password) and import it:

```
/import file-name=setup.rsc
```

It creates the restricted `ha-asic` user, the drop rule, the lease-script and
the REST service. Everything in it is idempotent.

## Configuration

Setup asks for the router (host, user, password, plain HTTP or TLS) and the
miners' web credentials (Antminer default `root` / `root`). Options let you
tune the poll interval, the hostname regex, alert thresholds, the de-bounce
count, and whether reboot and pool writes are permitted.

## Requirements

- Home Assistant 2025.2 or newer
- RouterOS v7 with the REST service reachable from Home Assistant
- Miners exposing the Antminer/Promminer CGI API over HTTP Digest

## License

MIT
