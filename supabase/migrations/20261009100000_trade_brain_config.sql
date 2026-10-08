-- Server-only editable Trade Brain research configuration.
create table public.trade_brain_configs (
  config_id text primary key check (config_id = 'active'),
  config_version text not null,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.trade_brain_configs enable row level security;
revoke all on table public.trade_brain_configs from anon, authenticated;
