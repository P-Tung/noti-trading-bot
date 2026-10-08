import unittest
from datetime import datetime, timedelta, timezone

from trade_brain.contracts import Profile
from trade_brain.market_data import Bar
from trade_brain.strategies.t1 import T1Config, build_t1_candidates, find_breakout_retest_setups


def make_bar(index: int, high: float, low: float, close: float, volume: float = 10) -> Bar:
    opened_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Bar(opened_at, opened_at + timedelta(hours=1), 100, high, low, close, volume)


def make_trigger_bar(index: int, high: float = 101, low: float = 99, close: float = 100, volume: float = 10) -> Bar:
    opened_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index)
    return Bar(opened_at, opened_at + timedelta(minutes=15), 100, high, low, close, volume)


class T1Tests(unittest.TestCase):
    def test_finds_long_breakout_and_retest(self) -> None:
        bars = [make_bar(index, 101, 99, 100) for index in range(15)]
        bars.append(make_bar(15, 105, 99, 104))
        bars.extend(make_bar(index, 102, 99, 100) for index in range(16, 20))
        bars.append(make_bar(20, 108, 101, 107, volume=30))
        bars.append(make_bar(21, 108, 104, 106))

        setups = find_breakout_retest_setups(bars, "snapshot-1")

        self.assertEqual(len(setups), 1)
        setup = setups[0]
        self.assertEqual(setup.side, "LONG")
        self.assertEqual(setup.breakout_index, 20)
        self.assertEqual(setup.retest_index, 21)
        self.assertFalse(setup.trigger_reached)

    def test_profiles_have_separate_entry_stages(self) -> None:
        bars = [make_bar(index, 101, 99, 100) for index in range(15)]
        bars.append(make_bar(15, 105, 99, 104))
        bars.extend(make_bar(index, 102, 99, 100) for index in range(16, 20))
        bars.append(make_bar(20, 108, 101, 107, volume=30))
        bars.append(make_bar(21, 108, 104, 106))
        setup = find_breakout_retest_setups(bars, "snapshot-1")[0]

        candidates = build_t1_candidates(setup)
        stages = {candidate.profile: candidate.entry_stage for candidate in candidates}

        self.assertEqual(stages[Profile.PROACTIVE], "EARLY")
        self.assertEqual(stages[Profile.BALANCED], "CONFIRMED")
        self.assertEqual(stages[Profile.CAUTIOUS], "STRICT")
        self.assertTrue(all(candidate.statistics_status.value == "RESEARCH_ONLY" for candidate in candidates))

    def test_uses_confirmed_15m_trigger_for_non_proactive_profiles(self) -> None:
        bars_1h = [make_bar(index, 101, 99, 100) for index in range(15)]
        bars_1h.append(make_bar(15, 105, 99, 104))
        bars_1h.extend(make_bar(index, 102, 99, 100) for index in range(16, 20))
        bars_1h.append(make_bar(20, 108, 101, 107, volume=30))
        bars_1h.append(make_bar(21, 108, 104, 106))

        bars_15m = [make_trigger_bar(index) for index in range(140)]
        bars_15m[92] = make_trigger_bar(92, 105, 99, 104)
        bars_15m[100] = make_trigger_bar(100, 108, 103, 106, volume=30)

        setups = find_breakout_retest_setups(
            bars_1h,
            "snapshot-1",
            T1Config(),
            trigger_bars=bars_15m,
        )

        self.assertEqual(len(setups), 1)
        self.assertTrue(setups[0].trigger_reached)
        self.assertEqual(setups[0].trigger_index, 100)
        candidates = build_t1_candidates(setups[0])
        self.assertTrue(all(candidate.eligible for candidate in candidates))


if __name__ == "__main__":
    unittest.main()
