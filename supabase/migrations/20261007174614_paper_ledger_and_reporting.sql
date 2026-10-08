-- Durable paper-trading events and locked report cohorts.
-- All tables remain service-only until an explicit authenticated access model is added.

create table public.paper_ledger (
  ledger_event_id uuid primary key default gen_random_uuid(),
  experiment_id text not null references public.experiments(experiment_id),
  account_id uuid not null references public.paper_accounts(account_id),
  recommendation_id uuid references public.paper_recommendations(recommendation_id),
  event_type text not null check (event_type in ('RECOMMENDED', 'FILLED', 'MARKED', 'EXPIRED', 'CANCELLED', 'CORRECTION')),
  occurred_at timestamptz not null,
  received_at timestamptz not null default now(),
  payload jsonb not null default '{}'::jsonb
);

create index paper_ledger_account_time_idx
  on public.paper_ledger (account_id, occurred_at desc);

create index paper_ledger_recommendation_time_idx
  on public.paper_ledger (recommendation_id, occurred_at desc);

create table public.evaluation_cohorts (
  cohort_id uuid primary key default gen_random_uuid(),
  experiment_id text not null references public.experiments(experiment_id),
  scope text not null check (scope in ('PROFILE_RECOMMENDATIONS', 'PROFILE_COMPLETED_TRADES', 'GLOBAL_SETUP_RECOMMENDATIONS')),
  profile text check (profile is null or profile in ('PROACTIVE', 'BALANCED', 'CAUTIOUS')),
  milestone integer not null check (milestone > 0),
  status text not null check (status in ('PROVISIONAL', 'FINAL')),
  member_ids jsonb not null check (jsonb_typeof(member_ids) = 'array'),
  config_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index evaluation_cohorts_identity_idx
  on public.evaluation_cohorts (experiment_id, scope, milestone, coalesce(profile, ''));

create table public.paper_reports (
  report_id uuid primary key default gen_random_uuid(),
  cohort_id uuid not null references public.evaluation_cohorts(cohort_id),
  report_version integer not null default 1 check (report_version > 0),
  status text not null check (status in ('PROVISIONAL', 'FINAL', 'CORRECTION')),
  report jsonb not null,
  created_at timestamptz not null default now(),
  unique (cohort_id, report_version)
);

create table public.report_milestones (
  milestone_key text primary key,
  experiment_id text not null references public.experiments(experiment_id),
  cohort_id uuid references public.evaluation_cohorts(cohort_id),
  scope text not null,
  profile text,
  milestone integer not null check (milestone > 0),
  status text not null check (status in ('PENDING', 'PROVISIONAL', 'FINAL', 'CORRECTION')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.paper_ledger enable row level security;
alter table public.evaluation_cohorts enable row level security;
alter table public.paper_reports enable row level security;
alter table public.report_milestones enable row level security;

revoke all on table
  public.paper_ledger,
  public.evaluation_cohorts,
  public.paper_reports,
  public.report_milestones
from anon, authenticated;
