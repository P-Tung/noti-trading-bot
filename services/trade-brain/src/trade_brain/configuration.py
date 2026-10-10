"""Validated, server-owned Trade V2 research configuration."""

from pydantic import Field, model_validator

from trade_brain.contracts import PaperMode, Profile, StrictModel, UniverseMode
from trade_brain.strategies.alternatives import R1Config, T2Config
from trade_brain.strategies.t1 import T1Config
from trade_brain.risk import RiskPolicy


class ProfilePolicyConfig(StrictModel):
    quality_minimum: float = Field(ge=0, le=100)
    expectancy_minimum_r: float
    effective_sample_minimum: int = Field(ge=0)
    risk_per_trade_pct: float = Field(gt=0, le=1)
    max_open_risk_pct: float = Field(gt=0, le=1)
    max_cluster_risk_pct: float = Field(gt=0, le=1)
    daily_drawdown_stop_pct: float = Field(gt=0, le=1)
    rolling_drawdown_stop_pct: float = Field(gt=0, le=1)


class UniverseConfig(StrictModel):
    """V2 Binance USDⓈ-M perpetual universe policy."""

    exchange: str = "BINANCE"
    asset_class: str = "CRYPTO"
    contract_type: str = "PERPETUAL"
    quote_asset: str = "USDT"
    margin_asset: str = "USDT"
    status: str = "TRADING"
    min_quote_volume_24h_usdt: float = Field(default=20_000_000, gt=0)
    refresh_seconds: int = Field(default=900, ge=60)
    max_universe_age_seconds: int = Field(default=1800, ge=60)
    max_symbols: int | None = Field(default=None, gt=0)
    min_history_days: int = Field(default=180, ge=1)
    require_strategy_indicator_warmup: bool = True

    @model_validator(mode="after")
    def validate_age_window(self) -> "UniverseConfig":
        if self.max_universe_age_seconds < self.refresh_seconds * 2:
            raise ValueError("max_universe_age_seconds must cover at least two refresh cycles")
        return self


class TradeBrainConfig(StrictModel):
    """V2 research settings, never secrets or live-trading keys."""

    config_version: str = Field(min_length=1, max_length=80)
    paper_mode: PaperMode = PaperMode.RESEARCH_PAPER
    symbols: list[str] = Field(default_factory=list)
    universe_mode: UniverseMode = UniverseMode.BINANCE_VOLUME
    universe: UniverseConfig = UniverseConfig()
    initial_equity_usdt: float = Field(gt=0)
    t1: T1Config = T1Config()
    t2: T2Config = T2Config()
    r1: R1Config = R1Config()
    profiles: dict[Profile, ProfilePolicyConfig]

    @model_validator(mode="after")
    def validate_symbol_source(self) -> "TradeBrainConfig":
        if self.universe_mode is UniverseMode.MANUAL and not self.symbols:
            raise ValueError("symbols must contain at least one symbol in MANUAL mode")
        return self

    @classmethod
    def defaults(cls) -> "TradeBrainConfig":
        return cls(
            config_version="config-v2",
            symbols=[],
            initial_equity_usdt=10000,
            profiles={
                Profile.PROACTIVE: ProfilePolicyConfig(
                    quality_minimum=65,
                    expectancy_minimum_r=0.10,
                    effective_sample_minimum=150,
                    risk_per_trade_pct=0.005,
                    max_open_risk_pct=0.020,
                    max_cluster_risk_pct=0.010,
                    daily_drawdown_stop_pct=0.020,
                    rolling_drawdown_stop_pct=0.080,
                ),
                Profile.BALANCED: ProfilePolicyConfig(
                    quality_minimum=75,
                    expectancy_minimum_r=0.15,
                    effective_sample_minimum=250,
                    risk_per_trade_pct=0.0035,
                    max_open_risk_pct=0.015,
                    max_cluster_risk_pct=0.0075,
                    daily_drawdown_stop_pct=0.015,
                    rolling_drawdown_stop_pct=0.060,
                ),
                Profile.CAUTIOUS: ProfilePolicyConfig(
                    quality_minimum=85,
                    expectancy_minimum_r=0.15,
                    effective_sample_minimum=400,
                    risk_per_trade_pct=0.002,
                    max_open_risk_pct=0.010,
                    max_cluster_risk_pct=0.005,
                    daily_drawdown_stop_pct=0.010,
                    rolling_drawdown_stop_pct=0.040,
                ),
            },
        )


def config_to_payload(config: TradeBrainConfig) -> dict[str, object]:
    """Serialize configuration for Supabase JSONB and the dashboard."""
    return config.model_dump(mode="json")


def config_from_payload(payload: object) -> TradeBrainConfig:
    """Validate persisted settings and fall back only when no settings exist."""
    if not isinstance(payload, dict):
        raise ValueError("Trade Brain config payload must be an object")
    legacy_payload = dict(payload)
    legacy_payload.pop("automatic_evaluation_enabled", None)
    config = TradeBrainConfig.model_validate(legacy_payload)
    if config.config_version == "config-v1":
        return config.model_copy(update={"config_version": "config-v2"})
    return config


def runtime_policies(config: TradeBrainConfig) -> dict[Profile, RiskPolicy]:
    """Convert editable policy values into the independent risk-gate type."""
    return {
        profile: RiskPolicy(**policy.model_dump())
        for profile, policy in config.profiles.items()
    }
