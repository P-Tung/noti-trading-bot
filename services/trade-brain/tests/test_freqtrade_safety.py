import json
from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_freqtrade_foundation_is_paper_only() -> None:
    config = json.loads((ROOT / "infra/freqtrade/config.example.json").read_text())
    exchange = config["exchange"]
    api_server = config["api_server"]

    assert config["dry_run"] is True
    assert config["stake_amount"] == 1000
    assert config["force_entry_enable"] is False
    assert config["trading_mode"] == "futures"
    assert exchange["key"] == ""
    assert exchange["secret"] == ""
    assert api_server["listen_ip_address"] == "0.0.0.0"
    compose = (ROOT / "infra/freqtrade/docker-compose.yml").read_text()
    assert '"127.0.0.1:8080:8080"' in compose


def test_freqtrade_foundation_strategy_has_no_entry_signals() -> None:
    strategy = (ROOT / "infra/freqtrade/user_data/strategies/TradeV1Foundation.py").read_text()

    assert 'dataframe["enter_long"] = 0' in strategy
    assert 'dataframe["enter_short"] = 0' in strategy
