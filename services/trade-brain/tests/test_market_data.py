import unittest
from datetime import datetime, timedelta, timezone

from trade_brain.market_data import Bar, PivotKind, atr_wilder, confirmed_pivots, ema, relative_volume


def make_bar(index: int, high: float, low: float, close: float, volume: float = 10) -> Bar:
    opened_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return Bar(opened_at, opened_at + timedelta(hours=1), 100, high, low, close, volume)


class MarketDataTests(unittest.TestCase):
    def test_atr_and_ema_wait_for_initial_period(self) -> None:
        bars = [make_bar(index, 101, 99, 100) for index in range(20)]
        atr_values = atr_wilder(bars, period=14)
        ema_values = ema(bars, period=10)
        self.assertIsNone(atr_values[12])
        self.assertIsNotNone(atr_values[13])
        self.assertIsNone(ema_values[8])
        self.assertIsNotNone(ema_values[9])

    def test_pivot_is_known_only_after_right_window(self) -> None:
        bars = [make_bar(index, 101, 99, 100) for index in range(12)]
        bars.append(make_bar(12, 105, 99, 104))
        bars.extend(make_bar(index, 102, 99, 100) for index in range(13, 16))
        pivots = confirmed_pivots(bars)
        pivot = next(item for item in pivots if item.index == 12)
        self.assertEqual(pivot.kind, PivotKind.HIGH)
        self.assertEqual(pivot.known_at_index, 15)

    def test_relative_volume_excludes_current_bar(self) -> None:
        bars = [make_bar(index, 101, 99, 100) for index in range(20)]
        bars.append(make_bar(20, 101, 99, 100, volume=30))
        self.assertEqual(relative_volume(bars, 20), 3.0)


if __name__ == "__main__":
    unittest.main()
