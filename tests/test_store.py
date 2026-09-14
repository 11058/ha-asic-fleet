"""Inventory merge rules.

The store is what keeps a miner from vanishing when it is switched off, so the
rule that matters is: never replace something known with nothing, and only
report a change when it is worth a write to disk.
"""

from __future__ import annotations

from conftest import load_module

models = load_module("models")
StoredMiner = models.StoredMiner


def miner(**kwargs) -> StoredMiner:
    return StoredMiner(mac="AA:BB:CC:DD:EE:FF", **kwargs)


class TestMerge:
    def test_new_information_is_applied(self) -> None:
        existing = miner(name="R1-ASIC7", ip="10.0.0.7")
        assert existing.merge_from(miner(ip="10.0.0.9")) is True
        assert existing.ip == "10.0.0.9"

    def test_blanks_never_overwrite_knowledge(self) -> None:
        # This is the whole point: a miner that is powered off reports nothing,
        # and must keep the name, address and model it had yesterday.
        existing = miner(
            name="R1-ASIC7", ip="10.0.0.7", model="Antminer L7", named=True
        )
        changed = existing.merge_from(miner(name="", ip=None, model=None))
        assert changed is False
        assert (existing.name, existing.ip, existing.model) == (
            "R1-ASIC7",
            "10.0.0.7",
            "Antminer L7",
        )

    def test_unknown_algorithm_is_treated_as_a_blank(self) -> None:
        existing = miner(algorithm="Scrypt")
        assert existing.merge_from(miner(algorithm="unknown")) is False
        assert existing.algorithm == "Scrypt"

    def test_identical_record_is_not_a_change(self) -> None:
        # Steady state must not schedule a disk write on every poll.
        existing = miner(name="R1-ASIC7", ip="10.0.0.7", named=True)
        assert (
            existing.merge_from(miner(name="R1-ASIC7", ip="10.0.0.7", named=True))
            is False
        )

    def test_volatile_fields_alone_do_not_force_a_write(self) -> None:
        existing = miner(name="R1-ASIC7", last_seen="2026-09-13T00:00:00")
        changed = existing.merge_from(
            miner(name="R1-ASIC7", last_seen="2026-09-14T00:00:00")
        )
        assert changed is False
        # ...but the newer timestamp is still kept for the next write.
        assert existing.last_seen == "2026-09-14T00:00:00"


class TestRoundTrip:
    def test_survives_serialisation(self) -> None:
        original = miner(
            name="R1-ASIC7",
            rack="R1",
            index=7,
            manual=True,
            probe={"minertype": "Antminer L7"},
        )
        restored = StoredMiner.from_dict(original.to_dict())
        assert restored == original

    def test_unknown_fields_are_ignored(self) -> None:
        restored = StoredMiner.from_dict(
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "R1-ASIC7", "from_the_future": 1}
        )
        assert restored.name == "R1-ASIC7"
