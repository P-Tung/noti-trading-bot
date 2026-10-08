"""Immutable snapshot construction for reproducible analysis decisions."""

from datetime import datetime, timedelta
from uuid import uuid4

from trade_brain.contracts import DataMode, FeatureValue, MarketSnapshot, QualityStatus


def create_snapshot(
    experiment_id: str,
    symbol: str,
    decision_time: datetime,
    features: dict[str, FeatureValue],
    *,
    market_type: str = "BINANCE_USDM_PERPETUAL",
    data_mode: DataMode = DataMode.PRICE_ONLY,
    quality_status: QualityStatus = QualityStatus.VALID,
    feature_version: str = "features-v1",
    strategy_version: str = "strategies-v1",
    policy_version: str = "policies-v1",
    probability_version: str | None = None,
    expiry_seconds: int = 60,
) -> MarketSnapshot:
    """Build one immutable snapshot with a bounded validity window."""
    if expiry_seconds <= 0:
        raise ValueError("expiry_seconds must be positive")
    snapshot_id = f"snapshot-{uuid4().hex}"
    return MarketSnapshot(
        snapshot_id=snapshot_id,
        experiment_id=experiment_id,
        symbol=symbol,
        market_type=market_type,
        decision_time=decision_time,
        expires_at=decision_time + timedelta(seconds=expiry_seconds),
        quality_status=quality_status,
        data_mode=data_mode,
        feature_version=feature_version,
        strategy_version=strategy_version,
        policy_version=policy_version,
        probability_version=probability_version,
        features=features,
    )
