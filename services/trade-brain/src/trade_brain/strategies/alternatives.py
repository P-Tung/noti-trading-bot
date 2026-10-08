"""Candidate construction for T2 continuation and R1 range-reversion setups."""

from dataclasses import dataclass

from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.market_data import Bar, atr_wilder, ema, relative_volume


@dataclass(frozen=True, slots=True)
class T2Config:
    """Research parameters for pullback continuation detection."""

    ema_period: int = 20
    atr_period: int = 14
    pullback_tolerance_atr: float = 0.35
    stop_buffer_atr: float = 0.20
    target_r: float = 2.0
    horizon_bars: int = 96


@dataclass(frozen=True, slots=True)
class R1Config:
    """Research parameters for range sweep and reversion detection."""

    range_bars: int = 20
    atr_period: int = 14
    sweep_buffer_atr: float = 0.10
    stop_buffer_atr: float = 0.15
    target_r: float = 1.5
    horizon_bars: int = 32


@dataclass(frozen=True, slots=True)
class StrategySetup:
    """Computed price plan shared by the three profile variants."""

    setup_id: str
    snapshot_id: str
    strategy: str
    side: Side
    entry_price: float
    stop_price: float
    target_price: float
    horizon_bars: int
    early_trigger: bool
    confirmed_trigger: bool
    strict_trigger: bool
    signal_index: int | None = None


def find_t2_setups(
    bars: list[Bar],
    snapshot_id: str,
    config: T2Config = T2Config(),
) -> list[StrategySetup]:
    """Find pullback continuation setups using only bars closed before the trigger."""
    _validate_periods(config.ema_period, config.atr_period)
    atr_values = atr_wilder(bars, config.atr_period)
    ema_values = ema(bars, config.ema_period)
    setups: list[StrategySetup] = []
    first_index = max(config.ema_period, config.atr_period) + 1
    for index in range(first_index, len(bars)):
        atr_value = atr_values[index - 1]
        previous_ema = ema_values[index - 1]
        older_ema = ema_values[index - 2]
        if atr_value is None or previous_ema is None or older_ema is None or atr_value <= 0:
            continue
        setup = _t2_setup_at_index(bars, index, snapshot_id, config, atr_value, previous_ema, older_ema)
        if setup is not None:
            setups.append(setup)
    return setups


def find_r1_setups(
    bars: list[Bar],
    snapshot_id: str,
    config: R1Config = R1Config(),
) -> list[StrategySetup]:
    """Find range sweeps using a range built only from preceding completed bars."""
    _validate_periods(config.range_bars, config.atr_period)
    atr_values = atr_wilder(bars, config.atr_period)
    first_index = max(config.range_bars, config.atr_period)
    setups: list[StrategySetup] = []
    for index in range(first_index, len(bars)):
        atr_value = atr_values[index - 1]
        if atr_value is None or atr_value <= 0:
            continue
        range_bars = bars[index - config.range_bars : index]
        range_high = max(bar.high for bar in range_bars)
        range_low = min(bar.low for bar in range_bars)
        setup = _r1_setup_at_index(
            bars[index], index, snapshot_id, config, atr_value, range_high, range_low
        )
        if setup is not None:
            setups.append(setup)
    return setups


def find_alternative_setups(
    bars: list[Bar],
    snapshot_id: str,
    t2_config: T2Config = T2Config(),
    r1_config: R1Config = R1Config(),
) -> list[StrategySetup]:
    """Return T2 and R1 setups in chronological detector order."""
    return sorted(
        [*find_t2_setups(bars, snapshot_id, t2_config), *find_r1_setups(bars, snapshot_id, r1_config)],
        key=lambda setup: setup.setup_id,
    )


def _t2_setup_at_index(
    bars: list[Bar],
    index: int,
    snapshot_id: str,
    config: T2Config,
    atr_value: float,
    previous_ema: float,
    older_ema: float,
) -> StrategySetup | None:
    pullback = bars[index - 1]
    trigger = bars[index]
    if previous_ema <= older_ema:
        return _t2_short_setup(bars, index, snapshot_id, config, atr_value, previous_ema, pullback, trigger)
    if pullback.low > previous_ema + config.pullback_tolerance_atr * atr_value:
        return None
    if pullback.close <= previous_ema or trigger.close <= pullback.high:
        return None
    stop = min(pullback.low, bars[index - 2].low) - config.stop_buffer_atr * atr_value
    risk = trigger.close - stop
    if risk <= 0:
        return None
    volume_ratio = relative_volume(bars, index)
    return StrategySetup(
        f"t2:{snapshot_id}:{index}:long",
        snapshot_id,
        "T2",
        Side.LONG,
        trigger.close,
        stop,
        trigger.close + config.target_r * risk,
        config.horizon_bars,
        True,
        True,
        volume_ratio is not None and volume_ratio >= 1.2,
        index,
    )


def _t2_short_setup(
    bars: list[Bar],
    index: int,
    snapshot_id: str,
    config: T2Config,
    atr_value: float,
    previous_ema: float,
    pullback: Bar,
    trigger: Bar,
) -> StrategySetup | None:
    if pullback.high < previous_ema - config.pullback_tolerance_atr * atr_value:
        return None
    if pullback.close >= previous_ema or trigger.close >= pullback.low:
        return None
    stop = max(pullback.high, bars[index - 2].high) + config.stop_buffer_atr * atr_value
    risk = stop - trigger.close
    if risk <= 0:
        return None
    volume_ratio = relative_volume(bars, index)
    return StrategySetup(
        f"t2:{snapshot_id}:{index}:short",
        snapshot_id,
        "T2",
        Side.SHORT,
        trigger.close,
        stop,
        trigger.close - config.target_r * risk,
        config.horizon_bars,
        True,
        True,
        volume_ratio is not None and volume_ratio >= 1.2,
        index,
    )


def _r1_setup_at_index(
    bar: Bar,
    index: int,
    snapshot_id: str,
    config: R1Config,
    atr_value: float,
    range_high: float,
    range_low: float,
) -> StrategySetup | None:
    swept_low = bar.low < range_low - config.sweep_buffer_atr * atr_value and bar.close > range_low
    swept_high = bar.high > range_high + config.sweep_buffer_atr * atr_value and bar.close < range_high
    if swept_low == swept_high:
        return None
    if swept_low:
        stop = bar.low - config.stop_buffer_atr * atr_value
        risk = bar.close - stop
        if risk <= 0:
            return None
        target = max(bar.close + config.target_r * risk, (range_high + bar.close) / 2)
        strict = bar.close > (range_low + range_high) / 2
        return StrategySetup(
            f"r1:{snapshot_id}:{index}:long",
            snapshot_id,
            "R1",
            Side.LONG,
            bar.close,
            stop,
            target,
            config.horizon_bars,
            True,
            True,
            strict,
            index,
        )
    stop = bar.high + config.stop_buffer_atr * atr_value
    risk = stop - bar.close
    if risk <= 0:
        return None
    target = min(bar.close - config.target_r * risk, (range_low + bar.close) / 2)
    strict = bar.close < (range_low + range_high) / 2
    return StrategySetup(
        f"r1:{snapshot_id}:{index}:short",
        snapshot_id,
        "R1",
        Side.SHORT,
        bar.close,
        stop,
        target,
        config.horizon_bars,
        True,
        True,
        strict,
        index,
    )


def _validate_periods(first: int, second: int) -> None:
    if first <= 0 or second <= 0:
        raise ValueError("strategy periods must be positive")


def build_strategy_candidates(
    setup: StrategySetup,
    statistics_status: StatisticsStatus = StatisticsStatus.RESEARCH_ONLY,
) -> list[TradeCandidate]:
    """Create profile candidates without changing the computed price plan."""
    _validate_setup(setup)
    stages = {
        Profile.PROACTIVE: ("EARLY", setup.early_trigger),
        Profile.BALANCED: ("CONFIRMED", setup.confirmed_trigger),
        Profile.CAUTIOUS: ("STRICT", setup.strict_trigger),
    }
    return [
        TradeCandidate(
            candidate_id=f"{setup.setup_id}:{profile.value.lower()}",
            snapshot_id=setup.snapshot_id,
            setup_id=setup.setup_id,
            profile=profile,
            strategy=setup.strategy,
            entry_stage=stage,
            side=setup.side,
            entry_estimate=setup.entry_price,
            stop_price=setup.stop_price,
            target_price=setup.target_price,
            horizon_bars=setup.horizon_bars,
            statistics_status=statistics_status,
            eligible=eligible,
        )
        for profile, (stage, eligible) in stages.items()
    ]


def _validate_setup(setup: StrategySetup) -> None:
    if setup.strategy not in {"T2", "R1"}:
        raise ValueError("alternative setup strategy must be T2 or R1")
    if setup.entry_price <= 0 or setup.stop_price <= 0 or setup.target_price <= 0:
        raise ValueError("setup prices must be positive")
    if setup.horizon_bars <= 0:
        raise ValueError("setup horizon must be positive")
    if setup.side is Side.LONG and not setup.stop_price < setup.entry_price < setup.target_price:
        raise ValueError("LONG setup prices must be stop < entry < target")
    if setup.side is Side.SHORT and not setup.target_price < setup.entry_price < setup.stop_price:
        raise ValueError("SHORT setup prices must be target < entry < stop")
