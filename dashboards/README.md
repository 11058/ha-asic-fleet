# Dashboard

`generate_view.py` builds a Lovelace view for the fleet and, optionally,
installs it as a new tab on an existing dashboard.

```bash
pip install websockets
export HA_URL=http://homeassistant.local:8123
export HA_TOKEN=<long-lived access token>

python generate_view.py                                  # print the view as JSON
python generate_view.py --dashboard lovelace             # install as a tab
python generate_view.py --dashboard lovelace --lang en   # English labels
```

It reads the entity registry over the websocket API, so the view is generated
from the miners that actually exist. Re-run it after adding machines; a view
with the same path (`asic-fleet`) is replaced rather than duplicated.

**Back up the dashboard first** if it holds work you care about — installing
saves the whole config back:

```bash
python - <<'EOF' > dashboard-backup.json
import asyncio, json, os
from generate_view import Client
async def main():
    async with Client(os.environ["HA_URL"], os.environ["HA_TOKEN"]) as c:
        print(json.dumps(await c.call(
            {"type": "lovelace/config", "url_path": "lovelace"}), indent=1))
asyncio.run(main())
EOF
```

## What the view contains

| Section | Contents |
|---|---|
| Fleet | hashrate, power, online / blocked / offline / problem counts, hottest miner, router reachability |
| Needs attention | four filtered lists — offline, active problems, above 88 °C, block not applied — each hidden when empty |
| Rack control | block, unblock and staggered reboot for the whole rack, each behind a confirmation, plus a manual refresh |
| Hashrate and temperature | every miner's number in a compact grid |
| Internet per miner | one tile per machine; the icon toggles, the card opens details |
| Unnamed | miners with no name yet, with the IP, switch port and MAC needed to identify them |

## Layout

The view uses the `sections` layout and built-in cards only — no HACS card
dependencies. Sections reflow from four columns on a laptop to one on a phone,
and the tiles inside them are sized in grid columns rather than pixels, so the
same view works at both sizes without a separate mobile dashboard.

Tapping a miner tile opens its details; only the icon toggles its internet.
That is deliberate — on a phone, a card-wide toggle makes it far too easy to
stop a machine by mis-tapping while scrolling.
