// Memo tab (spec section 24, Step 15).
// Renders the memo with inline citation markers. Clicking a citation opens a side
// panel that calls GET /corpus/{company}/{doc_id}/page/{n} and highlights the
// cited span within the full page text. Per-item confidence shown as
// "2 inputs grounded / 0 assumed". Item statuses: answered / abstained / error.

import { useEffect, useState } from "react";
import { ApiError, getMemo, getPage, listRuns } from "../api";
import type { Citation, Memo, MemoItem, PageResponse, RunCard } from "../types";

const STATUS_COLOR: Record<MemoItem["status"], string> = {
  answered: "#1c7a3c",
  abstained: "#b3391f",
};

function StatusPill({ status }: { status: MemoItem["status"] }) {
  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 700,
        color: "#fff",
        background: STATUS_COLOR[status],
        borderRadius: 10,
        padding: "1px 8px",
        textTransform: "capitalize",
      }}
    >
      {status}
    </span>
  );
}

function CitationMarker({ index, citation, onOpen }: { index: number; citation: Citation; onOpen: (c: Citation) => void }) {
  return (
    <sup>
      <button
        onClick={() => onOpen(citation)}
        title={`${citation.doc_name} p${citation.pdf_page}`}
        style={{
          fontSize: 11,
          fontWeight: 700,
          color: "#0a7ea4",
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: "0 1px",
        }}
      >
        [{index + 1}]
      </button>
    </sup>
  );
}

function MemoItemCard({ item, onOpenCitation }: { item: MemoItem; onOpenCitation: (c: Citation) => void }) {
  return (
    <div
      style={{
        border: "1px solid #e5e5e5",
        borderRadius: 6,
        padding: 14,
        marginBottom: 12,
        background: "#fafafa",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{item.question}</div>
        <StatusPill status={item.status} />
      </div>

      <div style={{ fontSize: 14, marginBottom: 8 }}>
        {item.answer}
        {item.citations.map((c, i) => (
          <CitationMarker key={c.citation_id} index={i} citation={c} onOpen={onOpenCitation} />
        ))}
      </div>

      <div style={{ display: "flex", gap: 14, fontSize: 12, color: "#666" }}>
        {item.value != null && (
          <span>
            value: <strong>{item.value}</strong> {item.unit}
          </span>
        )}
        <span>
          {item.confidence.grounded_inputs} inputs grounded / {item.confidence.assumed_inputs} assumed
        </span>
      </div>
    </div>
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
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        maxHeight: 260,
        overflowY: "auto",
        fontFamily: "inherit",
      }}
    >
      {start > 200 && "… "}
      {before}
      <mark style={{ background: "#ffe9a8", padding: "0 1px" }}>{match || "(span out of range)"}</mark>
      {after}
      {end + 200 < page.text.length && " …"}
    </pre>
  );
}

function CitationPanel({
  company,
  citation,
  onClose,
}: {
  company: string;
  citation: Citation;
  onClose: () => void;
}) {
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
        width: 340,
        flexShrink: 0,
        border: "1px solid #e5e5e5",
        borderRadius: 6,
        padding: 14,
        background: "#fff",
        alignSelf: "flex-start",
        position: "sticky",
        top: 16,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h3 style={{ fontSize: 13, margin: 0 }}>Source</h3>
        <button
          onClick={onClose}
          style={{ background: "none", border: "none", cursor: "pointer", color: "#888", fontSize: 13 }}
        >
          close
        </button>
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, marginTop: 8 }}>{citation.doc_name}</div>
      <div style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>
        {citation.doc_type ? `${citation.doc_type} · ` : ""}
        {citation.filing_period ? `${citation.filing_period} · ` : ""}
        page {citation.page_label ?? citation.pdf_page}
      </div>
      {citation.claim && <div style={{ fontSize: 13, marginBottom: 8 }}>{citation.claim}</div>}
      <blockquote
        style={{
          margin: "0 0 10px",
          padding: "6px 10px",
          borderLeft: "3px solid #0a7ea4",
          background: "#eef6fa",
          fontSize: 13,
          color: "#222",
        }}
      >
        {citation.quote}
      </blockquote>

      <div style={{ fontSize: 11, color: "#999", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.4 }}>
        Page text
      </div>
      {loading && <div style={{ fontSize: 12, color: "#888" }}>Loading page…</div>}
      {error && <div style={{ fontSize: 12, color: "#c0392b" }}>Failed to load page: {error}</div>}
      {page && <HighlightedPage page={page} citation={citation} />}

      <div style={{ fontSize: 11, color: "#999", marginTop: 8, wordBreak: "break-all" }}>
        GET /corpus/{company}/{citation.doc_id}/page/{citation.pdf_page}
      </div>
    </aside>
  );
}

function RunPicker({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: RunCard[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
      <label style={{ fontSize: 13, color: "#555" }}>Run</label>
      <select
        value={selectedRunId ?? ""}
        onChange={(e) => onSelect(e.target.value)}
        style={{ fontSize: 13, padding: "4px 6px", minWidth: 260 }}
      >
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
  );
}

export function MemoTab({
  runId,
  onSelectRun,
}: {
  runId: string | null;
  onSelectRun: (runId: string) => void;
}) {
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

  return (
    <section style={{ display: "flex", gap: 20 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h2 style={{ fontSize: 16 }}>Memo</h2>

        <RunPicker runs={runs} selectedRunId={runId} onSelect={onSelectRun} />

        {!runId && <div style={{ fontSize: 13, color: "#888" }}>Pick a run above, or start one from the Run tab.</div>}
        {runId && memoState === "pending" && (
          <div style={{ fontSize: 13, color: "#0a7ea4" }}>Run still in progress — memo not ready yet.</div>
        )}
        {runId && memoState === "missing" && <div style={{ fontSize: 13, color: "#c0392b" }}>No memo found for this run.</div>}
        {runId && memoState === "failed" && (
          <div style={{ fontSize: 13, color: "#c0392b" }}>Run failed: {memoError}</div>
        )}
        {runId && memoState === "error" && (
          <div style={{ fontSize: 13, color: "#c0392b" }}>Failed to load memo: {memoError}</div>
        )}

        {memo && (
          <>
            <div style={{ fontSize: 13, color: "#666", marginBottom: 14 }}>
              {memo.company} · {memo.summary.items_answered}/{memo.summary.items_total} answered ·{" "}
              {memo.summary.citations_total} citations
            </div>

            {memo.items.map((item) => (
              <MemoItemCard key={item.item_id} item={item} onOpenCitation={setOpenCitation} />
            ))}
          </>
        )}
      </div>

      {memo && openCitation && (
        <CitationPanel company={memo.company} citation={openCitation} onClose={() => setOpenCitation(null)} />
      )}
    </section>
  );
}
