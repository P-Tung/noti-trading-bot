import asyncio

from trade_brain.configuration import UniverseConfig
from trade_brain.universe import select_binance_universe


class FakeBinanceClient:
    async def get_exchange_info(self):
        return [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
            },
            {
                "symbol": "LOWVOLUSDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
            },
            {
                "symbol": "BTCUSDT_SPOT",
                "status": "TRADING",
                "contractType": "SPOT",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
            },
        ]

    async def get_24h_tickers(self):
        return [
            {"symbol": "BTCUSDT", "quote_volume": 25_000_000},
            {"symbol": "LOWVOLUSDT", "quote_volume": 19_999_999},
            {"symbol": "BTCUSDT_SPOT", "quote_volume": 100_000_000},
        ]


def test_v2_universe_filters_all_eligible_symbols_without_top_n_cap() -> None:
    selection = asyncio.run(select_binance_universe(FakeBinanceClient(), UniverseConfig()))

    assert selection.symbols == ("BTCUSDT",)
    assert selection.passed_count == 1
    assert selection.exclusion_counts["VOLUME_BELOW_MIN"] == 1
    assert selection.exclusion_counts["OUT_OF_SCOPE"] == 1
