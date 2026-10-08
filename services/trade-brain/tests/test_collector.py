import asyncio
import unittest
from datetime import datetime, timedelta, timezone

import httpx

from trade_brain.binance import BinanceBookTicker, BinanceDepth, BinanceKline, BinanceSymbolRules
from trade_brain.collector import PublicSnapshotCollector
from trade_brain.storage import InMemorySnapshotStore


class FakeBinanceClient:
    async def get_klines(self, symbol: str, interval: str, limit: int) -> list[BinanceKline]:
        opened_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            BinanceKline(
                opened_at_ms=int((opened_at + timedelta(minutes=15 * index)).timestamp() * 1000),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=10,
                closed_at_ms=int((opened_at + timedelta(minutes=15 * (index + 1))).timestamp() * 1000),
                quote_volume=1000,
                trade_count=10,
                taker_buy_base_volume=5,
                taker_buy_quote_volume=500,
            )
            for index in range(20)
        ] + [
            BinanceKline(
                opened_at_ms=int((opened_at + timedelta(minutes=300)).timestamp() * 1000),
                open=100,
                high=102,
                low=99,
                close=101,
                volume=20,
                closed_at_ms=int((opened_at + timedelta(minutes=315)).timestamp() * 1000),
                quote_volume=2020,
                trade_count=20,
                taker_buy_base_volume=10,
                taker_buy_quote_volume=1010,
            )
        ]

    async def get_mark_price(self, symbol: str) -> dict[str, float | int | str]:
        return {
            "symbol": symbol,
            "mark_price": 101.0,
            "index_price": 100.9,
            "last_funding_rate": 0.0001,
            "next_funding_time_ms": 1_767_255_600_000,
        }

    async def get_open_interest(self, symbol: str) -> dict[str, float | int | str]:
        return {"symbol": symbol, "open_interest": 500.0, "time_ms": 1_767_255_600_000}

    async def get_24h_ticker(self, symbol: str) -> dict[str, float | int | str]:
        return {"symbol": symbol, "quote_volume": 1_000_000.0, "volume": 10_000.0, "trade_count": 5000}

    async def get_open_interest_history(self, symbol: str):
        from trade_brain.binance import BinanceOpenInterestSample

        return [
            BinanceOpenInterestSample(0, 500),
            BinanceOpenInterestSample(3_600_000, 550),
            BinanceOpenInterestSample(14_400_000, 600),
        ]

    async def get_book_ticker(self, symbol: str) -> BinanceBookTicker:
        return BinanceBookTicker(symbol, 100.9, 2.0, 101.1, 3.0, 1_767_255_600_000)

    async def get_depth(self, symbol: str, limit: int = 100) -> BinanceDepth:
        return BinanceDepth(1, ((100.9, 2.0),), ((101.1, 3.0),))

    async def get_symbol_rules(self, symbol: str) -> BinanceSymbolRules:
        return BinanceSymbolRules(symbol, 0.001, 0.001, 0.1)


class CollectorTests(unittest.TestCase):
    def test_collects_only_closed_candles(self) -> None:
        store = InMemorySnapshotStore()
        collector = PublicSnapshotCollector(FakeBinanceClient(), store)
        now = datetime(2026, 1, 1, 5, 20, tzinfo=timezone.utc)

        async def run():
            return await collector.collect("experiment-1", "BTCUSDT", now)

        snapshot = asyncio.run(run())
        self.assertEqual(snapshot.features["close_15m"].value, 101.0)
        self.assertEqual(snapshot.features["funding_rate"].value, 0.0001)
        self.assertEqual(snapshot.features["quote_volume_24h"].value, 1_000_000.0)
        self.assertIn("ema20_distance_atr_15m", snapshot.features)
        self.assertEqual(snapshot.features["structure_state_15m"].value, "INSUFFICIENT")
        self.assertEqual(snapshot.features["taker_buy_fraction_15m"].value, 0.5)
        self.assertEqual(snapshot.features["taker_buy_sell_ratio_15m"].value, 1.0)
        self.assertIn("trade_count_relative_15m", snapshot.features)
        self.assertIn("close_location_15m", snapshot.features)
        self.assertIn("range_position_15m", snapshot.features)
        self.assertAlmostEqual(snapshot.features["mark_index_basis_bps"].value, 9.910802775024775)
        self.assertAlmostEqual(snapshot.features["oi_change_1h_pct"].value, 9.090909090909092)
        self.assertAlmostEqual(snapshot.features["oi_change_4h_pct"].value, 20.0)
        self.assertGreater(snapshot.features["spread_bps"].value, 0)
        self.assertIn("book_imbalance", snapshot.features)
        self.assertEqual(snapshot.features["quantity_step"].value, 0.001)
        self.assertEqual(store.get_snapshot(snapshot.snapshot_id).snapshot_id, snapshot.snapshot_id)

    def test_collects_selected_closed_timeframes_into_one_snapshot(self) -> None:
        store = InMemorySnapshotStore()
        collector = PublicSnapshotCollector(FakeBinanceClient(), store)

        async def run():
            return await collector.collect_timeframes(
                "experiment-1",
                "BTCUSDT",
                ("1h", "15m"),
                datetime(2026, 1, 1, 5, 20, tzinfo=timezone.utc),
            )

        collected = asyncio.run(run())
        self.assertEqual(set(collected.bars_by_timeframe), {"1h", "15m"})
        self.assertIn("close_1h", collected.snapshot.features)
        self.assertIn("close_15m", collected.snapshot.features)

    def test_auxiliary_market_data_failure_marks_snapshot_degraded(self) -> None:
        class DegradedClient(FakeBinanceClient):
            async def get_depth(self, symbol: str, limit: int = 100) -> BinanceDepth:
                raise httpx.ConnectError("depth unavailable")

        collector = PublicSnapshotCollector(DegradedClient(), InMemorySnapshotStore())

        async def run():
            return await collector.collect(
                "experiment-1",
                "BTCUSDT",
                datetime(2026, 1, 1, 5, 20, tzinfo=timezone.utc),
            )

        snapshot = asyncio.run(run())
        self.assertEqual(snapshot.quality_status.value, "DEGRADED")
        self.assertEqual(snapshot.features["depth_quality"].quality_status.value, "DEGRADED")


if __name__ == "__main__":
    unittest.main()
