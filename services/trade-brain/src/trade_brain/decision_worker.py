"""Automated snapshot-to-paper decision worker."""

import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import httpx
from trade_brain.binance import BinanceClientError, BinancePublicClient
from trade_brain.claude import ClaudeSelector
from trade_brain.collector import TIMEFRAMES, PublicSnapshotCollector
from trade_brain.configuration import TradeBrainConfig, runtime_policies
from trade_brain.contracts import MarketSnapshot, PaperMode, Profile, TradeCandidate
from trade_brain.discord import BroadcastNotifier, DiscordCommandPoller, DiscordNotifier
from trade_brain.orchestration import CandidateRiskInput, DecisionCycleResult, DecisionSelector, run_decision_cycle
from trade_brain.paper import PaperTradingEngine
from trade_brain.paper_service import PaperDecisionResult, create_paper_recommendations
from trade_brain.reporting import CohortScope, CohortTracker, PaperReport, ReportRecord
from trade_brain.risk import CandidateStatistics, RiskContext
from trade_brain.storage import InMemorySnapshotStore, SnapshotStore
from trade_brain.strategy_pipeline import build_all_candidates_multi_timeframe
from trade_brain.supabase_store import SupabaseSnapshotStore
from trade_brain.telegram import (
    TelegramCommandPoller,
    TelegramError,
    TelegramNotifier,
    format_evaluation,
    format_recommendation,
    format_report,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DecisionWorkerConfig:
    """Safe defaults for paper-only automated decision cycles."""

    equity_usdt: float = 10000
    available_margin_usdt: float = 10000
    quantity_step: float = 0.001
    minimum_quantity: float = 0.0
    price_tick: float = 0.1
    fee_entry_per_unit: float = 0.01
    fee_exit_per_unit: float = 0.01
    adverse_slippage_per_unit: float = 0.01
    adverse_funding_per_unit: float = 0.01
    paper_mode: PaperMode = PaperMode.RESEARCH_PAPER


async def run_decision_once(
    store: SnapshotStore,
    experiment_id: str,
    symbols: Sequence[str],
    selector: DecisionSelector,
    client: BinancePublicClient,
    paper_engine: PaperTradingEngine,
    config: DecisionWorkerConfig = DecisionWorkerConfig(),
    now: datetime | None = None,
    notifier: TelegramNotifier | None = None,
    notified_report_keys: set[tuple[str, int, str]] | None = None,
    brain_config: TradeBrainConfig | None = None,
) -> list[DecisionCycleResult]:
    """Collect, detect, risk-gate, select, and paper-record one cycle per symbol."""
    active_brain_config = brain_config or TradeBrainConfig.defaults()
    ensure_experiment = getattr(store, "ensure_experiment", None)
    if callable(ensure_experiment):
        ensure_experiment(experiment_id)
    collector = PublicSnapshotCollector(client, store)
    results: list[DecisionCycleResult] = []
    for symbol in symbols:
        try:
            collected = await collector.collect_timeframes(experiment_id, symbol, TIMEFRAMES, now)
        except (BinanceClientError, httpx.HTTPError, ValueError) as error:
            LOGGER.warning("Skipping decision cycle for %s: %s", symbol, error)
            continue
        snapshot = collected.snapshot
        bars_1h = collected.bars_by_timeframe["1h"]
        bars_15m = collected.bars_by_timeframe["15m"]
        pipeline = build_all_candidates_multi_timeframe(
            bars_1h,
            bars_15m,
            snapshot.snapshot_id,
            t1_config=active_brain_config.t1,
            t2_config=active_brain_config.t2,
            r1_config=active_brain_config.r1,
            bars_4h=collected.bars_by_timeframe["4h"],
            bars_1d=collected.bars_by_timeframe["1d"],
            current_only=True,
        )
        sizing_config = await _sizing_config_for_symbol(client, symbol, config)
        risk_inputs = {
            candidate.candidate_id: _default_risk_input(
                candidate,
                sizing_config,
                paper_engine,
                snapshot.decision_time,
            )
            for candidate in pipeline.candidates
        }
        result = await run_decision_cycle(
            snapshot,
            list(pipeline.candidates),
            risk_inputs,
            selector,
            policies=runtime_policies(active_brain_config),
        )
        paper_result = _persist_cycle(store, snapshot, result, paper_engine)
        await _notify_recommendations(notifier, paper_result)
        await _persist_and_notify_reports(
            store,
            snapshot.experiment_id,
            paper_engine,
            notifier,
            notified_report_keys if notified_report_keys is not None else set(),
        )
        results.append(result)
    return results


async def run_forever() -> None:
    """Run the Claude-to-paper cycle on the configured public symbols."""
    api_selector = ClaudeSelector.from_environment()
    store = _build_store()
    client = BinancePublicClient()
    paper_engine = PaperTradingEngine()
    telegram_notifier = _build_notifier()
    discord_notifier = _build_discord_notifier()
    notifier_sinks = [sink for sink in (telegram_notifier, discord_notifier) if sink is not None]
    notifier = BroadcastNotifier(notifier_sinks) if notifier_sinks else None
    notified_report_keys: set[tuple[str, int, str]] = set()
    experiment_id = os.environ.get("TRADE_V1_EXPERIMENT_ID", "trade-v1-local")
    _prepare_and_restore_paper_state(store, paper_engine, experiment_id)
    cycle_lock = asyncio.Lock()

    async def evaluate(notify: bool) -> list[DecisionCycleResult]:
        async with cycle_lock:
            brain_config = _load_brain_config(store)
            return await run_decision_once(
                store,
                experiment_id,
                brain_config.symbols,
                api_selector,
                client,
                paper_engine,
                config=DecisionWorkerConfig(
                    equity_usdt=brain_config.initial_equity_usdt,
                    available_margin_usdt=brain_config.initial_equity_usdt,
                    paper_mode=brain_config.paper_mode,
                ),
                notifier=notifier if notify else None,
                notified_report_keys=notified_report_keys,
                brain_config=brain_config,
            )

    async def evaluate_on_demand() -> str:
        before_ids = {
            recommendation.recommendation_id
            for recommendation in paper_engine.list_recommendations()
        }
        results = await evaluate(notify=False)
        new_recommendations = [
            recommendation
            for recommendation in paper_engine.list_recommendations()
            if recommendation.recommendation_id not in before_ids
        ]
        snapshot_symbols = {
            result.snapshot_id: snapshot.symbol
            for result in results
            if (snapshot := store.get_snapshot(result.snapshot_id)) is not None
        }
        return format_evaluation(results, new_recommendations, snapshot_symbols)

    command_pollers = []
    if telegram_notifier is not None:
        command_pollers.append(TelegramCommandPoller.from_environment(evaluate_on_demand))
    if os.environ.get("DISCORD_BOT_TOKEN"):
        command_pollers.append(DiscordCommandPoller.from_environment(evaluate_on_demand))

    async def run_periodic() -> None:
        while True:
            await evaluate(notify=True)
            await asyncio.sleep(_configured_interval())

    try:
        tasks = [asyncio.create_task(run_periodic())]
        tasks.extend(asyncio.create_task(poller.run_forever()) for poller in command_pollers)
        await asyncio.gather(*tasks)
    finally:
        for poller in command_pollers:
            await poller.close()
        await api_selector.close()
        await client.close()
        if notifier is not None:
            await notifier.close()


def main() -> None:
    """Console entry point for automated paper decisions."""
    asyncio.run(run_forever())


def _default_risk_input(
    candidate: TradeCandidate,
    config: DecisionWorkerConfig,
    paper_engine: PaperTradingEngine | None = None,
    as_of: datetime | None = None,
) -> CandidateRiskInput:
    statistics = candidate.statistics
    open_risk = float(paper_engine.open_risk_pct(candidate.profile)) if paper_engine else 0.0
    equity_usdt = config.equity_usdt
    available_margin_usdt = config.available_margin_usdt
    if paper_engine is not None:
        account = paper_engine.get_account(candidate.profile)
        equity_usdt = float(paper_engine.equity(candidate.profile))
        available_margin_usdt = min(available_margin_usdt, max(0.0, equity_usdt))
    daily_drawdown = 0.0
    rolling_drawdown = 0.0
    if paper_engine is not None and as_of is not None:
        daily, rolling = paper_engine.drawdown_pcts(candidate.profile, as_of)
        daily_drawdown = float(daily)
        rolling_drawdown = float(rolling)
    return CandidateRiskInput(
        statistics=CandidateStatistics(
            quality_score=_number_or_none(statistics.get("quality_score")),
            net_expectancy_r=_number_or_none(statistics.get("net_expectancy_r")),
            expectancy_lower_95_r=_number_or_none(statistics.get("expectancy_lower_95_r")),
            effective_sample_count=_integer_or_none(statistics.get("effective_sample_count")),
            cost_estimate_r=_number_or_none(statistics.get("cost_estimate_r")),
        ),
        context=RiskContext(
            equity_usdt=equity_usdt,
            open_risk_pct=open_risk,
            cluster_risk_pct=open_risk,
            daily_drawdown_pct=daily_drawdown,
            rolling_drawdown_pct=rolling_drawdown,
            available_margin_usdt=available_margin_usdt,
            paper_mode=config.paper_mode,
        ),
        fee_entry_per_unit=config.fee_entry_per_unit,
        fee_exit_per_unit=config.fee_exit_per_unit,
        adverse_slippage_per_unit=config.adverse_slippage_per_unit,
        adverse_funding_per_unit=config.adverse_funding_per_unit,
        quantity_step=config.quantity_step,
        minimum_quantity=config.minimum_quantity,
        price_tick=config.price_tick,
    )


def _persist_cycle(
    store: SnapshotStore,
    snapshot: MarketSnapshot,
    result: DecisionCycleResult,
    paper_engine: PaperTradingEngine,
) -> PaperDecisionResult:
    persist_cycle = getattr(store, "save_decision_cycle", None)
    decision_ids = persist_cycle(result) if callable(persist_cycle) else {}
    paper_result = create_paper_recommendations(result, paper_engine, snapshot.decision_time)
    ensure_accounts = getattr(store, "ensure_paper_accounts", None)
    account_ids = ensure_accounts(snapshot.experiment_id) if callable(ensure_accounts) else {}
    save_recommendation = getattr(store, "save_paper_recommendation", None)
    if not callable(save_recommendation):
        return paper_result
    for recommendation in paper_result.recommendations:
        profile = recommendation.profile.value
        account_id = account_ids.get(profile) if isinstance(account_ids, dict) else None
        decision_id = decision_ids.get(profile) if isinstance(decision_ids, dict) else None
        if account_id and decision_id:
            save_recommendation(
                snapshot.experiment_id,
                account_id,
                decision_id,
                recommendation,
                snapshot.symbol,
            )
    return paper_result


async def _notify_recommendations(
    notifier: TelegramNotifier | None,
    paper_result: PaperDecisionResult,
) -> None:
    if notifier is None:
        return
    for recommendation in paper_result.recommendations:
        try:
            await notifier.send(format_recommendation(recommendation))
        except (TelegramError, httpx.HTTPError):
            continue


async def _sizing_config_for_symbol(
    client: BinancePublicClient,
    symbol: str,
    config: DecisionWorkerConfig,
) -> DecisionWorkerConfig:
    """Use exchange quantity rules and fail closed when rules cannot be read."""
    get_symbol_rules = getattr(client, "get_symbol_rules", None)
    if not callable(get_symbol_rules):
        return config
    try:
        rules = await get_symbol_rules(symbol)
    except BinanceClientError:
        return replace(config, quantity_step=0, minimum_quantity=0, price_tick=0)
    return replace(
        config,
        quantity_step=rules.quantity_step,
        minimum_quantity=rules.minimum_quantity,
        price_tick=rules.price_tick,
    )


async def _persist_and_notify_reports(
    store: SnapshotStore,
    experiment_id: str,
    engine: PaperTradingEngine,
    notifier: TelegramNotifier | None,
    notified_keys: set[tuple[str, int, str]],
) -> None:
    """Persist available cohort reports and notify each status once."""
    tracker = _build_report_tracker(engine)
    report_scopes = [
        *( (CohortScope.PROFILE_RECOMMENDATIONS, profile) for profile in Profile ),
        *( (CohortScope.PROFILE_COMPLETED_TRADES, profile) for profile in Profile ),
        (CohortScope.GLOBAL_SETUP_RECOMMENDATIONS, None),
    ]
    for scope, profile in report_scopes:
        for milestone in tracker.available_milestones(scope, profile):
            report = tracker.report(scope, profile, milestone)
            if report is None:
                continue
            _persist_report(store, experiment_id, report)
            if notifier is None:
                continue
            key = (f"{scope.value}:{profile.value if profile else 'ALL'}", report.milestone, report.cohort_status)
            if key in notified_keys:
                continue
            try:
                await notifier.send(format_report(report))
            except (TelegramError, httpx.HTTPError):
                continue
            notified_keys.add(key)


def _build_report_tracker(engine: PaperTradingEngine) -> CohortTracker:
    tracker = CohortTracker(milestone_size=100)
    for recommendation in engine.list_recommendations():
        tracker.record(
            ReportRecord(
                recommendation_id=recommendation.recommendation_id,
                profile=recommendation.profile,
                setup_id=recommendation.candidate.setup_id,
                emitted_at=recommendation.emitted_at,
                state=recommendation.state,
                net_pnl=recommendation.net_pnl,
                net_r=recommendation.net_r,
                closed_at=recommendation.closed_at,
                opened_at=recommendation.opened_at,
                initial_equity=engine.get_account(recommendation.profile).initial_equity,
                data_quality=recommendation.data_quality,
            )
        )
    return tracker


def _persist_report(store: SnapshotStore, experiment_id: str, report: PaperReport) -> None:
    """Persist a report when the configured store supports durable artifacts."""
    save_report = getattr(store, "save_paper_report", None)
    if callable(save_report) and report.member_ids:
        save_report(experiment_id, report)


def _build_notifier() -> TelegramNotifier | None:
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        return TelegramNotifier.from_environment()
    return None


def _build_discord_notifier() -> DiscordNotifier | None:
    if os.environ.get("DISCORD_BOT_TOKEN") and os.environ.get("DISCORD_CHANNEL_ID"):
        return DiscordNotifier.from_environment()
    return None


def _build_store() -> SnapshotStore:
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"):
        return SupabaseSnapshotStore.from_environment()
    return InMemorySnapshotStore()


def _load_brain_config(store: SnapshotStore) -> TradeBrainConfig:
    """Load the last validated config, keeping safe defaults for first boot."""
    loader = getattr(store, "load_config", None)
    if callable(loader):
        loaded = loader()
        if loaded is not None:
            return loaded
    return TradeBrainConfig.defaults()


def _prepare_and_restore_paper_state(
    store: SnapshotStore,
    engine: PaperTradingEngine,
    experiment_id: str,
) -> None:
    """Restore durable paper state before the first automated decision cycle."""
    ensure_experiment = getattr(store, "ensure_experiment", None)
    if callable(ensure_experiment):
        ensure_experiment(experiment_id)
    ensure_accounts = getattr(store, "ensure_paper_accounts", None)
    if callable(ensure_accounts):
        ensure_accounts(experiment_id)
    load_accounts = getattr(store, "load_paper_accounts", None)
    if callable(load_accounts):
        for account in load_accounts():
            engine.restore_account(account)
    load_recommendations = getattr(store, "load_paper_recommendations", None)
    if callable(load_recommendations):
        for recommendation in load_recommendations():
            engine.restore(recommendation)


def _configured_symbols() -> tuple[str, ...]:
    raw = os.environ.get("TRADE_SYMBOLS", "BTCUSDT,ETHUSDT")
    symbols = tuple(symbol.strip().upper() for symbol in raw.split(",") if symbol.strip())
    if not symbols:
        raise RuntimeError("TRADE_SYMBOLS must contain at least one symbol")
    return symbols


def _configured_interval() -> int:
    value = int(os.environ.get("TRADE_COLLECTION_INTERVAL_SECONDS", "900"))
    if value < 60:
        raise RuntimeError("TRADE_COLLECTION_INTERVAL_SECONDS must be at least 60")
    return value


def _configured_mode() -> PaperMode:
    raw_mode = os.environ.get("TRADE_PAPER_MODE", PaperMode.RESEARCH_PAPER.value)
    try:
        return PaperMode(raw_mode)
    except ValueError as error:
        raise RuntimeError("TRADE_PAPER_MODE must be RESEARCH_PAPER or VERIFIED_PAPER") from error


def _number_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _integer_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


if __name__ == "__main__":
    main()
