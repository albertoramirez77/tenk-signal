import type { SignalRow } from "@/lib/client-api";

export function SignalsTable({ rows }: { rows: SignalRow[] }) {
  if (rows.length === 0) {
    return <div style={{ opacity: 0.6 }}>no signals yet — run /ingest then /extract</div>;
  }
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead>
        <tr style={{ textAlign: "left", borderBottom: "1px solid #1f242b" }}>
          <Th>Ticker</Th>
          <Th>Filed</Th>
          <Th>Guidance</Th>
          <Th>Sentiment</Th>
          <Th>Confidence</Th>
          <Th>Signal</Th>
          <Th>Flag</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} style={{ borderBottom: "1px solid #11151a" }}>
            <Td>{r.ticker}</Td>
            <Td>{r.filed_at.slice(0, 10)}</Td>
            <Td>
              <span
                style={{
                  padding: "2px 8px",
                  borderRadius: 4,
                  background:
                    r.guidance === "raised"
                      ? "#1e3a2e"
                      : r.guidance === "lowered"
                      ? "#3a1e1e"
                      : "#2a2d33",
                  color:
                    r.guidance === "raised"
                      ? "#74d0c0"
                      : r.guidance === "lowered"
                      ? "#d07474"
                      : "#aab0b8",
                }}
              >
                {r.guidance}
              </span>
            </Td>
            <Td>{r.sentiment.toFixed(2)}</Td>
            <Td>{r.confidence.toFixed(2)}</Td>
            <Td>{r.signal_value.toFixed(3)}</Td>
            <Td>{r.quarantined ? "⚠ quarantined" : ""}</Td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th
      style={{
        padding: "8px 12px",
        color: "#7a818b",
        fontWeight: 500,
        fontSize: 12,
        textTransform: "uppercase",
      }}
    >
      {children}
    </th>
  );
}
function Td({ children }: { children: React.ReactNode }) {
  return <td style={{ padding: "8px 12px" }}>{children}</td>;
}
