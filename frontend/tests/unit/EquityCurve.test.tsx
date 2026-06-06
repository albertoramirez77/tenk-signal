import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EquityCurve } from "@/components/EquityCurve";

describe("EquityCurve", () => {
  it("shows empty state when no points", () => {
    render(<EquityCurve points={[]} />);
    expect(screen.getByText(/no equity curve/i)).toBeTruthy();
  });

  it("renders a chart container when points present", () => {
    render(
      <EquityCurve
        points={[
          { date: "2024-01-02", equity: 1.0 },
          { date: "2024-01-03", equity: 1.02 },
        ]}
      />,
    );
    // recharts wraps in ResponsiveContainer; just confirm we don't crash and
    // the empty-state copy is gone.
    expect(screen.queryByText(/no equity curve/i)).toBeNull();
  });
});
