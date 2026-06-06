/**
 * Browser-side fetch helpers. These hit /api/* on the same origin —
 * they NEVER call the FastAPI backend directly, and they NEVER see the
 * viewer key (it lives only in server-api.ts, server-side).
 */

export async function fetchJson<T>(path: string): Promise<T> {
  const resp = await fetch(path, { cache: "no-store" });
  if (!resp.ok) {
    throw new Error(`request to ${path} failed: ${resp.status}`);
  }
  return (await resp.json()) as T;
}

// ---------------------------------------------------------------------------
// Types matching backend/tenk_signal/schemas.py
// ---------------------------------------------------------------------------

export type SignalRow = {
  id: number;
  ticker: string;
  filed_at: string;
  active_from: string;
  signal_value: number;
  guidance: "raised" | "maintained" | "lowered";
  sentiment: number;
  confidence: number;
  quarantined: boolean;
};

export type SignalsResponse = { rows: SignalRow[] };

export type EquityPoint = { date: string; equity: number };

export type BacktestDetail = {
  id: number;
  created_at: string;
  config: {
    horizon_days: number;
    execution_lag_days: number;
    transaction_cost_bps: number;
    benchmark: string;
    walk_forward: boolean;
  };
  hit_rate: number | null;
  mean_ret: number | null;
  vol: number | null;
  sharpe: number | null;
  equity_curve: EquityPoint[];
};

export type BacktestSummary = Omit<BacktestDetail, "equity_curve">;

export type EvalSnapshot = {
  run_at: string;
  n: number;
  guidance_precision: number;
  guidance_recall: number;
  guidance_f1: number;
  sentiment_mae: number;
  prompt_version: string;
  model: string;
};

export type EvalsResponse = {
  latest: EvalSnapshot | null;
  history: EvalSnapshot[];
};
