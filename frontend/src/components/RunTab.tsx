// Run tab (spec section 24, Steps 13/15).
// Components: company picker, checklist preview, run button, live vertical timeline,
// past-runs sidebar, status badge. Timeline card types: plan, scratchpad, retrieval,
// tool_call, tool_result, decision, citation, item_answer, verdict, error.
// Consume events via EventSource on GET /runs/{id}/events (live queue OR replay —
// the frontend must not be able to tell them apart).
//
// Step 13: built against src/fixtures/demo_trace.jsonl only (no backend calls yet).
// The "run" button below replays the fixture trace event-by-event to stand in for
// the live SSE stream that Step 15 will wire up — same rendering path either way.

import { useEffect, useMemo, useRef, useState } from "react";
import type { RunStatus, TraceEvent, VerdictBadge } from "../types";
import demoTraceRaw from "../fixtures/demo_trace.jsonl?raw";

const DEMO_COMPANY = "3M";
const DEMO_RUN_ID = "demo-run-3m-001";
const STEP_MS = 350; // pacing for the simulated live timeline

function parseTraceFixture(raw: string): TraceEvent[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as TraceEvent)
    .sort((a, b) => a.seq - b.seq);
}

const DEMO_TRACE = parseTraceFixture(demoTraceRaw);

interface PlanItem {
  item_id: string;
  question: string;
  strategy: string;
  planned_inputs: string[];
}

function planItems(events: TraceEvent[]): PlanItem[] {
  const plan = events.find((e) => e.type === "plan");
  if (!plan) return [];
  return (plan.payload.items as PlanItem[]) ?? [];
}

function itemStatuses(events: TraceEvent[]): Record<string, "answered" | "abstained"> {
  const out: Record<string, "answered" | "abstained"> = {};
  for (const e of events) {
    if (e.type === "item_answer" && e.item_id) {
      out[e.item_id] = (e.payload.status as "answered" | "abstained") ?? "answered";
    }
  }
  return out;
}

const TYPE_COLOR: Record<TraceEvent["type"], string> = {
  plan: "#5b5bd6",
  scratchpad: "#888",
  retrieval: "#0a7ea4",
  tool_call: "#a06a1c",
  tool_result: "#a06a1c",
  decision: "#b3391f",
  citation: "#1c7a3c",
  item_answer: "#1c7a3c",
  verdict: "#5b5bd6",
  error: "#c0392b",
};

const STATUS_COLOR: Record<RunStatus, string> = {
  queued: "#888",
  running: "#0a7ea4",
  completed: "#1c7a3c",
  failed: "#c0392b",
  cancelled: "#888",
};

function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        color: "#fff",
        background: STATUS_COLOR[status],
        textTransform: "capitalize",
      }}
    >
      {status}
    </span>
  );
}

function RetrievalCard({ event }: { event: TraceEvent }) {
  const [open, setOpen] = useState(false);
  const chunks = (event.payload.chunks as Array<Record<string, unknown>>) ?? [];
  return (
    <div>
      <div style={{ fontSize: 13, color: "#444", marginBottom: 4 }}>
        query: <code>{String(event.payload.query ?? "")}</code>
        {typeof event.payload.k === "number" ? ` (k=${event.payload.k})` : ""}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: open ? 6 : 0 }}>
        {chunks.map((c, i) => (
          <span
            key={i}
            style={{
              fontSize: 12,
              background: "#eef4f8",
              border: "1px solid #cfe0e8",
              borderRadius: 4,
              padding: "2px 6px",
            }}
          >
            {String(c.doc_name)} p{String(c.page)} · {Number(c.score).toFixed(2)}
          </span>
        ))}
      </div>
      {chunks.length > 0 && (
        <button
          onClick={() => setOpen((o) => !o)}
          style={{
            fontSize: 12,
            background: "none",
            border: "none",
            color: "#0a7ea4",
            cursor: "pointer",
            padding: 0,
          }}
        >
          {open ? "hide snippets" : "show snippets"}
        </button>
      )}
      {open && (
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {chunks.map((c, i) => (
            <li key={i} style={{ fontSize: 12, color: "#555", marginBottom: 4 }}>
              <em>{String(c.snippet ?? "")}</em>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CitationCard({ event }: { event: TraceEvent }) {
  const p = event.payload;
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{ color: "#444", marginBottom: 4 }}>
        {String(p.doc_name ?? "")} · p{String(p.pdf_page ?? "")}
        {p.claim ? <> — <span style={{ color: "#222" }}>{String(p.claim)}</span></> : null}
      </div>
      <blockquote
        style={{
          margin: 0,
          padding: "4px 10px",
          borderLeft: "3px solid #1c7a3c",
          background: "#f2f8f4",
          fontSize: 12,
          color: "#333",
        }}
      >
        {String(p.quote ?? "")}
      </blockquote>
    </div>
  );
}

function ToolCard({ event }: { event: TraceEvent }) {
  const isCall = event.type === "tool_call";
  const body = isCall ? event.payload.input : event.payload.output;
  return (
    <div>
      <div style={{ fontSize: 13, color: "#444", marginBottom: 4 }}>
        <code>{String(event.payload.tool ?? "")}</code> {isCall ? "called" : "returned"}
      </div>
      <pre
        style={{
          margin: 0,
          fontSize: 11,
          background: "#f7f4ec",
          border: "1px solid #e5ddc8",
          borderRadius: 4,
          padding: 8,
          overflowX: "auto",
        }}
      >
        {JSON.stringify(body, null, 2)}
      </pre>
    </div>
  );
}

function ItemAnswerCard({ event }: { event: TraceEvent }) {
  const p = event.payload;
  const status = String(p.status ?? "answered");
  const confidence = (p.confidence as { grounded_inputs?: number; assumed_inputs?: number }) ?? {};
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{ marginBottom: 4 }}>{String(p.answer ?? "")}</div>
      <div style={{ display: "flex", gap: 10, fontSize: 12, color: "#555" }}>
        <span
          style={{
            fontWeight: 600,
            color: status === "abstained" ? "#b3391f" : "#1c7a3c",
            textTransform: "capitalize",
          }}
        >
          {status}
        </span>
        <span>
          {confidence.grounded_inputs ?? 0} inputs grounded / {confidence.assumed_inputs ?? 0} assumed
        </span>
      </div>
    </div>
  );
}

function VerdictCard({ event }: { event: TraceEvent }) {
  const stats = (event.payload.summary_stats as Record<string, number>) ?? {};
  return (
    <div style={{ fontSize: 13, display: "flex", gap: 14, flexWrap: "wrap", color: "#333" }}>
      <span>{stats.items_answered ?? 0} answered</span>
      <span>{stats.items_abstained ?? 0} abstained</span>
      <span>{stats.citations_total ?? 0} citations</span>
      <span>{stats.calculate_calls ?? 0} calculations</span>
    </div>
  );
}

function PlanCard({ event, statuses }: { event: TraceEvent; statuses: Record<string, "answered" | "abstained"> }) {
  const items = (event.payload.items as PlanItem[]) ?? [];
  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {items.map((it) => {
        const status = statuses[it.item_id];
        const mark = status === "answered" ? "✓" : status === "abstained" ? "–" : "○";
        return (
          <li key={it.item_id} style={{ fontSize: 13, marginBottom: 4 }}>
            <span
              style={{
                display: "inline-block",
                width: 16,
                color: status === "answered" ? "#1c7a3c" : status === "abstained" ? "#b3391f" : "#999",
                fontWeight: 700,
              }}
            >
              {mark}
            </span>
            {it.question} <span style={{ color: "#888" }}>({it.strategy})</span>
          </li>
        );
      })}
    </ul>
  );
}

function EventBody({ event, statuses }: { event: TraceEvent; statuses: Record<string, "answered" | "abstained"> }) {
  switch (event.type) {
    case "plan":
      return <PlanCard event={event} statuses={statuses} />;
    case "retrieval":
      return <RetrievalCard event={event} />;
    case "citation":
      return <CitationCard event={event} />;
    case "tool_call":
    case "tool_result":
      return <ToolCard event={event} />;
    case "item_answer":
      return <ItemAnswerCard event={event} />;
    case "verdict":
      return <VerdictCard event={event} />;
    default:
      // scratchpad, decision, error: detail is already rendered by TimelineCard above.
      return null;
  }
}

function TimelineCard({ event, statuses }: { event: TraceEvent; statuses: Record<string, "answered" | "abstained"> }) {
  const color = TYPE_COLOR[event.type];
  const emphasized = event.type === "decision" || event.type === "error";
  return (
    <div style={{ display: "flex", gap: 10 }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ width: 10, height: 10, borderRadius: "50%", background: color, marginTop: 4 }} />
        <div style={{ flex: 1, width: 2, background: "#e2e2e2" }} />
      </div>
      <div
        style={{
          flex: 1,
          marginBottom: 14,
          padding: "8px 12px",
          borderRadius: 6,
          border: emphasized ? `1px solid ${color}` : "1px solid #e5e5e5",
          background: emphasized ? "#fff6f4" : "#fafafa",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color, textTransform: "uppercase", letterSpacing: 0.4 }}>
            {event.type}
          </span>
          <span style={{ fontSize: 11, color: "#999" }}>seq {event.seq}</span>
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, margin: "2px 0 4px" }}>{event.title}</div>
        {event.detail && event.type !== "plan" && event.type !== "item_answer" && event.type !== "verdict" && (
          <div style={{ fontSize: 12, color: "#666", marginBottom: 6 }}>{event.detail}</div>
        )}
        <EventBody event={event} statuses={statuses} />
      </div>
    </div>
  );
}

interface RunCardSummary {
  run_id: string;
  company: string;
  status: RunStatus;
  items_total: number;
  items_answered: number;
  items_abstained: number;
  verdict_badge: VerdictBadge;
}

function buildRunCardSummary(events: TraceEvent[]): RunCardSummary {
  const verdict = events.find((e) => e.type === "verdict");
  const stats = (verdict?.payload.summary_stats as Record<string, number>) ?? {};
  const abstained = stats.items_abstained ?? 0;
  const answered = stats.items_answered ?? 0;
  const total = stats.items_total ?? 0;
  const badge: VerdictBadge = !verdict ? "unknown" : abstained === 0 ? "strong" : answered > 0 ? "mixed" : "failed";
  return {
    run_id: DEMO_RUN_ID,
    company: DEMO_COMPANY,
    status: verdict ? "completed" : "queued",
    items_total: total,
    items_answered: answered,
    items_abstained: abstained,
    verdict_badge: badge,
  };
}

const BADGE_COLOR: Record<VerdictBadge, string> = {
  strong: "#1c7a3c",
  mixed: "#a06a1c",
  failed: "#c0392b",
  unknown: "#888",
};

export function RunTab() {
  const [selectedCompany, setSelectedCompany] = useState(DEMO_COMPANY);
  const [visibleCount, setVisibleCount] = useState(0);
  const [hasRun, setHasRun] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const visibleEvents = DEMO_TRACE.slice(0, visibleCount);
  const statuses = useMemo(() => itemStatuses(visibleEvents), [visibleEvents]);
  const checklist = planItems(DEMO_TRACE);

  const runStatus: RunStatus = !hasRun
    ? "queued"
    : visibleCount < DEMO_TRACE.length
      ? "running"
      : "completed";

  function startRun() {
    if (timerRef.current) clearInterval(timerRef.current);
    setHasRun(true);
    setVisibleCount(0);
    let i = 0;
    timerRef.current = setInterval(() => {
      i += 1;
      setVisibleCount(i);
      if (i >= DEMO_TRACE.length && timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }, STEP_MS);
  }

  function loadPastRun() {
    if (timerRef.current) clearInterval(timerRef.current);
    setHasRun(true);
    setVisibleCount(DEMO_TRACE.length);
  }

  const pastRuns: RunCardSummary[] = [buildRunCardSummary(DEMO_TRACE)];

  return (
    <section style={{ display: "flex", gap: 20 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h2 style={{ fontSize: 16 }}>Run</h2>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <label style={{ fontSize: 13, color: "#555" }}>Company</label>
          <select
            value={selectedCompany}
            onChange={(e) => setSelectedCompany(e.target.value)}
            style={{ fontSize: 13, padding: "4px 6px" }}
          >
            <option value={DEMO_COMPANY}>{DEMO_COMPANY}</option>
          </select>
          <button
            onClick={startRun}
            style={{
              fontSize: 13,
              padding: "6px 14px",
              background: "#111",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Run
          </button>
          <StatusBadge status={runStatus} />
        </div>

        {!hasRun && (
          <div style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 13, color: "#555", margin: "0 0 6px" }}>Checklist preview</h3>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {checklist.map((it) => (
                <li key={it.item_id} style={{ fontSize: 13, marginBottom: 4 }}>
                  {it.question} <span style={{ color: "#888" }}>({it.strategy})</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {hasRun && (
          <div style={{ marginTop: 8 }}>
            {visibleEvents.map((event) => (
              <TimelineCard key={event.seq} event={event} statuses={statuses} />
            ))}
          </div>
        )}
      </div>

      <aside style={{ width: 220, flexShrink: 0 }}>
        <h3 style={{ fontSize: 13, color: "#555", margin: "0 0 8px" }}>Past runs</h3>
        {pastRuns.map((run) => (
          <button
            key={run.run_id}
            onClick={loadPastRun}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              background: "#fafafa",
              border: "1px solid #e5e5e5",
              borderRadius: 6,
              padding: 10,
              marginBottom: 8,
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 2 }}>{run.company}</div>
            <div style={{ color: "#666", marginBottom: 4 }}>{run.run_id}</div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>
                {run.items_answered}/{run.items_total} answered
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "#fff",
                  background: BADGE_COLOR[run.verdict_badge],
                  borderRadius: 10,
                  padding: "1px 8px",
                  textTransform: "capitalize",
                }}
              >
                {run.verdict_badge}
              </span>
            </div>
          </button>
        ))}
      </aside>
    </section>
  );
}
