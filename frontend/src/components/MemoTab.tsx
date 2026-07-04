// Memo tab (spec section 24, Steps 13/15).
// Renders the memo with inline citation markers. Clicking a citation opens a side
// panel that (from Step 15 on) calls GET /corpus/{company}/{doc_id}/page/{n} and
// highlights the span. Per-item confidence shown as "2 inputs grounded / 0 assumed".
// Item statuses: answered / abstained / error.
//
// Step 13: built against src/fixtures/demo_memo.json only. The side panel renders
// the citation's own quote/doc/page fields (already present on the fixture) rather
// than fetching a page — real page fetch + span highlight lands in Step 15.

import { useState } from "react";
import type { Citation, Memo, MemoItem } from "../types";
import demoMemo from "../fixtures/demo_memo.json";

const MEMO = demoMemo as Memo;

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

function CitationPanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  return (
    <aside
      style={{
        width: 300,
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
          margin: 0,
          padding: "6px 10px",
          borderLeft: "3px solid #0a7ea4",
          background: "#eef6fa",
          fontSize: 13,
          color: "#222",
        }}
      >
        {citation.quote}
      </blockquote>
      <div style={{ fontSize: 11, color: "#999", marginTop: 8 }}>
        GET /corpus/{MEMO.company}/{citation.doc_id}/page/{citation.pdf_page}
      </div>
    </aside>
  );
}

export function MemoTab() {
  const [openCitation, setOpenCitation] = useState<Citation | null>(null);

  return (
    <section style={{ display: "flex", gap: 20 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h2 style={{ fontSize: 16 }}>Memo</h2>
        <div style={{ fontSize: 13, color: "#666", marginBottom: 14 }}>
          {MEMO.company} · {MEMO.summary.items_answered}/{MEMO.summary.items_total} answered ·{" "}
          {MEMO.summary.citations_total} citations
        </div>

        {MEMO.items.map((item) => (
          <MemoItemCard key={item.item_id} item={item} onOpenCitation={setOpenCitation} />
        ))}
      </div>

      {openCitation && <CitationPanel citation={openCitation} onClose={() => setOpenCitation(null)} />}
    </section>
  );
}
