import { useState } from "react";
import { RunTab } from "./components/RunTab";
import { MemoTab } from "./components/MemoTab";
import { EvalsTab } from "./components/EvalsTab";

// Three tabs (spec section 24): Run, Memo, Evals. No router needed.
// "The trace content is the aesthetics" — keep chrome minimal.
type Tab = "run" | "memo" | "evals";

const TABS: { id: Tab; label: string }[] = [
  { id: "run", label: "Run" },
  { id: "memo", label: "Memo" },
  { id: "evals", label: "Evals" },
];

export function App() {
  const [tab, setTab] = useState<Tab>("evals"); // demo opens on Evals (spec section 27)

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 1100, margin: "0 auto", padding: 16 }}>
      <header style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>Diligence Agent</h1>
        <span style={{ color: "#666", fontSize: 13 }}>
          FinanceBench-backed diligence memo generator — not a chatbot.
        </span>
      </header>

      <nav style={{ display: "flex", gap: 8, borderBottom: "1px solid #ddd", marginBottom: 16 }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: "8px 14px",
              border: "none",
              borderBottom: tab === t.id ? "2px solid #111" : "2px solid transparent",
              background: "none",
              fontWeight: tab === t.id ? 600 : 400,
              cursor: "pointer",
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "run" && <RunTab />}
      {tab === "memo" && <MemoTab />}
      {tab === "evals" && <EvalsTab />}
    </div>
  );
}
