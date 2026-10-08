import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from trade_brain.contracts import FeatureValue, QualityStatus
from trade_brain.contracts import ClaudeDecision, Decision, Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.orchestration import DecisionCycleResult
from trade_brain.risk import RiskResult
from trade_brain.snapshots import create_snapshot
from trade_brain.supabase_store import SupabaseSnapshotStore, _db_trade_outcome
from trade_brain.paper import MarketQuote, PaperState, PaperTradingEngine
from trade_brain.reporting import CohortScope, PaperReport


class FakeResponse:
    def __init__(self, data: list[dict[str, object]] | None = None, error: object = None) -> None:
        self.data = data
        self.error = error


class FakeQuery:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def execute(self) -> FakeResponse:
        return self.response

    def eq(self, column: str, value: str) -> "FakeQuery":
        if self.response.data is not None:
            self.response.data = [row for row in self.response.data if row.get(column) == value]
        return self

    def order(self, column: str, desc: bool = False) -> "FakeQuery":
        return self

    def limit(self, count: int) -> "FakeQuery":
        if self.response.data is not None:
            self.response.data = self.response.data[:count]
        return self


class FakeTable:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def insert(self, values: dict[str, object]) -> FakeQuery:
        self.row.update(values)
        return FakeQuery(FakeResponse())

    def select(self, columns: str) -> FakeQuery:
        return FakeQuery(FakeResponse([self.row]))

    def eq(self, column: str, value: str) -> FakeQuery:
        return FakeQuery(FakeResponse([self.row] if self.row.get(column) == value else []))


class FakeClient:
    def __init__(self) -> None:
        self.row: dict[str, object] = {}
        self.table_instance = FakeTable(self.row)

    def table(self, name: str) -> FakeTable:
        return self.table_instance


class RecordingTable:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def insert(self, values: dict[str, object]) -> FakeQuery:
        self.rows.append(values)
        return FakeQuery(FakeResponse())

    def select(self, columns: str) -> FakeQuery:
        return FakeQuery(FakeResponse(self.rows))

    def eq(self, column: str, value: str) -> FakeQuery:
        return FakeQuery(FakeResponse([row for row in self.rows if row.get(column) == value]))

    def update(self, values: dict[str, object]) -> FakeQuery:
        for row in self.rows:
            row.update(values)
        return FakeQuery(FakeResponse())

    def upsert(self, values: dict[str, object]) -> FakeQuery:
        self.rows.append(values)
        return FakeQuery(FakeResponse())

    def order(self, column: str, desc: bool = False) -> FakeQuery:
        return FakeQuery(FakeResponse(self.rows))

    def limit(self, count: int) -> FakeQuery:
        return FakeQuery(FakeResponse(self.rows[:count]))


class RecordingClient:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, object]]] = {
            "trade_candidates": [],
            "claude_decisions": [],
            "paper_accounts": [],
            "paper_recommendations": [],
            "paper_trades": [],
            "paper_ledger": [],
            "evaluation_cohorts": [],
            "paper_reports": [],
            "report_milestones": [],
            "paper_fills": [],
            "paper_positions": [],
            "equity_samples": [],
            "evaluation_cohort_members": [],
            "report_jobs": [],
        }

    def table(self, name: str) -> RecordingTable:
        return RecordingTable(self.rows[name])


class SupabaseStoreTests(unittest.TestCase):
    def test_maps_internal_close_reasons_to_database_outcomes(self) -> None:
        self.assertEqual(_db_trade_outcome(PaperState.CLOSED_TP), "WIN")
        self.assertEqual(_db_trade_outcome(PaperState.CLOSED_SL), "LOSS")
        self.assertEqual(_db_trade_outcome(PaperState.CLOSED_TIMEOUT), "TIMEOUT")
        self.assertIsNone(_db_trade_outcome(None))

    def test_round_trips_snapshot_features(self) -> None:
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        snapshot = create_snapshot(
            "experiment-1",
            "BTCUSDT",
            timestamp,
            {
                "spread_bps": FeatureValue(
                    value=2.5,
                    unit="bps",
                    observed_at=timestamp,
                    available_at=timestamp,
                    quality_status=QualityStatus.VALID,
                )
            },
        )
        store = SupabaseSnapshotStore(FakeClient())
        store.save_snapshot(snapshot)
        loaded = store.get_snapshot(snapshot.snapshot_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.features["spread_bps"].value, 2.5)

    def test_saves_candidates_and_decisions_for_audit(self) -> None:
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
        decision = ClaudeDecision(
            snapshot_id="snapshot-1",
            profile=Profile.BALANCED,
            decision=Decision.NO_TRADE,
            summary_vi="Không đủ điều kiện.",
        )
        result = DecisionCycleResult(
            snapshot_id="snapshot-1",
            candidates=(candidate,),
            risk_results={
                "candidate-1": RiskResult(True, (), 35, 0.1),
            },
            decisions=(decision,),
            validation_errors={},
        )
        client = RecordingClient()

        SupabaseSnapshotStore(client).save_decision_cycle(result)

        self.assertEqual(client.rows["trade_candidates"][0]["candidate_id"], "candidate-1")
        self.assertEqual(client.rows["trade_candidates"][0]["eligibility"]["eligible"], True)
        self.assertEqual(client.rows["claude_decisions"][0]["validation_status"], "VALID")

        first_ids = SupabaseSnapshotStore(client).save_decision_cycle(result)
        second_ids = SupabaseSnapshotStore(client).save_decision_cycle(result)
        self.assertEqual(first_ids, second_ids)

    def test_persists_paper_account_and_recommendation(self) -> None:
        client = RecordingClient()
        store = SupabaseSnapshotStore(client)
        account_ids = store.ensure_paper_accounts("experiment-1")

        self.assertEqual(set(account_ids), {"PROACTIVE", "BALANCED", "CAUTIOUS"})
        self.assertEqual(len(client.rows["paper_accounts"]), 3)

    def test_loads_persisted_paper_recommendation(self) -> None:
        from datetime import datetime, timezone
        from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate
        from trade_brain.paper import PaperTradingEngine

        candidate = TradeCandidate(
            candidate_id="candidate-restore",
            snapshot_id="snapshot-restore",
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
        emitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recommendation = PaperTradingEngine().recommend(candidate, Decimal("0.5"), emitted_at)
        client = RecordingClient()
        store = SupabaseSnapshotStore(client)
        store.save_paper_recommendation("experiment-1", "account-1", "decision-1", recommendation, "BTCUSDT")

        loaded = store.load_paper_recommendations()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].recommendation_id, recommendation.recommendation_id)
        self.assertEqual(loaded[0].candidate.setup_id, "setup-restore")
        self.assertEqual(len(client.rows["paper_ledger"]), 1)
        self.assertEqual(client.rows["paper_ledger"][0]["event_type"], "RECOMMENDED")
        self.assertEqual(client.rows["paper_positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(client.rows["paper_positions"][0]["state"], "PENDING_FILL")
        self.assertEqual(client.rows["paper_positions"][0]["account_id"], "account-1")

        engine = PaperTradingEngine()
        engine.restore(recommendation)
        quote = MarketQuote(emitted_at, Decimal("100"), Decimal("101"), Decimal("100.5"))
        engine.fill(recommendation.recommendation_id, quote)
        store.project_paper_state(
            "experiment-1",
            "BTCUSDT",
            recommendation,
            equity=Decimal("10000.5"),
            initial_equity=Decimal("10000"),
            realized_equity=Decimal("10000"),
        )
        self.assertEqual(client.rows["paper_fills"][0]["fill_role"], "ENTRY")
        self.assertEqual(
            client.rows["paper_positions"][0]["position_id"],
            client.rows["paper_positions"][1]["position_id"],
        )
        self.assertEqual(client.rows["equity_samples"][0]["unrealized_pnl_usdt"], 0.5)

        store.append_paper_ledger_event("experiment-1", recommendation, "RECOMMENDED", "account-1")
        self.assertEqual(
            client.rows["paper_ledger"][0]["ledger_event_id"],
            client.rows["paper_ledger"][1]["ledger_event_id"],
        )

    def test_distinct_mark_timestamps_create_distinct_ledger_events(self) -> None:
        from trade_brain.contracts import Profile, Side, StatisticsStatus, TradeCandidate

        emitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candidate = TradeCandidate(
            candidate_id="candidate-ledger",
            snapshot_id="snapshot-ledger",
            setup_id="setup-ledger",
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
        recommendation = engine.recommend(candidate, Decimal("0.5"), emitted_at)
        client = RecordingClient()
        store = SupabaseSnapshotStore(client)
        store.append_paper_ledger_event("experiment-1", recommendation, "RECOMMENDED", "account-1")
        first_quote = MarketQuote(emitted_at, Decimal("100"), Decimal("101"), Decimal("100.5"))
        engine.fill(recommendation.recommendation_id, first_quote)
        store.append_paper_ledger_event("experiment-1", recommendation, "MARKED", "account-1")
        second_quote = MarketQuote(emitted_at.replace(second=1), Decimal("101"), Decimal("102"), Decimal("101.5"))
        engine.mark(recommendation.recommendation_id, second_quote)
        store.append_paper_ledger_event("experiment-1", recommendation, "MARKED", "account-1")

        marked_ids = [
            row["ledger_event_id"]
            for row in client.rows["paper_ledger"]
            if row["event_type"] == "MARKED"
        ]
        self.assertEqual(len(marked_ids), 2)
        self.assertNotEqual(marked_ids[0], marked_ids[1])

    def test_persists_report_cohort_and_milestone(self) -> None:
        report = PaperReport(
            scope=CohortScope.PROFILE_RECOMMENDATIONS,
            profile=Profile.BALANCED,
            milestone=1,
            member_ids=("recommendation-1",),
            recommendation_count=1,
            filled_count=0,
            closed_count=0,
            open_count=1,
            expired_or_cancelled_count=0,
            ambiguous_count=0,
            wins=0,
            losses=0,
            breakeven=0,
            net_positive_rate=None,
            total_net_pnl=Decimal("0"),
            total_net_r=Decimal("0"),
            max_drawdown=Decimal("0"),
            cohort_status="PROVISIONAL",
        )
        client = RecordingClient()

        SupabaseSnapshotStore(client).save_paper_report("experiment-1", report)

        self.assertEqual(len(client.rows["evaluation_cohorts"]), 1)
        self.assertEqual(len(client.rows["paper_reports"]), 1)
        self.assertEqual(len(client.rows["report_milestones"]), 1)
        self.assertEqual(len(client.rows["evaluation_cohort_members"]), 1)
        self.assertEqual(len(client.rows["report_jobs"]), 1)
        self.assertEqual(client.rows["paper_reports"][0]["report"]["member_ids"], ["recommendation-1"])

        SupabaseSnapshotStore(client).save_paper_report(
            "experiment-1",
            replace(report, cohort_status="FINAL"),
        )
        self.assertEqual(
            client.rows["paper_reports"][0]["report_id"],
            client.rows["paper_reports"][1]["report_id"],
        )
        self.assertEqual(
            client.rows["report_jobs"][0]["job_id"],
            client.rows["report_jobs"][1]["job_id"],
        )


if __name__ == "__main__":
    unittest.main()
