"""One-off operator script: make a *real* Anthropic extraction call against
a recorded EDGAR fixture, validate the response, and save the result.

You pay for this call. The script:
  1. Loads the recorded fixture for the requested ticker.
  2. Counts input tokens via Anthropic's tokenizer.
  3. Prints an estimated cost (Sonnet 4.6 pricing as of 2026).
  4. Asks for confirmation (skippable with --yes).
  5. Calls the API with output_config.format (structured outputs, GA).
  6. Re-validates with Pydantic as belt-and-suspenders.
  7. Saves to tests/fixtures/anthropic/<text_sha256>.json so the
     FixtureExtractor can replay it forever after.

Usage::

    # Required env (same .env as the app):
    export ANTHROPIC_API_KEY=...
    export EDGAR_USER_AGENT="TenK Signal you@example.com"
    export DATABASE_URL=...
    export APP_API_KEY_ADMIN=...  APP_API_KEY_VIEWER=...

    # Live call against MSFT recorded fixture (default):
    cd backend
    uv run python -m scripts.extract_with_anthropic

    # Override ticker:
    uv run python -m scripts.extract_with_anthropic JPM

    # Skip the confirmation prompt (for scripting):
    uv run python -m scripts.extract_with_anthropic MSFT --yes
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from tenk_signal.config import get_settings
from tenk_signal.schemas import Extraction, extraction_json_schema
from tenk_signal.services.edgar import FixtureEdgarClient
from tenk_signal.services.prompt import (
    SYSTEM_PROMPT,
    build_user_message,
    contains_instruction_patterns,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Sonnet 4.6 pricing (USD per million tokens). Update if pricing changes —
# this is only for the cost preview, not billing.
PRICE_IN_PER_MTOK = 3.00
PRICE_OUT_PER_MTOK = 15.00


def _format_cost(tokens_in: int, tokens_out_max: int) -> str:
    cost_in = (tokens_in / 1_000_000) * PRICE_IN_PER_MTOK
    cost_out = (tokens_out_max / 1_000_000) * PRICE_OUT_PER_MTOK
    return (
        f"  input  : {tokens_in:>6,} tokens  ~ ${cost_in:.4f}\n"
        f"  output : ≤{tokens_out_max:>5,} tokens  ~ ${cost_out:.4f} (cap)\n"
        f"  total  : up to ${cost_in + cost_out:.4f}"
    )


async def _run(ticker: str, skip_confirm: bool, overwrite: bool) -> int:
    settings = get_settings()

    # 1) Load the recorded fixture for this ticker.
    client = FixtureEdgarClient(FIXTURES / "edgar")
    filings = await client.fetch_recent(ticker.upper(), ["10-K"], limit=1)
    if not filings:
        print(f"ERROR: no recorded fixture for {ticker}", file=sys.stderr)
        print(
            "Available fixtures:",
            sorted(p.stem.split("_")[0] for p in (FIXTURES / "edgar").glob("*.meta.json")),
            file=sys.stderr,
        )
        return 2
    f = filings[0]
    print(
        f"Fixture: {f.ticker} 10-K accession={f.accession_no} "
        f"filed={f.filed_at.date()} period_end={f.period_end} "
        f"mode={f.section_extraction_mode.value} text_len={len(f.text):,}"
    )
    print(f"text_sha256: {f.text_sha256}")
    out_path = FIXTURES / "anthropic" / f"{f.text_sha256}.json"
    if out_path.exists() and not overwrite:
        print(f"\nWARN: fixture already exists at {out_path}")
        print("Pass --overwrite to replace it. Aborting.")
        return 3

    # Injection guard — surfaces unexpected jailbreak attempts in real text.
    hits = contains_instruction_patterns(f.text)
    if hits:
        print(f"\nWARN: injection-pattern guard matched: {hits}")
        print(
            "Proceeding (LLM will see the FILING tag wrapper); the Filing "
            "row would be marked quarantined in production."
        )

    # 2) Lazy-import the SDK and count tokens for cost preview.
    import anthropic

    aclient = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    user_msg = build_user_message(f.text)
    try:
        ct = await aclient.messages.count_tokens(
            model=settings.anthropic_model,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        tokens_in = ct.input_tokens
    except Exception as exc:
        print(f"NOTE: count_tokens failed ({exc}); using char/4 estimate")
        tokens_in = (len(SYSTEM_PROMPT) + len(user_msg)) // 4

    print(f"\nModel: {settings.anthropic_model}")
    print("Cost estimate (Sonnet 4.6 list price):")
    print(_format_cost(tokens_in, settings.extraction_max_tokens))

    # 3) Confirm.
    if not skip_confirm:
        print("\nProceed with the API call? [y/N]: ", end="", flush=True)
        line = sys.stdin.readline().strip().lower()
        if line not in {"y", "yes"}:
            print("Aborted.")
            return 1

    # 4) Make the call.
    print("\nCalling Anthropic...")
    t0 = dt.datetime.now(dt.UTC)
    try:
        resp = await aclient.messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.extraction_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": extraction_json_schema(),
                }
            },
        )
    except Exception as exc:
        print(f"ERROR: API call failed: {exc}", file=sys.stderr)
        return 4
    finally:
        await aclient.close()
    elapsed = (dt.datetime.now(dt.UTC) - t0).total_seconds()

    # 5) Parse + validate.
    raw_text = resp.content[0].text if resp.content else ""
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Usage: input={resp.usage.input_tokens} output={resp.usage.output_tokens}")
    # Use real usage for the real cost.
    real_cost_in = (resp.usage.input_tokens / 1_000_000) * PRICE_IN_PER_MTOK
    real_cost_out = (resp.usage.output_tokens / 1_000_000) * PRICE_OUT_PER_MTOK
    print(f"Real cost: ${real_cost_in + real_cost_out:.4f}")

    try:
        raw_json = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"ERROR: model returned non-JSON despite output_config: {exc}")
        print(f"Raw: {raw_text!r}")
        return 5

    try:
        extraction = Extraction.model_validate(raw_json)
    except Exception as exc:
        print(f"ERROR: response failed Pydantic validation: {exc}")
        print(f"Raw JSON: {raw_json}")
        return 6

    # 6) Pretty-print result.
    print("\n--- Extraction ---")
    print(f"  guidance       : {extraction.guidance}")
    print(f"  sentiment      : {extraction.sentiment:+.3f}")
    print(f"  confidence     : {extraction.confidence:.3f}")
    print(f"  risk_flag_count: {extraction.risk_flag_count}")
    print(
        f"  rationale      : {extraction.rationale[:160]}"
        f"{'…' if len(extraction.rationale) > 160 else ''}"
    )

    # 7) Save.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(raw_json, indent=2) + "\n")
    print(f"\nSaved: {out_path.relative_to(FIXTURES.parent.parent)}")
    print(f"FixtureExtractor will replay this for sha={f.text_sha256[:12]}…")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "ticker",
        nargs="?",
        default="MSFT",
        help="Ticker symbol (must have a recorded EDGAR fixture). Default MSFT.",
    )
    p.add_argument("--yes", action="store_true", help="Skip the cost-confirmation prompt.")
    p.add_argument(
        "--overwrite", action="store_true", help="Replace an existing fixture if present."
    )
    args = p.parse_args()
    sys.exit(asyncio.run(_run(args.ticker, args.yes, args.overwrite)))


if __name__ == "__main__":
    main()
