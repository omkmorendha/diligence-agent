// Memo tab — Ledger redesign. Same wiring as before (GET /runs for the picker,
// getMemo, getPage for the citation panel with char_start/char_end highlighting).
// Rendering changes only: memo header with mono meta row, numbered item cards,
// chip-style citation markers, refined sticky source panel.

import { useEffect, useState } from "react";
import { ApiError, getMemo, getPage, listRuns } from "../api";
import type { Citation, Memo, MemoItem, PageResponse, RunCard } from "../types";
import { Card, MONO, Pill, selectStyle } from "../ui";

const STATUS_COLOR: Record<MemoItem["status"], [string, string]> = {
  answered: ["var(--green)", "var(--green-soft)"],
  abstained: ["var(--amber)", "var(--amber-soft)"],
};

function CitationMarker({
  index,
  citation,
  active,
  onOpen,
}: {
  index: number;
  citation: Citation;
  active: boolean;
  onOpen: (c: Citation) => void;
}) {
  return (
    <button
      onClick={() => onOpen(citation)}
      title={`${citation.doc_name} p${citation.pdf_page}`}
      style={{
        fontFamily: MONO,
        fontSize: 11,
        fontWeight: 600,
        color: "var(--accent-text)",
        background: active ? "var(--accent-soft)" : "transparent",
        border: `1px solid ${active ? "var(--accent-line)" : "var(--line-strong)"}`,
        borderRadius: 5,
        cursor: "pointer",
        padding: "1px 6px",
        verticalAlign: 2,
        marginLeft: 4,
      }}
    >
      {index + 1}
    </button>
  );
}

function MemoItemCard({
  item,
  index,
  openCitation,
  onOpenCitation,
}: {
  item: MemoItem;
  index: number;
  openCitation: Citation | null;
  onOpenCitation: (c: Citation) => void;
}) {
  const [statusColor, statusBg] = STATUS_COLOR[item.status];
  return (
    <Card style={{ padding: "18px 20px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
          <span style={{ fontFamily: MONO, fontSize: 12, color: "var(--text-3)" }}>{String(index + 1).padStart(2, "0")}</span>
          <span style={{ fontSize: 14.5, fontWeight: 600, lineHeight: 1.45 }}>{item.question}</span>
        </div>
        <Pill color={statusColor} bg={statusBg} style={{ alignSelf: "flex-start" }}>
          {item.status}
        </Pill>
      </div>

      <div style={{ fontSize: 14, lineHeight: 1.6, color: "var(--text)", marginBottom: 12 }}>
        {item.answer}
        {item.citations.map((c, i) => (
          <CitationMarker
            key={c.citation_id}
            index={i}
            citation={c}
            active={openCitation?.citation_id === c.citation_id}
            onOpen={onOpenCitation}
          />
        ))}
      </div>

      <div style={{ display: "flex", gap: 18, fontSize: 12, color: "var(--text-3)", fontFamily: MONO, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        {item.value != null && (
          <span style={{ color: "var(--text-2)" }}>
            value {item.value} {item.unit}
          </span>
        )}
        <span>
          {item.confidence.grounded_inputs} grounded · {item.confidence.assumed_inputs} assumed
        </span>
      </div>
    </Card>
  );
}

function HighlightedPage({ page, citation }: { page: PageResponse; citation: Citation }) {
  const start = Math.max(0, Math.min(citation.char_start, page.text.length));
  const end = Math.max(start, Math.min(citation.char_end, page.text.length));
  const before = page.text.slice(Math.max(0, start - 200), start);
  const match = page.text.slice(start, end);
  const after = page.text.slice(end, end + 200);
  return (
    <pre
      style={{
        margin: 0,
        fontSize: 12,
        lineHeight: 1.55,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        maxHeight: 240,
        overflowY: "auto",
        fontFamily: "inherit",
        color: "var(--text-2)",
        background: "var(--surface-2)",
        border: "1px solid var(--line)",
        borderRadius: 8,
        padding: "10px 12px",
      }}
    >
      {start > 200 && "… "}
      {before}
      <mark style={{ background: "var(--accent-soft)", color: "var(--accent-text)", padding: "0 1px", borderRadius: 2 }}>
        {match || "(span out of range)"}
      </mark>
      {after}
      {end + 200 < page.text.length && " …"}
    </pre>
  );
}

function CitationPanel({ company, citation, onClose }: { company: string; citation: Citation; onClose: () => void }) {
  const [page, setPage] = useState<PageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setPage(null);
    setError(null);
    setLoading(true);
    getPage(company, citation.doc_id, citation.pdf_page)
      .then(setPage)
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [company, citation.doc_id, citation.pdf_page]);

  return (
    <aside
      style={{
        minWidth: 0,
        position: "sticky",
        top: 84,
        alignSelf: "flex-start",
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12,
        boxShadow: "var(--shadow)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "12px 16px",
          borderBottom: "1px solid var(--line)",
          background: "var(--surface-2)",
        }}
      >
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: 0.7 }}>
          Source evidence
        </span>
        <button
          onClick={onClose}
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-3)", fontSize: 15, lineHeight: 1, padding: 2, fontFamily: "inherit" }}
        >
          ×
        </button>
      </div>
      <div style={{ padding: 16 }}>
        <div style={{ fontFamily: MONO, fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>{citation.doc_name}</div>
        <div style={{ fontSize: 12, color: "var(--text-3)", margin: "3px 0 12px" }}>
          {citation.doc_type ? `${citation.doc_type.toUpperCase()} · ` : ""}
          {citation.filing_period ? `${citation.filing_period} · ` : ""}
          page {citation.page_label ?? citation.pdf_page}
        </div>
        {citation.claim && <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--text)", marginBottom: 10 }}>{citation.claim}</div>}
        <div style={{ borderLeft: "2px solid var(--accent)", padding: "2px 0 2px 12px", fontFamily: MONO, fontSize: 12, color: "var(--text-2)", marginBottom: 14 }}>
          “{citation.quote}”
        </div>

        <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: 0.7, marginBottom: 6 }}>
          Page text
        </div>
        {loading && <div style={{ fontSize: 12, color: "var(--text-3)" }}>Loading page…</div>}
        {error && <div style={{ fontSize: 12, color: "var(--red)" }}>Failed to load page: {error}</div>}
        {page && <HighlightedPage page={page} citation={citation} />}

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text-3)", marginTop: 10, wordBreak: "break-all" }}>
          GET /corpus/{company}/{citation.doc_id}/page/{citation.pdf_page}
        </div>
      </div>
    </aside>
  );
}

export function MemoTab({ runId, onSelectRun }: { runId: string | null; onSelectRun: (runId: string) => void }) {
  const [runs, setRuns] = useState<RunCard[]>([]);
  const [memo, setMemo] = useState<Memo | null>(null);
  const [memoState, setMemoState] = useState<"idle" | "pending" | "missing" | "failed" | "error">("idle");
  const [memoError, setMemoError] = useState<string | null>(null);
  const [openCitation, setOpenCitation] = useState<Citation | null>(null);

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch(() => {
        /* run picker is best-effort */
      });
  }, []);

  useEffect(() => {
    setOpenCitation(null);
    if (!runId) {
      setMemo(null);
      setMemoState("idle");
      return;
    }
    setMemoState("idle");
    setMemo(null);
    getMemo(runId)
      .then((result) => {
        if (result.kind === "ready") {
          setMemo(result.memo);
          setMemoState("idle");
        } else if (result.kind === "pending") {
          setMemoState("pending");
        } else if (result.kind === "missing") {
          setMemoState("missing");
        } else {
          setMemoState("failed");
          setMemoError(result.error);
        }
      })
      .catch((err) => {
        setMemoState("error");
        setMemoError(err instanceof ApiError ? err.message : String(err));
      });
  }, [runId]);

  const emptyStateStyle = {
    fontSize: 13,
    color: "var(--text-3)",
    padding: "32px 0",
    textAlign: "center" as const,
    border: "1px dashed var(--line-strong)",
    borderRadius: 12,
  };

  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: memo && openCitation ? "minmax(0,1fr) 340px" : "minmax(0,1fr)",
        gap: 28,
      }}
    >
      <div style={{ minWidth: 0, maxWidth: 760 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
          <select value={runId ?? ""} onChange={(e) => onSelectRun(e.target.value)} style={{ ...selectStyle, minWidth: 280 }}>
            <option value="" disabled>
              Select a run…
            </option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.company} · {r.run_id} ({r.status})
              </option>
            ))}
          </select>
        </div>

        {!runId && <div style={emptyStateStyle}>Pick a run, or start one from the Run tab.</div>}
        {runId && memoState === "pending" && (
          <div style={{ ...emptyStateStyle, color: "var(--accent-text)" }}>Run still in progress — memo not ready yet.</div>
        )}
        {runId && memoState === "missing" && <div style={{ ...emptyStateStyle, color: "var(--red)" }}>No memo found for this run.</div>}
        {runId && memoState === "failed" && <div style={{ ...emptyStateStyle, color: "var(--red)" }}>Run failed: {memoError}</div>}
        {runId && memoState === "error" && <div style={{ ...emptyStateStyle, color: "var(--red)" }}>Failed to load memo: {memoError}</div>}

        {memo && (
          <>
            <h2 style={{ fontSize: 22, fontWeight: 700, letterSpacing: -0.3, margin: "0 0 6px" }}>{memo.company} diligence memo</h2>
            <div style={{ display: "flex", gap: 16, fontSize: 12.5, color: "var(--text-2)", marginBottom: 22, fontFamily: MONO }}>
              <span>
                {memo.summary.items_answered}/{memo.summary.items_total} answered
              </span>
              <span>{memo.summary.citations_total} citations</span>
              <span>{memo.summary.calculate_calls} calculator calls</span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {memo.items.map((item, i) => (
                <MemoItemCard key={item.item_id} item={item} index={i} openCitation={openCitation} onOpenCitation={setOpenCitation} />
              ))}
            </div>
          </>
        )}
      </div>

      {memo && openCitation && <CitationPanel company={memo.company} citation={openCitation} onClose={() => setOpenCitation(null)} />}
    </section>
  );
}
