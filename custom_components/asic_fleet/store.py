"""Everything the integration has ever learned about a miner, kept on disk.

Without this the fleet is only as good as the router's current DHCP lease
table: restart Home Assistant while a machine is powered down and its lease has
expired, and the miner — along with its name, its history and its entities —
simply vanishes. Miners are physical objects; they should not disappear from
the inventory because they were switched off.

A record here outlives leases, restarts and outages. Discovery adds to it,
`asic_fleet.forget_device` removes from it, and nothing else deletes a record.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .models import StoredMiner

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Must stay well below the poll interval. Every cycle folds its findings back
# into the inventory, and a debounce as long as the cycle would be reset each
# time just before firing — the file would then only ever be written on a
# clean shutdown, which is exactly the case a crash does not provide.
SAVE_DELAY = 5.0


class MinerStore:
    """Loads, holds and saves the miner inventory."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"asic_fleet.{entry_id}"
        )
        self.miners: dict[str, StoredMiner] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for mac, raw in (data.get("miners") or {}).items():
            try:
                self.miners[mac] = StoredMiner.from_dict({**raw, "mac": mac})
            except TypeError:  # a record written by a future version
                _LOGGER.warning("Skipping unreadable stored miner %s", mac)
        _LOGGER.debug("Loaded %d miners from storage", len(self.miners))

    def save(self) -> None:
        """Queue a debounced write."""
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {"miners": {mac: m.to_dict() for mac, m in self.miners.items()}}

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data())

    # --- mutations -----------------------------------------------------------

    def get(self, mac: str) -> StoredMiner | None:
        return self.miners.get(mac)

    def upsert(self, miner: StoredMiner) -> StoredMiner:
        existing = self.miners.get(miner.mac)
        if existing is None:
            miner.first_seen = miner.first_seen or dt_util.utcnow().isoformat()
            self.miners[miner.mac] = miner
            self.save()
        elif existing.merge_from(miner):
            self.save()
        return self.miners[miner.mac]

    def touch(self, mac: str) -> None:
        if miner := self.miners.get(mac):
            miner.last_seen = dt_util.utcnow().isoformat()
            self.save()

    def forget(self, mac: str) -> bool:
        if mac in self.miners:
            del self.miners[mac]
            self.save()
            return True
        return False

    def clear(self, *, keep_manual: bool = True) -> int:
        removed = 0
        for mac in list(self.miners):
            if keep_manual and self.miners[mac].manual:
                continue
            del self.miners[mac]
            removed += 1
        self.save()
        return removed
