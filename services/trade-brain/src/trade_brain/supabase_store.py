"""Supabase persistence adapter for immutable market snapshots."""

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from trade_brain.contracts import FeatureValue, MarketSnapshot, PaperMode, QualityStatus
from trade_brain.configuration import TradeBrainConfig, config_from_payload, config_to_payload
from trade_brain.orchestration import DecisionCycleResult
from trade_brain.paper import PaperAccount, PaperRecommendation, PaperState
from trade_brain.reporting import PaperReport
from trade_brain.contracts import Profile
from trade_brain.universe import UniverseSelection


class SupabaseQuery(Protocol):
    def execute(self) -> object:
        """Execute a Supabase query."""


class SupabaseTable(Protocol):
    def insert(self, values: dict[str, object]) -> SupabaseQuery:
        """Insert one row."""

    def select(self, columns: str) -> SupabaseQuery:
        """Select rows."""

    def eq(self, column: str, value: str) -> SupabaseQuery:
        """Add an equality filter."""

    def order(self, column: str, desc: bool = False) -> SupabaseQuery:
        """Order query results."""

    def limit(self, count: int) -> SupabaseQuery:
        """Limit query results."""

    def update(self, values: dict[str, object]) -> SupabaseQuery:
        """Update matching rows."""

    def upsert(self, values: dict[str, object]) -> SupabaseQuery:
        """Insert or update a row."""


class DecisionCycleStore(Protocol):
    def save_decision_cycle(self, result: DecisionCycleResult) -> dict[str, str]:
        """Persist candidates and validated Claude decisions."""


class SupabaseClient(Protocol):
    def table(self, name: str) -> SupabaseTable:
        """Return a table query builder."""


class SupabaseSnapshotStore:
    """Write snapshots through the server-side Supabase client."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    @classmethod
    def from_environment(cls) -> "SupabaseSnapshotStore":
        """Create a store from server-only environment variables."""
        url = os.environ.get("SUPABASE_URL")
        secret_key = os.environ.get("SUPABASE_SECRET_KEY")
        if not url or not secret_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
        from supabase import create_client

        return cls(create_client(url, secret_key))

    def save_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Insert one immutable snapshot and reject remote errors."""
        response = self._client.table("market_snapshots").insert(self._to_row(snapshot)).execute()
        error = getattr(response, "error", None)
        if error:
            raise RuntimeError(f"Supabase snapshot insert failed: {error}")

    def load_config(self) -> TradeBrainConfig | None:
        """Load the active configuration through the server-only client."""
        response = (
            self._client.table("trade_brain_configs")
            .select("payload")
            .eq("config_id", "active")
            .limit(1)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase config lookup failed: {response.error}")
        rows = getattr(response, "data", None) or []
        if not rows:
            return None
        config = config_from_payload(rows[0].get("payload"))
        if rows[0].get("payload", {}).get("config_version") == "config-v1":
            self.save_config(config)
        return config

    def save_config(self, config: TradeBrainConfig) -> None:
        """Upsert the active configuration without exposing it to clients."""
        response = self._client.table("trade_brain_configs").upsert(
            {
                "config_id": "active",
                "config_version": config.config_version,
                "payload": config_to_payload(config),
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase config save failed: {response.error}")

    def save_universe_scan(self, selection: UniverseSelection, config: TradeBrainConfig) -> None:
        """Persist one V2 universe refresh and its exclusion counts."""
        response = self._client.table("universe_scans").upsert(
            {
                "scan_id": selection.scan_id,
                "observed_at": selection.observed_at.isoformat(),
                "exchange": config.universe.exchange,
                "market_type": "BINANCE_USDM_PERPETUAL",
                "quote_asset": config.universe.quote_asset,
                "min_quote_volume_24h_usdt": config.universe.min_quote_volume_24h_usdt,
                "input_ticker_count": selection.input_ticker_count,
                "registry_count": selection.registry_count,
                "passed_count": selection.passed_count,
                "passed_symbols": list(selection.symbols),
                "exclusion_counts": selection.exclusion_counts,
                "config_version": config.config_version,
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase universe scan save failed: {response.error}")

    def list_universe_scans(self, limit: int = 20) -> list[dict[str, object]]:
        """Read recent universe audits for the dashboard and operators."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        response = (
            self._client.table("universe_scans")
            .select("*")
            .order("observed_at", desc=True)
            .limit(limit)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase universe scan list failed: {response.error}")
        rows = getattr(response, "data", None)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def save_evaluation_status(self, status: dict[str, object]) -> None:
        """Persist shared queue progress for the dashboard and Telegram worker."""
        response = self._client.table("evaluation_queue_status").upsert(
            {
                "status_id": "active",
                "run_id": status.get("run_id"),
                "status": status.get("status", "IDLE"),
                "started_at": status.get("started_at"),
                "finished_at": status.get("finished_at"),
                "total_count": status.get("total_count", 0),
                "completed_count": status.get("completed_count", 0),
                "current_index": status.get("current_index", 0),
                "current_symbol": status.get("current_symbol"),
                "symbols": status.get("symbols", []),
                "result_summary": status.get("result_summary", []),
                "error": status.get("error"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase evaluation status save failed: {response.error}")

    def get_evaluation_status(self) -> dict[str, object] | None:
        """Read the latest shared queue state."""
        response = (
            self._client.table("evaluation_queue_status")
            .select("*")
            .eq("status_id", "active")
            .limit(1)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase evaluation status read failed: {response.error}")
        rows = getattr(response, "data", None) or []
        return dict(rows[0]) if rows and isinstance(rows[0], dict) else None

    def list_telegram_chat_ids(self) -> tuple[str, ...]:
        """Load all Telegram chats that opted in with /start."""
        response = (
            self._client.table("telegram_subscribers")
            .select("chat_id")
            .order("created_at")
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase Telegram subscriber read failed: {response.error}")
        rows = getattr(response, "data", None) or []
        return tuple(
            str(row["chat_id"])
            for row in rows
            if isinstance(row, dict) and row.get("chat_id")
        )

    def register_telegram_chat_id(self, chat_id: str) -> None:
        """Persist a Telegram chat that opted in with /start."""
        response = self._client.table("telegram_subscribers").upsert(
            {"chat_id": str(chat_id), "last_seen_at": datetime.now(timezone.utc).isoformat()}
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase Telegram subscriber write failed: {response.error}")

    def ensure_experiment(
        self,
        experiment_id: str,
        name: str = "Trade V2 Paper Research",
        data_mode: str = "PRICE_ONLY",
        config_version: str = "config-v2",
        initial_equity_usdt: float = 10000,
        paper_mode: PaperMode = PaperMode.RESEARCH_PAPER,
    ) -> None:
        """Create the paper experiment once, preserving restart safety."""
        existing = (
            self._client.table("experiments")
            .select("experiment_id")
            .eq("experiment_id", experiment_id)
            .execute()
        )
        if getattr(existing, "error", None):
            raise RuntimeError(f"Supabase experiment lookup failed: {existing.error}")
        if getattr(existing, "data", None):
            return
        response = self._client.table("experiments").insert(
            {
                "experiment_id": experiment_id,
                "name": name,
                "execution_mode": "PAPER",
                "data_mode": data_mode,
                "config_version": config_version,
                "initial_equity_usdt": initial_equity_usdt,
                "paper_mode": paper_mode.value,
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase experiment insert failed: {response.error}")

    def get_snapshot(self, snapshot_id: str) -> MarketSnapshot | None:
        """Read one snapshot and reconstruct the typed contract."""
        response = (
            self._client.table("market_snapshots")
            .select("*")
            .eq("snapshot_id", snapshot_id)
            .execute()
        )
        error = getattr(response, "error", None)
        if error:
            raise RuntimeError(f"Supabase snapshot read failed: {error}")
        data = getattr(response, "data", None)
        if not isinstance(data, list) or not data:
            return None
        return self._from_row(data[0])

    def list_snapshots(self, limit: int = 50) -> list[MarketSnapshot]:
        """Read recent snapshots for the dashboard API."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        response = (
            self._client.table("market_snapshots")
            .select("*")
            .order("decision_time", desc=True)
            .limit(limit)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase snapshot list failed: {response.error}")
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            return []
        return [self._from_row(row) for row in data]

    def save_decision_cycle(self, result: DecisionCycleResult) -> dict[str, str]:
        """Persist backend candidates and Claude outputs for audit and journaling."""
        for candidate in result.candidates:
            risk_result = result.risk_results[candidate.candidate_id]
            response = self._client.table("trade_candidates").upsert(
                {
                    "candidate_id": candidate.candidate_id,
                    "snapshot_id": candidate.snapshot_id,
                    "setup_id": candidate.setup_id,
                    "profile": candidate.profile.value,
                    "strategy": candidate.strategy,
                    "entry_stage": candidate.entry_stage,
                    "side": candidate.side.value,
                    "entry_estimate": candidate.entry_estimate,
                    "stop_price": candidate.stop_price,
                    "target_price": candidate.target_price,
                    "horizon_bars": candidate.horizon_bars,
                    "statistics_status": candidate.statistics_status.value,
                    "statistics": candidate.statistics,
                    "eligibility": {
                        "eligible": candidate.eligible,
                        "reasons": candidate.eligibility_reasons,
                        "risk_codes": list(risk_result.codes),
                        "risk_budget_usdt": risk_result.risk_budget_usdt,
                        "quantity_allowed": risk_result.quantity_allowed,
                    },
                }
            ).execute()
            if getattr(response, "error", None):
                raise RuntimeError(f"Supabase candidate insert failed: {response.error}")

        decision_ids: dict[str, str] = {}
        for decision in result.decisions:
            validation_status = "INVALID" if decision.profile in result.validation_errors else "VALID"
            if "DECISION_SERVICE_ERROR" in decision.reason_codes:
                validation_status = "SERVICE_ERROR"
            decision_id = str(
                uuid5(NAMESPACE_URL, f"trade-v1-decision:{decision.snapshot_id}:{decision.profile.value}")
            )
            response = self._client.table("claude_decisions").upsert(
                {
                    "decision_id": decision_id,
                    "snapshot_id": decision.snapshot_id,
                    "profile": decision.profile.value,
                    "decision": decision.decision.value,
                    "selected_candidate_id": decision.selected_candidate_id,
                    "watch_candidate_id": decision.watch_candidate_id,
                    "condition_ids": decision.condition_ids,
                    "reason_codes": decision.reason_codes,
                    "evidence_ids": decision.evidence_ids,
                    "summary_vi": decision.summary_vi,
                    "gate_audit": result.gate_audit,
                    "validation_status": validation_status,
                    "raw_response": decision.model_dump(mode="json"),
                    "model": _audit_value(result.claude_audit, "model"),
                    "prompt_version": _audit_value(result.claude_audit, "prompt_version"),
                    "schema_version": _audit_value(result.claude_audit, "schema_version"),
                    "payload_hash": _audit_value(result.claude_audit, "payload_hash"),
                    "latency_ms": _audit_value(result.claude_audit, "latency_ms"),
                }
            ).execute()
            if getattr(response, "error", None):
                raise RuntimeError(f"Supabase decision insert failed: {response.error}")
            decision_ids[decision.profile.value] = decision_id
        return decision_ids

    def list_decision_history(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent Claude decisions for the read-only dashboard."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        response = (
            self._client.table("claude_decisions")
            .select(
                "decision_id,snapshot_id,profile,decision,selected_candidate_id,"
                "watch_candidate_id,condition_ids,reason_codes,evidence_ids,"
                "summary_vi,validation_status,created_at"
            )
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase decision history read failed: {response.error}")
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            return []
        decisions = [dict(row) for row in data if isinstance(row, dict)]
        for decision in decisions:
            candidate_id = decision.get("selected_candidate_id") or decision.get("watch_candidate_id")
            if not isinstance(candidate_id, str):
                continue
            candidate_response = (
                self._client.table("trade_candidates")
                .select("*")
                .eq("candidate_id", candidate_id)
                .limit(1)
                .execute()
            )
            candidate_data = getattr(candidate_response, "data", None)
            if isinstance(candidate_data, list) and candidate_data and isinstance(candidate_data[0], dict):
                candidate = dict(candidate_data[0])
                decision["candidate"] = candidate
                decision["risk"] = candidate.get("eligibility")
        return decisions

    def ensure_paper_accounts(
        self,
        experiment_id: str,
        initial_equity_usdt: float = 10000,
    ) -> dict[str, str]:
        """Create one stable paper account per profile for an experiment."""
        account_ids: dict[str, str] = {}
        for profile in ("PROACTIVE", "BALANCED", "CAUTIOUS"):
            existing = (
                self._client.table("paper_accounts")
                .select("account_id")
                .eq("experiment_id", experiment_id)
                .eq("profile", profile)
                .execute()
            )
            if getattr(existing, "error", None):
                raise RuntimeError(f"Supabase paper account lookup failed: {existing.error}")
            data = getattr(existing, "data", None)
            if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("account_id"):
                account_ids[profile] = str(data[0]["account_id"])
                continue
            account_id = str(uuid5(NAMESPACE_URL, f"trade-v1:{experiment_id}:{profile}"))
            response = self._client.table("paper_accounts").insert(
                {
                    "account_id": account_id,
                    "experiment_id": experiment_id,
                    "profile": profile,
                    "initial_equity_usdt": initial_equity_usdt,
                    "current_equity_usdt": initial_equity_usdt,
                }
            ).execute()
            if getattr(response, "error", None):
                raise RuntimeError(f"Supabase paper account insert failed: {response.error}")
            account_ids[profile] = account_id
        return account_ids

    def save_paper_recommendation(
        self,
        experiment_id: str,
        account_id: str,
        decision_id: str,
        recommendation: PaperRecommendation,
        symbol: str | None = None,
    ) -> None:
        """Persist a new paper recommendation with its audit payload."""
        response = self._client.table("paper_recommendations").upsert(
            {
                "recommendation_id": recommendation.recommendation_id,
                "account_id": account_id,
                "decision_id": decision_id,
                "candidate_id": recommendation.candidate.candidate_id,
                "setup_id": recommendation.candidate.setup_id,
                "state": _db_recommendation_state(recommendation.state),
                "emitted_at": recommendation.emitted_at.isoformat(),
                "payload": {
                    "experiment_id": experiment_id,
                    "profile": recommendation.profile.value,
                    **_recommendation_payload(recommendation),
                },
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper recommendation insert failed: {response.error}")
        self.append_paper_ledger_event(experiment_id, recommendation, "RECOMMENDED", account_id)
        if symbol:
            self.project_paper_state(experiment_id, symbol, recommendation, account_id=account_id)

    def project_paper_state(
        self,
        experiment_id: str,
        symbol: str,
        recommendation: PaperRecommendation,
        account_id: str | None = None,
        equity: Decimal | None = None,
        initial_equity: Decimal | None = None,
        realized_equity: Decimal | None = None,
        drawdown_pct: Decimal | None = None,
    ) -> None:
        """Write normalized position, fill, and optional equity projections."""
        resolved_account_id = account_id or str(
            uuid5(NAMESPACE_URL, f"trade-v1:{experiment_id}:{recommendation.profile.value}")
        )
        position_state = _projection_position_state(recommendation.state)
        self._client.table("paper_positions").upsert(
            {
                "position_id": str(
                    uuid5(NAMESPACE_URL, f"trade-v1-position:{recommendation.recommendation_id}")
                ),
                "account_id": resolved_account_id,
                "recommendation_id": recommendation.recommendation_id,
                "setup_id": recommendation.candidate.setup_id,
                "symbol": symbol,
                "side": recommendation.candidate.side.value,
                "state": position_state,
                "quantity": _decimal_value(recommendation.quantity),
                "entry_fill": _decimal_value(recommendation.entry_fill),
                "initial_stop": recommendation.candidate.stop_price,
                "target_price": recommendation.candidate.target_price,
                "opened_at": recommendation.opened_at.isoformat() if recommendation.opened_at else None,
                "closed_at": recommendation.closed_at.isoformat() if recommendation.closed_at else None,
                "updated_at": (
                    recommendation.last_quote_at or recommendation.closed_at or recommendation.emitted_at
                ).isoformat(),
                "payload": _recommendation_payload(recommendation),
            }
        ).execute()
        if recommendation.entry_fill is not None and recommendation.opened_at is not None:
            self._upsert_paper_fill(recommendation, "ENTRY", recommendation.entry_fill, recommendation.opened_at)
        if recommendation.exit_fill is not None and recommendation.closed_at is not None:
            self._upsert_paper_fill(recommendation, "EXIT", recommendation.exit_fill, recommendation.closed_at)
        if equity is not None:
            sampled_at = recommendation.last_quote_at or recommendation.closed_at or recommendation.emitted_at
            self._client.table("equity_samples").upsert(
                {
                    "sample_id": str(
                        uuid5(
                            NAMESPACE_URL,
                            f"trade-v1-equity:{resolved_account_id}:{sampled_at.isoformat()}",
                        )
                    ),
                    "account_id": resolved_account_id,
                    "sampled_at": sampled_at.isoformat(),
                    "equity_usdt": _decimal_value(equity),
                    "realized_pnl_usdt": _decimal_value(
                        (realized_equity or equity) - (initial_equity or Decimal("0"))
                    ),
                    "unrealized_pnl_usdt": _decimal_value(equity - (realized_equity or equity)),
                    "drawdown_pct": _decimal_value(drawdown_pct or Decimal("0")),
                    "data_quality": recommendation.data_quality.value,
                    "payload": {"recommendation_id": recommendation.recommendation_id},
                }
            ).execute()

    def _upsert_paper_fill(
        self,
        recommendation: PaperRecommendation,
        fill_role: str,
        price: Decimal,
        occurred_at: datetime,
    ) -> None:
        idempotency_key = f"{recommendation.recommendation_id}:{fill_role}:{occurred_at.isoformat()}"
        fill_id = str(uuid5(NAMESPACE_URL, f"trade-v1-fill:{idempotency_key}"))
        self._client.table("paper_fills").upsert(
            {
                "fill_id": fill_id,
                "recommendation_id": recommendation.recommendation_id,
                "fill_role": fill_role,
                "side": recommendation.candidate.side.value,
                "quantity": _decimal_value(recommendation.quantity),
                "price": _decimal_value(price),
                "fee_usdt": 0,
                "slippage_usdt": 0,
                "occurred_at": occurred_at.isoformat(),
                "idempotency_key": idempotency_key,
                "payload": {"data_quality": recommendation.data_quality.value},
            }
        ).execute()

    def append_paper_ledger_event(
        self,
        experiment_id: str,
        recommendation: PaperRecommendation,
        event_type: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """Upsert one deterministic lifecycle event into the append-only ledger."""
        resolved_event_type = event_type or _ledger_event_type(recommendation.state)
        occurred_at = (
            recommendation.last_quote_at
            or recommendation.closed_at
            or recommendation.opened_at
            or recommendation.emitted_at
        )
        event_key = f"{recommendation.recommendation_id}:{resolved_event_type}:{occurred_at.isoformat()}"
        ledger_event_id = str(uuid5(NAMESPACE_URL, f"trade-v1-ledger:{event_key}"))
        resolved_account_id = account_id or str(
            uuid5(NAMESPACE_URL, f"trade-v1:{experiment_id}:{recommendation.profile.value}")
        )
        response = self._client.table("paper_ledger").upsert(
            {
                "ledger_event_id": ledger_event_id,
                "experiment_id": experiment_id,
                "account_id": resolved_account_id,
                "recommendation_id": recommendation.recommendation_id,
                "event_type": resolved_event_type,
                "occurred_at": occurred_at.isoformat(),
                "payload": _recommendation_payload(recommendation),
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper ledger upsert failed: {response.error}")

    def update_paper_recommendation(self, recommendation: PaperRecommendation) -> None:
        """Update the durable paper recommendation state and lifecycle payload."""
        response = (
            self._client.table("paper_recommendations")
            .update(
                {
                    "state": _db_recommendation_state(recommendation.state),
                    "payload": _recommendation_payload(recommendation),
                }
            )
            .eq("recommendation_id", recommendation.recommendation_id)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper recommendation update failed: {response.error}")

    def save_paper_trade(self, recommendation: PaperRecommendation) -> None:
        """Upsert a closed paper trade into the journal."""
        response = self._client.table("paper_trades").upsert(
            {
                "recommendation_id": recommendation.recommendation_id,
                "entry_fill": _decimal_value(recommendation.entry_fill),
                "exit_fill": _decimal_value(recommendation.exit_fill),
                "quantity": _decimal_value(recommendation.quantity),
                "initial_stop": recommendation.candidate.stop_price,
                "net_pnl_usdt": _decimal_value(recommendation.net_pnl),
                "net_r": _decimal_value(recommendation.net_r),
                "outcome": _db_trade_outcome(recommendation.outcome),
                "entry_time": recommendation.opened_at.isoformat() if recommendation.opened_at else None,
                "exit_time": recommendation.closed_at.isoformat() if recommendation.closed_at else None,
                "costs": {"funding_pnl": str(recommendation.funding_pnl)},
            }
        ).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper trade upsert failed: {response.error}")

    def load_paper_recommendations(self, limit: int = 500) -> list[PaperRecommendation]:
        """Reconstruct persisted paper recommendations for process restart recovery."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        response = (
            self._client.table("paper_recommendations")
            .select("*")
            .order("emitted_at", desc=True)
            .limit(limit)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper recommendation load failed: {response.error}")
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            return []
        return [_recommendation_from_row(row) for row in data if isinstance(row, dict)]

    def load_paper_accounts(self, limit: int = 3) -> list[PaperAccount]:
        """Load the independent profile equity ledgers."""
        response = self._client.table("paper_accounts").select("*").limit(limit).execute()
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper account load failed: {response.error}")
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            return []
        return [
            PaperAccount(
                profile=Profile(str(row["profile"])),
                initial_equity=Decimal(str(row["initial_equity_usdt"])),
                current_equity=Decimal(str(row["current_equity_usdt"])),
                realized_pnl=Decimal(str(row.get("current_equity_usdt", 0)))
                - Decimal(str(row["initial_equity_usdt"])),
            )
            for row in data
            if isinstance(row, dict)
        ]

    def update_paper_account(
        self,
        experiment_id: str,
        profile: Profile,
        current_equity: Decimal,
    ) -> None:
        """Persist realized equity after a closed paper trade."""
        response = (
            self._client.table("paper_accounts")
            .update({"current_equity_usdt": float(current_equity)})
            .eq("experiment_id", experiment_id)
            .eq("profile", profile.value)
            .execute()
        )
        if getattr(response, "error", None):
            raise RuntimeError(f"Supabase paper account update failed: {response.error}")

    def save_paper_report(
        self,
        experiment_id: str,
        report: PaperReport,
        config_version: str = "config-v1",
    ) -> None:
        """Persist a deterministic cohort and its latest report artifact."""
        profile = report.profile.value if report.profile is not None else None
        cohort_key = f"{experiment_id}:{report.scope.value}:{profile or 'ALL'}:{report.milestone}"
        cohort_id = str(uuid5(NAMESPACE_URL, f"trade-v1-cohort:{cohort_key}"))
        cohort_row = {
            "cohort_id": cohort_id,
            "experiment_id": experiment_id,
            "scope": report.scope.value,
            "profile": profile,
            "milestone": report.milestone,
            "status": report.cohort_status,
            "member_ids": list(report.member_ids),
            "config_version": config_version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        cohort_response = self._client.table("evaluation_cohorts").upsert(cohort_row).execute()
        if getattr(cohort_response, "error", None):
            raise RuntimeError(f"Supabase cohort upsert failed: {cohort_response.error}")

        report_status = report.cohort_status
        report_id = str(uuid5(NAMESPACE_URL, f"trade-v1-report:{cohort_id}"))
        report_response = self._client.table("paper_reports").upsert(
            {
                "report_id": report_id,
                "cohort_id": cohort_id,
                "report_version": 1,
                "status": report_status,
                "report": _report_payload(report),
            }
        ).execute()
        if getattr(report_response, "error", None):
            raise RuntimeError(f"Supabase report upsert failed: {report_response.error}")

        for ordinal, member_id in enumerate(report.member_ids, start=1):
            member_response = self._client.table("evaluation_cohort_members").upsert(
                {
                    "cohort_id": cohort_id,
                    "ordinal": ordinal,
                    "recommendation_id": member_id,
                    "setup_id": member_id,
                }
            ).execute()
            if getattr(member_response, "error", None):
                raise RuntimeError(f"Supabase cohort member upsert failed: {member_response.error}")

        job_response = self._client.table("report_jobs").upsert(
            {
                "job_id": str(uuid5(NAMESPACE_URL, f"trade-v1-report-job:{cohort_key}")),
                "experiment_id": experiment_id,
                "scope": report.scope.value,
                "profile": profile,
                "milestone": report.milestone,
                "status": "SUCCEEDED",
                "idempotency_key": f"{experiment_id}:{report.scope.value}:{profile or 'ALL'}:{report.milestone}",
                "attempts": 1,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
        if getattr(job_response, "error", None):
            raise RuntimeError(f"Supabase report job upsert failed: {job_response.error}")

        milestone_response = self._client.table("report_milestones").upsert(
            {
                "milestone_key": cohort_key,
                "experiment_id": experiment_id,
                "cohort_id": cohort_id,
                "scope": report.scope.value,
                "profile": profile,
                "milestone": report.milestone,
                "status": report_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
        if getattr(milestone_response, "error", None):
            raise RuntimeError(f"Supabase milestone upsert failed: {milestone_response.error}")

    @staticmethod
    def _to_row(snapshot: MarketSnapshot) -> dict[str, object]:
        values = snapshot.model_dump(mode="json")
        features = values.pop("features")
        values["payload"] = {"features": features}
        return values

    @staticmethod
    def _from_row(row: object) -> MarketSnapshot:
        if not isinstance(row, dict):
            raise RuntimeError("Supabase snapshot row has an invalid shape")
        payload = row.get("payload", {})
        raw_features = payload.get("features", {}) if isinstance(payload, dict) else {}
        features = {
            name: FeatureValue.model_validate(value)
            for name, value in raw_features.items()
        }
        row_values = dict(row)
        row_values["features"] = features
        row_values.pop("payload", None)
        row_values.pop("created_at", None)
        return MarketSnapshot.model_validate(row_values)


def _decimal_value(value: object) -> float | None:
    return float(value) if value is not None else None


def _audit_value(audit: dict[str, object] | None, key: str) -> object:
    return audit.get(key) if audit else None


def _db_recommendation_state(state: PaperState) -> str:
    if state is PaperState.CLOSED_TP or state is PaperState.CLOSED_SL or state is PaperState.CLOSED_TIMEOUT:
        return "CLOSED"
    return state.value


def _db_trade_outcome(state: PaperState | None) -> str | None:
    """Map internal close reasons to the durable trade outcome contract."""
    if state is None:
        return None
    if state is PaperState.CLOSED_TP:
        return "WIN"
    if state is PaperState.CLOSED_SL:
        return "LOSS"
    if state is PaperState.CLOSED_TIMEOUT:
        return "TIMEOUT"
    raise ValueError(f"paper trade outcome is not terminal: {state.value}")


def _projection_position_state(state: PaperState) -> str:
    if state in {PaperState.RECOMMENDED, PaperState.PENDING_FILL}:
        return "PENDING_FILL"
    if state is PaperState.OPEN:
        return "OPEN"
    if state in {PaperState.CLOSED_TP, PaperState.CLOSED_SL, PaperState.CLOSED_TIMEOUT}:
        return "CLOSED"
    return state.value


def _ledger_event_type(state: PaperState) -> str:
    if state is PaperState.RECOMMENDED:
        return "RECOMMENDED"
    if state is PaperState.OPEN:
        return "FILLED"
    if state is PaperState.EXPIRED:
        return "EXPIRED"
    if state is PaperState.CANCELLED:
        return "CANCELLED"
    if state in {PaperState.CLOSED_TP, PaperState.CLOSED_SL, PaperState.CLOSED_TIMEOUT}:
        return "MARKED"
    return "CORRECTION"


def _recommendation_payload(recommendation: PaperRecommendation) -> dict[str, object]:
    return {
        "candidate": recommendation.candidate.model_dump(mode="json"),
        "profile": recommendation.profile.value,
        "quantity": str(recommendation.quantity),
        "emitted_at": recommendation.emitted_at.isoformat(),
        "expires_at": recommendation.expires_at.isoformat(),
        "state": recommendation.state.value,
        "outcome": recommendation.outcome.value if recommendation.outcome else None,
        "entry_fill": str(recommendation.entry_fill) if recommendation.entry_fill is not None else None,
        "opened_at": recommendation.opened_at.isoformat() if recommendation.opened_at else None,
        "exit_fill": str(recommendation.exit_fill) if recommendation.exit_fill is not None else None,
        "net_pnl": str(recommendation.net_pnl) if recommendation.net_pnl is not None else None,
        "net_r": str(recommendation.net_r) if recommendation.net_r is not None else None,
        "closed_at": recommendation.closed_at.isoformat() if recommendation.closed_at else None,
        "funding_pnl": str(recommendation.funding_pnl),
        "last_funding_time": recommendation.last_funding_time.isoformat()
        if recommendation.last_funding_time
        else None,
        "last_mark": str(recommendation.last_mark) if recommendation.last_mark is not None else None,
        "data_quality": recommendation.data_quality.value,
        "last_quote_at": recommendation.last_quote_at.isoformat() if recommendation.last_quote_at else None,
    }


def _report_payload(report: PaperReport) -> dict[str, object]:
    return {
        "scope": report.scope.value,
        "profile": report.profile.value if report.profile else None,
        "milestone": report.milestone,
        "member_ids": list(report.member_ids),
        "recommendation_count": report.recommendation_count,
        "filled_count": report.filled_count,
        "closed_count": report.closed_count,
        "open_count": report.open_count,
        "expired_or_cancelled_count": report.expired_or_cancelled_count,
        "ambiguous_count": report.ambiguous_count,
        "wins": report.wins,
        "losses": report.losses,
        "breakeven": report.breakeven,
        "net_positive_rate": str(report.net_positive_rate) if report.net_positive_rate is not None else None,
        "total_net_pnl": str(report.total_net_pnl),
        "total_net_r": str(report.total_net_r),
        "max_drawdown": str(report.max_drawdown),
        "cohort_status": report.cohort_status,
    }


def _recommendation_from_row(row: dict[str, object]) -> PaperRecommendation:
    from trade_brain.contracts import Profile, QualityStatus, TradeCandidate
    from trade_brain.paper import PaperState

    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("Supabase paper recommendation payload is invalid")
    candidate_value = payload.get("candidate")
    if not isinstance(candidate_value, dict):
        raise RuntimeError("Supabase paper recommendation candidate is missing")
    candidate = TradeCandidate.model_validate(candidate_value)
    state_value = payload.get("state", row.get("state"))
    state = PaperState(str(state_value))
    outcome_value = payload.get("outcome")
    outcome = PaperState(str(outcome_value)) if outcome_value else None
    return PaperRecommendation(
        recommendation_id=str(row["recommendation_id"]),
        profile=Profile(str(payload["profile"])),
        candidate=candidate,
        quantity=Decimal(str(payload["quantity"])),
        emitted_at=datetime.fromisoformat(str(payload["emitted_at"])),
        expires_at=datetime.fromisoformat(str(payload["expires_at"])),
        state=state,
        entry_fill=_decimal_from_payload(payload.get("entry_fill")),
        opened_at=_datetime_from_payload(payload.get("opened_at")),
        exit_fill=_decimal_from_payload(payload.get("exit_fill")),
        closed_at=_datetime_from_payload(payload.get("closed_at")),
        outcome=outcome,
        net_pnl=_decimal_from_payload(payload.get("net_pnl")),
        net_r=_decimal_from_payload(payload.get("net_r")),
        funding_pnl=_decimal_from_payload(payload.get("funding_pnl")) or Decimal("0"),
        last_funding_time=_datetime_from_payload(payload.get("last_funding_time")),
        last_mark=_decimal_from_payload(payload.get("last_mark")),
        data_quality=QualityStatus(str(payload.get("data_quality", QualityStatus.VALID.value))),
        last_quote_at=_datetime_from_payload(payload.get("last_quote_at")),
    )


def _decimal_from_payload(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _datetime_from_payload(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value is not None else None
