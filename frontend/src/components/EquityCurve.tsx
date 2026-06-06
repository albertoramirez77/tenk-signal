"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { EquityPoint } from "@/lib/client-api";

export function EquityCurve({ points }: { points: EquityPoint[] }) {
  if (points.length === 0) {
    return (
      <div style={{ opacity: 0.6, padding: 24 }}>
        no equity curve yet — run a backtest to populate
      </div>
    );
  }
  return (
    <div style={{ width: "100%", height: 320 }}>
      <ResponsiveContainer>
        <LineChart data={points}>
          <CartesianGrid stroke="#1f242b" />
          <XAxis dataKey="date" stroke="#7a818b" tick={{ fontSize: 11 }} />
          <YAxis stroke="#7a818b" tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{ background: "#11151a", border: "1px solid #1f242b" }}
            labelStyle={{ color: "#e6e8eb" }}
          />
          <Line
            type="monotone"
            dataKey="equity"
            stroke="#74d0c0"
            dot={false}
            strokeWidth={2}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
