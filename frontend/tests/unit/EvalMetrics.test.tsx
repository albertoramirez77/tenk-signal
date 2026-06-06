import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvalMetrics } from "@/components/EvalMetrics";
import type { EvalSnapshot, EvalsResponse } from "@/lib/client-api";

const SNAP: EvalSnapshot = {
  run_at: "2026-03-01T12:00:00Z",
  n: 6,
  guidance_precision: 0.778,
  guidance_recall: 0.667,
  guidance_f1: 0.714,
  sentiment_mae: 0.123,
  prompt_version: "v1",
  model: "claude-sonnet-4-6",
};

describe("EvalMetrics", () => {
  it("renders empty state with null data", () => {
    render(<EvalMetrics data={null} />);
    expect(screen.getByText(/no eval run yet/i)).toBeTruthy();
  });

  it("renders empty state with null latest", () => {
    const data: EvalsResponse = { latest: null, history: [] };
    render(<EvalMetrics data={data} />);
    expect(screen.getByText(/no eval run yet/i)).toBeTruthy();
  });

  it("renders metrics when latest present", () => {
    const data: EvalsResponse = { latest: SNAP, history: [SNAP] };
    render(<EvalMetrics data={data} />);
    expect(screen.getByText("0.778")).toBeTruthy(); // precision
    expect(screen.getByText("0.714")).toBeTruthy(); // f1
    expect(screen.getByText("0.123")).toBeTruthy(); // sentiment MAE
    expect(screen.getByText(/placeholder/i)).toBeTruthy(); // warning banner
  });

  it("renders sparkline when >1 history entries", () => {
    const data: EvalsResponse = {
      latest: SNAP,
      history: [SNAP, { ...SNAP, guidance_f1: 0.6 }, { ...SNAP, guidance_f1: 0.5 }],
    };
    render(<EvalMetrics data={data} />);
    expect(screen.getByRole("img", { name: /sparkline/i })).toBeTruthy();
  });
});
