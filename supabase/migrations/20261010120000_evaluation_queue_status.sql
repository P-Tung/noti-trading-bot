-- Shared progress and last result for manual or future scheduled evaluation queues.
create table public.evaluation_queue_status (
  status_id text primary key check (status_id = 'active'),
  run_id text,
  status text not null check (status in ('IDLE', 'RUNNING', 'COMPLETED', 'FAILED')),
  started_at timestamptz,
  finished_at timestamptz,
  total_count integer not null default 0 check (total_count >= 0),
  completed_count integer not null default 0 check (completed_count >= 0),
  current_index integer not null default 0 check (current_index >= 0),
  current_symbol text,
  symbols jsonb not null default '[]'::jsonb,
  result_summary jsonb not null default '[]'::jsonb,
  error text,
  updated_at timestamptz not null default now()
);

alter table public.evaluation_queue_status enable row level security;
revoke all on table public.evaluation_queue_status from anon, authenticated;
grant select, insert, update on table public.evaluation_queue_status to service_role;
