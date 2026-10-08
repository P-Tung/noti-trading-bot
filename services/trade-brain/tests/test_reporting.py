import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trade_brain.contracts import Profile, QualityStatus
from trade_brain.paper import PaperState
from trade_brain.reporting import CohortScope, CohortTracker, ReportRecord


class ReportingTests(unittest.TestCase):
    def test_cohort_is_locked_and_stays_provisional_until_closed(self) -> None:
        tracker = CohortTracker(milestone_size=2)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(2):
            tracker.record(
                ReportRecord(
                    f"recommendation-{index}",
                    Profile.BALANCED,
                    f"setup-{index}",
                    start + timedelta(minutes=index),
                    PaperState.OPEN,
                )
            )

        report = tracker.report(CohortScope.PROFILE_RECOMMENDATIONS, Profile.BALANCED, 1)
        self.assertEqual(report.cohort_status, "PROVISIONAL")
        self.assertEqual(report.open_count, 2)

        tracker.replace_state(
            ReportRecord(
                "recommendation-0",
                Profile.BALANCED,
                "setup-0",
                start,
                PaperState.CLOSED_TP,
                Decimal("10"),
                Decimal("2"),
                start + timedelta(hours=1),
            )
        )
        updated = tracker.report(CohortScope.PROFILE_RECOMMENDATIONS, Profile.BALANCED, 1)
        self.assertEqual(updated.recommendation_count, 2)
        self.assertEqual(updated.closed_count, 1)

    def test_completed_trade_cohort_counts_only_closed_results(self) -> None:
        tracker = CohortTracker(milestone_size=2)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(2):
            tracker.record(
                ReportRecord(
                    f"trade-{index}",
                    Profile.CAUTIOUS,
                    f"setup-{index}",
                    start + timedelta(minutes=index),
                    PaperState.CLOSED_TP,
                    Decimal("5"),
                    Decimal("1"),
                    start + timedelta(hours=index + 1),
                )
            )
        report = tracker.report(CohortScope.PROFILE_COMPLETED_TRADES, Profile.CAUTIOUS, 1)
        self.assertEqual(report.closed_count, 2)
        self.assertEqual(report.net_positive_rate, Decimal("1"))

    def test_completed_trade_cohort_is_ordered_by_entry_time(self) -> None:
        tracker = CohortTracker(milestone_size=2)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tracker.record(
            ReportRecord(
                "emitted-first",
                Profile.BALANCED,
                "setup-first",
                start,
                PaperState.CLOSED_TP,
                Decimal("1"),
                Decimal("1"),
                start + timedelta(hours=3),
                start + timedelta(hours=2),
            )
        )
        tracker.record(
            ReportRecord(
                "emitted-second",
                Profile.BALANCED,
                "setup-second",
                start + timedelta(minutes=1),
                PaperState.CLOSED_TP,
                Decimal("1"),
                Decimal("1"),
                start + timedelta(hours=1),
                start + timedelta(hours=1),
            )
        )

        cohort = tracker.get_cohort(CohortScope.PROFILE_COMPLETED_TRADES, Profile.BALANCED, 1)

        self.assertEqual(cohort.member_ids, ("emitted-second", "emitted-first"))

    def test_drawdown_is_relative_to_initial_paper_equity(self) -> None:
        tracker = CohortTracker(milestone_size=2)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tracker.record(
            ReportRecord(
                "trade-win",
                Profile.BALANCED,
                "setup-win",
                start,
                PaperState.CLOSED_TP,
                Decimal("100"),
                Decimal("1"),
                start + timedelta(hours=1),
                start,
                Decimal("10000"),
            )
        )
        tracker.record(
            ReportRecord(
                "trade-loss",
                Profile.BALANCED,
                "setup-loss",
                start + timedelta(minutes=1),
                PaperState.CLOSED_SL,
                Decimal("-50"),
                Decimal("-0.5"),
                start + timedelta(hours=2),
                start + timedelta(minutes=1),
                Decimal("10000"),
            )
        )

        report = tracker.report(CohortScope.PROFILE_COMPLETED_TRADES, Profile.BALANCED, 1)

        assert report is not None
        assert report.max_drawdown == Decimal("50") / Decimal("10100")

    def test_report_counts_ambiguous_data_quality_separately(self) -> None:
        tracker = CohortTracker(milestone_size=2)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tracker.record(
            ReportRecord(
                recommendation_id="ambiguous",
                profile=Profile.BALANCED,
                setup_id="setup-ambiguous",
                emitted_at=start,
                state=PaperState.OPEN,
                data_quality=QualityStatus.AMBIGUOUS,
            )
        )
        tracker.record(
            ReportRecord(
                recommendation_id="valid",
                profile=Profile.BALANCED,
                setup_id="setup-valid",
                emitted_at=start + timedelta(minutes=1),
                state=PaperState.OPEN,
            )
        )

        report = tracker.report(CohortScope.PROFILE_RECOMMENDATIONS, Profile.BALANCED, 1)

        assert report is not None
        assert report.ambiguous_count == 1

    def test_available_milestones_include_second_locked_batch(self) -> None:
        tracker = CohortTracker(milestone_size=2)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(4):
            tracker.record(
                ReportRecord(
                    recommendation_id=f"recommendation-{index}",
                    profile=Profile.BALANCED,
                    setup_id=f"setup-{index}",
                    emitted_at=start + timedelta(minutes=index),
                    state=PaperState.RECOMMENDED,
                )
            )

        assert tracker.available_milestones(CohortScope.PROFILE_RECOMMENDATIONS, Profile.BALANCED) == (1, 2)

    def test_pending_fill_is_not_counted_as_filled(self) -> None:
        tracker = CohortTracker(milestone_size=1)
        tracker.record(
            ReportRecord(
                recommendation_id="pending-fill",
                profile=Profile.BALANCED,
                setup_id="setup-pending",
                emitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                state=PaperState.PENDING_FILL,
            )
        )

        report = tracker.report(CohortScope.PROFILE_RECOMMENDATIONS, Profile.BALANCED, 1)

        assert report is not None
        assert report.filled_count == 0


if __name__ == "__main__":
    unittest.main()
