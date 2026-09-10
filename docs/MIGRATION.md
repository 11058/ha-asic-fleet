# Migrating from the YAML package

The predecessor of this integration was a generated `asic_fleet.yaml` package:
`command_line` sensors shelling out to `curl`, `rest_command` entries for the
router, and template switches reading a blocklist sensor's attribute. This
integration replaces all of it. The two can run side by side while you compare
them — that is the recommended path.

## Running both at once

Safe, with two caveats:

1. **Entity ids collide.** The package owns `switch.asic_r1_asic1_internet` and
   friends. The integration creates its own entities from device names, so the
   second one to register gets an `_2` suffix. Decide which set you want to
   keep the clean ids and rename the other set first.

2. **Both write the same address-lists.** That is fine — they read the same
   lists too, so they converge. What differs is the marker `comment` for
   nameless miners: the package only ever wrote hostnames, the integration
   writes `mac:<MAC>` when there is no hostname. Import the updated
   `mikrotik/setup.rsc` (or just the lease-script) before you start, so the
   router understands both forms.

## Cutover

1. Install the integration, confirm every miner appears with a device, and
   compare a handful of hashrate and temperature readings against the package's
   sensors for a day.
2. Confirm the L9 units — the ones with no DHCP hostname — were discovered, and
   give them names with `asic_fleet.assign_name`.
3. Point your dashboards and automations at the new entities.
4. Remove `packages/asic_fleet.yaml`, the `command_line` block, the
   `rest_command` block and `/config/asic_scripts/`, then restart.
5. Delete `/config/secrets/asic_creds` and `/config/secrets/mikrotik_creds` —
   the integration keeps credentials in the config entry, not on disk.

## What changes behaviourally

- **No inventory file.** Miners are discovered from DHCP leases. Adding a rack
  means plugging it in, not editing YAML and regenerating.
- **Identity is the MAC**, not the hostname, so a machine keeps its history
  across a hostname change.
- **Blocking drops live connections**, so a switch flip stops hashing in
  seconds rather than when the pool times the session out.
- **Problems are de-bounced and emit events**, replacing template-based alerting.
- **Polling is concurrent inside one coordinator** — one router read plus a
  bounded fan-out to the miners, instead of 56 independent shell invocations
  every 30 seconds.
