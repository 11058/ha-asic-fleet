"""The miner record as it is stored, and the rules for merging one into another.

Kept free of Home Assistant imports so the merge rules — the thing standing
between a switched-off miner and its disappearance — can be tested directly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Fields that change constantly and on their own are not a reason to write.
VOLATILE_FIELDS = frozenset({"last_seen", "probe"})

# Facts that only ever get established, never withdrawn by a quiet poll. A
# miner that has been named stays named when discovery next sees nothing, and
# one added by hand stays hand-added even after DHCP finds it on its own.
STICKY_TRUE_FIELDS = frozenset({"named", "manual"})


@dataclass
class StoredMiner:
    """One miner as last known, independent of whether it answers today."""

    mac: str
    name: str = ""
    rack: str | None = None
    index: int | None = None
    marker: str = ""
    lease_hostname: str | None = None
    ip: str | None = None
    port: str | None = None
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    algorithm: str = "unknown"
    named: bool = False
    # Added by hand through the service rather than found on the network.
    manual: bool = False
    first_seen: str | None = None
    last_seen: str | None = None
    # Smart plug entity feeding this miner ("" = none).
    plug: str = ""
    # Raw get_system_info payload, so a miner that is offline at startup does
    # not have to be re-probed before it can be identified.
    probe: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredMiner:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def merge_from(self, other: StoredMiner) -> bool:
        """Apply another record over this one; True if anything worth saving changed.

        Never overwrites something known with nothing: a miner that is offline
        today keeps yesterday's model, name and address.
        """
        changed = False
        for key, value in other.to_dict().items():
            if value in (None, "", {}, "unknown"):
                continue
            if key in STICKY_TRUE_FIELDS and not value:
                continue
            if getattr(self, key) == value:
                continue
            setattr(self, key, value)
            if key not in VOLATILE_FIELDS:
                changed = True
        return changed
