"""Safe Phase 1 strategy boundary for Trade V1.

This strategy intentionally produces no entries or exits. Trading logic is added
only after the data contract, candidate model, and independent risk checks exist.
"""

from pandas import DataFrame

from freqtrade.strategy import IStrategy


class TradeV1Foundation(IStrategy):
    """No-signal strategy used to verify the Freqtrade foundation safely."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    process_only_new_candles = True
    startup_candle_count = 250
    minimal_roi = {"0": 0.0}
    stoploss = -0.1

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Return the source dataframe without adding trading signals."""
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Keep all entry signals disabled during Phase 1."""
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Keep signal-based exits disabled during Phase 1."""
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = None
        return dataframe
