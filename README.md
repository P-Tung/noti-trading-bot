# Trade V1

Paper-trading research platform built around Freqtrade, Supabase, a Python Trade Brain, Claude candidate selection, a React dashboard, and Telegram notifications.

## Current phase

**Phase 9, automated paper monitoring and risk hardening**

- Freqtrade dry-run configuration is prepared for Binance USDⓈ-M futures.
- The starter strategy intentionally produces no signals.
- Real-money execution is out of scope.
- Supabase credentials are environment-only and are not committed.
- Paper ledger events, evaluation cohorts, report artifacts, and milestone state have additive Supabase migrations with service-only RLS.
- Claude decision records keep model, prompt/schema versions, request payload hash, and latency metadata without storing API keys.
- Deterministic market-data primitives and T1 breakout/retest detection are implemented.
- Trade Brain decision contracts and validation API are implemented.
- T2 and R1 detection, risk gates, paper execution, reporting, Supabase journaling, Claude selection, and Telegram adapters are implemented.
- The decision worker collects closed `1d`, `4h`, `1h`, `15m`, and `5m` data; T1 uses 1H structure plus confirmed 15m triggers, while T2/R1 use 15m trigger data.
- T1/T2 candidates are hard-gated against clear aligned 1D/4H EMA context; conflicting or countertrend context blocks them, while R1 remains a range strategy.
- PRICE_ONLY snapshots also capture EMA20/EMA50 context, normalized distance/slope features, confirmed-pivot structure state, range position, taker-buy fraction/ratio, trade-count relative volume, candle body/close location, mark/index basis, timestamped 1-hour/4-hour OI changes, current mark/index price, funding, open interest, 24-hour quote volume, bid/ask spread, 10 bps depth, book imbalance, and exchange quantity/price rules when the public endpoints are available; missing auxiliary data stays explicitly degraded.
- The automated decision worker composes the closed-bar to paper recommendation flow.
- The live decision worker filters out historical setups and only evaluates a setup triggered by the latest closed candle; the research detectors retain historical setup output for backtests.
- The quote monitor advances active paper recommendations using public Binance bid/ask data.
- Open stop risk and daily/rolling paper drawdown feed the decision risk gates.
- A responsive dashboard shows snapshots, paper journal records, and locked report metrics under `apps/dashboard`.
- The dashboard refreshes its read-only data every 15 seconds and also supports manual synchronization.

## Repository layout

```text
infra/freqtrade/       Freqtrade configuration and strategies
services/trade-brain/  Custom analysis service and strategy logic
apps/dashboard/        Next.js dashboard, added in Phase 7
supabase/              Database migrations, added when the data model is ready
```

## Phase 1 setup

1. Install Docker Desktop.
2. Copy `.env.example` to `.env`.
3. Review `infra/freqtrade/config.example.json`.
4. Copy the example into the ignored local path:

   ```bash
   cp infra/freqtrade/config.example.json infra/freqtrade/user_data/config.json
   ```

5. Start the safe foundation:

   ```bash
   cd infra/freqtrade
   docker compose up
   ```

6. Open the local API at `http://127.0.0.1:8080`.

The starter strategy does not generate trades. This is intentional until the data contracts, strategy rules, and risk checks are implemented and tested.

If Docker is unavailable, Freqtrade cannot be runtime-verified locally. The committed config remains a static safety boundary: dry-run is enabled, exchange credentials are empty, the API binds to localhost, and the foundation strategy emits no entries.

## Supabase local verification

After Docker Desktop is running, apply the committed migrations with the installed Supabase CLI:

```bash
supabase start
supabase migration up --local
supabase status
```

The reporting migration creates service-only `paper_ledger`, `evaluation_cohorts`, `paper_reports`, and `report_milestones` tables. The local migration command is intentionally kept separate from the Trade Brain startup so schema failures cannot be mistaken for a healthy application.

Run the safe prerequisite check before starting local services:

```bash
bash scripts/trade-v1-preflight.sh
```

The preflight checks required command-line tools, Docker daemon availability, the local environment file, Supabase seed configuration, and the Freqtrade paper-only safety boundary. It prints variable names only and never prints secret values.

## Trade Brain API

Install the locked Python environment from the repository root:

```bash
uv sync --project services/trade-brain --extra dev
```

The lockfile is `services/trade-brain/uv.lock`. It keeps the tested Supabase and Python dependency versions reproducible.

Run the Trade Brain test suite from its package directory:

```bash
cd services/trade-brain
uv run pytest -q
```

If the nested environment was created manually, the equivalent repository-root command is:

```bash
PYTHONPATH=services/trade-brain/src services/trade-brain/.venv/bin/pytest -q
```

Run the contract service locally:

```bash
services/trade-brain/.venv/bin/uvicorn trade_brain.app:app --app-dir services/trade-brain/src --reload --port 8090
```

Current endpoints:

- `GET /health`, returns liveness plus `execution_mode: PAPER` and `real_money_enabled: false`
- `GET /v1/snapshots?limit=50`
- `POST /v1/snapshots/collect`
- `POST /v1/decisions/run`, risk-gates backend candidates, calls Claude when configured, and returns paper-only decisions
- `GET /v1/decisions/recent?limit=20`, returns recent Claude decisions for the read-only dashboard
- `POST /v1/decisions/validate`
- `GET /v1/paper/recommendations`
- `POST /v1/paper/recommendations/{id}/fill`
- `POST /v1/paper/recommendations/{id}/mark`
- `POST /v1/paper/recommendations/{id}/quote`, advances a recommendation from fill to mark based on its current paper state
- `GET /v1/reports?milestone=1`, locked 100-recommendation profile cohorts
- `POST /v1/backtests/run`, deterministic research-only backtest over closed bars

WAIT decisions are fail-closed against a backend-owned condition registry. V1 exposes only
`WAIT_FOR_NEXT_CLOSED_15M` and `WAIT_FOR_PRICE_RECHECK`; Claude cannot invent a condition ID
or promote WAIT into a trade without a new evaluation.

Paper quote quality is stored independently from lifecycle state. A quote can be `DEGRADED`
when optional funding context is unavailable, while `AMBIGUOUS` or `INVALID` data is fail-closed
and cannot create a fill or close an open paper position. Cohort reports count those quality states
separately instead of treating them as wins or losses.

Paper modes:

- `RESEARCH_PAPER` allows eligible strategy setups to be simulated before verified statistics exist. Missing probabilities and expectancy remain null and are never invented by Claude.
- `RESEARCH_PAPER` relaxes statistical evidence only; unconfirmed setups, invalid prices, unavailable quantity, drawdown stops, and portfolio risk limits still block recommendations.
- `VERIFIED_PAPER` applies the full statistics thresholds and blocks candidates without verified evidence.

When both Telegram variables are configured, newly created paper recommendations are sent as alerts. The notifier retries one transient network, `429`, or `5xx` failure, then returns the error in `notification_errors`; it never turns a paper decision into a real order.
Send `/danh-gia` to the configured Telegram chat to run one on-demand PAPER evaluation. The bot replies in Vietnamese with the evaluated symbols, all profile decisions, and any new recommendations. It also replies explicitly when the cycle produces no paper trade recommendation. `/danhgia`, `/danh_gia`, `/evaluate`, and `/evaluate_now` remain accepted as aliases.
Paper lifecycle transitions are also notified once when a recommendation changes state, such as `OPEN`, `CLOSED_TP`, `CLOSED_SL`, `CLOSED_TIMEOUT`, or `EXPIRED`; unchanged quote updates are not sent again.
When a profile or global cohort reaches a 100-recommendation milestone, including milestone 2, 3, and later batches, a `PROVISIONAL` or `FINAL` report is persisted and sent once per process lifecycle and status. Completed-trade cohorts are included when they become available.

Run the periodic public-data collector after installing the project:

```bash
PYTHONPATH=services/trade-brain/src services/trade-brain/.venv/bin/python -m trade_brain.worker
```

Optional environment settings:

- `TRADE_V1_EXPERIMENT_ID`, default `trade-v1-local`
- `TRADE_SYMBOLS`, default `BTCUSDT,ETHUSDT`
- `TRADE_COLLECTION_INTERVAL_SECONDS`, default `900`, minimum `60`
- `TRADE_PAPER_MODE`, default `RESEARCH_PAPER`, alternatively `VERIFIED_PAPER`

The collector only stores closed-candle PRICE_ONLY snapshots. It does not place orders.
If one configured symbol has a temporary Binance or data-quality error, the collector logs and skips that symbol while continuing the cycle.

Run the automated decision-to-paper worker when Claude is configured:

```bash
PYTHONPATH=services/trade-brain/src services/trade-brain/.venv/bin/python -m trade_brain.decision_worker
```

The worker runs the full safe path: closed bars, T1/T2/R1 candidates, risk gates, Claude selection, paper recommendation, Supabase journal persistence, and optional Telegram notification.
Automatic Claude evaluation is not available in the current testing phase. Telegram and Discord commands remain available for on-demand evaluation only.
The decision worker also skips a symbol when its collection cycle fails, so one market-data outage does not stop the other configured symbols.
Each profile sizes from its restored current paper equity, so gains and losses change later paper risk budgets independently.
The paper account endpoint also exposes mark-to-market equity for open positions; realized P&L remains separate.
Risk gates include the current open stop-risk and closed-result daily/rolling drawdown of each paper profile, so subsequent cycles do not assume an empty or loss-free portfolio.
Decision sizing uses the symbol's Binance `LOT_SIZE.stepSize` and `minQty`, validates the stop/target direction, and validates entry, stop, and target against `PRICE_FILTER.tickSize`; if exchange rules cannot be read, that symbol fails closed with no paper quantity.
When a durable cohort reaches a report milestone, the worker also upserts the report artifact and sends the corresponding Telegram report once per cohort status.
Completed-trade cohorts are ordered by recorded entry time, not by which trade happened to close first.
Report drawdown is calculated from each profile's initial paper equity and stored as a ratio, not an absolute P&L amount.

Run the public-quote paper monitor separately after Trade Brain is running:

```bash
PYTHONPATH=services/trade-brain/src services/trade-brain/.venv/bin/python -m trade_brain.paper_quote_worker
```

The monitor reads active paper recommendations, fetches Binance USDⓈ-M public `bookTicker` bid/ask data plus funding metadata, and submits quotes to the paper-only lifecycle endpoint. Funding events are applied at most once per funding timestamp. `TRADE_QUOTE_INTERVAL_SECONDS` defaults to `15` and cannot be set below `5`.
An individual Binance or quote failure is recorded for that recommendation and does not stop the rest of the monitoring cycle.

## Dashboard

Start the read-only operator dashboard in a second terminal:

```bash
cd apps/dashboard
npm install
npm run dev
```

Open `http://localhost:3000`. The dashboard proxies requests to `TRADE_BRAIN_URL`; it never exposes the Supabase secret key or calls Binance directly from the browser.

Recommended local process order:

1. Start Docker Desktop and apply the Supabase migrations.
2. Start the Trade Brain API on port `8090`.
3. Start the decision worker after `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` are configured.
4. Start the public-quote paper monitor.
5. Open the dashboard and confirm snapshots, paper accounts, and journal state.

Claude audit metadata is attached to each decision cycle and persisted by the `claude_decisions` table after the `claude_decision_audit` migration. The payload hash is for request lineage; it is not a substitute for storing secrets or raw credentials.

The research backtest module is available at `trade_brain.backtest`. It simulates only bars after each signal, marks same-bar TP/SL hits as `AMBIGUOUS`, excludes ambiguous outcomes from measured win-rate statistics, and always returns `RESEARCH_ONLY` statistics until an explicit validation process promotes them.

On startup, a Supabase-backed service restores persisted paper recommendations into the local lifecycle engine so the journal survives process restarts.
When the durable reporting migration is applied, report cohorts and milestone artifacts are also upserted with deterministic identifiers, so provisional-to-final updates remain idempotent across retries.
Decision-cycle candidates and Claude audit rows also use retry-safe upserts and deterministic decision IDs derived from the snapshot and profile.
Paper recommendation IDs are deterministic from the profile, setup, and candidate identity, and recommendation persistence uses upsert for restart-safe lifecycle creation.

## Safety boundary

- Do not add Binance withdrawal permissions.
- Do not add real trading permissions.
- Do not set `dry_run` to `false`.
- Do not treat backtest results as proof of profitability.
