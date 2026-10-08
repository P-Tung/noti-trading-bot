from datetime import datetime, timedelta, timezone

from trade_brain.market_data import Bar
from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.strategy_pipeline import _apply_higher_timeframe_gate, build_all_candidates, build_all_candidates_multi_timeframe
from trade_brain.strategies.alternatives import R1Config, T2Config


def make_bar(index: int, close: float, high: float, low: float, volume: float = 100) -> Bar:
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Bar(opened, opened + timedelta(hours=1), close, high, low, close, volume)


def test_pipeline_builds_profile_candidates_from_alternative_setups() -> None:
    bars = [make_bar(index, 100, 105, 95) for index in range(20)]
    bars.append(make_bar(20, 101, 103, 93))

    result = build_all_candidates(
        bars,
        "snapshot-1",
        t2_config=T2Config(ema_period=3, atr_period=3),
        r1_config=R1Config(range_bars=20, atr_period=14),
    )

    assert result.setup_ids
    assert len(result.candidates) == len(result.setup_ids) * 3
    assert {candidate.strategy for candidate in result.candidates} == {"R1"}


def test_higher_timeframe_gate_blocks_countertrend_only() -> None:
    rising = [make_bar(index, 100 + index, 101 + index, 99 + index) for index in range(60)]
    candidates = [
        TradeCandidate(
            candidate_id=f"candidate-{strategy}-{side.value}",
            snapshot_id="snapshot-1",
            setup_id=f"setup-{strategy}-{side.value}",
            profile=Profile.BALANCED,
            strategy=strategy,
            entry_stage="CONFIRMED",
            side=side,
            entry_estimate=100,
            stop_price=95,
            target_price=110,
            horizon_bars=10,
            statistics_status=StatisticsStatus.RESEARCH_ONLY,
            eligible=True,
        )
        for strategy, side in (("T1", Side.LONG), ("T1", Side.SHORT), ("R1", Side.SHORT))
    ]

    gated = _apply_higher_timeframe_gate(candidates, rising, rising)

    assert gated[0].eligible is True
    assert gated[1].eligible is False
    assert gated[2].eligible is True


def test_current_only_keeps_only_latest_alternative_setup() -> None:
    bars = [make_bar(index, 100, 105, 95) for index in range(20)]
    bars.append(make_bar(20, 101, 103, 93))
    result = build_all_candidates_multi_timeframe(
        bars,
        bars,
        "snapshot-1",
        current_only=True,
    )

    assert result.setup_ids == ("r1:snapshot-1:20:long",)
