-- Trade V1 core persistence model.
-- This migration is intentionally additive and paper-trading only.

create table public.experiments (
  experiment_id text primary key,
  name text not null,
  execution_mode text not null check (execution_mode in ('PAPER')),
  data_mode text not null check (data_mode in ('PRICE_ONLY', 'FULL_DATA')),
  config_version text not null,
  initial_equity_usdt numeric(24, 8) not null check (initial_equity_usdt > 0),
  created_at timestamptz not null default now()
);

create table public.market_snapshots (
  snapshot_id text primary key,
  experiment_id text not null references public.experiments(experiment_id),
  symbol text not null,
  market_type text not null default 'BINANCE_USDM_PERPETUAL',
  decision_time timestamptz not null,
  expires_at timestamptz not null,
  quality_status text not null check (quality_status in ('VALID', 'DEGRADED', 'AMBIGUOUS', 'INVALID')),
  data_mode text not null check (data_mode in ('PRICE_ONLY', 'FULL_DATA')),
  feature_version text not null,
  strategy_version text not null,
  policy_version text not null,
  probability_version text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (expires_at > decision_time)
);

create index market_snapshots_symbol_time_idx
  on public.market_snapshots (symbol, decision_time desc);

create table public.trade_candidates (
  candidate_id text primary key,
  snapshot_id text not null references public.market_snapshots(snapshot_id),
  setup_id text not null,
  profile text not null check (profile in ('PROACTIVE', 'BALANCED', 'CAUTIOUS')),
  strategy text not null check (strategy in ('T1', 'T2', 'R1')),
  entry_stage text not null check (entry_stage in ('EARLY', 'CONFIRMED', 'STRICT')),
  side text not null check (side in ('LONG', 'SHORT')),
  entry_estimate numeric(24, 8) not null check (entry_estimate > 0),
  stop_price numeric(24, 8) not null check (stop_price > 0),
  target_price numeric(24, 8) not null check (target_price > 0),
  horizon_bars integer not null check (horizon_bars > 0),
  statistics_status text not null check (statistics_status in ('VERIFIED', 'RESEARCH_ONLY', 'INSUFFICIENT', 'OUT_OF_DISTRIBUTION')),
  statistics jsonb not null default '{}'::jsonb,
  eligibility jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (setup_id, profile, entry_stage)
);

create index trade_candidates_snapshot_profile_idx
  on public.trade_candidates (snapshot_id, profile);

create table public.claude_decisions (
  decision_id uuid primary key default gen_random_uuid(),
  snapshot_id text not null references public.market_snapshots(snapshot_id),
  profile text not null check (profile in ('PROACTIVE', 'BALANCED', 'CAUTIOUS')),
  decision text not null check (decision in ('LONG', 'SHORT', 'WAIT', 'NO_TRADE')),
  selected_candidate_id text references public.trade_candidates(candidate_id),
  watch_candidate_id text references public.trade_candidates(candidate_id),
  condition_ids jsonb not null default '[]'::jsonb,
  reason_codes jsonb not null default '[]'::jsonb,
  evidence_ids jsonb not null default '[]'::jsonb,
  summary_vi text not null,
  validation_status text not null check (validation_status in ('VALID', 'INVALID', 'SERVICE_ERROR')),
  raw_response jsonb,
  created_at timestamptz not null default now()
);

create table public.paper_accounts (
  account_id uuid primary key default gen_random_uuid(),
  experiment_id text not null references public.experiments(experiment_id),
  profile text not null check (profile in ('PROACTIVE', 'BALANCED', 'CAUTIOUS')),
  initial_equity_usdt numeric(24, 8) not null check (initial_equity_usdt > 0),
  current_equity_usdt numeric(24, 8) not null,
  created_at timestamptz not null default now(),
  unique (experiment_id, profile)
);

create table public.paper_recommendations (
  recommendation_id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.paper_accounts(account_id),
  decision_id uuid not null references public.claude_decisions(decision_id),
  candidate_id text references public.trade_candidates(candidate_id),
  setup_id text not null,
  state text not null check (state in ('RECOMMENDED', 'PENDING_FILL', 'OPEN', 'CLOSED', 'CANCELLED', 'EXPIRED')),
  emitted_at timestamptz not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (account_id, setup_id)
);

create table public.paper_trades (
  trade_id uuid primary key default gen_random_uuid(),
  recommendation_id uuid not null unique references public.paper_recommendations(recommendation_id),
  entry_fill numeric(24, 8),
  exit_fill numeric(24, 8),
  quantity numeric(24, 8),
  initial_stop numeric(24, 8),
  net_pnl_usdt numeric(24, 8),
  net_r numeric(24, 8),
  outcome text check (outcome in ('WIN', 'LOSS', 'BREAKEVEN', 'TIMEOUT', 'AMBIGUOUS')),
  entry_time timestamptz,
  exit_time timestamptz,
  costs jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.experiments enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.trade_candidates enable row level security;
alter table public.claude_decisions enable row level security;
alter table public.paper_accounts enable row level security;
alter table public.paper_recommendations enable row level security;
alter table public.paper_trades enable row level security;

-- No public or authenticated policies are granted yet. The service layer will
-- use a server-side secret, while dashboard access policies are added with the
-- authenticated ownership model in the dashboard phase.
