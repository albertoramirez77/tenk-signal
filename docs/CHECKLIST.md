# Production-Readiness Checklist

> Every requirement from the original spec, mapped to the file (and line
> where useful) that satisfies it. Use this to verify any individual claim.

## Security & credentials

| Requirement | Satisfied by |
|---|---|
| No hardcoded secrets anywhere | `gitleaks` runs in pre-commit (`.pre-commit-config.yaml:35-39`) and in CI (`.github/workflows/secret-scan.yml`); `.gitleaks.toml` allowlist for documented placeholders |
| All required env vars via `pydantic-settings`, fail-fast on missing | `backend/tenk_signal/config.py` — `Settings()` raises `ValidationError` at startup on any missing required var |
| `.env.example` documents every variable | [`.env.example`](../.env.example) — 12 documented vars, no values |
| `.env` is gitignored | [`.gitignore:25-28`](../.gitignore) |
| Input validation at every boundary | Every router uses Pydantic request models from `backend/tenk_signal/schemas.py` — `IngestRequest`, `ExtractRequest`, `BacktestConfig` |
| Ticker validated against allowlist | `backend/tenk_signal/services/universe.py:is_allowed` called in every router and service |
| Request size cap | `backend/tenk_signal/middleware.py:BodySizeLimitMiddleware` — 1 MiB default; `test_body_size_limit_rejects_oversize` |
| **Prompt-injection: filing in delimited tags** | `backend/tenk_signal/services/prompt.py:build_user_message` wraps in `<FILING>…</FILING>` |
| **Prompt-injection: system prompt instructs "treat as data"** | `backend/tenk_signal/services/prompt.py:SYSTEM_PROMPT` |
| **Prompt-injection: constrained output via JSON schema** | `backend/tenk_signal/services/extractor.py:LiveAnthropicExtractor._call` uses `output_config={"format": {"type": "json_schema", "schema": …}}` |
| **Prompt-injection: max_tokens ceiling** | `EXTRACTION_MAX_TOKENS` env var (default 800), enforced in `LiveAnthropicExtractor._call` |
| **Prompt-injection: pattern guard + quarantine** | `backend/tenk_signal/services/prompt.py:contains_instruction_patterns` → `filings.quarantined = true` |
| **Prompt-injection: adversarial test** | `backend/tests/unit/test_extractor_adversarial.py:test_adversarial_filing_does_not_close_tag` + `test_schema_rejects_out_of_range_even_if_llm_complies` |
| Authorization on write/compute endpoints | All POSTs use `Depends(require_admin)` — see `routers/{ingest,extract,backtest,evals}.py` |
| Admin/viewer role distinction | `backend/tenk_signal/auth.py:Role` enum + `_resolve_role` constant-time compare |
| Auth tests cover full matrix | `backend/tests/unit/test_auth.py` — 6 cases: missing, invalid, viewer-on-viewer, admin-on-viewer (superset), viewer-blocked-from-admin, admin-on-admin |
| Logs never emit secrets / PII | `backend/tenk_signal/logging.py:configure_logging` uses structlog; `SECRET_KEY` env vars are typed `SecretStr` so they print as `'**********'` |

## Code quality & testing

| Requirement | Satisfied by |
|---|---|
| Backend lint: ruff + mypy, zero warnings | `backend/pyproject.toml:[tool.ruff]` and `[tool.mypy]` with `strict = true`. CI gate at `.github/workflows/backend.yml` (ruff → format → mypy steps) |
| Frontend lint: eslint + `tsc --noEmit`, zero warnings | `frontend/.eslintrc.cjs`, `tsconfig.json:strict:true`. CI: `.github/workflows/frontend.yml` (ESLint with `--max-warnings=0` + tsc) |
| Unit tests on backtest math | `backend/tests/unit/test_backtest.py` — 12 cases |
| Unit tests on schema parsing | `backend/tests/unit/test_prompt.py:test_extraction_json_schema_matches_pydantic_model` |
| **No-look-ahead test** | `backend/tests/unit/test_backtest.py:test_no_lookahead_with_perfect_future_signal` — asserts `|mean_ret| < 0.003` under oracle signal |
| Eval metrics tests | `backend/tests/unit/test_evals.py` — 9 cases covering P/R/F1, MAE, edge cases |
| Integration tests against ephemeral DB | `backend/tests/e2e/test_pipeline_on_fixtures.py` — runs in CI's pg service |
| Mock Anthropic in ALL tests | `backend/tests/conftest.py:_no_live_anthropic` autouse fixture overrides `anthropic.Anthropic` to raise; `FixtureExtractor` injected via `app.dependency_overrides` |
| Dependency audits | `backend.yml` runs `pip-audit`; `frontend.yml` runs `npm audit --audit-level=high` |
| Committed lockfiles | `backend/uv.lock`, `frontend/package-lock.json` |
| Dependabot | `.github/dependabot.yml` — weekly for pip + npm + github-actions |
| No debug code (no print/console.log) | Ruff `T20` rule; pre-commit hook + CI |

## CI/CD & deployment

| Requirement | Satisfied by |
|---|---|
| PR runs lint → type-check → test → audit → build | `.github/workflows/backend.yml` and `.github/workflows/frontend.yml` |
| Migrations up/down tested in CI | `.github/workflows/migrations.yml` runs `upgrade head → downgrade base → upgrade head` on ephemeral pg |
| Migrations are expand/contract | Convention documented in `docs/ARCHITECTURE.md:§9`; reviewer-enforced |
| Env vars documented per environment | `.env.example` (all envs) + `docs/RUNBOOK.md` deploy section |
| Fail fast on missing env var | `backend/tenk_signal/config.py:Settings()` raises at startup |
| Backend host supports instant rollback | Render — covered in `docs/RUNBOOK.md` "Rollback procedures" |
| Frontend host supports instant rollback | Vercel — same |

## Observability & reliability

| Requirement | Satisfied by |
|---|---|
| Structured JSON logs | `backend/tenk_signal/logging.py:configure_logging` — structlog with `JSONRenderer` |
| Request-ID correlation | `backend/tenk_signal/middleware.py:RequestIDMiddleware` — generates or propagates `X-Request-ID`, sets contextvar consumed by `_inject_request_id` |
| Sentry backend | `backend/tenk_signal/observability.py:init_sentry` |
| Sentry frontend | (Configured via `NEXT_PUBLIC_SENTRY_DSN`; client init shipped) |
| Global error boundary frontend | Next.js App Router auto-handles via `error.tsx` (not customized; Next default is sufficient) |
| `/metrics` endpoint with Four Golden Signals | `backend/tenk_signal/observability.py:init_prometheus` exposes `http_request_duration_seconds` (latency), `http_requests_total` (traffic + errors), `http_requests_in_flight` (saturation) |
| Domain metric for EDGAR parser fallback | `backend/tenk_signal/services/edgar.py:_section_parse_total` Counter |
| Documented rollback for backend | `docs/RUNBOOK.md` |
| Documented rollback for frontend | `docs/RUNBOOK.md` |
| Documented rollback for DB (tested alembic downgrade) | `docs/RUNBOOK.md` + `.github/workflows/migrations.yml` runs both directions |
| Documented rollback trigger thresholds | `docs/RUNBOOK.md` "When to roll back" table |

## Cost & operational hygiene

| Requirement | Satisfied by |
|---|---|
| Anthropic cache by filing hash | `backend/tenk_signal/services/extractor.py:_cache_insert` uses `INSERT … ON CONFLICT DO NOTHING` against `UNIQUE(text_sha256, prompt_version, model)` |
| Model + token caps configurable | `ANTHROPIC_MODEL`, `EXTRACTION_MAX_TOKENS` env vars |
| Never call paid API in tests/CI | `backend/tests/conftest.py:_no_live_anthropic` guard; `FixtureExtractor` for tests |
| Ask before adding paid services | (Process discipline; logged in chat history) |

## Deliverables shipped

- [x] `PLAN.md` (architectural blueprint, approved Phase 0)
- [x] `/backend` (FastAPI + Alembic + uv) with 71 unit tests + e2e
- [x] `/frontend` (Next.js 14) with 9 vitest tests + Playwright config
- [x] Alembic migrations (initial schema, up/down both tested in CI)
- [x] Test suites (backend + frontend)
- [x] `.github/workflows` (backend, frontend, migrations, secret-scan)
- [x] `.env.example` + Dependabot + pre-commit
- [x] `README.md` — see project root
- [x] `docs/RUNBOOK.md` — this directory
- [x] `docs/ARCHITECTURE.md` — this directory
- [x] `docs/CHECKLIST.md` — this file
