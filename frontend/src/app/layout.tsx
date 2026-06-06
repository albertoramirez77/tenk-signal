import type { ReactNode } from "react";

export const metadata = {
  title: "TenK Signal",
  description: "Alpha-from-text dashboard",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
          background: "#0b0d10",
          color: "#e6e8eb",
        }}
      >
        <header
          style={{
            padding: "16px 24px",
            borderBottom: "1px solid #1f242b",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <strong>TenK Signal</strong>
          <span style={{ opacity: 0.6, fontSize: 12 }}>vertical slice</span>
        </header>
        <main style={{ padding: 24, maxWidth: 1100, margin: "0 auto" }}>
          {children}
        </main>
      </body>
    </html>
  );
}
