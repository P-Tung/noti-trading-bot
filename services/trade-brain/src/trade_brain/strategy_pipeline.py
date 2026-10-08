"""Compose backend-owned T1, T2, and R1 candidate generation."""

from dataclasses import dataclass

from trade_brain.contracts import Side, StatisticsStatus, TradeCandidate
from trade_brain.market_data import Bar, ema
from trade_brain.strategies.alternatives import (
    R1Config,
    T2Config,
    build_strategy_candidates,
    find_alternative_setups,
)
from trade_brain.strategies.t1 import T1Config, T1Setup, build_t1_candidates, find_breakout_retest_setups


@dataclass(frozen=True, slots=True)
class StrategyPipelineResult:
    """All backend candidates generated for one immutable snapshot."""

    candidates: tuple[TradeCandidate, ...]
    setup_ids: tuple[str, ...]


def build_all_candidates(
    bars: list[Bar],
    snapshot_id: str,
    t1_config: T1Config = T1Config(),
    t2_config: T2Config = T2Config(),
    r1_config: R1Config = R1Config(),
    statistics_status: StatisticsStatus = StatisticsStatus.RESEARCH_ONLY,
) -> StrategyPipelineResult:
    """Detect all supported setups and create exactly three profile candidates per setup."""
    t1_setups = find_breakout_retest_setups(bars, snapshot_id, t1_config)
    alternative_setups = find_alternative_setups(bars, snapshot_id, t2_config, r1_config)
    candidates: list[TradeCandidate] = []
    setup_ids: list[str] = []
    for setup in t1_setups:
        candidates.extend(build_t1_candidates(setup, statistics_status))
        setup_ids.append(setup.setup_id)
    for setup in alternative_setups:
        candidates.extend(build_strategy_candidates(setup, statistics_status))
        setup_ids.append(setup.setup_id)
    return StrategyPipelineResult(tuple(candidates), tuple(setup_ids))


def build_all_candidates_multi_timeframe(
    bars_1h: list[Bar],
    bars_15m: list[Bar],
    snapshot_id: str,
    t1_config: T1Config = T1Config(),
    t2_config: T2Config = T2Config(),
    r1_config: R1Config = R1Config(),
    statistics_status: StatisticsStatus = StatisticsStatus.RESEARCH_ONLY,
    bars_4h: list[Bar] | None = None,
    bars_1d: list[Bar] | None = None,
    current_only: bool = False,
) -> StrategyPipelineResult:
    """Run T1 on 1H structure and T2/R1 on 15m trigger data."""
    t1_setups = find_breakout_retest_setups(bars_1h, snapshot_id, t1_config, trigger_bars=bars_15m)
    alternative_setups = find_alternative_setups(bars_15m, snapshot_id, t2_config, r1_config)
    if current_only:
        t1_setups = _current_t1_setups(t1_setups, len(bars_1h), len(bars_15m))
        alternative_setups = [
            setup for setup in alternative_setups if setup.signal_index == len(bars_15m) - 1
        ]
    candidates: list[TradeCandidate] = []
    setup_ids: list[str] = []
    for setup in t1_setups:
        candidates.extend(build_t1_candidates(setup, statistics_status))
        setup_ids.append(setup.setup_id)
    for setup in alternative_setups:
        candidates.extend(build_strategy_candidates(setup, statistics_status))
        setup_ids.append(setup.setup_id)
    return StrategyPipelineResult(
        tuple(_apply_higher_timeframe_gate(candidates, bars_4h, bars_1d)),
        tuple(setup_ids),
    )


def _current_t1_setups(
    setups: list[T1Setup],
    bars_1h_count: int,
    bars_15m_count: int,
) -> list[T1Setup]:
    """Keep only a newly triggered T1 setup for live decision cycles."""
    return [
        setup
        for setup in setups
        if (
            setup.trigger_index is not None
            and setup.trigger_index >= bars_15m_count - 1
        )
        or (
            setup.trigger_index is None
            and setup.retest_index == bars_1h_count - 1
        )
    ]


def _apply_higher_timeframe_gate(
    candidates: list[TradeCandidate],
    bars_4h: list[Bar] | None,
    bars_1d: list[Bar] | None,
) -> list[TradeCandidate]:
    """Block trend candidates that conflict with clear 1D/4H context."""
    context = _higher_timeframe_context(bars_4h, bars_1d)
    if context is None:
        return candidates
    return [
        candidate.model_copy(
            update={"eligible": _candidate_is_allowed(candidate, context)}
        )
        for candidate in candidates
    ]


def _candidate_is_allowed(candidate: TradeCandidate, context: str) -> bool:
    if not candidate.eligible:
        return False
    if candidate.strategy == "R1":
        return True
    return context != "CONFLICT" and candidate.side.value == context


def _higher_timeframe_context(
    bars_4h: list[Bar] | None,
    bars_1d: list[Bar] | None,
) -> str | None:
    if not bars_4h or not bars_1d:
        return None
    side_4h = _ema_trend_side(bars_4h)
    side_1d = _ema_trend_side(bars_1d)
    if side_4h is None or side_1d is None:
        return None
    return side_4h if side_4h == side_1d else "CONFLICT"


def _ema_trend_side(bars: list[Bar]) -> str | None:
    values_20 = ema(bars, 20)
    values_50 = ema(bars, 50)
    if values_20[-1] is None or values_50[-1] is None:
        return None
    if values_20[-1] == values_50[-1]:
        return None
    return Side.LONG.value if values_20[-1] > values_50[-1] else Side.SHORT.value
