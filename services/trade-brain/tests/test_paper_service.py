from datetime import datetime, timezone
from decimal import Decimal

from trade_brain.contracts import ClaudeDecision, Decision, Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.orchestration import DecisionCycleResult
from trade_brain.paper import PaperTradingEngine
from trade_brain.paper_service import create_paper_recommendations
from trade_brain.risk import RiskResult


def test_creates_recommendation_only_for_valid_trade_decision() -> None:
    candidate = TradeCandidate(
        candidate_id="candidate-1",
        snapshot_id="snapshot-1",
        setup_id="setup-1",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=95,
        target_price=110,
        horizon_bars=96,
        statistics_status=StatisticsStatus.VERIFIED,
        eligible=True,
    )
    cycle = DecisionCycleResult(
        snapshot_id="snapshot-1",
        candidates=(candidate,),
        risk_results={"candidate-1": RiskResult(True, (), 35, 0.25)},
        decisions=(
            ClaudeDecision(
                snapshot_id="snapshot-1",
                profile=Profile.BALANCED,
                decision=Decision.LONG,
                selected_candidate_id="candidate-1",
                summary_vi="Đủ điều kiện.",
            ),
        ),
        validation_errors={},
    )

    result = create_paper_recommendations(
        cycle,
        PaperTradingEngine(),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert len(result.recommendations) == 1
    assert result.recommendations[0].quantity == Decimal("0.25")


def test_skips_no_trade_decision() -> None:
    cycle = DecisionCycleResult(
        snapshot_id="snapshot-1",
        candidates=(),
        risk_results={},
        decisions=(
            ClaudeDecision(
                snapshot_id="snapshot-1",
                profile=Profile.CAUTIOUS,
                decision=Decision.NO_TRADE,
                summary_vi="Không giao dịch.",
            ),
        ),
        validation_errors={},
    )

    result = create_paper_recommendations(cycle, PaperTradingEngine(), datetime.now(timezone.utc))

    assert not result.recommendations
    assert result.skipped[Profile.CAUTIOUS] == "DECISION_NO_TRADE"


def test_skips_duplicate_setup_after_restart_restore() -> None:
    candidate = TradeCandidate(
        candidate_id="candidate-restore",
        snapshot_id="snapshot-1",
        setup_id="setup-restore",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=95,
        target_price=110,
        horizon_bars=96,
        statistics_status=StatisticsStatus.VERIFIED,
        eligible=True,
    )
    engine = PaperTradingEngine()
    existing = engine.recommend(candidate, Decimal("0.5"), datetime.now(timezone.utc))
    engine.restore(existing)
    cycle = DecisionCycleResult(
        snapshot_id="snapshot-1",
        candidates=(candidate,),
        risk_results={"candidate-restore": RiskResult(True, (), 35, 0.5)},
        decisions=(
            ClaudeDecision(
                snapshot_id="snapshot-1",
                profile=Profile.BALANCED,
                decision=Decision.LONG,
                selected_candidate_id="candidate-restore",
                summary_vi="Đủ điều kiện.",
            ),
        ),
        validation_errors={},
    )

    result = create_paper_recommendations(cycle, engine, datetime.now(timezone.utc))

    assert not result.recommendations
    assert result.skipped[Profile.BALANCED] == "DUPLICATE_EXISTING_RECOMMENDATION"
