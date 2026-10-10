"""HTTP boundary for collection, snapshots, and validated decisions."""

import asyncio
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from trade_brain.binance import BinancePublicClient
from trade_brain.backtest import BacktestCase, run_backtest
from trade_brain.claude import ClaudeSelector
from trade_brain.collector import PublicSnapshotCollector
from trade_brain.configuration import TradeBrainConfig
from trade_brain.decision_worker import DecisionWorkerConfig, _configured_mode, run_decision_once
from trade_brain.evaluation_reporting import build_evaluation_summary
from trade_brain.evaluation_status import EvaluationProgress
from trade_brain.contracts import ClaudeDecision, PaperMode, Profile, QualityStatus, StrictModel, TradeCandidate
from trade_brain.orchestration import CandidateRiskInput, DecisionSelector, run_decision_cycle
from trade_brain.paper import MarketQuote, PaperAccount, PaperRecommendation, PaperState, PaperTradingEngine, PaperTradingError
from trade_brain.market_data import Bar
from trade_brain.paper_service import create_paper_recommendations
from trade_brain.reporting import CohortScope, CohortTracker, PaperReport, ReportRecord
from trade_brain.telegram import (
    TelegramError,
    TelegramNotifier,
    configured_chat_ids,
    format_recommendation,
    format_report,
)
from trade_brain.risk import CandidateStatistics, RiskContext
from trade_brain.storage import InMemorySnapshotStore, SnapshotStore
from trade_brain.supabase_store import SupabaseSnapshotStore
from trade_brain.universe import manual_universe, select_binance_universe
from trade_brain.validation import DecisionValidationError, validate_decision


class DecisionValidationRequest(StrictModel):
    decision: ClaudeDecision
    candidates: list[TradeCandidate] = Field(default_factory=list)


class SnapshotCollectionRequest(StrictModel):
    experiment_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)


class CandidateStatisticsRequest(StrictModel):
    quality_score: float | None = None
    net_expectancy_r: float | None = None
    expectancy_lower_95_r: float | None = None
    effective_sample_count: int | None = None
    cost_estimate_r: float | None = None


class RiskContextRequest(StrictModel):
    equity_usdt: float = Field(gt=0)
    open_risk_pct: float = Field(ge=0)
    cluster_risk_pct: float = Field(ge=0)
    daily_drawdown_pct: float = Field(ge=0)
    rolling_drawdown_pct: float = Field(ge=0)
    available_margin_usdt: float = Field(ge=0)
    liquidity_quantity_cap: float | None = Field(default=None, gt=0)
    paper_mode: PaperMode = PaperMode.RESEARCH_PAPER


class CandidateRiskInputRequest(StrictModel):
    statistics: CandidateStatisticsRequest
    context: RiskContextRequest
    fee_entry_per_unit: float = Field(ge=0)
    fee_exit_per_unit: float = Field(ge=0)
    adverse_slippage_per_unit: float = Field(ge=0)
    adverse_funding_per_unit: float = Field(ge=0)
    quantity_step: float = Field(gt=0)
    minimum_quantity: float = Field(default=0, ge=0)
    price_tick: float = Field(default=0, ge=0)


class DecisionRunRequest(StrictModel):
    snapshot_id: str = Field(min_length=1)
    candidates: list[TradeCandidate]
    risk_inputs: dict[str, CandidateRiskInputRequest]


class MarketQuoteRequest(StrictModel):
    observed_at: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    mark: float = Field(gt=0)
    funding_rate: float | None = None
    funding_time: datetime | None = None
    quality_status: QualityStatus = QualityStatus.VALID


class BacktestBarRequest(StrictModel):
    opened_at: datetime
    closed_at: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class BacktestCaseRequest(StrictModel):
    candidate: TradeCandidate
    signal_index: int = Field(ge=0)


class BacktestRunRequest(StrictModel):
    bars: list[BacktestBarRequest] = Field(min_length=2)
    cases: list[BacktestCaseRequest] = Field(min_length=1)
    fee_rate: float = Field(default=0.0004, ge=0, lt=1)


def _build_store() -> SnapshotStore:
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"):
        return SupabaseSnapshotStore.from_environment()
    return InMemorySnapshotStore()


def create_app(
    store: SnapshotStore | None = None,
    selector: DecisionSelector | None = None,
    paper_engine: PaperTradingEngine | None = None,
    notifier: TelegramNotifier | None = None,
) -> FastAPI:
    """Create the API with an injectable snapshot store for tests."""
    snapshot_store = store or _build_store()
    load_config = getattr(snapshot_store, "load_config", None)
    runtime_config = load_config() if callable(load_config) else None
    if runtime_config is None:
        runtime_config = TradeBrainConfig.defaults()
    active_paper_engine = paper_engine or PaperTradingEngine()
    notified_report_keys: set[tuple[str, int, str]] = set()
    evaluation_progress = EvaluationProgress(snapshot_store)
    evaluation_lock = asyncio.Lock()
    load_paper_recommendations = getattr(snapshot_store, "load_paper_recommendations", None)
    if callable(load_paper_recommendations):
        for recommendation in load_paper_recommendations():
            active_paper_engine.restore(recommendation)
    load_paper_accounts = getattr(snapshot_store, "load_paper_accounts", None)
    if callable(load_paper_accounts):
        for account in load_paper_accounts():
            active_paper_engine.restore_account(account)
    service = FastAPI(title="Trade V2 Brain", version="0.2.0")

    @service.get("/health")
    async def health() -> dict[str, str | bool]:
        """Return liveness plus the execution safety boundary."""
        return {
            "status": "ok",
            "service": "trade-brain",
            "execution_mode": "PAPER",
            "real_money_enabled": False,
        }

    @service.get("/v1/snapshots")
    async def list_snapshots(limit: int = 50) -> dict[str, object]:
        """Return recent snapshots for the dashboard."""
        return {
            "snapshots": [
                snapshot.model_dump(mode="json")
                for snapshot in snapshot_store.list_snapshots(limit)
            ]
        }

    @service.get("/v1/config")
    async def get_config() -> dict[str, object]:
        """Return editable research settings, never credentials."""
        return {"config": runtime_config.model_dump(mode="json")}

    @service.get("/v1/universe/scans")
    async def list_universe_scans(limit: int = 20) -> dict[str, object]:
        """Return recent Binance universe audits, including exclusion reasons."""
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
        loader = getattr(snapshot_store, "list_universe_scans", None)
        scans = loader(limit) if callable(loader) else []
        return {"scans": scans}

    @service.get("/v1/evaluation/status")
    async def evaluation_status() -> dict[str, object]:
        """Return shared evaluation queue progress and the latest result."""
        return {"evaluation": evaluation_progress.current()}

    @service.put("/v1/config")
    async def update_config(request: TradeBrainConfig) -> dict[str, object]:
        """Persist validated research settings for future PAPER cycles."""
        nonlocal runtime_config
        if request.config_version == "config-v1":
            request = request.model_copy(update={"config_version": "config-v2"})
        saver = getattr(snapshot_store, "save_config", None)
        if not callable(saver):
            raise HTTPException(status_code=503, detail="Config store chưa được cấu hình.")
        saver(request)
        runtime_config = request
        return {"status": "ok", "config": runtime_config.model_dump(mode="json")}

    @service.get("/v1/decisions/recent")
    async def list_recent_decisions(limit: int = 20) -> dict[str, object]:
        """Return recent Claude decisions without exposing secrets or raw prompts."""
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
        loader = getattr(snapshot_store, "list_decision_history", None)
        decisions = loader(limit) if callable(loader) else []
        return {"decisions": decisions}

    @service.post("/v1/snapshots/collect")
    async def collect_snapshot(request: SnapshotCollectionRequest) -> dict[str, object]:
        """Collect and persist one public-data snapshot."""
        ensure_experiment = getattr(snapshot_store, "ensure_experiment", None)
        if ensure_experiment is not None:
            ensure_experiment(request.experiment_id)
        client = BinancePublicClient()
        try:
            snapshot = await PublicSnapshotCollector(client, snapshot_store).collect(
                request.experiment_id,
                request.symbol,
                now=None,
            )
        finally:
            await client.close()
        return {"snapshot": snapshot.model_dump(mode="json")}

    @service.post("/v1/evaluate")
    async def evaluate_now() -> dict[str, object]:
        """Run one Binance-to-paper decision cycle on demand."""
        if evaluation_lock.locked():
            raise HTTPException(status_code=409, detail="Một lượt đánh giá đang chạy.")

        async with evaluation_lock:
            experiment_id = os.environ.get("TRADE_V1_EXPERIMENT_ID", "trade-v1-local")
            active_selector = selector
            owns_selector = False
            active_client = BinancePublicClient()
            active_notifier = notifier
            owns_notifier = False
            try:
                if active_notifier is None and os.environ.get("TELEGRAM_BOT_TOKEN"):
                    chat_ids = set(configured_chat_ids())
                    load_chat_ids = getattr(snapshot_store, "list_telegram_chat_ids", None)
                    if callable(load_chat_ids):
                        chat_ids.update(load_chat_ids())
                    active_notifier = TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], tuple(chat_ids))
                    owns_notifier = True

                symbols = manual_universe(runtime_config.symbols)
                universe_mode = getattr(runtime_config.universe_mode, "value", runtime_config.universe_mode)
                universe_summary: dict[str, object] = {
                    "mode": universe_mode,
                    "symbol_count": len(symbols),
                }
                if universe_mode == "BINANCE_VOLUME":
                    selection = await select_binance_universe(active_client, runtime_config.universe)
                    save_universe_scan = getattr(snapshot_store, "save_universe_scan", None)
                    if callable(save_universe_scan):
                        save_universe_scan(selection, runtime_config)
                    symbols = selection.symbols
                    universe_summary = {
                        "mode": universe_mode,
                        "scan_id": selection.scan_id,
                        "symbol_count": selection.passed_count,
                        "input_ticker_count": selection.input_ticker_count,
                        "registry_count": selection.registry_count,
                        "exclusion_counts": selection.exclusion_counts,
                    }
                    if not symbols:
                        return {
                            "status": "ok",
                            "message": "Universe V2 chưa có mã đạt volume 24h tối thiểu.",
                            "evaluated_snapshot_ids": [],
                            "decision_count": 0,
                            "recommendation_count": 0,
                            "universe": universe_summary,
                        }

                run_id = evaluation_progress.start(tuple(symbols))
                if active_notifier is not None:
                    await active_notifier.send(
                        f"🔄 BẮT ĐẦU QUÉT PAPER\n📦 Queue: {len(symbols)} mã\n🧠 Trade Brain V2 đang phân tích..."
                    )
                recommendation_ids_before = {
                    recommendation.recommendation_id
                    for recommendation in active_paper_engine.list_recommendations()
                }

                async def on_progress(index: int, total: int, symbol: str, completed: bool) -> None:
                    evaluation_progress.mark_symbol(run_id, index, symbol, completed)

                if active_selector is None:
                    try:
                        active_selector = ClaudeSelector.from_environment()
                    except ValueError as error:
                        raise HTTPException(status_code=503, detail="Claude selector is not configured") from error
                    owns_selector = True

                recommendation_ids_before = {
                    recommendation.recommendation_id
                    for recommendation in active_paper_engine.list_recommendations()
                }
                results = await run_decision_once(
                    snapshot_store,
                    experiment_id,
                    symbols,
                    active_selector,
                    active_client,
                    active_paper_engine,
                    config=DecisionWorkerConfig(
                        equity_usdt=runtime_config.initial_equity_usdt,
                        available_margin_usdt=runtime_config.initial_equity_usdt,
                        paper_mode=runtime_config.paper_mode,
                    ),
                    brain_config=runtime_config,
                    notifier=active_notifier,
                    notified_report_keys=notified_report_keys,
                    on_progress=on_progress,
                )
                decision_count = sum(len(result.decisions) for result in results)
                recommendation_count = sum(
                    recommendation.recommendation_id not in recommendation_ids_before
                    for recommendation in active_paper_engine.list_recommendations()
                )
                snapshot_symbols = {
                    result.snapshot_id: snapshot.symbol
                    for result in results
                    if (snapshot := snapshot_store.get_snapshot(result.snapshot_id)) is not None
                }
                new_recommendations = [
                    recommendation
                    for recommendation in active_paper_engine.list_recommendations()
                    if recommendation.recommendation_id not in recommendation_ids_before
                ]
                result_summary = build_evaluation_summary(results, new_recommendations, snapshot_symbols)
                evaluation_progress.finish(run_id, result_summary)
                return {
                    "status": "ok",
                    "message": f"Đã đánh giá {len(results)} mã, tạo {decision_count} quyết định PAPER.",
                    "evaluated_snapshot_ids": [result.snapshot_id for result in results],
                    "decision_count": decision_count,
                    "recommendation_count": recommendation_count,
                    "universe": universe_summary,
                    "result_summary": result_summary,
                }
            except Exception as error:
                if "run_id" in locals():
                    evaluation_progress.fail(run_id, str(error))
                raise
            finally:
                if owns_selector and isinstance(active_selector, ClaudeSelector):
                    await active_selector.close()
                await active_client.close()
                if owns_notifier and active_notifier is not None:
                    await active_notifier.close()

    @service.post("/v1/decisions/run")
    async def run_decision(request: DecisionRunRequest) -> dict[str, object]:
        """Run risk-gated Claude selection for one stored snapshot."""
        snapshot = snapshot_store.get_snapshot(request.snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="snapshot not found")
        if any(candidate.snapshot_id != snapshot.snapshot_id for candidate in request.candidates):
            raise HTTPException(status_code=422, detail="candidate snapshot does not match request")

        risk_inputs = {
            candidate_id: _to_risk_input(value)
            for candidate_id, value in request.risk_inputs.items()
        }
        active_selector = selector
        owns_selector = False
        if active_selector is None:
            try:
                active_selector = ClaudeSelector.from_environment()
            except ValueError as error:
                raise HTTPException(status_code=503, detail="Claude selector is not configured") from error
            owns_selector = True
        try:
            result = await run_decision_cycle(snapshot, request.candidates, risk_inputs, active_selector)
        finally:
            if owns_selector and isinstance(active_selector, ClaudeSelector):
                await active_selector.close()
        persist_cycle = getattr(snapshot_store, "save_decision_cycle", None)
        decision_ids: dict[str, str] = {}
        if callable(persist_cycle):
            persisted_ids = persist_cycle(result)
            if isinstance(persisted_ids, dict):
                decision_ids = {str(profile): str(decision_id) for profile, decision_id in persisted_ids.items()}
        paper_result = create_paper_recommendations(
            result,
            active_paper_engine,
            snapshot.decision_time,
        )
        ensure_accounts = getattr(snapshot_store, "ensure_paper_accounts", None)
        account_ids: dict[str, str] = {}
        if callable(ensure_accounts):
            account_result = ensure_accounts(snapshot.experiment_id)
            if isinstance(account_result, dict):
                account_ids = {str(profile): str(account_id) for profile, account_id in account_result.items()}
        save_recommendation = getattr(snapshot_store, "save_paper_recommendation", None)
        if callable(save_recommendation):
            for recommendation in paper_result.recommendations:
                profile = recommendation.profile.value
                account_id = account_ids.get(profile)
                decision_id = decision_ids.get(profile)
                if account_id and decision_id:
                    save_recommendation(
                        snapshot.experiment_id,
                        account_id,
                        decision_id,
                        recommendation,
                        snapshot.symbol,
                    )
        notification_errors: list[str] = []
        active_notifier = notifier
        owns_notifier = False
        if active_notifier is None and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            active_notifier = TelegramNotifier.from_environment()
            owns_notifier = True
        if active_notifier is not None:
            try:
                for recommendation in paper_result.recommendations:
                    try:
                        await active_notifier.send(format_recommendation(recommendation))
                    except (TelegramError, httpx.HTTPError) as error:
                        notification_errors.append(str(error))
            except (TelegramError, httpx.HTTPError) as error:
                notification_errors.append(str(error))
            finally:
                if owns_notifier:
                    await active_notifier.close()
        report_errors = await _notify_milestone_reports(
            active_paper_engine,
            notifier if not owns_notifier else None,
            notified_report_keys,
        )
        notification_errors.extend(report_errors)
        return {
            "snapshot_id": result.snapshot_id,
            "candidates": [candidate.model_dump(mode="json") for candidate in result.candidates],
            "risk_results": {
                candidate_id: asdict(risk_result)
                for candidate_id, risk_result in result.risk_results.items()
            },
            "decisions": [decision.model_dump(mode="json") for decision in result.decisions],
            "claude_audit": result.claude_audit,
            "gate_audit": result.gate_audit,
            "validation_errors": {
                profile.value: error for profile, error in result.validation_errors.items()
            },
            "paper_recommendations": [
                {
                    "recommendation_id": recommendation.recommendation_id,
                    "profile": recommendation.profile.value,
                    "candidate_id": recommendation.candidate.candidate_id,
                    "quantity": str(recommendation.quantity),
                    "state": recommendation.state.value,
                    "emitted_at": recommendation.emitted_at.isoformat(),
                    "expires_at": recommendation.expires_at.isoformat(),
                }
                for recommendation in paper_result.recommendations
            ],
            "paper_skipped": {
                profile.value: reason for profile, reason in paper_result.skipped.items()
            },
            "notification_errors": notification_errors,
        }

    @service.post("/v1/decisions/validate")
    async def validate_decision_endpoint(request: DecisionValidationRequest) -> dict[str, object]:
        """Validate one Claude decision against backend-issued candidates."""
        candidates = {candidate.candidate_id: candidate for candidate in request.candidates}
        try:
            decision = validate_decision(request.decision, candidates)
        except DecisionValidationError as error:
            return JSONResponse(
                status_code=422,
                content={"valid": False, "code": "DECISION_CONTRACT_ERROR", "error": str(error)},
            )
        return {"valid": True, "decision": decision.model_dump(mode="json")}

    @service.get("/v1/paper/recommendations")
    async def list_paper_recommendations() -> dict[str, object]:
        """Return the current in-memory paper journal."""
        return {
            "recommendations": [
                _serialize_recommendation(recommendation, snapshot_store)
                for recommendation in active_paper_engine.list_recommendations()
            ]
        }

    @service.get("/v1/paper/accounts")
    async def list_paper_accounts() -> dict[str, object]:
        """Return independent paper equity ledgers by profile."""
        return {
            "accounts": [
                _serialize_account(account, active_paper_engine.equity(account.profile))
                for account in active_paper_engine.list_accounts()
            ]
        }

    @service.get("/v1/reports")
    async def list_reports(milestone: int = Query(default=1, gt=0)) -> dict[str, object]:
        """Return locked 100-member profile reports from the paper journal."""
        tracker = CohortTracker(milestone_size=100)
        for recommendation in active_paper_engine.list_recommendations():
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
                    initial_equity=active_paper_engine.get_account(recommendation.profile).initial_equity,
                    data_quality=recommendation.data_quality,
                )
            )
        reports = [
            tracker.report(CohortScope.PROFILE_RECOMMENDATIONS, profile, milestone)
            for profile in Profile
        ]
        completed_trade_reports = [
            tracker.report(CohortScope.PROFILE_COMPLETED_TRADES, profile, milestone)
            for profile in Profile
        ]
        global_setup_report = tracker.report(CohortScope.GLOBAL_SETUP_RECOMMENDATIONS, None, milestone)
        reports_to_persist = [*reports, *completed_trade_reports, global_setup_report]
        for report in reports_to_persist:
            _persist_report(snapshot_store, active_paper_engine, report)
        return {
            "milestone": milestone,
            "reports": [_serialize_report(report) for report in reports],
            "completed_trade_reports": [_serialize_report(report) for report in completed_trade_reports],
            "global_setup_report": _serialize_report(global_setup_report),
        }

    @service.post("/v1/backtests/run")
    async def run_backtest_endpoint(request: BacktestRunRequest) -> dict[str, object]:
        """Run a deterministic research-only backtest over supplied closed bars."""
        bars = [_to_bar(value) for value in request.bars]
        cases = [BacktestCase(value.candidate, value.signal_index) for value in request.cases]
        try:
            trades, summary = run_backtest(cases, bars, Decimal(str(request.fee_rate)))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "statistics_status": summary.statistics_status.value,
            "summary": {
                "trade_count": summary.trade_count,
                "wins": summary.wins,
                "losses": summary.losses,
                "timeouts": summary.timeouts,
                "ambiguous": summary.ambiguous,
                "total_net_pnl": str(summary.total_net_pnl),
                "total_net_r": str(summary.total_net_r),
                "win_rate": str(summary.win_rate) if summary.win_rate is not None else None,
                "net_positive_probability": (
                    str(summary.net_positive_probability)
                    if summary.net_positive_probability is not None
                    else None
                ),
                "net_expectancy_r": str(summary.net_expectancy_r) if summary.net_expectancy_r is not None else None,
                "expectancy_lower_95_r": str(summary.expectancy_lower_95_r)
                if summary.expectancy_lower_95_r is not None
                else None,
                "effective_sample_count": summary.effective_sample_count,
            },
            "trades": [
                {
                    "candidate_id": trade.candidate_id,
                    "outcome": trade.outcome.value,
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat(),
                    "net_pnl": str(trade.net_pnl),
                    "net_r": str(trade.net_r),
                }
                for trade in trades
            ],
        }

    @service.post("/v1/paper/recommendations/{recommendation_id}/fill")
    async def fill_paper_recommendation(
        recommendation_id: str,
        request: MarketQuoteRequest,
    ) -> dict[str, object]:
        """Simulate a paper fill from a supplied market quote."""
        try:
            previous_state = active_paper_engine.get(recommendation_id).state
            recommendation = active_paper_engine.fill(recommendation_id, _to_market_quote(request))
        except PaperTradingError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        _persist_paper_state(snapshot_store, active_paper_engine, recommendation)
        report_errors = await _notify_milestone_reports(
            active_paper_engine,
            notifier,
            notified_report_keys,
            snapshot_store,
        )
        transition_errors = await _notify_paper_transition(recommendation, previous_state, notifier)
        return {
            "recommendation": _serialize_recommendation(recommendation, snapshot_store),
            "notification_errors": [*transition_errors, *report_errors],
        }

    @service.post("/v1/paper/recommendations/{recommendation_id}/mark")
    async def mark_paper_recommendation(
        recommendation_id: str,
        request: MarketQuoteRequest,
    ) -> dict[str, object]:
        """Advance an open paper position through TP, SL, or timeout."""
        try:
            previous_state = active_paper_engine.get(recommendation_id).state
            recommendation = active_paper_engine.mark(recommendation_id, _to_market_quote(request))
        except PaperTradingError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        _persist_paper_state(snapshot_store, active_paper_engine, recommendation)
        report_errors = await _notify_milestone_reports(
            active_paper_engine,
            notifier,
            notified_report_keys,
            snapshot_store,
        )
        transition_errors = await _notify_paper_transition(recommendation, previous_state, notifier)
        return {
            "recommendation": _serialize_recommendation(recommendation, snapshot_store),
            "notification_errors": [*transition_errors, *report_errors],
        }

    @service.post("/v1/paper/recommendations/{recommendation_id}/quote")
    async def process_paper_quote(
        recommendation_id: str,
        request: MarketQuoteRequest,
    ) -> dict[str, object]:
        """Advance one paper lifecycle from the latest quote without real orders."""
        try:
            recommendation = active_paper_engine.get(recommendation_id)
            if recommendation.state is PaperState.RECOMMENDED:
                result = await fill_paper_recommendation(recommendation_id, request)
                result["action"] = "FILLED_OR_EXPIRED"
                return result
            if recommendation.state is PaperState.OPEN:
                result = await mark_paper_recommendation(recommendation_id, request)
                result["action"] = "MARKED_OR_OPEN"
                return result
            return {
                "action": "NOOP_TERMINAL",
                "recommendation": _serialize_recommendation(recommendation, snapshot_store),
                "notification_errors": [],
            }
        except PaperTradingError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return service


app = create_app()


def _to_risk_input(request: CandidateRiskInputRequest) -> CandidateRiskInput:
    return CandidateRiskInput(
        statistics=CandidateStatistics(**request.statistics.model_dump()),
        context=RiskContext(**request.context.model_dump()),
        fee_entry_per_unit=request.fee_entry_per_unit,
        fee_exit_per_unit=request.fee_exit_per_unit,
        adverse_slippage_per_unit=request.adverse_slippage_per_unit,
        adverse_funding_per_unit=request.adverse_funding_per_unit,
        quantity_step=request.quantity_step,
        minimum_quantity=request.minimum_quantity,
        price_tick=request.price_tick,
    )


def _to_market_quote(request: MarketQuoteRequest) -> MarketQuote:
    return MarketQuote(
        observed_at=request.observed_at,
        bid=Decimal(str(request.bid)),
        ask=Decimal(str(request.ask)),
        mark=Decimal(str(request.mark)),
        funding_rate=Decimal(str(request.funding_rate)) if request.funding_rate is not None else None,
        funding_time=request.funding_time,
        quality_status=request.quality_status,
    )


def _to_bar(request: BacktestBarRequest) -> Bar:
    if request.high < max(request.open, request.close) or request.low > min(request.open, request.close):
        raise HTTPException(status_code=422, detail="bar high/low must contain open and close")
    if request.high < request.low:
        raise HTTPException(status_code=422, detail="bar high must be >= low")
    return Bar(
        opened_at=request.opened_at,
        closed_at=request.closed_at,
        open=request.open,
        high=request.high,
        low=request.low,
        close=request.close,
        volume=request.volume,
    )


def _serialize_recommendation(
    recommendation: PaperRecommendation,
    store: SnapshotStore | None = None,
) -> dict[str, object]:
    snapshot = store.get_snapshot(recommendation.candidate.snapshot_id) if store is not None else None
    return {
        "recommendation_id": recommendation.recommendation_id,
        "profile": recommendation.profile.value,
        "candidate_id": recommendation.candidate.candidate_id,
        "setup_id": recommendation.candidate.setup_id,
        "snapshot_id": recommendation.candidate.snapshot_id,
        "symbol": snapshot.symbol if snapshot is not None else None,
        "side": recommendation.candidate.side.value,
        "quantity": str(recommendation.quantity),
        "entry_estimate": recommendation.candidate.entry_estimate,
        "stop_price": recommendation.candidate.stop_price,
        "target_price": recommendation.candidate.target_price,
        "state": recommendation.state.value,
        "outcome": recommendation.outcome.value if recommendation.outcome else None,
        "emitted_at": recommendation.emitted_at.isoformat(),
        "expires_at": recommendation.expires_at.isoformat(),
        "entry_fill": str(recommendation.entry_fill) if recommendation.entry_fill is not None else None,
        "exit_fill": str(recommendation.exit_fill) if recommendation.exit_fill is not None else None,
        "data_quality": recommendation.data_quality.value,
        "funding_pnl": str(recommendation.funding_pnl),
        "closed_at": recommendation.closed_at.isoformat() if recommendation.closed_at else None,
        "net_pnl": str(recommendation.net_pnl) if recommendation.net_pnl is not None else None,
        "net_r": str(recommendation.net_r) if recommendation.net_r is not None else None,
    }


def _persist_paper_state(
    store: SnapshotStore,
    engine: PaperTradingEngine,
    recommendation: PaperRecommendation,
) -> None:
    snapshot = store.get_snapshot(recommendation.candidate.snapshot_id)
    append_ledger_event = getattr(store, "append_paper_ledger_event", None)
    if snapshot is not None and callable(append_ledger_event):
        append_ledger_event(snapshot.experiment_id, recommendation)
    update_recommendation = getattr(store, "update_paper_recommendation", None)
    if callable(update_recommendation):
        update_recommendation(recommendation)
    project_paper_state = getattr(store, "project_paper_state", None)
    if snapshot is not None and callable(project_paper_state):
        account = engine.get_account(recommendation.profile)
        project_paper_state(
            snapshot.experiment_id,
            snapshot.symbol,
            recommendation,
            equity=engine.equity(recommendation.profile),
            initial_equity=account.initial_equity,
            realized_equity=account.current_equity,
            drawdown_pct=engine.drawdown_pcts(recommendation.profile, recommendation.last_quote_at or recommendation.emitted_at)[1],
        )
    if recommendation.outcome is not None:
        save_trade = getattr(store, "save_paper_trade", None)
        if callable(save_trade):
            save_trade(recommendation)
        update_account = getattr(store, "update_paper_account", None)
        if snapshot is not None and callable(update_account):
            update_account(
                snapshot.experiment_id,
                recommendation.profile,
                engine.get_account(recommendation.profile).current_equity,
            )


def _serialize_account(account: PaperAccount, mark_to_market_equity: Decimal | None = None) -> dict[str, object]:
    return {
        "profile": account.profile.value,
        "initial_equity": str(account.initial_equity),
        "current_equity": str(account.current_equity),
        "mark_to_market_equity": str(mark_to_market_equity if mark_to_market_equity is not None else account.current_equity),
        "realized_pnl": str(account.realized_pnl),
    }


def _serialize_report(report: PaperReport | None) -> dict[str, object] | None:
    if report is None:
        return None
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


def _persist_report(
    store: SnapshotStore,
    engine: PaperTradingEngine,
    report: PaperReport | None,
) -> None:
    """Persist a report when the configured store supports durable artifacts."""
    save_report = getattr(store, "save_paper_report", None)
    if report is None or not callable(save_report) or not report.member_ids:
        return
    recommendation = next(
        (
            item
            for item in engine.list_recommendations()
            if item.recommendation_id == report.member_ids[0]
        ),
        None,
    )
    if recommendation is None:
        return
    snapshot = store.get_snapshot(recommendation.candidate.snapshot_id)
    if snapshot is None:
        return
    save_report(snapshot.experiment_id, report)


async def _notify_milestone_reports(
    engine: PaperTradingEngine,
    configured_notifier: TelegramNotifier | None,
    notified_keys: set[tuple[str, int, str]],
    store: SnapshotStore | None = None,
) -> list[str]:
    reports = _build_report_tracker(engine)
    final_reports = []
    report_scopes = [
        (CohortScope.PROFILE_RECOMMENDATIONS, profile)
        for profile in Profile
    ]
    report_scopes.extend(
        (CohortScope.PROFILE_COMPLETED_TRADES, profile)
        for profile in Profile
    )
    report_scopes.append((CohortScope.GLOBAL_SETUP_RECOMMENDATIONS, None))
    for scope, profile in report_scopes:
        for milestone in reports.available_milestones(scope, profile):
            report = reports.report(scope, profile, milestone)
            if report is None:
                continue
            if store is not None:
                _persist_report(store, engine, report)
            scope_key = f"{scope.value}:{profile.value if profile else 'ALL'}"
            key = (scope_key, report.milestone, report.cohort_status)
            if key not in notified_keys:
                final_reports.append(report)
                notified_keys.add(key)
    if not final_reports:
        return []
    notifier = configured_notifier
    owns_notifier = False
    if notifier is None and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        notifier = TelegramNotifier.from_environment()
        owns_notifier = True
    if notifier is None:
        return []
    errors: list[str] = []
    try:
        for report in final_reports:
            try:
                await notifier.send(format_report(report))
            except (TelegramError, httpx.HTTPError) as error:
                errors.append(str(error))
    finally:
        if owns_notifier:
            await notifier.close()
    return errors


async def _notify_paper_transition(
    recommendation: PaperRecommendation,
    previous_state: PaperState,
    configured_notifier: TelegramNotifier | None,
) -> list[str]:
    """Notify only when a paper recommendation changes lifecycle state."""
    if recommendation.state is previous_state:
        return []
    notifier = configured_notifier
    owns_notifier = False
    if notifier is None and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        notifier = TelegramNotifier.from_environment()
        owns_notifier = True
    if notifier is None:
        return []
    try:
        await notifier.send(format_recommendation(recommendation))
    except (TelegramError, httpx.HTTPError) as error:
        return [str(error)]
    finally:
        if owns_notifier:
            await notifier.close()
    return []


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
            )
        )
    return tracker
