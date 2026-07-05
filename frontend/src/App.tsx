import { useEffect, useState } from "react";
import { AgentTab } from "./components/AgentTab";
import { RunTab } from "./components/RunTab";
import { MemoTab } from "./components/MemoTab";
import { EvalsTab } from "./components/EvalsTab";
import { DocumentationTab } from "./components/DocumentationTab";

// Top-level tabs (v1-spec §1.9 / §12): Agent | RAG | Documentation, Agent default. The existing
// v0 Run/Memo/Evals tabs mount unchanged as sub-tabs under RAG. No router needed.
// Ledger redesign: sticky top bar, tab nav, theme toggle (auto/light/dark).
type TopTab = "agent" | "rag" | "documentation";
type RagTab = "run" | "memo" | "evals";
type Theme = "auto" | "light" | "dark";

const TOP_TABS: { id: TopTab; label: string }[] = [
  { id: "agent", label: "Agent" },
  { id: "rag", label: "RAG" },
  { id: "documentation", label: "Documentation" },
];

const RAG_TABS: { id: RagTab; label: string }[] = [
  { id: "run", label: "Run" },
  { id: "memo", label: "Memo" },
  { id: "evals", label: "Evals" },
];

const THEME_GLYPH: Record<Theme, string> = { auto: "◐", light: "○", dark: "◑" };
const THEME_NEXT: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };

export function App() {
  const [topTab, setTopTab] = useState<TopTab>("agent"); // Agent is the v1 default (§1.9)
  const [ragTab, setRagTab] = useState<RagTab>("evals"); // demo opens on Evals (spec section 27)
  const [memoRunId, setMemoRunId] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("da-theme") as Theme) || "auto",
  );

  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("da-theme", theme);
  }, [theme]);

  function openMemo(runId: string) {
    setMemoRunId(runId);
    setTopTab("rag");
    setRagTab("memo");
  }

  return (
    <div style={{ minHeight: "100vh" }}>
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 20,
          background: "var(--surface)",
          borderBottom: "1px solid var(--line)",
        }}
      >
        <header
          style={{
            maxWidth: 1240,
            margin: "0 auto",
            padding: "0 28px",
            height: 56,
            display: "flex",
            alignItems: "center",
            gap: 24,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 26,
                height: 26,
                borderRadius: 7,
                background: "var(--mark-bg)",
                color: "var(--mark-fg)",
                display: "grid",
                placeItems: "center",
                fontFamily: "var(--mono)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              D
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: -0.1 }}>DiliAgent</div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 500,
                color: "var(--text-2)",
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
                borderRadius: 20,
                padding: "2px 9px",
              }}
            >
              FinanceBench
            </div>
          </div>

          <nav style={{ display: "flex", gap: 4, marginLeft: 12 }}>
            {TOP_TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTopTab(t.id)}
                style={{
                  padding: "6px 13px",
                  border: "none",
                  borderRadius: 7,
                  background: topTab === t.id ? "var(--surface-2)" : "transparent",
                  color: topTab === t.id ? "var(--text)" : "var(--text-2)",
                  fontWeight: topTab === t.id ? 600 : 500,
                  fontSize: 13,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
            <button
              onClick={() => setTheme(THEME_NEXT[theme])}
              title={`Theme: ${theme}`}
              style={{
                width: 30,
                height: 30,
                border: "1px solid var(--line)",
                borderRadius: 7,
                background: "var(--surface)",
                color: "var(--text-2)",
                cursor: "pointer",
                fontSize: 13,
                display: "grid",
                placeItems: "center",
                fontFamily: "inherit",
              }}
            >
              {THEME_GLYPH[theme]}
            </button>
          </div>
        </header>

        {topTab === "rag" && (
          <div style={{ borderTop: "1px solid var(--line)" }}>
            <nav
              style={{
                maxWidth: 1240,
                margin: "0 auto",
                padding: "0 28px",
                height: 42,
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              {RAG_TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setRagTab(t.id)}
                  style={{
                    padding: "5px 11px",
                    border: "none",
                    borderRadius: 7,
                    background: ragTab === t.id ? "var(--surface-2)" : "transparent",
                    color: ragTab === t.id ? "var(--text)" : "var(--text-2)",
                    fontWeight: ragTab === t.id ? 600 : 500,
                    fontSize: 12.5,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  {t.label}
                </button>
              ))}
            </nav>
          </div>
        )}
      </div>

      <main style={{ maxWidth: 1240, margin: "0 auto", padding: "28px 28px 64px" }}>
        {topTab === "agent" && <AgentTab />}
        {topTab === "documentation" && <DocumentationTab />}
        {topTab === "rag" && ragTab === "run" && <RunTab onOpenMemo={openMemo} />}
        {topTab === "rag" && ragTab === "memo" && (
          <MemoTab runId={memoRunId} onSelectRun={setMemoRunId} />
        )}
        {topTab === "rag" && ragTab === "evals" && <EvalsTab />}
      </main>
    </div>
  );
}
