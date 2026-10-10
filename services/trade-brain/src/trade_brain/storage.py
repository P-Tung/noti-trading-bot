"""Storage contracts for snapshots and future paper-trading ledgers."""

from dataclasses import asdict
from typing import Protocol
from uuid import uuid4

from trade_brain.contracts import MarketSnapshot
from trade_brain.configuration import TradeBrainConfig
from trade_brain.orchestration import DecisionCycleResult
from trade_brain.universe import UniverseSelection


class SnapshotStore(Protocol):
    """Persistence boundary used by the Trade Brain."""

    def save_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Persist a snapshot or raise on a duplicate identifier."""

    def get_snapshot(self, snapshot_id: str) -> MarketSnapshot | None:
        """Return a stored snapshot by identifier."""

    def list_snapshots(self, limit: int = 50) -> list[MarketSnapshot]:
        """Return recent snapshots in descending decision-time order."""

    def load_config(self) -> TradeBrainConfig | None:
        """Return the active server-side configuration, if one exists."""

    def save_config(self, config: TradeBrainConfig) -> None:
        """Persist the active server-side configuration."""

    def list_universe_scans(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent universe scans with exclusion audit counts."""

    def save_evaluation_status(self, status: dict[str, object]) -> None:
        """Persist the active evaluation queue status."""

    def get_evaluation_status(self) -> dict[str, object] | None:
        """Return the latest evaluation queue status."""


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
        self._universe_scans: list[dict[str, object]] = []
        self._evaluation_status: dict[str, object] | None = None
        self._config: TradeBrainConfig | None = None

    def load_config(self) -> TradeBrainConfig | None:
        """Return a copy of the local runtime configuration."""
        return self._config.model_copy(deep=True) if self._config is not None else None

    def save_config(self, config: TradeBrainConfig) -> None:
        """Store a validated runtime configuration for local use."""
        self._config = config.model_copy(deep=True)

    def save_universe_scan(self, selection: UniverseSelection, config: TradeBrainConfig) -> None:
        """Keep the latest local universe audit without requiring a database."""
        scan_id = selection.scan_id
        record = {
            "scan_id": scan_id,
            "observed_at": selection.observed_at.isoformat(),
            "exchange": config.universe.exchange,
            "market_type": "BINANCE_USDM_PERPETUAL",
            "quote_asset": config.universe.quote_asset,
            "min_quote_volume_24h_usdt": config.universe.min_quote_volume_24h_usdt,
            "input_ticker_count": selection.input_ticker_count,
            "registry_count": selection.registry_count,
            "passed_count": selection.passed_count,
            "passed_symbols": list(selection.symbols),
            "exclusion_counts": dict(selection.exclusion_counts),
            "config_version": config.config_version,
        }
        self._universe_scans = [
            existing for existing in self._universe_scans if existing["scan_id"] != scan_id
        ]
        self._universe_scans.insert(0, record)

    def list_universe_scans(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent local universe audits in newest-first order."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return [dict(record) for record in self._universe_scans[:limit]]

    def save_evaluation_status(self, status: dict[str, object]) -> None:
        self._evaluation_status = dict(status)

    def get_evaluation_status(self) -> dict[str, object] | None:
        return dict(self._evaluation_status) if self._evaluation_status is not None else None

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
                    "gate_audit": result.gate_audit,
                },
            )
        return decision_ids

    def list_decision_history(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent local decision records in newest-first order."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return [dict(record) for record in self._decision_history[:limit]]
