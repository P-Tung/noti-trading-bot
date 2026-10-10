"""Compose backend-owned T1, T2, and R1 candidate generation."""

from dataclasses import dataclass, field, replace
from datetime import datetime

from trade_brain.contracts import Side, StatisticsStatus, TradeCandidate
from trade_brain.market_data import Bar, Pivot, atr_wilder, confirmed_pivots, ema, relative_volume
from trade_brain.strategies.alternatives import (
    R1Config,
    StrategySetup,
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
    audit: dict[str, object] = field(default_factory=dict)


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
    return StrategyPipelineResult(tuple(candidates), tuple(setup_ids), _pipeline_audit(candidates, setup_ids))


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
    """Run 1H setup detection, then use closed 15m data for confirmation gates."""
    t1_setups = find_breakout_retest_setups(bars_1h, snapshot_id, t1_config, trigger_bars=bars_15m)
    alternative_setups = find_alternative_setups(bars_1h, snapshot_id, t2_config, r1_config)
    if current_only:
        t1_setups = _current_t1_setups(t1_setups, len(bars_1h), len(bars_15m))
        alternative_setups = [
            setup for setup in alternative_setups if setup.signal_index == len(bars_1h) - 1
        ]
    alternative_setups = _apply_15m_confirmation(alternative_setups, bars_1h, bars_15m)
    candidates: list[TradeCandidate] = []
    setup_ids: list[str] = []
    for setup in t1_setups:
        candidates.extend(build_t1_candidates(setup, statistics_status))
        setup_ids.append(setup.setup_id)
    for setup in alternative_setups:
        candidates.extend(build_strategy_candidates(setup, statistics_status))
        setup_ids.append(setup.setup_id)
    candidates = _apply_higher_timeframe_gate(candidates, bars_4h, bars_1d)
    candidates = _apply_strict_delta_gate(candidates, bars_15m)
    return StrategyPipelineResult(
        tuple(candidates),
        tuple(setup_ids),
        _pipeline_audit(candidates, setup_ids),
    )


def _pipeline_audit(candidates: list[TradeCandidate], setup_ids: list[str]) -> dict[str, object]:
    by_strategy_side: dict[str, int] = {}
    eligible_by_strategy_side: dict[str, int] = {}
    for candidate in candidates:
        key = f"{candidate.strategy}:{candidate.side.value}"
        by_strategy_side[key] = by_strategy_side.get(key, 0) + 1
        if candidate.eligible:
            eligible_by_strategy_side[key] = eligible_by_strategy_side.get(key, 0) + 1
    return {
        "setup_count": len(setup_ids),
        "no_setup": not setup_ids,
        "candidate_count": len(candidates),
        "candidates_by_strategy_side": by_strategy_side,
        "eligible_by_strategy_side": eligible_by_strategy_side,
    }


def _apply_15m_confirmation(
    setups: list[StrategySetup],
    bars_1h: list[Bar],
    bars_15m: list[Bar],
) -> list[StrategySetup]:
    """Map each 1H setup to the first closed 15m confirmation after its signal."""
    if not bars_15m:
        return [replace(setup, early_trigger=False, confirmed_trigger=False, strict_trigger=False) for setup in setups]
    atr_values = atr_wilder(bars_15m)
    pivots = confirmed_pivots(bars_15m)
    confirmed: list[StrategySetup] = []
    for setup in setups:
        if setup.signal_index is None or setup.signal_index >= len(bars_1h):
            confirmed.append(replace(setup, early_trigger=False, confirmed_trigger=False, strict_trigger=False))
            continue
        signal_time = bars_1h[setup.signal_index].closed_at
        trigger = _find_15m_confirmation(setup.side, signal_time, bars_15m, atr_values, pivots)
        if trigger is None:
            confirmed.append(replace(setup, early_trigger=False, confirmed_trigger=False, strict_trigger=False))
            continue
        delta = _bar_delta(trigger)
        strict = delta is not None and ((setup.side is Side.LONG and delta > 0) or (setup.side is Side.SHORT and delta < 0))
        confirmed.append(replace(setup, early_trigger=True, confirmed_trigger=True, strict_trigger=strict))
    return confirmed


def _find_15m_confirmation(
    side: Side,
    signal_time: datetime,
    bars: list[Bar],
    atr_values: list[float | None],
    pivots: list[Pivot],
) -> Bar | None:
    pivot_kind = "HIGH" if side is Side.LONG else "LOW"
    for index, bar in enumerate(bars):
        if bar.closed_at <= signal_time or index == 0:
            continue
        atr_value = atr_values[index - 1]
        volume_ratio = relative_volume(bars, index)
        known = [pivot for pivot in pivots if pivot.known_at_index < index and pivot.kind.value == pivot_kind]
        if atr_value is None or volume_ratio is None or not known or volume_ratio < 1.10:
            continue
        threshold = known[-1].price + (0.05 * atr_value if side is Side.LONG else -0.05 * atr_value)
        if (side is Side.LONG and bar.close > threshold) or (side is Side.SHORT and bar.close < threshold):
            return bar
    return None


def _bar_delta(bar: Bar) -> float | None:
    if bar.quote_volume is None or bar.taker_buy_quote_volume is None:
        return None
    return (2 * bar.taker_buy_quote_volume) - bar.quote_volume


def _apply_strict_delta_gate(candidates: list[TradeCandidate], bars_15m: list[Bar]) -> list[TradeCandidate]:
    """V2 STRICT requires a known 15m taker delta in the candidate direction."""
    if not bars_15m:
        return [
            candidate.model_copy(
                update={
                    "eligible": False,
                    "eligibility_reasons": [*candidate.eligibility_reasons, "REQUIRED_SOURCE_MISSING"],
                }
            )
            if candidate.entry_stage == "STRICT"
            else candidate
            for candidate in candidates
        ]
    latest = bars_15m[-1]
    if latest.quote_volume is None or latest.taker_buy_quote_volume is None:
        delta: float | None = None
    else:
        delta = (2 * latest.taker_buy_quote_volume) - latest.quote_volume
    updated: list[TradeCandidate] = []
    for candidate in candidates:
        if candidate.entry_stage != "STRICT" or not candidate.eligible:
            updated.append(candidate)
            continue
        matches_direction = delta is not None and (
            (candidate.side is Side.LONG and delta > 0)
            or (candidate.side is Side.SHORT and delta < 0)
        )
        updated.append(
            candidate.model_copy(
                update={
                    "eligible": matches_direction,
                    "eligibility_reasons": []
                    if matches_direction
                    else [*candidate.eligibility_reasons, "STRICT_DELTA_MISMATCH"],
                }
            )
        )
    return updated


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
    updated: list[TradeCandidate] = []
    for candidate in candidates:
        allowed = _candidate_is_allowed(candidate, context)
        reasons = candidate.eligibility_reasons
        if candidate.strategy != "R1" and candidate.eligible and not allowed:
            reasons = [*reasons, "REGIME_BLOCKED"]
        updated.append(candidate.model_copy(update={"eligible": allowed, "eligibility_reasons": reasons}))
    return updated


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
