"use client";

import { useBackendHealth } from "@/lib/useBackendHealth";

export default function Home() {
  const result = useBackendHealth();

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "3rem" }}>
      <h1>TenderPilot DE</h1>
      <p style={{ color: "#666" }}>
        Development connectivity check — rechecked automatically every 5 seconds.
      </p>

      <section
        style={{
          marginTop: "1.5rem",
          padding: "1rem",
          borderRadius: "8px",
          border: "1px solid #ddd",
          maxWidth: "32rem",
        }}
      >
        {result === null && <p>Checking backend connection…</p>}

        {result?.status === "connected" && (
          <div>
            <p style={{ color: "#0a7d2c", fontWeight: 600 }}>
              ✓ Connected to backend
            </p>
            <pre
              style={{
                background: "#f6f6f6",
                padding: "0.75rem",
                borderRadius: "4px",
                overflowX: "auto",
                fontSize: "0.85rem",
              }}
            >
              {JSON.stringify(result.backend, null, 2)}
            </pre>
          </div>
        )}

        {result?.status === "unavailable" && (
          <div>
            <p style={{ color: "#b00020", fontWeight: 600 }}>
              ✗ Backend unavailable
            </p>
            <p style={{ fontSize: "0.9rem", color: "#666" }}>{result.message}</p>
          </div>
        )}
      </section>
    </main>
  );
}
