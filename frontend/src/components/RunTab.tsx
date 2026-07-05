// Run tab — Ledger redesign. Same wiring as before (POST /runs, GET /runs, GET
// /companies, SSE via streamRunEvents; live and replay share one code path, spec
// section 23). Rendering changes only: control card, segmented System control,
// rail timeline with type-colored dots, retrieval/tool payloads behind expanders,
// past-runs rail with progress bars.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, createRun, listCompanies, listRuns, streamRunEvents } from "../api";
import { formatDecimal } from "../format";
import type { CompanyChecklist, RunCard as RunCardData, RunStatus, TraceEvent, VerdictBadge } from "../types";
import { Card, ExpanderButton, MONO, Pill, SectionLabel, selectStyle } from "../ui";

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

// Type colors map onto the shared semantic tokens.
const TYPE_COLOR: Record<TraceEvent["type"], string> = {
  plan: "var(--accent-text)",
  scratchpad: "var(--text-3)",
  retrieval: "var(--accent-text)",
  tool_call: "var(--amber)",
  tool_result: "var(--amber)",
  decision: "var(--amber)",
  citation: "var(--green)",
  item_answer: "var(--green)",
  verdict: "var(--accent-text)",
  error: "var(--red)",
  // v1 review pipeline event types (not surfaced in the RAG Run tab, but the map
  // must stay exhaustive over TraceEventType).
  claim_extracted: "var(--accent-text)",
  scope_check: "var(--amber)",
  claim_verdict: "var(--green)",
  annotation: "var(--accent-text)",
};

const STATUS_DOT: Record<RunStatus, string> = {
  queued: "var(--text-3)",
  running: "var(--accent-text)",
  completed: "var(--green)",
  failed: "var(--red)",
  cancelled: "var(--text-3)",
};

const STATUS_BG: Record<RunStatus, string> = {
  queued: "var(--surface-2)",
  running: "var(--accent-soft)",
  completed: "var(--green-soft)",
  failed: "var(--red-soft)",
  cancelled: "var(--surface-2)",
};

const BADGE_COLOR: Record<VerdictBadge, [string, string]> = {
  strong: ["var(--green)", "var(--green-soft)"],
  mixed: ["var(--amber)", "var(--amber-soft)"],
  failed: ["var(--red)", "var(--red-soft)"],
  unknown: ["var(--text-3)", "var(--surface-2)"],
};

const fmtTime = (ts: string) => ts.slice(11, 19);

function PlanCard({ event, statuses }: { event: TraceEvent; statuses: Record<string, "answered" | "abstained"> }) {
  const items = (event.payload.items as PlanItem[]) ?? [];
  return (
    <div style={{ marginTop: 10, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
      {items.map((it) => {
        const status = statuses[it.item_id];
        const mark = status === "answered" ? "✓" : status === "abstained" ? "–" : "○";
        const markColor = status === "answered" ? "var(--green)" : status === "abstained" ? "var(--amber)" : "var(--text-3)";
        return (
          <div key={it.item_id} style={{ display: "flex", gap: 12, alignItems: "baseline", padding: "10px 14px", borderBottom: "1px solid var(--line)" }}>
            <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: markColor, width: 14 }}>{mark}</span>
            <span style={{ fontSize: 13, lineHeight: 1.5 }}>{it.question}</span>
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-3)", whiteSpace: "nowrap", fontFamily: MONO }}>{it.strategy}</span>
          </div>
        );
      })}
    </div>
  );
}

function RetrievalCard({ event, open, onToggle }: { event: TraceEvent; open: boolean; onToggle: () => void }) {
  const chunks = (event.payload.chunks as Array<Record<string, unknown>>) ?? [];
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <code
          style={{
            fontFamily: MONO,
            fontSize: 12,
            color: "var(--text-2)",
            background: "var(--surface-2)",
            border: "1px solid var(--line)",
            borderRadius: 6,
            padding: "3px 8px",
          }}
        >
          {String(event.payload.query ?? "")}
        </code>
        {chunks.length > 0 && (
          <ExpanderButton open={open} showLabel={`Show ${chunks.length} passages`} hideLabel={`Hide ${chunks.length} passages`} onClick={onToggle} />
        )}
      </div>
      {open && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
          {chunks.map((c, i) => (
            <div key={i} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, padding: "9px 12px" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline", fontFamily: MONO, fontSize: 11, color: "var(--text-3)", marginBottom: 4 }}>
                <span style={{ color: "var(--accent-text)" }}>
                  {String(c.doc_name)} · p{String(c.page)}
                </span>
                <span>score {formatDecimal(Number(c.score))}</span>
              </div>
              <div style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.5 }}>{String(c.snippet ?? "")}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CitationCard({ event }: { event: TraceEvent }) {
  const p = event.payload;
  return (
    <div style={{ marginTop: 8, borderLeft: "2px solid var(--green)", padding: "2px 0 2px 12px" }}>
      {p.claim != null && <div style={{ fontSize: 13, color: "var(--text)", marginBottom: 3 }}>{String(p.claim)}</div>}
      <div style={{ fontFamily: MONO, fontSize: 12, color: "var(--text-2)" }}>“{String(p.quote ?? "")}”</div>
      <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 3 }}>
        {String(p.doc_name ?? "")} · p{String(p.pdf_page ?? "")}
      </div>
    </div>
  );
}

function ToolCard({ event, open, onToggle }: { event: TraceEvent; open: boolean; onToggle: () => void }) {
  const isCall = event.type === "tool_call";
  const body = isCall ? event.payload.input : event.payload.output;
  return (
    <div style={{ marginTop: 8 }}>
      <ExpanderButton open={open} showLabel="Show payload" hideLabel="Hide payload" onClick={onToggle} />
      {open && (
        <pre
          style={{
            margin: "8px 0 0",
            fontFamily: MONO,
            fontSize: 11.5,
            lineHeight: 1.55,
            color: "var(--text-2)",
            background: "var(--surface)",
            border: "1px solid var(--line)",
            borderRadius: 8,
            padding: 12,
            overflowX: "auto",
          }}
        >
          {JSON.stringify(body, null, 2)}
        </pre>
      )}
    </div>
  );
}

function DecisionCard({ event }: { event: TraceEvent }) {
  const kind = String(event.payload.kind ?? "decision").replace("_", " ");
  const text = String(event.payload.text ?? event.detail ?? "");
  return (
    <div
      style={{
        marginTop: 8,
        background: "var(--amber-soft)",
        border: "1px solid var(--line)",
        borderRadius: 8,
        padding: "9px 12px",
        fontSize: 12.5,
        color: "var(--text-2)",
        lineHeight: 1.5,
      }}
    >
      <span style={{ fontWeight: 600, color: "var(--amber)", textTransform: "capitalize" }}>{kind}</span> — {text}
    </div>
  );
}

function ItemAnswerCard({ event }: { event: TraceEvent }) {
  const p = event.payload;
  const status = String(p.status ?? "answered");
  const confidence = (p.confidence as { grounded_inputs?: number; assumed_inputs?: number }) ?? {};
  const [color, bg] = status === "abstained" ? ["var(--amber)", "var(--amber-soft)"] : ["var(--green)", "var(--green-soft)"];
  return (
    <div style={{ marginTop: 8, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "12px 14px", boxShadow: "var(--shadow)" }}>
      <div style={{ fontSize: 13.5, lineHeight: 1.55, marginBottom: 8 }}>{String(p.answer ?? "")}</div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Pill color={color} bg={bg}>{status}</Pill>
        <span style={{ fontSize: 12, color: "var(--text-3)", fontFamily: MONO }}>
          {confidence.grounded_inputs ?? 0} grounded · {confidence.assumed_inputs ?? 0} assumed
        </span>
      </div>
    </div>
  );
}

function VerdictCard({ event }: { event: TraceEvent }) {
  const stats = (event.payload.summary_stats as Record<string, number>) ?? {};
  const tiles = [
    { value: `${stats.items_answered ?? 0}/${stats.items_total ?? 0}`, label: "answered" },
    { value: String(stats.items_abstained ?? 0), label: "abstained" },
    { value: String(stats.citations_total ?? 0), label: "citations" },
    { value: String(stats.calculate_calls ?? 0), label: "calculations" },
  ];
  return (
    <div style={{ marginTop: 8, display: "flex", gap: 10, flexWrap: "wrap" }}>
      {tiles.map((t) => (
        <div key={t.label} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "10px 16px", minWidth: 92 }}>
          <div style={{ fontFamily: MONO, fontSize: 18, fontWeight: 600, color: "var(--text)" }}>{t.value}</div>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 1 }}>{t.label}</div>
        </div>
      ))}
    </div>
  );
}

function TimelineEvent({
  event,
  statuses,
  open,
  onToggle,
}: {
  event: TraceEvent;
  statuses: Record<string, "answered" | "abstained">;
  open: boolean;
  onToggle: () => void;
}) {
  const color = TYPE_COLOR[event.type];
  const showDetail =
    !!event.detail && event.type !== "plan" && event.type !== "item_answer" && event.type !== "verdict" && event.type !== "decision";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "20px minmax(0,1fr)", gap: 14 }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        <span
          style={{
            width: 9,
            height: 9,
            borderRadius: "50%",
            background: color,
            marginTop: 6,
            flexShrink: 0,
            boxShadow: `0 0 0 3px color-mix(in srgb, ${color} 14%, transparent)`,
          }}
        />
        <span style={{ flex: 1, width: 1, background: "var(--line-strong)", marginTop: 4 }} />
      </div>
      <div style={{ paddingBottom: 18, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, fontWeight: 600, color, textTransform: "uppercase", letterSpacing: 0.6 }}>
            {event.type.replace("_", " ")}
          </span>
          <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text)" }}>{event.title}</span>
          <span style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 11, color: "var(--text-3)" }}>{fmtTime(event.ts)}</span>
        </div>
        {showDetail && <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.55, marginTop: 3 }}>{event.detail}</div>}

        {event.type === "plan" && <PlanCard event={event} statuses={statuses} />}
        {event.type === "retrieval" && <RetrievalCard event={event} open={open} onToggle={onToggle} />}
        {event.type === "citation" && <CitationCard event={event} />}
        {(event.type === "tool_call" || event.type === "tool_result") && <ToolCard event={event} open={open} onToggle={onToggle} />}
        {event.type === "decision" && <DecisionCard event={event} />}
        {event.type === "item_answer" && <ItemAnswerCard event={event} />}
        {event.type === "verdict" && <VerdictCard event={event} />}
      </div>
    </div>
  );
}

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
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const closeStreamRef = useRef<(() => void) | null>(null);

  const refreshPastRuns = useCallback(() => {
    listRuns()
      .then(setPastRuns)
      .catch(() => {
        /* past-runs rail is best-effort; leave the previous list on failure */
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
    setExpanded({});
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

  const statuses = useMemo(() => itemStatuses(events), [events]);
  const checklist = companies?.find((c) => c.company === selectedCompany)?.items ?? [];
  const runStatus = statusFromEvents(events);
  const hasRun = currentRunId !== null;
  const verdictSeen = events.some((e) => e.type === "verdict");

  const labelStyle = {
    fontSize: 11,
    fontWeight: 600,
    color: "var(--text-3)",
    textTransform: "uppercase" as const,
    letterSpacing: 0.6,
  };

  return (
    <section style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 280px", gap: 28 }}>
      <div style={{ minWidth: 0 }}>
        <Card style={{ padding: "16px 18px", display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            <label style={labelStyle}>Company</label>
            <select
              value={selectedCompany}
              onChange={(e) => setSelectedCompany(e.target.value)}
              disabled={!companies || companies.length === 0}
              style={selectStyle}
            >
              {(companies ?? []).map((c) => (
                <option key={c.company} value={c.company}>
                  {c.company}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            <label style={labelStyle}>System</label>
            <div style={{ display: "flex", border: "1px solid var(--line-strong)", borderRadius: 8, overflow: "hidden" }}>
              {(["agent", "baseline"] as const).map((s, i) => (
                <button
                  key={s}
                  onClick={() => setSystem(s)}
                  style={{
                    padding: "7px 14px",
                    border: "none",
                    borderLeft: i > 0 ? "1px solid var(--line-strong)" : "none",
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: "pointer",
                    fontFamily: "inherit",
                    background: system === s ? "var(--accent-soft)" : "var(--surface)",
                    color: system === s ? "var(--accent-text)" : "var(--text-2)",
                    textTransform: "capitalize",
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <button
            onClick={startRun}
            disabled={launching || !selectedCompany}
            style={{
              padding: "8px 20px",
              border: "none",
              borderRadius: 8,
              background: "var(--accent)",
              color: "#fff",
              fontSize: 13,
              fontWeight: 600,
              cursor: launching || !selectedCompany ? "default" : "pointer",
              opacity: launching || !selectedCompany ? 0.6 : 1,
              fontFamily: "inherit",
              boxShadow: "var(--shadow)",
            }}
          >
            {launching ? "Starting…" : "Run diligence"}
          </button>

          {hasRun && (
            <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "6px 12px", borderRadius: 20, background: STATUS_BG[runStatus], marginBottom: 1 }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: STATUS_DOT[runStatus] }} />
              <span style={{ fontSize: 12, fontWeight: 600, color: STATUS_DOT[runStatus], textTransform: "capitalize" }}>{runStatus}</span>
            </div>
          )}
          {hasRun && verdictSeen && (
            <button
              onClick={() => onOpenMemo(currentRunId!)}
              style={{
                padding: "7px 14px",
                border: "1px solid var(--accent-line)",
                borderRadius: 8,
                background: "var(--accent-soft)",
                color: "var(--accent-text)",
                fontSize: 13,
                fontWeight: 600,
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              Open memo →
            </button>
          )}
        </Card>

        {companiesError && (
          <div style={{ fontSize: 13, color: "var(--red)", marginTop: 12 }}>Failed to load companies: {companiesError}</div>
        )}
        {runError && <div style={{ fontSize: 13, color: "var(--red)", marginTop: 12 }}>Failed to start run: {runError}</div>}

        {!hasRun && (
          <div style={{ marginTop: 24 }}>
            <SectionLabel>Checklist · {selectedCompany || "—"}</SectionLabel>
            {companies === null && !companiesError && <div style={{ fontSize: 13, color: "var(--text-3)" }}>Loading…</div>}
            {checklist.length > 0 && (
              <Card style={{ overflow: "hidden" }}>
                {checklist.map((it, i) => (
                  <div key={it.item_id} style={{ display: "flex", gap: 14, alignItems: "baseline", padding: "14px 18px", borderBottom: "1px solid var(--line)" }}>
                    <span style={{ fontFamily: MONO, fontSize: 12, color: "var(--text-3)" }}>{String(i + 1).padStart(2, "0")}</span>
                    <span style={{ fontSize: 14, lineHeight: 1.5, color: "var(--text)" }}>{it.question}</span>
                  </div>
                ))}
                <div style={{ padding: "12px 18px", fontSize: 12, color: "var(--text-3)", background: "var(--surface-2)" }}>
                  Each item is answered with cited evidence, or the agent abstains.
                </div>
              </Card>
            )}
          </div>
        )}

        {hasRun && (
          <div style={{ marginTop: 24 }}>
            <SectionLabel>Trace · {currentRunId}</SectionLabel>
            {events.length === 0 && runStatus === "queued" && (
              <div style={{ fontSize: 13, color: "var(--text-3)" }}>Waiting for the first event…</div>
            )}
            <div style={{ display: "flex", flexDirection: "column" }}>
              {events.map((event) => (
                <TimelineEvent
                  key={event.seq}
                  event={event}
                  statuses={statuses}
                  open={!!expanded[event.seq]}
                  onToggle={() => setExpanded((prev) => ({ ...prev, [event.seq]: !prev[event.seq] }))}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      <aside style={{ minWidth: 0 }}>
        <SectionLabel style={{ marginTop: 6 }}>Past runs</SectionLabel>
        {pastRuns.length === 0 && <div style={{ fontSize: 12, color: "var(--text-3)" }}>No runs yet.</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {pastRuns.map((run) => {
            const [badgeColor, badgeBg] = BADGE_COLOR[run.verdict_badge];
            const active = run.run_id === currentRunId;
            const pct = run.items_total > 0 ? Math.round((run.items_answered / run.items_total) * 100) : 0;
            return (
              <button
                key={run.run_id}
                onClick={() => openStream(run.run_id)}
                style={{
                  textAlign: "left",
                  background: "var(--surface)",
                  border: `1px solid ${active ? "var(--accent-line)" : "var(--line)"}`,
                  borderRadius: 10,
                  padding: "11px 13px",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  boxShadow: active ? "0 0 0 3px var(--accent-soft)" : "var(--shadow)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, marginBottom: 3 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>{run.company}</span>
                  <Pill color={badgeColor} bg={badgeBg} style={{ fontSize: 10.5, padding: "2px 8px" }}>
                    {run.verdict_badge}
                  </Pill>
                </div>
                <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--text-3)", marginBottom: 6, wordBreak: "break-all" }}>{run.run_id}</div>
                <div style={{ height: 3, borderRadius: 2, background: "var(--surface-2)", overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${pct}%`, background: badgeColor }} />
                </div>
                <div style={{ fontSize: 11, color: "var(--text-2)", marginTop: 5 }}>
                  {run.items_answered} of {run.items_total} answered
                </div>
              </button>
            );
          })}
        </div>
      </aside>
    </section>
  );
}
