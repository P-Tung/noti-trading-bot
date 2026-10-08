"""Stable typed contracts shared by analysis, Claude, and paper trading."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Reject unknown fields at service boundaries."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DataMode(StrEnum):
    PRICE_ONLY = "PRICE_ONLY"
    FULL_DATA = "FULL_DATA"


class PaperMode(StrEnum):
    RESEARCH_PAPER = "RESEARCH_PAPER"
    VERIFIED_PAPER = "VERIFIED_PAPER"


class QualityStatus(StrEnum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


class Profile(StrEnum):
    PROACTIVE = "PROACTIVE"
    BALANCED = "BALANCED"
    CAUTIOUS = "CAUTIOUS"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Decision(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"


class StatisticsStatus(StrEnum):
    VERIFIED = "VERIFIED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    INSUFFICIENT = "INSUFFICIENT"
    OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"


class FeatureValue(StrictModel):
    value: float | int | str | bool | None
    unit: str
    observed_at: datetime
    available_at: datetime
    quality_status: QualityStatus


class MarketSnapshot(StrictModel):
    snapshot_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    market_type: str = Field(default="BINANCE_USDM_PERPETUAL", min_length=1)
    decision_time: datetime
    expires_at: datetime
    quality_status: QualityStatus
    data_mode: DataMode
    feature_version: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    probability_version: str | None = None
    features: dict[str, FeatureValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_expiry(self) -> "MarketSnapshot":
        if self.expires_at <= self.decision_time:
            raise ValueError("expires_at must be later than decision_time")
        return self


class TradeCandidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    setup_id: str = Field(min_length=1)
    profile: Profile
    strategy: str = Field(pattern="^(T1|T2|R1)$")
    entry_stage: str = Field(pattern="^(EARLY|CONFIRMED|STRICT)$")
    side: Side
    entry_estimate: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    horizon_bars: int = Field(gt=0)
    statistics_status: StatisticsStatus
    statistics: dict[str, object] = Field(default_factory=dict)
    eligible: bool = False


class ClaudeDecision(StrictModel):
    snapshot_id: str = Field(min_length=1)
    profile: Profile
    decision: Decision
    selected_candidate_id: str | None = None
    watch_candidate_id: str | None = None
    condition_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    summary_vi: str = Field(min_length=1, max_length=500)


class ClaudeDecisionBatch(StrictModel):
    """One Claude response containing exactly one decision per profile."""

    snapshot_id: str = Field(min_length=1)
    decisions: list[ClaudeDecision] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_profiles_and_snapshot(self) -> "ClaudeDecisionBatch":
        profiles = [decision.profile for decision in self.decisions]
        if len(set(profiles)) != 3 or set(profiles) != set(Profile):
            raise ValueError("decision batch must contain exactly the three profiles")
        if any(decision.snapshot_id != self.snapshot_id for decision in self.decisions):
            raise ValueError("all decisions must use the batch snapshot_id")
        return self
