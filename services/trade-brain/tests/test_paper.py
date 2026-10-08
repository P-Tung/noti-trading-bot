import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trade_brain.contracts import Profile, QualityStatus, Side, StatisticsStatus, TradeCandidate
from trade_brain.paper import MarketQuote, PaperState, PaperTradingEngine, PaperTradingError


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
        horizon_bars=2,
        statistics_status=StatisticsStatus.RESEARCH_ONLY,
        eligible=True,
    )


def quote(
    timestamp: datetime,
    bid: str,
    ask: str,
    funding_rate: str | None = None,
    funding_time: datetime | None = None,
) -> MarketQuote:
    return MarketQuote(
        timestamp,
        Decimal(bid),
        Decimal(ask),
        Decimal(bid),
        Decimal(funding_rate) if funding_rate is not None else None,
        funding_time,
    )


class PaperTradingTests(unittest.TestCase):
    def test_long_trade_closes_at_target_with_costs(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        engine.fill(recommendation.recommendation_id, quote(start, "99.9", "100.1"))
        closed = engine.mark(recommendation.recommendation_id, quote(start + timedelta(minutes=1), "110", "110.1"))

        self.assertEqual(closed.state, PaperState.CLOSED_TP)
        self.assertIsNotNone(closed.net_pnl)
        self.assertLess(closed.net_pnl, Decimal("10"))
        self.assertEqual(engine.get_account(Profile.BALANCED).realized_pnl, closed.net_pnl)
        self.assertEqual(
            engine.get_account(Profile.BALANCED).current_equity,
            Decimal("10000") + closed.net_pnl,
        )

    def test_duplicate_setup_is_rejected(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.recommend(make_candidate(), Decimal("1"), start)
        with self.assertRaises(PaperTradingError):
            engine.recommend(make_candidate(), Decimal("1"), start)

    def test_recommendation_id_is_stable_across_engine_restarts(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = PaperTradingEngine().recommend(make_candidate(), Decimal("1"), start)
        second = PaperTradingEngine().recommend(make_candidate(), Decimal("1"), start)

        self.assertEqual(first.recommendation_id, second.recommendation_id)

    def test_expired_recommendation_does_not_fill(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        expired = engine.fill(recommendation.recommendation_id, quote(start + timedelta(seconds=61), "100", "100.1"))
        self.assertEqual(expired.state, PaperState.EXPIRED)

    def test_fill_before_emission_is_rejected(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        with self.assertRaises(PaperTradingError):
            engine.fill(recommendation.recommendation_id, quote(start - timedelta(seconds=1), "100", "100.1"))

    def test_invalid_quote_is_rejected(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        with self.assertRaises(PaperTradingError):
            engine.fill(recommendation.recommendation_id, quote(start, "100.2", "100"))

    def test_ambiguous_quote_is_recorded_but_cannot_fill_or_close(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        ambiguous = MarketQuote(
            start,
            Decimal("100"),
            Decimal("100.1"),
            Decimal("100"),
            quality_status=QualityStatus.AMBIGUOUS,
        )

        engine.fill(recommendation.recommendation_id, ambiguous)

        self.assertEqual(recommendation.state, PaperState.RECOMMENDED)
        self.assertEqual(recommendation.data_quality, QualityStatus.AMBIGUOUS)

        valid = quote(start + timedelta(seconds=1), "100", "100.1")
        engine.fill(recommendation.recommendation_id, valid)
        self.assertEqual(recommendation.state, PaperState.RECOMMENDED)

    def test_timeout_is_not_classified_as_stop_loss(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        engine.fill(recommendation.recommendation_id, quote(start, "99.9", "100.1"))
        closed = engine.mark(recommendation.recommendation_id, quote(start + timedelta(minutes=30), "101", "101.1"))
        self.assertEqual(closed.state, PaperState.CLOSED_TIMEOUT)

    def test_open_risk_is_exposed_to_future_decision_cycles(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        self.assertEqual(engine.open_risk_pct(Profile.BALANCED), Decimal("0"))
        engine.fill(recommendation.recommendation_id, quote(start, "99.9", "100.1"))
        self.assertGreater(engine.open_risk_pct(Profile.BALANCED), Decimal("0"))

    def test_mark_to_market_equity_includes_open_position(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        engine.fill(recommendation.recommendation_id, quote(start, "99.9", "100.1"))
        before = engine.equity(Profile.BALANCED)

        engine.mark(recommendation.recommendation_id, quote(start + timedelta(minutes=1), "101", "101.1"))

        self.assertGreater(engine.equity(Profile.BALANCED), before)
        self.assertEqual(recommendation.last_mark, Decimal("101"))

    def test_drawdown_is_derived_from_closed_paper_results(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        engine.fill(recommendation.recommendation_id, quote(start, "99.9", "100.1"))
        closed = engine.mark(
            recommendation.recommendation_id,
            quote(start + timedelta(minutes=1), "94", "94.1"),
        )
        daily, rolling = engine.drawdown_pcts(Profile.BALANCED, closed.closed_at)
        self.assertGreater(daily, Decimal("0"))
        self.assertGreater(rolling, Decimal("0"))

    def test_funding_event_is_applied_once_before_close(self) -> None:
        engine = PaperTradingEngine()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = engine.recommend(make_candidate(), Decimal("1"), start)
        engine.fill(recommendation.recommendation_id, quote(start, "99.9", "100.1"))
        funding_time = start + timedelta(minutes=5)
        funding_quote = quote(
            start + timedelta(minutes=6),
            "100",
            "100.1",
            funding_rate="0.001",
            funding_time=funding_time,
        )
        engine.mark(recommendation.recommendation_id, funding_quote)
        engine.mark(recommendation.recommendation_id, funding_quote)

        self.assertEqual(recommendation.funding_pnl, Decimal("-0.1"))
        self.assertIsNotNone(recommendation.last_funding_time)


if __name__ == "__main__":
    unittest.main()
