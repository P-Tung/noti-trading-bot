-- Telegram recipients are registered by /start and managed only by Trade Brain.
create table public.telegram_subscribers (
  chat_id text primary key check (length(trim(chat_id)) > 0),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

alter table public.telegram_subscribers enable row level security;
revoke all on table public.telegram_subscribers from anon, authenticated;
grant select, insert, update on table public.telegram_subscribers to service_role;
