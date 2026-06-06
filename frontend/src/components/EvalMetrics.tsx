import type { EvalsResponse } from "@/lib/client-api";

export function EvalMetrics({ data }: { data: EvalsResponse | null }) {
  if (!data || !data.latest) {
    return (
      <div style={{ opacity: 0.6, padding: 12, fontSize: 13 }}>
        no eval run yet — admin can POST /evals/run
      </div>
    );
  }
  const e = data.latest;
  return (
    <div>
      <div style={{ display: "flex", gap: 16, fontSize: 13, opacity: 0.85, marginBottom: 8 }}>
        <Metric label="n" v={e.n} digits={0} />
        <Metric label="Precision" v={e.guidance_precision} />
        <Metric label="Recall" v={e.guidance_recall} />
        <Metric label="F1" v={e.guidance_f1} />
        <Metric label="Sentiment MAE" v={e.sentiment_mae} />
      </div>
      <div style={{ fontSize: 11, opacity: 0.55 }}>
        Last run: {new Date(e.run_at).toLocaleString()} · model {e.model} · prompt {e.prompt_version}
      </div>
      {data.history.length > 1 ? (
        <div style={{ marginTop: 12 }}>
          <Sparkline values={data.history.map((h) => h.guidance_f1).reverse()} />
          <div style={{ fontSize: 11, opacity: 0.55, marginTop: 4 }}>
            F1 history ({data.history.length} runs)
          </div>
        </div>
      ) : null}
      <div
        style={{
          marginTop: 12,
          padding: 8,
          fontSize: 11,
          color: "#d0c47a",
          background: "#1f1c0e",
          border: "1px solid #3a3216",
          borderRadius: 4,
        }}
      >
        Heads-up: ground-truth labels are placeholders. See
        <code style={{ marginLeft: 4 }}>data/ground_truth.jsonl</code>.
      </div>
    </div>
  );
}

function Metric({ label, v, digits = 3 }: { label: string; v: number; digits?: number }) {
  return (
    <div>
      <span style={{ color: "#7a818b" }}>{label}: </span>
      <strong>{v.toFixed(digits)}</strong>
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length === 0) return null;
  const w = 200;
  const h = 32;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pts = values
    .map((v, i) => `${(i / Math.max(values.length - 1, 1)) * w},${h - ((v - min) / range) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h} role="img" aria-label="F1 history sparkline">
      <polyline points={pts} fill="none" stroke="#74d0c0" strokeWidth={1.5} />
    </svg>
  );
}
