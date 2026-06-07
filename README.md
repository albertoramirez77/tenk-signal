# TenK Signal

End-to-end pipeline that reads MD&A + Risk Factors sections from SEC
filings, extracts a structured signal via Claude with constrained-decoding
JSON outputs, backtests against forward returns with point-in-time
correctness, and evaluates LLM extraction quality against a ground-truth
set.

[`PLAN.md`](PLAN.md) is the architectural blueprint.
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explains the data flow and
the load-bearing safety properties (point-in-time, prompt-injection
defense, cache contract). [`docs/RUNBOOK.md`](docs/RUNBOOK.md) covers
rollback + incident response. [`docs/CHECKLIST.md`](docs/CHECKLIST.md)
maps every production-readiness requirement to the file that satisfies it.

## Repo layout

```
backend/   FastAPI + SQLAlchemy + Alembic + uv  (Python 3.12)
frontend/  Next.js 14 (App Router) + Recharts   (TypeScript)
docs/      ARCHITECTURE.md, RUNBOOK.md, CHECKLIST.md
.github/   workflows: backend, frontend, migrations, secret-scan
data/      ground_truth.jsonl (placeholder labels — see warning in file)
```

## Local setup

### 1. Prereqs

- Python 3.12 (installed by `uv` automatically).
- Node.js 20.
- A Postgres reachable locally (Supabase free tier works, or
  `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`).
- [`uv`](https://docs.astral.sh/uv/):
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
  ```

### 2. Backend

```bash
cd backend
uv sync --all-extras
cp ../.env.example ../.env
```

Now edit `../.env` with real values. Then:

```bash
set -a; source ../.env; set +a
uv run alembic upgrade head
uv run pytest -q
```

> **Important when editing `.env`**: any value with spaces must be quoted
> (e.g. `EDGAR_USER_AGENT="TenK Signal me@example.com"`). The shipped
> `.env.example` already does this. An unquoted space causes `source` to
> fail silently for every variable below the bad line.

### 3. Frontend

```bash
cd frontend
npm install
npm run typecheck
npm run test
```

### 4. Clickable demo (no Anthropic credits required)

In `backend/` with `.env` loaded and Postgres up:

```bash
uv run python -m scripts.seed_demo
uv run uvicorn tenk_signal.main:app --reload --port 8000
```

In a second terminal, start the dashboard:

```bash
cd frontend
set -a; source ../.env; set +a
BACKEND_API_URL=http://localhost:8000 \
APP_API_KEY_VIEWER_SERVER="$APP_API_KEY_VIEWER" \
  npm run dev
```

Open <http://localhost:3000> — you'll see the AAPL equity curve, eval
metrics, and signals table.

> **Don't put `#` comments after commands in zsh.** macOS zsh doesn't
> treat `#` as a comment in interactive mode by default, so
> `npm install # one time` tries to install packages literally named
> `#`, `one`, and `time`. Put comments on their own lines.

### 5. Optional: one real Anthropic call

`scripts/extract_with_anthropic.py` makes a single live call against a
recorded EDGAR fixture, shows the cost estimate, asks for confirmation,
and saves the response as a fixture so future runs replay for free.
~$0.05 per call.

```bash
cd backend
export ANTHROPIC_API_KEY='sk-ant-...'
uv run python -m scripts.extract_with_anthropic MSFT   # default
# or: JPM / JNJ / XOM / KO
```

## Running tests

```bash
cd backend && uv run pytest -q
cd frontend && npm test
```

CI runs both on every PR plus a migrations-roundtrip check and a gitleaks
secret scan. See `.github/workflows/`.

## Deploy

Both targets (Render for backend, Vercel for frontend) support instant
rollback. See [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Render env vars: every var from `.env.example`.
Vercel env vars: `BACKEND_API_URL` and `APP_API_KEY_VIEWER_SERVER`
(server-side, **no `NEXT_PUBLIC_` prefix**) plus optional
`NEXT_PUBLIC_SENTRY_DSN`.
