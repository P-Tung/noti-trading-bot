import unittest

from trade_brain.contracts import PaperMode, Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.risk import CandidateStatistics, RiskContext, calculate_quantity, evaluate_candidate_risk


def make_candidate() -> TradeCandidate:
    return TradeCandidate(
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


class RiskTests(unittest.TestCase):
    def test_quantity_rounds_down_to_exchange_step(self) -> None:
        quantity = calculate_quantity(100, 100, 95, 0, 0, 0, 0, 0.03)
        self.assertEqual(quantity, 19.98)

    def test_quantity_below_exchange_minimum_is_rejected(self) -> None:
        quantity = calculate_quantity(1, 100, 95, 0, 0, 0, 0, 0.01, 0.21)
        self.assertEqual(quantity, 0.0)

    def test_verified_candidate_passes_profile_gates(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate(),
            CandidateStatistics(80, 0.20, 0.05, 300, 0.10),
            RiskContext(10_000, 0.005, 0.002, 0.001, 0.01, 5_000),
            0,
            0,
            0,
            0,
            0.01,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.quantity_allowed, 7.0)

    def test_unverified_candidate_is_blocked(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate().model_copy(update={"statistics_status": StatisticsStatus.RESEARCH_ONLY}),
            CandidateStatistics(80, 0.20, 0.05, 300, 0.10),
            RiskContext(10_000, 0.005, 0.002, 0.001, 0.01, 5_000),
            0,
            0,
            0,
            0,
            0.01,
        )
        self.assertFalse(result.allowed)
        self.assertIn("STATISTICS_NOT_VERIFIED", result.codes)

    def test_research_paper_allows_missing_statistics_but_keeps_budget_gates(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate().model_copy(update={"statistics_status": StatisticsStatus.RESEARCH_ONLY}),
            CandidateStatistics(None, None, None, None, None),
            RiskContext(10_000, 0, 0, 0, 0, 10_000, paper_mode=PaperMode.RESEARCH_PAPER),
            0.01,
            0.01,
            0.01,
            0.01,
            0.001,
        )
        self.assertTrue(result.allowed)
        self.assertGreater(result.quantity_allowed, 0)

    def test_research_paper_still_blocks_unconfirmed_candidate(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate().model_copy(update={"eligible": False, "statistics_status": StatisticsStatus.RESEARCH_ONLY}),
            CandidateStatistics(None, None, None, None, None),
            RiskContext(10_000, 0, 0, 0, 0, 10_000, paper_mode=PaperMode.RESEARCH_PAPER),
            0.01,
            0.01,
            0.01,
            0.01,
            0.001,
        )

        self.assertFalse(result.allowed)
        self.assertIn("CANDIDATE_NOT_ELIGIBLE", result.codes)

    def test_price_tick_mismatch_is_blocked(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate().model_copy(update={"entry_estimate": 100.05}),
            CandidateStatistics(None, None, None, None, None),
            RiskContext(10_000, 0, 0, 0, 0, 10_000, paper_mode=PaperMode.RESEARCH_PAPER),
            0,
            0,
            0,
            0,
            0.01,
            price_tick=0.1,
        )

        self.assertFalse(result.allowed)
        self.assertIn("PRICE_TICK_INVALID", result.codes)

    def test_candidate_risk_budget_cannot_push_open_caps_over_limit(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate(),
            CandidateStatistics(80, 0.20, 0.05, 300, 0.10),
            RiskContext(10_000, 0.017, 0.007, 0, 0, 10_000),
            0,
            0,
            0,
            0,
            0.01,
        )

        self.assertFalse(result.allowed)
        self.assertIn("OPEN_RISK_LIMIT", result.codes)
        self.assertIn("CLUSTER_RISK_LIMIT", result.codes)

    def test_invalid_long_price_plan_is_blocked(self) -> None:
        result = evaluate_candidate_risk(
            make_candidate().model_copy(update={"stop_price": 105, "target_price": 95}),
            CandidateStatistics(None, None, None, None, None),
            RiskContext(10_000, 0, 0, 0, 0, 10_000, paper_mode=PaperMode.RESEARCH_PAPER),
            0,
            0,
            0,
            0,
            0.01,
        )

        self.assertFalse(result.allowed)
        self.assertIn("STOP_WRONG_SIDE", result.codes)
        self.assertIn("TARGET_WRONG_SIDE", result.codes)


if __name__ == "__main__":
    unittest.main()
