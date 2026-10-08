"""Deterministic, conservative backtest calculations for research statistics."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trade_brain.contracts import Side, StatisticsStatus, TradeCandidate
from trade_brain.market_data import Bar


class BacktestOutcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    TIMEOUT = "TIMEOUT"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class BacktestCase:
    """A candidate plus the closed bar where its signal became available."""

    candidate: TradeCandidate
    signal_index: int


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    candidate_id: str
    outcome: BacktestOutcome
    entry_time: datetime
    exit_time: datetime
    net_pnl: Decimal
    net_r: Decimal


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    """Research-only summary that can be reviewed before promotion to VERIFIED."""

    statistics_status: StatisticsStatus
    trade_count: int
    wins: int
    losses: int
    timeouts: int
    ambiguous: int
    total_net_pnl: Decimal
    total_net_r: Decimal
    win_rate: Decimal | None
    net_positive_probability: Decimal | None
    net_expectancy_r: Decimal | None
    expectancy_lower_95_r: Decimal | None
    effective_sample_count: int


def run_backtest(
    cases: list[BacktestCase],
    bars: list[Bar],
    fee_rate: Decimal = Decimal("0.0004"),
) -> tuple[list[BacktestTrade], BacktestSummary]:
    """Simulate exits using only bars after each signal, with stop-first ambiguity."""
    trades = [simulate_case(case, bars, fee_rate) for case in cases]
    determinate_trades = [trade for trade in trades if trade.outcome is not BacktestOutcome.AMBIGUOUS]
    net_rs = [trade.net_r for trade in determinate_trades]
    wins = sum(trade.outcome is BacktestOutcome.WIN for trade in trades)
    losses = sum(trade.outcome is BacktestOutcome.LOSS for trade in trades)
    timeouts = sum(trade.outcome is BacktestOutcome.TIMEOUT for trade in trades)
    ambiguous = sum(trade.outcome is BacktestOutcome.AMBIGUOUS for trade in trades)
    expectancy = sum(net_rs, Decimal("0")) / Decimal(len(net_rs)) if net_rs else None
    lower_bound = _lower_95(net_rs, expectancy)
    summary = BacktestSummary(
        statistics_status=StatisticsStatus.RESEARCH_ONLY,
        trade_count=len(trades),
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        ambiguous=ambiguous,
        total_net_pnl=sum((trade.net_pnl for trade in trades), Decimal("0")),
        total_net_r=sum(net_rs, Decimal("0")),
        win_rate=Decimal(wins) / Decimal(len(determinate_trades)) if determinate_trades else None,
        net_positive_probability=(
            Decimal(sum(trade.net_pnl > 0 for trade in determinate_trades))
            / Decimal(len(determinate_trades))
            if determinate_trades
            else None
        ),
        net_expectancy_r=expectancy,
        expectancy_lower_95_r=lower_bound,
        effective_sample_count=len(trades) - ambiguous,
    )
    return trades, summary


def simulate_case(
    case: BacktestCase,
    bars: list[Bar],
    fee_rate: Decimal = Decimal("0.0004"),
) -> BacktestTrade:
    """Simulate one candidate from the next completed bar after its signal."""
    candidate = case.candidate
    if not 0 <= case.signal_index < len(bars):
        raise ValueError("signal_index must reference an existing bar")
    entry_index = case.signal_index + 1
    entry = Decimal(str(candidate.entry_estimate))
    stop = Decimal(str(candidate.stop_price))
    target = Decimal(str(candidate.target_price))
    initial_risk = abs(entry - stop)
    if initial_risk <= 0:
        raise ValueError("candidate must have positive initial risk")
    last_index = min(len(bars) - 1, entry_index + candidate.horizon_bars - 1)
    for index in range(entry_index, last_index + 1):
        bar = bars[index]
        hit_stop = _touches_stop(candidate.side, bar, stop)
        hit_target = _touches_target(candidate.side, bar, target)
        if hit_stop and hit_target:
            return _result(candidate, bars[entry_index], bar, BacktestOutcome.AMBIGUOUS, stop, initial_risk, fee_rate)
        if hit_stop:
            return _result(candidate, bars[entry_index], bar, BacktestOutcome.LOSS, stop, initial_risk, fee_rate)
        if hit_target:
            return _result(candidate, bars[entry_index], bar, BacktestOutcome.WIN, target, initial_risk, fee_rate)
    exit_bar = bars[last_index]
    return _result(candidate, bars[entry_index], exit_bar, BacktestOutcome.TIMEOUT, Decimal(str(exit_bar.close)), initial_risk, fee_rate)


def _result(
    candidate: TradeCandidate,
    entry_bar: Bar,
    exit_bar: Bar,
    outcome: BacktestOutcome,
    exit_price: Decimal,
    initial_risk: Decimal,
    fee_rate: Decimal,
) -> BacktestTrade:
    entry_price = Decimal(str(candidate.entry_estimate))
    gross = (
        exit_price - entry_price
        if candidate.side is Side.LONG
        else entry_price - exit_price
    )
    fees = (entry_price + exit_price) * fee_rate
    net_pnl = gross - fees
    return BacktestTrade(
        candidate_id=candidate.candidate_id,
        outcome=outcome,
        entry_time=entry_bar.closed_at,
        exit_time=exit_bar.closed_at,
        net_pnl=net_pnl,
        net_r=net_pnl / initial_risk,
    )


def _touches_stop(side: Side, bar: Bar, stop: Decimal) -> bool:
    return Decimal(str(bar.low)) <= stop if side is Side.LONG else Decimal(str(bar.high)) >= stop


def _touches_target(side: Side, bar: Bar, target: Decimal) -> bool:
    return Decimal(str(bar.high)) >= target if side is Side.LONG else Decimal(str(bar.low)) <= target


def _lower_95(values: list[Decimal], mean: Decimal | None) -> Decimal | None:
    if not values or mean is None or len(values) < 2:
        return mean
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    standard_error = (variance / Decimal(len(values))).sqrt()
    return mean - Decimal("1.96") * standard_error
