-- Preserve the exact Claude request lineage without storing API credentials.
alter table public.claude_decisions
  add column if not exists model text,
  add column if not exists prompt_version text,
  add column if not exists schema_version text,
  add column if not exists payload_hash text,
  add column if not exists latency_ms numeric(16, 3);

alter table public.claude_decisions enable row level security;

revoke all on table public.claude_decisions from anon, authenticated;
