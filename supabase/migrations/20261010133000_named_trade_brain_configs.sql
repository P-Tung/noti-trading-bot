-- Named, server-only Trade Brain configurations for reusable research presets.
create table public.trade_brain_config_versions (
  config_id text primary key,
  name text not null unique,
  config_version text not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.trade_brain_config_versions enable row level security;
revoke all on table public.trade_brain_config_versions from anon, authenticated;
grant select, insert, update on table public.trade_brain_config_versions to service_role;

create index trade_brain_config_versions_updated_at_idx
  on public.trade_brain_config_versions (updated_at desc);
