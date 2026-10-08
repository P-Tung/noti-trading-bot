from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_paper_reporting_migration_is_service_only_and_null_safe() -> None:
    migration = (
        ROOT / "supabase/migrations/20261007174614_paper_ledger_and_reporting.sql"
    ).read_text()

    for table_name in (
        "paper_ledger",
        "evaluation_cohorts",
        "paper_reports",
        "report_milestones",
    ):
        assert f"create table public.{table_name}" in migration
        assert f"alter table public.{table_name} enable row level security" in migration
        assert f"public.{table_name}" in migration.split("revoke all on table", 1)[1]

    assert "coalesce(profile, '')" in migration

    audit_migration = (
        ROOT / "supabase/migrations/20261007175002_claude_decision_audit.sql"
    ).read_text()
    for column in ("model", "prompt_version", "schema_version", "payload_hash", "latency_ms"):
        assert f"add column if not exists {column}" in audit_migration


def test_normalized_paper_audit_migration_is_service_only_and_idempotent() -> None:
    migration = (
        ROOT / "supabase/migrations/20261008120000_normalized_paper_audit.sql"
    ).read_text()

    for table_name in (
        "paper_fills",
        "paper_positions",
        "equity_samples",
        "evaluation_cohort_members",
        "report_jobs",
    ):
        assert f"create table public.{table_name}" in migration
        assert f"alter table public.{table_name} enable row level security" in migration
        assert f"public.{table_name}" in migration.split("revoke all on table", 1)[1]

    for key_name in ("idempotency_key", "position_id", "sample_id", "job_id"):
        assert key_name in migration
