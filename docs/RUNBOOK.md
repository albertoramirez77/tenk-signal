# TenK Signal — Operations Runbook

> **When to read this**: when something is on fire, or you're about to ship
> a change that could put it there.

## Rollback procedures

### Backend (Render)

1. Render UI → service → **Deploys** tab → click the last green deploy →
   **Rollback to this deploy**. Render keeps a configurable history; we
   ship with 10.
2. Confirm `/healthz` returns 200 on the rolled-back deploy URL.
3. Tail `/metrics` for ~5 minutes: error rate
   (`http_requests_total{status=~"5.."}`) should drop within the first
   minute. If not, escalate.

**Estimated rollback time:** ~30 seconds for the deploy switchover, ~2
minutes for the new replicas to pass health checks.

### Frontend (Vercel)

1. Vercel UI → project → **Deployments** → click the last good production
   deploy → **... menu → Promote to Production**.
2. The CDN reflects within ~30 seconds. Hard-refresh the dashboard.

**Estimated rollback time:** ~60 seconds.

### Database (Alembic)

Database rollbacks are higher-risk and should be a last resort. The
sequence:

```bash
# 1. Take a snapshot before doing anything (Supabase makes this easy in UI).
# 2. Locally, with prod DATABASE_URL set:
cd backend
uv run alembic current        # Note the current head revision.
uv run alembic history --verbose | head -20

# 3. Downgrade ONE step at a time. Never multi-step blind:
uv run alembic downgrade -1

# 4. Verify the app on the rolled-back schema:
uv run pytest tests/e2e/test_pipeline_on_fixtures.py -v
```

**CI exercises every migration's `upgrade head → downgrade base → upgrade
head` cycle on every PR**, so a "downgrade doesn't reverse" surprise in
prod indicates a CI gap, not a migration that was never tested.

## When to roll back (failure signals)

Roll back if **any** of these happen within 15 minutes of a deploy:

| Signal | Threshold | Action |
|---|---|---|
| Sentry new error spike | > 10 unique fingerprints / minute | Backend rollback |
| `/metrics` 5xx rate | > 1% of total requests | Backend rollback |
| EDGAR parser fallback rate | > 50% (was 0%) | Backend rollback — SEC HTML changed |
| Dashboard returns 5xx | Any | Frontend rollback first, then backend |
| Anthropic cost spike | > 2× expected per hour | Pause `/extract` (revoke admin key) |
| DB connection saturation | `http_requests_in_flight` > pool limit sustained | Backend rollback + investigate query |

Rollback is **always cheaper than diagnosis on a degraded service**. Do
not "wait and see" — roll back, then diagnose calmly.

## Pause buttons (no rollback needed)

These don't require a deploy:

- **Stop accepting new ingest/extract jobs**: revoke
  `APP_API_KEY_ADMIN` in Render env. All write endpoints return 401
  within ~30 seconds (next request triggers config reload). Viewer GET
  endpoints continue to work.
- **Stop new backtests**: same — admin key revoke covers it.
- **Quarantine a misbehaving filing**: SQL update on
  `filings.quarantined = true`. Tag is surfaced in the dashboard's
  signals table.

## Common diagnostic queries

```sql
-- How many filings are stuck in fallback mode (no section split)?
SELECT count(*) FROM filings
WHERE section_extraction_mode = 'full_doc_truncated'
  AND created_at > now() - interval '24 hours';

-- Extractions with low confidence (LLM uncertain) — investigate prompt:
SELECT f.ticker, f.accession_no, e.confidence, e.rationale
FROM extractions e JOIN filings f ON f.id = e.filing_id
WHERE e.confidence < 0.4 ORDER BY e.created_at DESC LIMIT 20;

-- Cache hit rate for the last day:
SELECT
  count(*) FILTER (WHERE created_at < (
    SELECT max(created_at) FROM extractions WHERE text_sha256 = e.text_sha256
  )) AS cached_responses,
  count(*) AS total_extractions
FROM extractions e
WHERE created_at > now() - interval '24 hours';
```

## Cost monitoring

Set up budget alerts in:
- **Anthropic console**: `https://console.anthropic.com/settings/usage`
  → budget alerts. Recommended: alert at 50% / 80% / 100% of the
  monthly budget.
- **Supabase**: project settings → billing → spending caps.
- **Render**: account settings → billing.

Per-extraction cost ≈ $0.05 on Sonnet 4.6 at the 60k-char prompt budget.
30 tickers × 4 filings/year × $0.05 ≈ **$6/year for the universe**. If
monthly spend climbs above ~$3, the cache or `PROMPT_VERSION` was bumped
and we're re-extracting unnecessarily.

## Restoring lost data

EDGAR ingest is idempotent and content-addressed. To recover lost
filings:

```bash
cd backend
# Re-ingest the universe (skips existing by accession_no):
curl -X POST -H "X-API-Key: $APP_API_KEY_ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["MSFT", "JPM", "JNJ", ...], "limit_per_ticker": 4}' \
  $BACKEND_API_URL/ingest

# Re-extract pending:
curl -X POST -H "X-API-Key: $APP_API_KEY_ADMIN" \
  -d '{"all_pending": true}' $BACKEND_API_URL/extract

# Re-run latest backtest (signals are reconstructed deterministically):
curl -X POST -H "X-API-Key: $APP_API_KEY_ADMIN" \
  -d '{}' $BACKEND_API_URL/backtest
```

Recovery cost: ~$6 in Anthropic spend per full re-extract, ~5 minutes
wall-clock. The cache prevents accidental double-billing.
