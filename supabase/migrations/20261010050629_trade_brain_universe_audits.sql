-- V2 universe refresh audit. Service-only, no public API access.
create table public.universe_scans (
  scan_id text primary key,
  observed_at timestamptz not null,
  exchange text not null,
  market_type text not null,
  quote_asset text not null,
  min_quote_volume_24h_usdt numeric(24, 8) not null check (min_quote_volume_24h_usdt > 0),
  input_ticker_count integer not null check (input_ticker_count >= 0),
  registry_count integer not null check (registry_count >= 0),
  passed_count integer not null check (passed_count >= 0),
  passed_symbols jsonb not null default '[]'::jsonb,
  exclusion_counts jsonb not null default '{}'::jsonb,
  config_version text not null,
  created_at timestamptz not null default now()
);

create index universe_scans_observed_at_idx
  on public.universe_scans (observed_at desc);

alter table public.universe_scans enable row level security;
revoke all on table public.universe_scans from anon, authenticated;

alter table public.claude_decisions
  add column if not exists gate_audit jsonb not null default '{}'::jsonb;
