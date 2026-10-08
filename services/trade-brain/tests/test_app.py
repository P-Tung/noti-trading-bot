import asyncio
from dataclasses import replace
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from decimal import Decimal

from trade_brain.app import app
from trade_brain.contracts import FeatureValue, QualityStatus
from trade_brain.contracts import ClaudeDecision, ClaudeDecisionBatch, Decision, Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.snapshots import create_snapshot
from trade_brain.storage import InMemorySnapshotStore
from trade_brain.paper import PaperTradingEngine
from trade_brain.app import create_app
from trade_brain.app import _notify_milestone_reports
from trade_brain.reporting import CohortScope, PaperReport
from unittest.mock import patch


class NoTradeSelector:
    async def select(self, snapshot, candidates):
        return ClaudeDecisionBatch(
            snapshot_id=snapshot.snapshot_id,
            decisions=[
                ClaudeDecision(
                    snapshot_id=snapshot.snapshot_id,
                    profile=profile,
                    decision=Decision.NO_TRADE,
                    summary_vi="Chưa có giao dịch.",
                )
                for profile in Profile
            ],
        )


class ResearchSelector:
    async def select(self, snapshot, candidates):
        candidate_id = candidates[0].candidate_id
        return ClaudeDecisionBatch(
            snapshot_id=snapshot.snapshot_id,
            decisions=[
                ClaudeDecision(
                    snapshot_id=snapshot.snapshot_id,
                    profile=profile,
                    decision=Decision.LONG if profile is Profile.BALANCED else Decision.NO_TRADE,
                    selected_candidate_id=candidate_id if profile is Profile.BALANCED else None,
                    summary_vi="Nghiên cứu paper.",
                )
                for profile in Profile
            ],
        )


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "trade-brain",
        "execution_mode": "PAPER",
        "real_money_enabled": False,
    }


def test_invalid_decision_has_stable_error_shape() -> None:
    payload = {
        "decision": {
            "snapshot_id": "snapshot-1",
            "profile": "BALANCED",
            "decision": "LONG",
            "selected_candidate_id": "missing",
            "summary_vi": "Không hợp lệ.",
        },
        "candidates": [],
    }
    response = client.post("/v1/decisions/validate", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "DECISION_CONTRACT_ERROR"


def test_snapshot_list_returns_recent_typed_data() -> None:
    store = InMemorySnapshotStore()
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.save_snapshot(
        create_snapshot(
            "experiment-1",
            "BTCUSDT",
            timestamp,
            {
                "close_15m": FeatureValue(
                    value=100,
                    unit="USDT",
                    observed_at=timestamp,
                    available_at=timestamp,
                    quality_status=QualityStatus.VALID,
                )
            },
        )
    )

    response = TestClient(create_app(store)).get("/v1/snapshots?limit=10")

    assert response.status_code == 200
    assert response.json()["snapshots"][0]["symbol"] == "BTCUSDT"


def test_decision_run_returns_paper_only_decisions() -> None:
    store = InMemorySnapshotStore()
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snapshot = create_snapshot("experiment-1", "BTCUSDT", timestamp, {})
    store.save_snapshot(snapshot)
    response = TestClient(create_app(store, NoTradeSelector())).post(
        "/v1/decisions/run",
        json={"snapshot_id": snapshot.snapshot_id, "candidates": [], "risk_inputs": {}},
    )

    assert response.status_code == 200
    assert len(response.json()["decisions"]) == 3
    assert {decision["decision"] for decision in response.json()["decisions"]} == {"NO_TRADE"}
    assert response.json()["claude_audit"] is None

    history = TestClient(create_app(store)).get("/v1/decisions/recent?limit=10")
    assert history.status_code == 200
    assert len(history.json()["decisions"]) == 3
    assert {decision["profile"] for decision in history.json()["decisions"]} == {
        "PROACTIVE",
        "BALANCED",
        "CAUTIOUS",
    }


def test_paper_journal_quote_router_advances_fill_and_mark() -> None:
    emitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candidate = TradeCandidate(
        candidate_id="candidate-1",
        snapshot_id="snapshot-1",
        setup_id="setup-1",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=95,
        target_price=110,
        horizon_bars=10,
        statistics_status=StatisticsStatus.VERIFIED,
        eligible=True,
    )
    engine = PaperTradingEngine()
    recommendation = engine.recommend(candidate, Decimal("1"), emitted_at)
    notifier = RecordingNotifier()
    client = TestClient(create_app(InMemorySnapshotStore(), NoTradeSelector(), engine, notifier))

    listed = client.get("/v1/paper/recommendations")
    assert listed.status_code == 200
    assert listed.json()["recommendations"][0]["state"] == "RECOMMENDED"

    filled = client.post(
        f"/v1/paper/recommendations/{recommendation.recommendation_id}/quote",
        json={"observed_at": "2026-01-01T00:01:00Z", "bid": 100, "ask": 100.1, "mark": 100.05},
    )
    assert filled.json()["recommendation"]["state"] == "OPEN"
    assert filled.json()["action"] == "FILLED_OR_EXPIRED"

    marked = client.post(
        f"/v1/paper/recommendations/{recommendation.recommendation_id}/quote",
        json={"observed_at": "2026-01-01T00:02:00Z", "bid": 110, "ask": 110.1, "mark": 110.05},
    )
    assert marked.json()["recommendation"]["state"] == "CLOSED_TP"
    assert marked.json()["action"] == "MARKED_OR_OPEN"
    assert len(notifier.messages) == 2
    assert "State: OPEN" in notifier.messages[0]
    assert "State: CLOSED_TP" in notifier.messages[1]


def test_unknown_paper_recommendation_returns_client_error() -> None:
    client = TestClient(create_app(InMemorySnapshotStore(), NoTradeSelector()))
    payload = {"observed_at": "2026-01-01T00:01:00Z", "bid": 100, "ask": 100.1, "mark": 100.05}

    response = client.post("/v1/paper/recommendations/missing/quote", json=payload)

    assert response.status_code == 422
    assert "unknown paper recommendation" in response.json()["detail"]


def test_reports_wait_for_locked_100_member_cohort() -> None:
    response = TestClient(create_app(InMemorySnapshotStore(), NoTradeSelector())).get("/v1/reports?milestone=1")

    assert response.status_code == 200
    assert response.json()["milestone"] == 1
    assert response.json()["reports"] == [None, None, None]
    assert response.json()["completed_trade_reports"] == [None, None, None]


def test_milestone_notifications_cover_all_scopes_once() -> None:
    base_report = PaperReport(
        scope=CohortScope.PROFILE_RECOMMENDATIONS,
        profile=Profile.BALANCED,
        milestone=1,
        member_ids=(),
        recommendation_count=100,
        filled_count=100,
        closed_count=100,
        open_count=0,
        expired_or_cancelled_count=0,
        ambiguous_count=0,
        wins=60,
        losses=40,
        breakeven=0,
        net_positive_rate=Decimal("0.6"),
        total_net_pnl=Decimal("10"),
        total_net_r=Decimal("2"),
        max_drawdown=Decimal("5"),
        cohort_status="FINAL",
    )

    class FakeTracker:
        def available_milestones(self, scope, profile):
            return (1,)

        def report(self, scope, profile, milestone):
            return replace(base_report, scope=scope, profile=profile)

    class FakeNotifier:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(message)

    async def run() -> tuple[list[str], FakeNotifier]:
        notifier = FakeNotifier()
        notified_keys = set()
        with patch("trade_brain.app._build_report_tracker", return_value=FakeTracker()):
            with patch("trade_brain.app._persist_report") as persist_report:
                errors = await _notify_milestone_reports(
                    PaperTradingEngine(),
                    notifier,
                    notified_keys,
                    InMemorySnapshotStore(),
                )
                assert persist_report.call_count == 7
            await _notify_milestone_reports(PaperTradingEngine(), notifier, notified_keys)
        return errors, notifier

    errors, notifier = asyncio.run(run())
    assert errors == []
    assert len(notifier.messages) == 7


def test_paper_accounts_are_independent_by_profile() -> None:
    response = TestClient(create_app(InMemorySnapshotStore(), NoTradeSelector())).get("/v1/paper/accounts")

    assert response.status_code == 200
    assert [account["profile"] for account in response.json()["accounts"]] == [
        "PROACTIVE",
        "BALANCED",
        "CAUTIOUS",
    ]
    assert all(account["current_equity"] == "10000" for account in response.json()["accounts"])


def test_backtest_endpoint_returns_research_only_statistics() -> None:
    candidate = {
        "candidate_id": "candidate-backtest",
        "snapshot_id": "snapshot-backtest",
        "setup_id": "setup-backtest",
        "profile": "BALANCED",
        "strategy": "T1",
        "entry_stage": "CONFIRMED",
        "side": "LONG",
        "entry_estimate": 100,
        "stop_price": 95,
        "target_price": 110,
        "horizon_bars": 3,
        "statistics_status": "RESEARCH_ONLY",
        "eligible": True,
    }
    bars = [
        {"opened_at": "2026-01-01T00:00:00Z", "closed_at": "2026-01-01T01:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100},
        {"opened_at": "2026-01-01T01:00:00Z", "closed_at": "2026-01-01T02:00:00Z", "open": 100, "high": 111, "low": 99, "close": 110, "volume": 100},
    ]
    response = TestClient(create_app(InMemorySnapshotStore(), NoTradeSelector())).post(
        "/v1/backtests/run",
        json={"bars": bars, "cases": [{"candidate": candidate, "signal_index": 0}]},
    )

    assert response.status_code == 200
    assert response.json()["statistics_status"] == "RESEARCH_ONLY"
    assert response.json()["summary"]["wins"] == 1


def test_research_paper_can_create_recommendation_without_verified_statistics() -> None:
    store = InMemorySnapshotStore()
    snapshot = create_snapshot("experiment-research", "BTCUSDT", datetime(2026, 1, 1, tzinfo=timezone.utc), {})
    store.save_snapshot(snapshot)
    candidate = {
        "candidate_id": "candidate-research",
        "snapshot_id": snapshot.snapshot_id,
        "setup_id": "setup-research",
        "profile": "BALANCED",
        "strategy": "T2",
        "entry_stage": "CONFIRMED",
        "side": "LONG",
        "entry_estimate": 100,
        "stop_price": 95,
        "target_price": 110,
        "horizon_bars": 96,
        "statistics_status": "RESEARCH_ONLY",
        "eligible": True,
    }
    response = TestClient(create_app(store, ResearchSelector())).post(
        "/v1/decisions/run",
        json={
            "snapshot_id": snapshot.snapshot_id,
            "candidates": [candidate],
            "risk_inputs": {
                "candidate-research": {
                    "statistics": {},
                    "context": {
                        "equity_usdt": 10000,
                        "open_risk_pct": 0,
                        "cluster_risk_pct": 0,
                        "daily_drawdown_pct": 0,
                        "rolling_drawdown_pct": 0,
                        "available_margin_usdt": 10000,
                    },
                    "fee_entry_per_unit": 0.01,
                    "fee_exit_per_unit": 0.01,
                    "adverse_slippage_per_unit": 0.01,
                    "adverse_funding_per_unit": 0.01,
                    "quantity_step": 0.001,
                }
            },
        },
    )

    assert response.status_code == 200
    assert len(response.json()["paper_recommendations"]) == 1
