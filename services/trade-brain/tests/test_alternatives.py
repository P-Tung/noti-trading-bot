import unittest
from datetime import datetime, timedelta, timezone

from trade_brain.contracts import Profile, Side
from trade_brain.market_data import Bar
from trade_brain.strategies.alternatives import (
    StrategySetup,
    build_strategy_candidates,
    find_r1_setups,
    find_t2_setups,
)


def make_bar(index: int, close: float, high: float, low: float, volume: float = 100) -> Bar:
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Bar(opened, opened + timedelta(hours=1), close, high, low, close, volume)


class AlternativeStrategyTests(unittest.TestCase):
    def test_t2_uses_96_bar_horizon_and_profile_stages(self) -> None:
        candidates = build_strategy_candidates(
            StrategySetup(
                "t2-1", "snapshot-1", "T2", Side.LONG, 100, 95, 110, 96, True, False, False
            )
        )
        self.assertEqual([candidate.entry_stage for candidate in candidates], ["EARLY", "CONFIRMED", "STRICT"])
        self.assertTrue(candidates[0].eligible)
        self.assertFalse(candidates[1].eligible)
        self.assertTrue(all(candidate.horizon_bars == 96 for candidate in candidates))

    def test_r1_uses_32_bar_horizon_and_short_price_order(self) -> None:
        candidates = build_strategy_candidates(
            StrategySetup(
                "r1-1", "snapshot-1", "R1", Side.SHORT, 100, 105, 95, 32, True, True, True
            )
        )
        self.assertTrue(all(candidate.eligible for candidate in candidates))
        self.assertTrue(all(candidate.horizon_bars == 32 for candidate in candidates))

    def test_t2_detects_long_pullback_without_future_data(self) -> None:
        bars = [make_bar(index, 100 + index, 100.5 + index, 99.5 + index) for index in range(20)]
        bars.append(make_bar(20, 119.5, 120, 110, 100))
        bars.append(make_bar(21, 123, 124, 119, 130))

        setups = find_t2_setups(bars, "snapshot-1")

        self.assertTrue(any(setup.side is Side.LONG for setup in setups))
        self.assertTrue(any(setup.strict_trigger for setup in setups))

    def test_r1_detects_low_sweep_and_reversion(self) -> None:
        bars = [make_bar(index, 100, 105, 95) for index in range(20)]
        bars.append(make_bar(20, 101, 103, 93))

        setups = find_r1_setups(bars, "snapshot-1")

        self.assertEqual(len(setups), 1)
        self.assertIs(setups[0].side, Side.LONG)
        self.assertGreater(setups[0].target_price, setups[0].entry_price)


if __name__ == "__main__":
    unittest.main()
