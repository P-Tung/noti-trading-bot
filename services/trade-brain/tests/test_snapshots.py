import unittest
from datetime import datetime, timezone

from trade_brain.contracts import FeatureValue, QualityStatus
from trade_brain.snapshots import create_snapshot
from trade_brain.storage import InMemorySnapshotStore


class SnapshotTests(unittest.TestCase):
    def test_snapshot_has_bounded_expiry_and_is_persisted_immutably(self) -> None:
        decision_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        snapshot = create_snapshot(
            "experiment-1",
            "BTCUSDT",
            decision_time,
            {
                "atr_pct": FeatureValue(
                    value=1.2,
                    unit="percent",
                    observed_at=decision_time,
                    available_at=decision_time,
                    quality_status=QualityStatus.VALID,
                )
            },
        )
        store = InMemorySnapshotStore()
        store.save_snapshot(snapshot)
        loaded = store.get_snapshot(snapshot.snapshot_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.expires_at.timestamp() - decision_time.timestamp(), 60)
        self.assertEqual(loaded.features["atr_pct"].value, 1.2)

    def test_duplicate_snapshot_is_rejected(self) -> None:
        decision_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        snapshot = create_snapshot("experiment-1", "BTCUSDT", decision_time, {})
        store = InMemorySnapshotStore()
        store.save_snapshot(snapshot)
        with self.assertRaises(ValueError):
            store.save_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
