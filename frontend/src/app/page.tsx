import { EquityCurve } from "@/components/EquityCurve";
import { EvalMetrics } from "@/components/EvalMetrics";
import { SignalsTable } from "@/components/SignalsTable";
import { fetchBackend } from "@/lib/server-api";
import type {
  BacktestDetail,
  EvalsResponse,
  SignalsResponse,
} from "@/lib/client-api";

export const dynamic = "force-dynamic";

async function loadDashboard(): Promise<{
  signals: SignalsResponse;
  latest: BacktestDetail | null;
  evals: EvalsResponse | null;
}> {
  try {
    const signals = await fetchBackend<SignalsResponse>("/signals");
    const runs = await fetchBackend<{ id: number }[]>("/backtest");
    let latest: BacktestDetail | null = null;
    if (runs.length > 0) {
      latest = await fetchBackend<BacktestDetail>(`/backtest/${runs[0].id}`);
    }
    let evals: EvalsResponse | null = null;
    try {
      evals = await fetchBackend<EvalsResponse>("/evals");
    } catch {
      evals = null;
    }
    return { signals, latest, evals };
  } catch {
    return { signals: { rows: [] }, latest: null, evals: null };
  }
}

function fmt(v: number | null, digits = 3): string {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

export default async function Page() {
  const { signals, latest, evals } = await loadDashboard();
  return (
    <div style={{ display: "grid", gap: 32 }}>
      <section>
        <h2 style={{ margin: "0 0 8px" }}>Equity curve</h2>
        <div style={{ display: "flex", gap: 16, fontSize: 13, opacity: 0.85 }}>
          <Metric label="Hit rate" v={latest?.hit_rate ?? null} />
          <Metric label="Mean ret" v={latest?.mean_ret ?? null} />
          <Metric label="Vol" v={latest?.vol ?? null} />
          <Metric label="Sharpe" v={latest?.sharpe ?? null} />
        </div>
        <div style={{ marginTop: 12 }}>
          <EquityCurve points={latest?.equity_curve ?? []} />
        </div>
      </section>

      <section>
        <h2 style={{ margin: "0 0 8px" }}>Extraction eval</h2>
        <EvalMetrics data={evals} />
      </section>

      <section>
        <h2 style={{ margin: "0 0 8px" }}>Signals</h2>
        <SignalsTable rows={signals.rows} />
      </section>
    </div>
  );
}

function Metric({ label, v }: { label: string; v: number | null }) {
  return (
    <div>
      <span style={{ color: "#7a818b" }}>{label}: </span>
      <strong>{v === null ? "—" : v.toFixed(3)}</strong>
    </div>
  );
}
