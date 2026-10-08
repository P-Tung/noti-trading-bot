-- Trade V1 keeps the initial journal service-only.
-- The Next.js dashboard reads through the Trade Brain server proxy, which
-- uses SUPABASE_SECRET_KEY. No browser client should query these tables yet.

revoke all on table
  public.experiments,
  public.market_snapshots,
  public.trade_candidates,
  public.claude_decisions,
  public.paper_accounts,
  public.paper_recommendations,
  public.paper_trades
from anon, authenticated;

-- Keep RLS enabled as defense in depth if table exposure is changed later.
alter table public.experiments enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.trade_candidates enable row level security;
alter table public.claude_decisions enable row level security;
alter table public.paper_accounts enable row level security;
alter table public.paper_recommendations enable row level security;
alter table public.paper_trades enable row level security;
