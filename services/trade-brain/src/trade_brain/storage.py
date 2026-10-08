"""Storage contracts for snapshots and future paper-trading ledgers."""

from dataclasses import asdict
from typing import Protocol
from uuid import uuid4

from trade_brain.contracts import MarketSnapshot
from trade_brain.orchestration import DecisionCycleResult


class SnapshotStore(Protocol):
    """Persistence boundary used by the Trade Brain."""

    def save_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Persist a snapshot or raise on a duplicate identifier."""

    def get_snapshot(self, snapshot_id: str) -> MarketSnapshot | None:
        """Return a stored snapshot by identifier."""

    def list_snapshots(self, limit: int = 50) -> list[MarketSnapshot]:
        """Return recent snapshots in descending decision-time order."""


class DecisionHistoryStore(Protocol):
    """Optional persistence boundary for recent Claude decisions."""

    def save_decision_cycle(self, result: DecisionCycleResult) -> dict[str, str]:
        """Persist candidates and validated decisions."""

    def list_decision_history(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent validated decision records."""


class InMemorySnapshotStore:
    """Deterministic store for unit tests and local contract development."""

    def __init__(self) -> None:
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._decision_history: list[dict[str, object]] = []

    def save_snapshot(self, snapshot: MarketSnapshot) -> None:
        if snapshot.snapshot_id in self._snapshots:
            raise ValueError(f"snapshot already exists: {snapshot.snapshot_id}")
        self._snapshots[snapshot.snapshot_id] = snapshot.model_copy(deep=True)

    def get_snapshot(self, snapshot_id: str) -> MarketSnapshot | None:
        snapshot = self._snapshots.get(snapshot_id)
        return snapshot.model_copy(deep=True) if snapshot is not None else None

    def list_snapshots(self, limit: int = 50) -> list[MarketSnapshot]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        snapshots = sorted(
            self._snapshots.values(),
            key=lambda snapshot: snapshot.decision_time,
            reverse=True,
        )
        return [snapshot.model_copy(deep=True) for snapshot in snapshots[:limit]]

    def save_decision_cycle(self, result: DecisionCycleResult) -> dict[str, str]:
        """Keep recent decision records available to the local dashboard."""
        decision_ids: dict[str, str] = {}
        candidates_by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
        for decision in result.decisions:
            decision_id = str(uuid4())
            decision_ids[decision.profile.value] = decision_id
            watched_id = decision.selected_candidate_id or decision.watch_candidate_id
            watched_candidate = candidates_by_id.get(watched_id) if watched_id else None
            risk_result = result.risk_results.get(watched_id) if watched_id else None
            self._decision_history.insert(
                0,
                {
                    "decision_id": decision_id,
                    "snapshot_id": decision.snapshot_id,
                    "profile": decision.profile.value,
                    "decision": decision.decision.value,
                    "selected_candidate_id": decision.selected_candidate_id,
                    "watch_candidate_id": decision.watch_candidate_id,
                    "condition_ids": list(decision.condition_ids),
                    "reason_codes": list(decision.reason_codes),
                    "evidence_ids": list(decision.evidence_ids),
                    "summary_vi": decision.summary_vi,
                    "validation_status": "INVALID" if decision.profile in result.validation_errors else "VALID",
                    "candidate": watched_candidate.model_dump(mode="json") if watched_candidate else None,
                    "risk": asdict(risk_result) if risk_result else None,
                },
            )
        return decision_ids

    def list_decision_history(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent local decision records in newest-first order."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return [dict(record) for record in self._decision_history[:limit]]
