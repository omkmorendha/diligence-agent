// Agent tab — DiliAgent document-review pipeline (v1-spec §12). This wave ships a
// self-contained functional placeholder: a dropzone-styled panel and the corpus-
// coverage hint, wired to no backend yet ("coming online"). A later agent replaces
// the internals with the upload → SSE → report → download flow, so everything stays
// local to this file.

import { useState } from "react";
import { Card, SectionLabel } from "../ui";

const ACCEPT = ".pdf,.docx,.md";

// The 11-company FinanceBench corpus and the fiscal years covered per company
// (derived from data/subset.json gold evidence). Shown so users know what is
// verifiable before they upload — an out-of-corpus memo yields only OUT_OF_SCOPE.
const CORPUS: { company: string; periods: string }[] = [
  { company: "Adobe", periods: "FY2015–17, FY2022" },
  { company: "AMD", periods: "FY2015, FY2022" },
  { company: "Best Buy", periods: "FY2017, FY2019, FY2023" },
  { company: "Boeing", periods: "FY2018, FY2022" },
  { company: "General Mills", periods: "FY2019, FY2020, FY2022" },
  { company: "Johnson & Johnson", periods: "FY2022, FY2023" },
  { company: "MGM Resorts", periods: "FY2018, FY2020, FY2022" },
  { company: "Nike", periods: "FY2018, FY2019, FY2021, FY2023" },
  { company: "PepsiCo", periods: "FY2022, FY2023" },
  { company: "Pfizer", periods: "FY2021" },
  { company: "Verizon", periods: "FY2021, FY2022" },
];

export function AgentTab() {
  const [dragging, setDragging] = useState(false);

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", display: "grid", gap: 20 }}>
      <div style={{ display: "grid", gap: 6 }}>
        <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: -0.2 }}>Review a document</div>
        <div style={{ fontSize: 14, color: "var(--text-2)", lineHeight: 1.5 }}>
          Upload a draft diligence document. DiliAgent extracts every material claim and
          verifies it against the FinanceBench filing corpus, then returns your document
          annotated with verdicts and citations.
        </div>
      </div>

      <Card style={{ padding: 4 }}>
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
          }}
          style={{
            display: "grid",
            placeItems: "center",
            gap: 10,
            padding: "44px 24px",
            borderRadius: 10,
            border: `1.5px dashed ${dragging ? "var(--accent-line)" : "var(--line-strong)"}`,
            background: dragging ? "var(--accent-soft)" : "var(--surface-2)",
            textAlign: "center",
            cursor: "not-allowed",
            transition: "background 120ms, border-color 120ms",
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              display: "grid",
              placeItems: "center",
              background: "var(--surface)",
              border: "1px solid var(--line)",
              color: "var(--text-3)",
              fontSize: 18,
            }}
          >
            ↑
          </div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Drag a document here, or click to browse</div>
          <div style={{ fontSize: 12, color: "var(--text-3)" }}>
            PDF, DOCX, or Markdown · up to 20 MB · one document per review
          </div>
          {/* Disabled until the review backend is wired in a later wave. */}
          <input type="file" accept={ACCEPT} disabled hidden />
        </label>
      </Card>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 14px",
          borderRadius: 10,
          background: "var(--amber-soft)",
          border: "1px solid var(--line)",
          fontSize: 12.5,
          color: "var(--amber)",
          fontWeight: 500,
        }}
      >
        <span style={{ fontSize: 14 }}>◷</span>
        Review pipeline is coming online — uploads are not yet accepted.
      </div>

      <Card style={{ padding: 20 }}>
        <SectionLabel style={{ marginBottom: 8 }}>Corpus coverage</SectionLabel>
        <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.5, marginBottom: 16 }}>
          Claims are only verifiable for these 11 companies and fiscal years. Anything
          outside this corpus is reported as out of scope, not incorrect.
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: 10,
          }}
        >
          {CORPUS.map((c) => (
            <div
              key={c.company}
              style={{
                display: "grid",
                gap: 2,
                padding: "10px 12px",
                borderRadius: 9,
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600 }}>{c.company}</div>
              <div style={{ fontSize: 11.5, color: "var(--text-3)", fontFamily: "var(--mono)" }}>
                {c.periods}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
