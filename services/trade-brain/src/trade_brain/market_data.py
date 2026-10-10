"""Deterministic market-data primitives with no future-data access."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PivotKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class Bar:
    """One completed OHLCV bar."""

    opened_at: datetime
    closed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    trade_count: int | None = None
    taker_buy_base_volume: float | None = None
    taker_buy_quote_volume: float | None = None


@dataclass(frozen=True, slots=True)
class Pivot:
    """A pivot and the first bar close at which it became knowable."""

    index: int
    kind: PivotKind
    price: float
    known_at_index: int


def true_range(current: Bar, previous: Bar | None) -> float:
    """Calculate true range for a completed bar."""
    if previous is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def atr_wilder(bars: list[Bar], period: int = 14) -> list[float | None]:
    """Calculate Wilder ATR without emitting values before initialization."""
    if period <= 0:
        raise ValueError("period must be positive")
    ranges = [true_range(bar, bars[index - 1] if index else None) for index, bar in enumerate(bars)]
    values: list[float | None] = [None] * len(bars)
    if len(ranges) < period:
        return values
    values[period - 1] = sum(ranges[:period]) / period
    for index in range(period, len(ranges)):
        previous = values[index - 1]
        if previous is None:
            continue
        values[index] = ((previous * (period - 1)) + ranges[index]) / period
    return values


def ema(bars: list[Bar], period: int) -> list[float | None]:
    """Calculate an EMA initialized with the first period closes."""
    if period <= 0:
        raise ValueError("period must be positive")
    values: list[float | None] = [None] * len(bars)
    if len(bars) < period:
        return values
    alpha = 2 / (period + 1)
    current = sum(bar.close for bar in bars[:period]) / period
    values[period - 1] = current
    for index in range(period, len(bars)):
        current = (bars[index].close * alpha) + (current * (1 - alpha))
        values[index] = current
    return values


def adx_wilder(bars: list[Bar], period: int = 14) -> list[float | None]:
    """Calculate non-directional Wilder ADX without early neutral values."""
    if period <= 0:
        raise ValueError("period must be positive")
    values: list[float | None] = [None] * len(bars)
    if len(bars) < (period * 2) + 1:
        return values
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(bars)):
        current = bars[index]
        previous = bars[index - 1]
        true_ranges.append(true_range(current, previous))
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    tr_smoothed = sum(true_ranges[:period])
    plus_smoothed = sum(plus_dm[:period])
    minus_smoothed = sum(minus_dm[:period])
    dx_values: list[float] = []
    for index in range(period - 1, len(true_ranges)):
        if index > period - 1:
            tr_smoothed = tr_smoothed - tr_smoothed / period + true_ranges[index]
            plus_smoothed = plus_smoothed - plus_smoothed / period + plus_dm[index]
            minus_smoothed = minus_smoothed - minus_smoothed / period + minus_dm[index]
        if tr_smoothed <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100 * plus_smoothed / tr_smoothed
        minus_di = 100 * minus_smoothed / tr_smoothed
        denominator = plus_di + minus_di
        dx_values.append(0.0 if denominator <= 0 else 100 * abs(plus_di - minus_di) / denominator)
    if len(dx_values) < period:
        return values
    first_adx = sum(dx_values[:period]) / period
    adx_index = (period - 1) + period
    values[adx_index] = first_adx
    current_adx = first_adx
    for dx_index in range(period, len(dx_values)):
        current_adx = ((current_adx * (period - 1)) + dx_values[dx_index]) / period
        values[adx_index + dx_index - period + 1] = current_adx
    return values


def confirmed_pivots(
    bars: list[Bar],
    left: int = 3,
    right: int = 3,
) -> list[Pivot]:
    """Find strict pivots and mark them known only after the right window closes."""
    if left < 1 or right < 1:
        raise ValueError("left and right must be positive")
    pivots: list[Pivot] = []
    first_index = left
    last_index = len(bars) - right
    for index in range(first_index, last_index):
        window = bars[index - left : index + right + 1]
        high = bars[index].high
        low = bars[index].low
        if all(high > bar.high for offset, bar in enumerate(window) if offset != left):
            pivots.append(Pivot(index, PivotKind.HIGH, high, index + right))
        if all(low < bar.low for offset, bar in enumerate(window) if offset != left):
            pivots.append(Pivot(index, PivotKind.LOW, low, index + right))
    return pivots


def relative_volume(bars: list[Bar], index: int, window: int = 20) -> float | None:
    """Compare a completed bar with preceding completed bars only."""
    if index < window or index >= len(bars):
        return None
    baseline = sum(bar.volume for bar in bars[index - window : index]) / window
    if baseline <= 0:
        return None
    return bars[index].volume / baseline
