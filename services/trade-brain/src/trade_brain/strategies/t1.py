"""T1 breakout and retest candidate construction."""

from dataclasses import dataclass
from datetime import datetime

from trade_brain.contracts import Profile, StatisticsStatus, TradeCandidate
from trade_brain.market_data import Bar, PivotKind, atr_wilder, confirmed_pivots, relative_volume


@dataclass(frozen=True, slots=True)
class T1Config:
    """Research parameters for the first T1 implementation."""

    pivot_left: int = 3
    pivot_right: int = 3
    breakout_buffer_atr: float = 0.10
    min_break_body_ratio: float = 0.50
    min_break_volume_ratio: float = 1.30
    retest_window_bars: int = 8
    opposite_penetration_atr: float = 0.25
    stop_buffer_atr: float = 0.20
    target_r: float = 2.0


@dataclass(frozen=True, slots=True)
class T1Setup:
    """A completed 1H breakout and retest ready for 15m confirmation."""

    setup_id: str
    snapshot_id: str
    side: str
    level: float
    zone_atr: float
    breakout_index: int
    retest_index: int
    trigger_index: int | None
    stop_price: float
    entry_price: float
    target_price: float
    trigger_reached: bool


def find_breakout_retest_setups(
    bars: list[Bar],
    snapshot_id: str,
    config: T1Config = T1Config(),
    trigger_bars: list[Bar] | None = None,
) -> list[T1Setup]:
    """Find confirmed 1H breakout and retest setups without future leakage."""
    atr_values = atr_wilder(bars)
    pivots = confirmed_pivots(bars, config.pivot_left, config.pivot_right)
    setups: list[T1Setup] = []
    for pivot in pivots:
        if pivot.kind is PivotKind.HIGH:
            setup = _find_long_setup(bars, atr_values, pivot.index, snapshot_id, config, trigger_bars)
        else:
            setup = _find_short_setup(bars, atr_values, pivot.index, snapshot_id, config, trigger_bars)
        if setup is not None:
            setups.append(setup)
    return setups


def build_t1_candidates(
    setup: T1Setup,
    statistics_status: StatisticsStatus = StatisticsStatus.RESEARCH_ONLY,
) -> list[TradeCandidate]:
    """Create one candidate per profile without inventing statistics."""
    stages = {
        Profile.PROACTIVE: "EARLY",
        Profile.BALANCED: "CONFIRMED",
        Profile.CAUTIOUS: "STRICT",
    }
    return [
        TradeCandidate(
            candidate_id=f"{setup.setup_id}:{profile.value.lower()}",
            snapshot_id=setup.snapshot_id,
            setup_id=setup.setup_id,
            profile=profile,
            strategy="T1",
            entry_stage=stages[profile],
            side=setup.side,
            entry_estimate=setup.entry_price,
            stop_price=setup.stop_price,
            target_price=setup.target_price,
            horizon_bars=96,
            statistics_status=statistics_status,
            eligible=setup.trigger_reached or profile is Profile.PROACTIVE,
        )
        for profile in Profile
    ]


def _find_long_setup(
    bars: list[Bar],
    atr_values: list[float | None],
    level_index: int,
    snapshot_id: str,
    config: T1Config,
    trigger_bars: list[Bar] | None,
) -> T1Setup | None:
    return _find_directional_setup(
        bars, atr_values, level_index, snapshot_id, config, is_long=True, trigger_bars=trigger_bars
    )


def _find_short_setup(
    bars: list[Bar],
    atr_values: list[float | None],
    level_index: int,
    snapshot_id: str,
    config: T1Config,
    trigger_bars: list[Bar] | None,
) -> T1Setup | None:
    return _find_directional_setup(
        bars, atr_values, level_index, snapshot_id, config, is_long=False, trigger_bars=trigger_bars
    )


def _find_directional_setup(
    bars: list[Bar],
    atr_values: list[float | None],
    level_index: int,
    snapshot_id: str,
    config: T1Config,
    is_long: bool,
    trigger_bars: list[Bar] | None,
) -> T1Setup | None:
    level_atr = atr_values[level_index]
    if level_atr is None or level_atr <= 0:
        return None
    breakout_index = _find_breakout_index(bars, atr_values, level_index, config, is_long)
    if breakout_index is None:
        return None
    retest_index = _find_retest_index(bars, atr_values, breakout_index, level_index, config, is_long)
    if retest_index is None:
        return None
    trigger_index = None
    if trigger_bars is not None:
        trigger_index = _find_trigger_index(
            trigger_bars,
            bars[retest_index].closed_at,
            bars[level_index].high if is_long else bars[level_index].low,
            config,
            is_long,
        )
    entry = bars[retest_index].close
    stop = _stop_price(bars, retest_index, atr_values[retest_index], is_long, config)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    target = entry + (risk * config.target_r) if is_long else entry - (risk * config.target_r)
    return T1Setup(
        setup_id=f"t1:{snapshot_id}:{level_index}:{retest_index}",
        snapshot_id=snapshot_id,
        side="LONG" if is_long else "SHORT",
        level=bars[level_index].high if is_long else bars[level_index].low,
        zone_atr=level_atr,
        breakout_index=breakout_index,
        retest_index=retest_index,
        trigger_index=trigger_index,
        stop_price=stop,
        entry_price=entry,
        target_price=target,
        trigger_reached=trigger_index is not None,
    )


def _find_trigger_index(
    bars: list[Bar],
    retest_closed_at: datetime,
    level: float,
    config: T1Config,
    is_long: bool,
) -> int | None:
    """Find a confirmed 15m trigger without using a pivot before it is knowable."""
    atr_values = atr_wilder(bars)
    pivots = confirmed_pivots(bars, config.pivot_left, config.pivot_right)
    for index, bar in enumerate(bars):
        if bar.closed_at <= retest_closed_at:
            continue
        atr_value = atr_values[index - 1] if index > 0 else None
        volume_ratio = relative_volume(bars, index)
        if atr_value is None or atr_value <= 0 or volume_ratio is None:
            continue
        known_pivots = [
            pivot
            for pivot in pivots
            if pivot.known_at_index < index
            and pivot.kind is (PivotKind.HIGH if is_long else PivotKind.LOW)
        ]
        if not known_pivots:
            continue
        trigger_pivot = known_pivots[-1]
        threshold = trigger_pivot.price + config.breakout_buffer_atr / 2 * atr_value
        reached = bar.close > threshold if is_long else bar.close < threshold
        if reached and volume_ratio >= 1.10:
            return index
    return None


def _find_breakout_index(
    bars: list[Bar],
    atr_values: list[float | None],
    level_index: int,
    config: T1Config,
    is_long: bool,
) -> int | None:
    for index in range(level_index + 1, len(bars)):
        atr_value = atr_values[index - 1]
        if atr_value is None or atr_value <= 0:
            continue
        bar = bars[index]
        body_ratio = abs(bar.close - bar.open) / (bar.high - bar.low) if bar.high > bar.low else 0
        volume_ratio = relative_volume(bars, index)
        if volume_ratio is None or body_ratio < config.min_break_body_ratio:
            continue
        threshold = bars[level_index].high + config.breakout_buffer_atr * atr_value
        if is_long and bar.close > threshold and volume_ratio >= config.min_break_volume_ratio:
            return index
        threshold = bars[level_index].low - config.breakout_buffer_atr * atr_value
        if not is_long and bar.close < threshold and volume_ratio >= config.min_break_volume_ratio:
            return index
    return None


def _find_retest_index(
    bars: list[Bar],
    atr_values: list[float | None],
    breakout_index: int,
    level_index: int,
    config: T1Config,
    is_long: bool,
) -> int | None:
    level = bars[level_index].high if is_long else bars[level_index].low
    end = min(len(bars), breakout_index + config.retest_window_bars + 1)
    for index in range(breakout_index + 1, end):
        atr_value = atr_values[index - 1]
        if atr_value is None or atr_value <= 0:
            continue
        bar = bars[index]
        touched = bar.low <= level <= bar.high
        closes_correctly = bar.close > level if is_long else bar.close < level
        opposite_limit = level - config.opposite_penetration_atr * atr_value if is_long else level + config.opposite_penetration_atr * atr_value
        invalidated = bar.close < opposite_limit if is_long else bar.close > opposite_limit
        if touched and closes_correctly and not invalidated:
            return index
    return None


def _stop_price(
    bars: list[Bar],
    retest_index: int,
    atr_value: float | None,
    is_long: bool,
    config: T1Config,
) -> float:
    if atr_value is None or atr_value <= 0:
        raise ValueError("ATR is required to calculate the stop")
    window = bars[max(0, retest_index - 8) : retest_index + 1]
    if is_long:
        return min(bar.low for bar in window) - config.stop_buffer_atr * atr_value
    return max(bar.high for bar in window) + config.stop_buffer_atr * atr_value
