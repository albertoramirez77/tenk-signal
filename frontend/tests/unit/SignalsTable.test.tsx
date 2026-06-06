import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SignalsTable } from "@/components/SignalsTable";
import type { SignalRow } from "@/lib/client-api";

const ROW: SignalRow = {
  id: 1,
  ticker: "AAPL",
  filed_at: "2023-11-03T00:00:00Z",
  active_from: "2023-11-04T00:00:00Z",
  signal_value: 0.42,
  guidance: "raised",
  sentiment: 0.5,
  confidence: 0.8,
  quarantined: false,
};

describe("SignalsTable", () => {
  it("renders empty state with no rows", () => {
    render(<SignalsTable rows={[]} />);
    expect(screen.getByText(/no signals yet/i)).toBeTruthy();
  });

  it("renders a row with ticker, guidance, and numeric formatting", () => {
    render(<SignalsTable rows={[ROW]} />);
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("raised")).toBeTruthy();
    // sentiment 0.5 → "0.50", confidence 0.8 → "0.80", signal 0.42 → "0.420"
    expect(screen.getByText("0.50")).toBeTruthy();
    expect(screen.getByText("0.80")).toBeTruthy();
    expect(screen.getByText("0.420")).toBeTruthy();
  });

  it("shows quarantined flag when set", () => {
    render(<SignalsTable rows={[{ ...ROW, quarantined: true }]} />);
    expect(screen.getByText(/quarantined/i)).toBeTruthy();
  });
});
