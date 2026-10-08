from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trade_brain.backtest import BacktestCase, BacktestOutcome, run_backtest, simulate_case
from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.market_data import Bar


def bar(index: int, close: float, high: float, low: float) -> Bar:
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Bar(opened, opened + timedelta(hours=1), close, high, low, close, 100)


def candidate(side: Side = Side.LONG) -> TradeCandidate:
    return TradeCandidate(
        candidate_id="candidate-1",
        snapshot_id="snapshot-1",
        setup_id="setup-1",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=side,
        entry_estimate=100,
        stop_price=95 if side is Side.LONG else 105,
        target_price=110 if side is Side.LONG else 90,
        horizon_bars=3,
        statistics_status=StatisticsStatus.RESEARCH_ONLY,
        eligible=True,
    )


def test_backtest_uses_only_bars_after_signal_for_a_win() -> None:
    bars = [bar(0, 100, 101, 99), bar(1, 100, 101, 99), bar(2, 105, 111, 100)]

    result = simulate_case(BacktestCase(candidate(), 0), bars)

    assert result.outcome is BacktestOutcome.WIN
    assert result.entry_time == bars[1].closed_at


def test_backtest_marks_same_bar_stop_and_target_ambiguous() -> None:
    bars = [bar(0, 100, 101, 99), bar(1, 100, 111, 94)]

    result = simulate_case(BacktestCase(candidate(), 0), bars)

    assert result.outcome is BacktestOutcome.AMBIGUOUS


def test_summary_is_explicitly_research_only() -> None:
    bars = [
        bar(0, 100, 101, 99),
        bar(1, 100, 111, 99),
        bar(2, 100, 101, 94),
    ]
    trades, summary = run_backtest([BacktestCase(candidate(), 0)], bars)

    assert len(trades) == 1
    assert summary.statistics_status is StatisticsStatus.RESEARCH_ONLY
    assert summary.effective_sample_count == 1
    assert isinstance(summary.total_net_r, Decimal)


def test_ambiguous_trade_is_excluded_from_measured_win_rate() -> None:
    bars = [
        bar(0, 100, 101, 99),
        bar(1, 100, 111, 94),
        bar(2, 100, 101, 99),
    ]

    _, summary = run_backtest([BacktestCase(candidate(), 0)], bars)

    assert summary.ambiguous == 1
    assert summary.losses == 0
    assert summary.win_rate is None
    assert summary.net_positive_probability is None
