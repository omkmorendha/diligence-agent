// Run tab (spec section 24, Step 15).
// Components: company picker, checklist preview, run button, live vertical timeline,
// past-runs sidebar, status badge. Timeline card types: plan, scratchpad, retrieval,
// tool_call, tool_result, decision, citation, item_answer, verdict, error.
//
// Wired to the live backend (Step 15): POST /runs starts a run, GET /runs lists past
// runs, GET /companies sources the picker + checklist preview (gold-free), and
// GET /runs/{id}/events (SSE) drives the timeline -- the same EventSource code path
// whether the run is still live or being replayed from a completed trace, per spec
// section 23 ("the frontend should not be able to tell live from replay").

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, createRun, listCompanies, listRuns, streamRunEvents } from "../api";
import type { CompanyChecklist, RunCard as RunCardData, RunStatus, TraceEvent, VerdictBadge } from "../types";

interface PlanItem {
  item_id: string;
  question: string;
  strategy: string;
  planned_inputs: string[];
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

function statusFromEvents(events: TraceEvent[]): RunStatus {
  if (events.length === 0) return "queued";
  const last = events[events.length - 1];
  if (last.type === "verdict") return "completed";
  if (last.type === "error") return "failed";
  return "running";
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

const BADGE_COLOR: Record<VerdictBadge, string> = {
  strong: "#1c7a3c",
  mixed: "#a06a1c",
  failed: "#c0392b",
  unknown: "#888",
};

export function RunTab({ onOpenMemo }: { onOpenMemo: (runId: string) => void }) {
  const [companies, setCompanies] = useState<CompanyChecklist[] | null>(null);
  const [companiesError, setCompaniesError] = useState<string | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<string>("");
  const [system, setSystem] = useState<"agent" | "baseline">("agent");

  const [pastRuns, setPastRuns] = useState<RunCardData[]>([]);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [launching, setLaunching] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const closeStreamRef = useRef<(() => void) | null>(null);

  const refreshPastRuns = useCallback(() => {
    listRuns()
      .then(setPastRuns)
      .catch(() => {
        /* past-runs sidebar is best-effort; leave the previous list on failure */
      });
  }, []);

  useEffect(() => {
    listCompanies()
      .then((cs) => {
        setCompanies(cs);
        if (cs.length > 0) setSelectedCompany(cs[0].company);
      })
      .catch((err) => setCompaniesError(err instanceof ApiError ? err.message : String(err)));
    refreshPastRuns();
    return () => closeStreamRef.current?.();
  }, [refreshPastRuns]);

  function openStream(runId: string) {
    closeStreamRef.current?.();
    setCurrentRunId(runId);
    setEvents([]);
    closeStreamRef.current = streamRunEvents<TraceEvent>(
      runId,
      (event) => setEvents((prev) => [...prev, event]),
      () => refreshPastRuns(),
    );
  }

  async function startRun() {
    if (!selectedCompany) return;
    setLaunching(true);
    setRunError(null);
    try {
      const res = await createRun({ company: selectedCompany, system });
      openStream(res.run_id);
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLaunching(false);
    }
  }

  function loadPastRun(runId: string) {
    openStream(runId);
  }

  const statuses = useMemo(() => itemStatuses(events), [events]);
  const checklist = companies?.find((c) => c.company === selectedCompany)?.items ?? [];
  const runStatus = statusFromEvents(events);
  const hasRun = currentRunId !== null;
  const verdictSeen = events.some((e) => e.type === "verdict");

  return (
    <section style={{ display: "flex", gap: 20 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h2 style={{ fontSize: 16 }}>Run</h2>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
          <label style={{ fontSize: 13, color: "#555" }}>Company</label>
          <select
            value={selectedCompany}
            onChange={(e) => setSelectedCompany(e.target.value)}
            disabled={!companies || companies.length === 0}
            style={{ fontSize: 13, padding: "4px 6px" }}
          >
            {(companies ?? []).map((c) => (
              <option key={c.company} value={c.company}>
                {c.company}
              </option>
            ))}
          </select>

          <label style={{ fontSize: 13, color: "#555" }}>System</label>
          <select
            value={system}
            onChange={(e) => setSystem(e.target.value as "agent" | "baseline")}
            style={{ fontSize: 13, padding: "4px 6px" }}
          >
            <option value="agent">agent</option>
            <option value="baseline">baseline</option>
          </select>

          <button
            onClick={startRun}
            disabled={launching || !selectedCompany}
            style={{
              fontSize: 13,
              padding: "6px 14px",
              background: "#111",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: launching || !selectedCompany ? "default" : "pointer",
              opacity: launching || !selectedCompany ? 0.6 : 1,
            }}
          >
            {launching ? "Starting…" : "Run"}
          </button>
          {hasRun && <StatusBadge status={runStatus} />}
          {hasRun && verdictSeen && (
            <button
              onClick={() => onOpenMemo(currentRunId!)}
              style={{
                fontSize: 12,
                background: "none",
                border: "1px solid #0a7ea4",
                color: "#0a7ea4",
                borderRadius: 4,
                padding: "4px 10px",
                cursor: "pointer",
              }}
            >
              View memo →
            </button>
          )}
        </div>

        {companiesError && (
          <div style={{ fontSize: 13, color: "#c0392b", marginBottom: 12 }}>
            Failed to load companies: {companiesError}
          </div>
        )}
        {runError && (
          <div style={{ fontSize: 13, color: "#c0392b", marginBottom: 12 }}>Failed to start run: {runError}</div>
        )}

        {!hasRun && (
          <div style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 13, color: "#555", margin: "0 0 6px" }}>Checklist preview</h3>
            {companies === null && !companiesError && (
              <div style={{ fontSize: 13, color: "#888" }}>Loading…</div>
            )}
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {checklist.map((it) => (
                <li key={it.item_id} style={{ fontSize: 13, marginBottom: 4 }}>
                  {it.question}
                </li>
              ))}
            </ul>
          </div>
        )}

        {hasRun && (
          <div style={{ marginTop: 8 }}>
            {events.length === 0 && runStatus === "queued" && (
              <div style={{ fontSize: 13, color: "#888" }}>Waiting for the first event…</div>
            )}
            {events.map((event) => (
              <TimelineCard key={event.seq} event={event} statuses={statuses} />
            ))}
          </div>
        )}
      </div>

      <aside style={{ width: 240, flexShrink: 0 }}>
        <h3 style={{ fontSize: 13, color: "#555", margin: "0 0 8px" }}>Past runs</h3>
        {pastRuns.length === 0 && <div style={{ fontSize: 12, color: "#999" }}>No runs yet.</div>}
        {pastRuns.map((run) => (
          <button
            key={run.run_id}
            onClick={() => loadPastRun(run.run_id)}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              background: run.run_id === currentRunId ? "#eef6fa" : "#fafafa",
              border: run.run_id === currentRunId ? "1px solid #0a7ea4" : "1px solid #e5e5e5",
              borderRadius: 6,
              padding: 10,
              marginBottom: 8,
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 2 }}>{run.company}</div>
            <div style={{ color: "#666", marginBottom: 4, wordBreak: "break-all" }}>{run.run_id}</div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
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
                  whiteSpace: "nowrap",
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
