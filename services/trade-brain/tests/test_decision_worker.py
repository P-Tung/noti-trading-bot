import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trade_brain.binance import BinanceClientError, BinanceKline, BinanceSymbolRules
from trade_brain.contracts import (
    ClaudeDecision,
    ClaudeDecisionBatch,
    Decision,
    PaperMode,
    Profile,
    Side,
    StatisticsStatus,
    TradeCandidate,
)
from trade_brain.decision_worker import (
    _default_risk_input,
    _configured_mode,
    _persist_and_notify_reports,
    _sizing_config_for_symbol,
    DecisionWorkerConfig,
    run_decision_once,
)
from trade_brain.paper import PaperAccount, PaperRecommendation, PaperState, PaperTradingEngine
from trade_brain.storage import InMemorySnapshotStore


class FlatClient:
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[BinanceKline]:
        opened_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            BinanceKline(
                opened_at_ms=int((opened_at + timedelta(minutes=15 * index)).timestamp() * 1000),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=10,
                closed_at_ms=int((opened_at + timedelta(minutes=15 * (index + 1))).timestamp() * 1000),
                quote_volume=1000,
                trade_count=10,
                taker_buy_base_volume=5,
                taker_buy_quote_volume=500,
            )
            for index in range(20)
        ] + [
            BinanceKline(
                opened_at_ms=int((opened_at + timedelta(minutes=300)).timestamp() * 1000),
                open=100,
                high=102,
                low=99,
                close=101,
                volume=20,
                closed_at_ms=int((opened_at + timedelta(minutes=315)).timestamp() * 1000),
                quote_volume=2020,
                trade_count=20,
                taker_buy_base_volume=10,
                taker_buy_quote_volume=1010,
            )
        ]


class NoTradeSelector:
    async def select(self, snapshot, candidates):
        return ClaudeDecisionBatch(
            snapshot_id=snapshot.snapshot_id,
            decisions=[
                ClaudeDecision(
                    snapshot_id=snapshot.snapshot_id,
                    profile=profile,
                    decision=Decision.NO_TRADE,
                    summary_vi="Chưa có setup.",
                )
                for profile in Profile
            ],
        )


def test_decision_worker_runs_collection_to_no_trade_cycle() -> None:
    store = InMemorySnapshotStore()

    results = asyncio.run(
        run_decision_once(
            store,
            "experiment-1",
            ["BTCUSDT"],
            NoTradeSelector(),
            FlatClient(),
            PaperTradingEngine(),
            now=datetime(2026, 1, 1, 5, 20, tzinfo=timezone.utc),
        )
    )

    assert len(results) == 1
    assert results[0].candidates == ()
    assert len(store.list_snapshots()) == 1


def test_decision_worker_defaults_to_research_paper(monkeypatch) -> None:
    monkeypatch.delenv("TRADE_PAPER_MODE", raising=False)

    assert _configured_mode() is PaperMode.RESEARCH_PAPER


def test_decision_worker_uses_exchange_quantity_step_and_fails_closed() -> None:
    config = DecisionWorkerConfig()

    class RulesClient:
        async def get_symbol_rules(self, symbol: str) -> BinanceSymbolRules:
            return BinanceSymbolRules(symbol, 0.01, 0.01, 0.1)

    class FailingRulesClient:
        async def get_symbol_rules(self, symbol: str) -> BinanceSymbolRules:
            raise BinanceClientError("exchange metadata unavailable")

    configured = asyncio.run(_sizing_config_for_symbol(RulesClient(), "BTCUSDT", config))
    failed = asyncio.run(_sizing_config_for_symbol(FailingRulesClient(), "BTCUSDT", config))

    assert configured.quantity_step == 0.01
    assert failed.quantity_step == 0


def test_decision_worker_restores_persisted_paper_state_before_cycles() -> None:
    from trade_brain.decision_worker import _prepare_and_restore_paper_state
    from trade_brain.paper import PaperAccount

    candidate = TradeCandidate(
        candidate_id="candidate-restored",
        snapshot_id="snapshot-restored",
        setup_id="setup-restored",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=95,
        target_price=110,
        horizon_bars=4,
        statistics_status=StatisticsStatus.RESEARCH_ONLY,
        eligible=True,
    )
    recommendation = PaperRecommendation(
        recommendation_id="recommendation-restored",
        profile=Profile.BALANCED,
        candidate=candidate,
        quantity=Decimal("0.1"),
        emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )

    class PersistentStore:
        def ensure_experiment(self, experiment_id: str) -> None:
            self.experiment_id = experiment_id

        def ensure_paper_accounts(self, experiment_id: str) -> None:
            self.accounts_created_for = experiment_id

        def load_paper_accounts(self):
            return [PaperAccount(Profile.BALANCED, Decimal("10000"), Decimal("9975"), Decimal("-25"))]

        def load_paper_recommendations(self):
            return [recommendation]

    engine = PaperTradingEngine()
    _prepare_and_restore_paper_state(PersistentStore(), engine, "experiment-restore")

    assert engine.find_by_setup(Profile.BALANCED, "setup-restored") is recommendation
    assert engine.get_account(Profile.BALANCED).current_equity == Decimal("9975")


def test_worker_risk_sizing_uses_current_profile_equity() -> None:
    candidate = TradeCandidate(
        candidate_id="candidate-equity",
        snapshot_id="snapshot-equity",
        setup_id="setup-equity",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=95,
        target_price=110,
        horizon_bars=4,
        statistics_status=StatisticsStatus.RESEARCH_ONLY,
        eligible=True,
    )
    engine = PaperTradingEngine()
    engine.restore_account(
        PaperAccount(Profile.BALANCED, Decimal("10000"), Decimal("8000"), Decimal("-2000"))
    )

    risk_input = _default_risk_input(candidate, DecisionWorkerConfig(), engine)

    assert risk_input.context.equity_usdt == 8000
    assert risk_input.context.available_margin_usdt == 8000


class ReportStore:
    def __init__(self) -> None:
        self.saved_reports = []

    def save_paper_report(self, experiment_id, report) -> None:
        self.saved_reports.append((experiment_id, report))


class ReportNotifier:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def test_decision_worker_persists_and_notifies_milestone_report() -> None:
    emitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    engine = PaperTradingEngine()
    for index in range(100):
        candidate = TradeCandidate(
            candidate_id=f"candidate-{index}",
            snapshot_id="snapshot-1",
            setup_id=f"setup-{index}",
            profile=Profile.BALANCED,
            strategy="T1",
            entry_stage="CONFIRMED",
            side=Side.LONG,
            entry_estimate=100,
            stop_price=95,
            target_price=110,
            horizon_bars=4,
            statistics_status=StatisticsStatus.RESEARCH_ONLY,
            eligible=True,
        )
        engine.restore(
            PaperRecommendation(
                recommendation_id=f"recommendation-{index}",
                profile=Profile.BALANCED,
                candidate=candidate,
                quantity=Decimal("0.1"),
                emitted_at=emitted_at + timedelta(minutes=index),
                expires_at=emitted_at + timedelta(minutes=index + 1),
                state=PaperState.RECOMMENDED,
            )
        )

    store = ReportStore()
    notifier = ReportNotifier()
    notified_keys = set()
    asyncio.run(_persist_and_notify_reports(store, "experiment-1", engine, notifier, notified_keys))
    asyncio.run(_persist_and_notify_reports(store, "experiment-1", engine, notifier, notified_keys))

    assert len(store.saved_reports) == 4
    assert len(notifier.messages) == 2
    assert any("PAPER REPORT BALANCED #1" in message for message in notifier.messages)
