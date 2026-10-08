-- Normalized paper-trading audit projections from the V1 specification.
-- Existing paper_recommendations and paper_ledger remain the source of lifecycle
-- truth. These tables provide stable, queryable projections for fills, positions,
-- equity samples, locked cohort members, and report jobs.

create table public.paper_fills (
  fill_id uuid primary key default gen_random_uuid(),
  recommendation_id uuid not null references public.paper_recommendations(recommendation_id),
  fill_role text not null check (fill_role in ('ENTRY', 'EXIT')),
  side text not null check (side in ('LONG', 'SHORT')),
  quantity numeric(24, 8) not null check (quantity > 0),
  price numeric(24, 8) not null check (price > 0),
  fee_usdt numeric(24, 8) not null default 0 check (fee_usdt >= 0),
  slippage_usdt numeric(24, 8) not null default 0,
  occurred_at timestamptz not null,
  received_at timestamptz not null default now(),
  idempotency_key text not null unique,
  payload jsonb not null default '{}'::jsonb
);

create index paper_fills_recommendation_time_idx
  on public.paper_fills (recommendation_id, occurred_at);

create table public.paper_positions (
  position_id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.paper_accounts(account_id),
  recommendation_id uuid not null unique references public.paper_recommendations(recommendation_id),
  setup_id text not null,
  symbol text not null,
  side text not null check (side in ('LONG', 'SHORT')),
  state text not null check (state in ('PENDING_FILL', 'OPEN', 'CLOSED', 'CANCELLED', 'EXPIRED')),
  quantity numeric(24, 8) not null check (quantity > 0),
  entry_fill numeric(24, 8),
  initial_stop numeric(24, 8) not null check (initial_stop > 0),
  target_price numeric(24, 8) not null check (target_price > 0),
  opened_at timestamptz,
  closed_at timestamptz,
  updated_at timestamptz not null default now(),
  payload jsonb not null default '{}'::jsonb
);

create index paper_positions_account_state_idx
  on public.paper_positions (account_id, state, updated_at desc);

create table public.equity_samples (
  sample_id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.paper_accounts(account_id),
  sampled_at timestamptz not null,
  equity_usdt numeric(24, 8) not null,
  realized_pnl_usdt numeric(24, 8) not null default 0,
  unrealized_pnl_usdt numeric(24, 8) not null default 0,
  drawdown_pct numeric(16, 8) not null default 0,
  data_quality text not null check (data_quality in ('VALID', 'DEGRADED', 'AMBIGUOUS', 'INVALID')),
  payload jsonb not null default '{}'::jsonb,
  unique (account_id, sampled_at)
);

create index equity_samples_account_time_idx
  on public.equity_samples (account_id, sampled_at desc);

create table public.evaluation_cohort_members (
  cohort_id uuid not null references public.evaluation_cohorts(cohort_id),
  ordinal integer not null check (ordinal > 0),
  recommendation_id uuid references public.paper_recommendations(recommendation_id),
  setup_id text not null,
  created_at timestamptz not null default now(),
  primary key (cohort_id, ordinal),
  unique (cohort_id, recommendation_id)
);

create index evaluation_cohort_members_setup_idx
  on public.evaluation_cohort_members (setup_id);

create table public.report_jobs (
  job_id uuid primary key default gen_random_uuid(),
  experiment_id text not null references public.experiments(experiment_id),
  scope text not null,
  profile text,
  milestone integer not null check (milestone > 0),
  status text not null check (status in ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  idempotency_key text not null unique,
  attempts integer not null default 0 check (attempts >= 0),
  last_error text,
  available_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index report_jobs_status_time_idx
  on public.report_jobs (status, available_at);

alter table public.paper_fills enable row level security;
alter table public.paper_positions enable row level security;
alter table public.equity_samples enable row level security;
alter table public.evaluation_cohort_members enable row level security;
alter table public.report_jobs enable row level security;

revoke all on table
  public.paper_fills,
  public.paper_positions,
  public.equity_samples,
  public.evaluation_cohort_members,
  public.report_jobs
from anon, authenticated;
