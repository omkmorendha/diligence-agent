// Evals tab — Ledger redesign. Same wiring as before (GET /evals/results ->
// results/comparison.json). Rendering changes only: header with mono subset stats,
// card-grid table with colored mono values + proportion bars, published-reference
// footnote strip, notes as left-rule paragraphs.

import { useEffect, useState } from "react";
import { ApiError, getEvalIterations, getEvalResults } from "../api";
import { formatDecimal, formatPercent } from "../format";
import type { Comparison, IterationEntry, IterationsReport, SystemMetrics } from "../types";
import { Card, MONO } from "../ui";

type Cell = { kind: "fraction" | "score5"; value: number | null | undefined };

const fraction = (value: number | null | undefined): Cell => ({ kind: "fraction", value });
const score5 = (value: number | null | undefined): Cell => ({ kind: "score5", value });

interface Row {
  key: string;
  label: string;
  sub?: string;
  definition: string;
  /** Set on the overall accuracy row: the detail panel shows the cumulative per-bucket breakdown. */
  showBuckets?: boolean;
  cell: (metrics: SystemMetrics | undefined) => Cell;
}

const BUCKET_NAME: Record<string, string> = {
  A_multi_input: "Multi-input",
  B_judgment: "Judgment",
  C_lookup: "Lookup",
};

// Condensed from the deterministic-metric definitions in v0-spec.md section 20.
const ACCURACY_DEFINITION =
  "Share of checklist questions scored correct: numeric answers within ±1% relative tolerance, string answers by normalized exact match. Abstentions count as correct only when the item is genuinely unanswerable.";

function buildRows(comparison: Comparison): Row[] {
  const bucketCounts = comparison.subset.bucket_counts;
  return [
    {
      key: "answer_accuracy",
      label: "Answer accuracy",
      definition: ACCURACY_DEFINITION,
      showBuckets: true,
      cell: (m) => fraction(m?.answer_accuracy),
    },
    ...Object.keys(bucketCounts).map(
      (bucket): Row => ({
        key: `bucket:${bucket}`,
        label: `Answer accuracy — ${BUCKET_NAME[bucket] ?? bucket}`,
        sub: `${bucket} · ${bucketCounts[bucket]} questions`,
        definition: `${ACCURACY_DEFINITION} Restricted to the ${bucketCounts[bucket]} ${BUCKET_NAME[bucket] ?? bucket} questions in this subset.`,
        cell: (m) => fraction(m?.by_bucket?.[bucket]?.answer_accuracy),
      }),
    ),
    {
      key: "citation_precision",
      label: "Citation precision",
      definition:
        "Share of citations whose doc_id matches a gold evidence document, with the cited page within ±1 page of the gold range.",
      cell: (m) => fraction(m?.citation_precision),
    },
    {
      key: "citation_provenance",
      label: "Citation provenance",
      definition:
        "Share of cited chunk_ids that appear in a prior retrieval event in the same trace — catches citations invented from model memory, gold leakage, or unlogged retrievals.",
      cell: (m) => fraction(m?.citation_provenance),
    },
    {
      key: "arithmetic_integrity",
      label: "Arithmetic integrity",
      definition:
        "Share of material financial numeric claims in the memo that trace to either a calculate tool result or a cited quote span.",
      cell: (m) => fraction(m?.arithmetic_integrity),
    },
    {
      key: "trace_shape",
      label: "Agentic trace shape",
      definition:
        "Share of checklist items whose trace follows the required workflow shape: plan before retrieval, exactly one final answer or abstention, and multi-input items using multiple retrievals plus calculation.",
      cell: (m) => fraction(m?.trace_shape),
    },
    {
      key: "abstention_correct_rate",
      label: "Abstention calibration",
      definition:
        "Share of abstentions that are correct because the item is genuinely unanswerable or evidence-insufficient. “—” means the system made no scoreable abstentions.",
      cell: (m) => fraction(m?.abstention_correct_rate),
    },
    {
      key: "groundedness_judge",
      label: "Groundedness (judge, 1–5)",
      definition:
        "LLM-judge score (1–5) for how well memo claims are grounded in the cited evidence. “—” means the judge pass has not been run for this system.",
      cell: (m) => score5(m?.groundedness_judge),
    },
    {
      key: "actionability_judge",
      label: "Actionability (judge, 1–5)",
      definition:
        "LLM-judge score (1–5) for how decision-ready the memo is for a diligence analyst. “—” means the judge pass has not been run for this system.",
      cell: (m) => score5(m?.actionability_judge),
    },
  ];
}

// Emphasis thresholds are relative to each metric's own scale (spec section 24).
function cellRatio(cell: Cell): number | null {
  if (cell.value == null) return null;
  return cell.kind === "fraction" ? cell.value : (cell.value - 1) / 4;
}

function cellColor(cell: Cell): string {
  const ratio = cellRatio(cell);
  if (ratio == null) return "var(--text-3)";
  if (ratio >= 0.7) return "var(--green)";
  if (ratio >= 0.4) return "var(--amber)";
  return "var(--red)";
}

function formatCell(cell: Cell): string {
  if (cell.value == null) return "—";
  return cell.kind === "fraction" ? formatPercent(cell.value) : formatDecimal(cell.value);
}

function MetricValue({ cell }: { cell: Cell }) {
  const ratio = cellRatio(cell);
  const color = cellColor(cell);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ fontFamily: MONO, fontSize: 13.5, fontWeight: 600, color, minWidth: 44 }}>{formatCell(cell)}</span>
      <span style={{ flex: 1, maxWidth: 90, height: 4, borderRadius: 2, background: "var(--surface-2)", overflow: "hidden" }}>
        <span style={{ display: "block", height: "100%", width: `${Math.round((ratio ?? 0) * 100)}%`, background: color }} />
      </span>
    </div>
  );
}

const headerCellStyle = {
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-3)",
  textTransform: "uppercase" as const,
  letterSpacing: 0.7,
};

const panelLabelStyle = {
  fontSize: 10.5,
  fontWeight: 600,
  color: "var(--text-3)",
  textTransform: "uppercase" as const,
  letterSpacing: 0.7,
  marginBottom: 8,
};

function PanelScore({ label, cell, emphasized }: { label: string; cell: Cell; emphasized?: boolean }) {
  const ratio = cellRatio(cell);
  const color = cellColor(cell);
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
        <span style={{ fontSize: 12.5, fontWeight: emphasized ? 600 : 500, color: emphasized ? "var(--text)" : "var(--text-2)" }}>
          {label}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 600, color }}>{formatCell(cell)}</span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${Math.round((ratio ?? 0) * 100)}%`, background: color }} />
      </div>
    </div>
  );
}

function MetricDetailPanel({
  row,
  comparison,
  onClose,
}: {
  row: Row;
  comparison: Comparison;
  onClose: () => void;
}) {
  const baseline = comparison.systems.baseline as SystemMetrics | undefined;
  const agent = comparison.systems.agent as SystemMetrics | undefined;
  const baselineCell = row.cell(baseline);
  const agentCell = row.cell(agent);

  let delta: { text: string; color: string } | null = null;
  if (baselineCell.value != null && agentCell.value != null) {
    const diff = agentCell.value - baselineCell.value;
    const text =
      baselineCell.kind === "fraction"
        ? `${diff >= 0 ? "+" : "−"}${formatDecimal(Math.abs(diff * 100))} pts`
        : `${diff >= 0 ? "+" : "−"}${formatDecimal(Math.abs(diff))}`;
    delta = {
      text,
      color: diff > 0 ? "var(--green)" : diff < 0 ? "var(--red)" : "var(--text-3)",
    };
  }

  const bucketCounts = comparison.subset.bucket_counts;

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
          Metric detail
        </span>
        <button
          onClick={onClose}
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-3)", fontSize: 15, lineHeight: 1, padding: 2, fontFamily: "inherit" }}
        >
          ×
        </button>
      </div>
      <div style={{ padding: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>{row.label}</div>
        {row.sub && <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--text-3)", marginTop: 2 }}>{row.sub}</div>}
        <div style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--text-2)", margin: "8px 0 16px" }}>{row.definition}</div>

        <div style={panelLabelStyle}>Scores</div>
        <PanelScore label={baseline?.label ?? "Naive-RAG baseline"} cell={baselineCell} />
        <PanelScore label={agent?.label ?? "Agent"} cell={agentCell} emphasized />
        {delta && (
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", borderTop: "1px solid var(--line)", paddingTop: 10, marginTop: 12 }}>
            <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>Agent vs. baseline</span>
            <span style={{ fontFamily: MONO, fontSize: 13.5, fontWeight: 600, color: delta.color }}>{delta.text}</span>
          </div>
        )}

        {row.showBuckets && (
          <div style={{ marginTop: 18 }}>
            <div style={panelLabelStyle}>Cumulative by bucket</div>
            <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
              {Object.keys(bucketCounts).map((bucket, i) => {
                const bCell = (m: SystemMetrics | undefined) => fraction(m?.by_bucket?.[bucket]?.answer_accuracy);
                return (
                  <div key={bucket} style={{ padding: "9px 12px", borderTop: i > 0 ? "1px solid var(--line)" : "none" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                      <span style={{ fontSize: 12.5, color: "var(--text)" }}>{BUCKET_NAME[bucket] ?? bucket}</span>
                      <span style={{ fontFamily: MONO, fontSize: 11, color: "var(--text-3)" }}>{bucketCounts[bucket]} questions</span>
                    </div>
                    <div style={{ display: "flex", gap: 14 }}>
                      {[
                        { name: "baseline", cell: bCell(baseline) },
                        { name: "agent", cell: bCell(agent) },
                      ].map((s) => (
                        <span key={s.name} style={{ fontFamily: MONO, fontSize: 11.5, color: "var(--text-3)" }}>
                          {s.name} <span style={{ color: cellColor(s.cell), fontWeight: 600 }}>{formatCell(s.cell)}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Improvement loop — cumulative iteration trend (GET /evals/iterations ->
// results/iterations/report_data.json). baseline61 + iter1..iter5, all rescored
// under the final scorer. Rendered with the same hand-rolled Ledger primitives
// as the baseline-vs-agent table; no chart libraries.
// ---------------------------------------------------------------------------

const NUM = new Intl.NumberFormat("en-US");

/** correct-of-61 is the headline: fraction of the full 61-item subset scored
 * correct (abstentions never count), the metric the whole loop moved 22 -> 51. */
function correctRatio(entry: IterationEntry): number {
  return entry.aggregate.n_items ? entry.correct_of_61 / entry.aggregate.n_items : 0;
}

function shortLabel(entry: IterationEntry): string {
  if (entry.key === "baseline61") return "Baseline";
  const m = /iter(\d+)/.exec(entry.key);
  return m ? `Iteration ${m[1]}` : entry.label;
}

interface TrendCol {
  key: string;
  header: string;
  sub?: string;
  /** column-level note shown under the header, e.g. judge scale */
  render: (entry: IterationEntry, ctx: { maxLatency: number; maxTokens: number }) => JSX.Element;
}

function MonoValue({ text, color = "var(--text)", bold = true }: { text: string; color?: string; bold?: boolean }) {
  return <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: bold ? 600 : 500, color }}>{text}</span>;
}

/** thin horizontal bar (Ledger proportion bar) used for latency/token trends. */
function TrendBar({ ratio, color }: { ratio: number; color: string }) {
  return (
    <span style={{ display: "block", marginTop: 4, height: 3, borderRadius: 2, background: "var(--surface-2)", overflow: "hidden" }}>
      <span style={{ display: "block", height: "100%", width: `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`, background: color }} />
    </span>
  );
}

const TREND_COLS: TrendCol[] = [
  {
    key: "correct",
    header: "Correct of 61",
    sub: "abstentions excluded",
    render: (e) => {
      const ratio = correctRatio(e);
      const color = cellColor(fraction(ratio));
      return (
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
            <span style={{ fontFamily: MONO, fontSize: 18, fontWeight: 700, color }}>{e.correct_of_61}</span>
            <span style={{ fontFamily: MONO, fontSize: 12, color: "var(--text-3)" }}>/ {e.aggregate.n_items}</span>
          </div>
          <TrendBar ratio={ratio} color={color} />
        </div>
      );
    },
  },
  {
    key: "acc_answered",
    header: "Acc. of answered",
    sub: "correct ÷ answered",
    render: (e) => {
      const cell = fraction(e.aggregate.answer_accuracy);
      return <MonoValue text={formatCell(cell)} color={cellColor(cell)} />;
    },
  },
  {
    key: "citation_precision",
    header: "Citation precision",
    render: (e) => {
      const cell = fraction(e.aggregate.citation_precision);
      return <MonoValue text={formatCell(cell)} color={cellColor(cell)} />;
    },
  },
  {
    key: "answered",
    header: "Answered",
    sub: "of 61",
    render: (e) => (
      <span>
        <MonoValue text={`${e.aggregate.answered}`} />
        <span style={{ fontFamily: MONO, fontSize: 11.5, color: "var(--text-3)" }}> · {e.aggregate.abstained} abst.</span>
      </span>
    ),
  },
  {
    key: "p95",
    header: "p95 latency",
    sub: "per item",
    render: (e, ctx) => {
      const v = e.timing.item_wall_p95_s;
      if (v == null) return <MonoValue text="—" color="var(--text-3)" bold={false} />;
      return (
        <div>
          <MonoValue text={`${formatDecimal(v)}s`} color="var(--text-2)" />
          <TrendBar ratio={ctx.maxLatency ? v / ctx.maxLatency : 0} color="var(--text-3)" />
        </div>
      );
    },
  },
  {
    key: "prompt_tokens",
    header: "Prompt tokens",
    render: (e, ctx) => {
      const v = e.tokens.prompt_total;
      return (
        <div>
          <MonoValue text={NUM.format(v)} color="var(--text-2)" bold={false} />
          <TrendBar ratio={ctx.maxTokens ? v / ctx.maxTokens : 0} color="var(--text-3)" />
        </div>
      );
    },
  },
  {
    key: "groundedness_judge",
    header: "Groundedness",
    sub: "judge 1–5",
    render: (e) => {
      const cell = score5(e.judges.groundedness_judge);
      return <MonoValue text={formatCell(cell)} color={cellColor(cell)} />;
    },
  },
  {
    key: "actionability_judge",
    header: "Actionability",
    sub: "judge 1–5",
    render: (e) => {
      const cell = score5(e.judges.actionability_judge);
      return <MonoValue text={formatCell(cell)} color={cellColor(cell)} />;
    },
  },
  {
    key: "gold_agreement_judge",
    header: "Gold agreement",
    sub: "judge 1–5",
    render: (e) => {
      const cell = score5(e.judges.gold_agreement_judge);
      return <MonoValue text={formatCell(cell)} color={cellColor(cell)} />;
    },
  },
];

const TREND_GRID = `minmax(112px,1.1fr) ${TREND_COLS.map(() => "minmax(96px,1fr)").join(" ")}`;

function ImprovementLoop() {
  const [report, setReport] = useState<IterationsReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getEvalIterations()
      .then((r) => {
        setReport(r);
        setState("ready");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setState("missing");
        } else {
          setState("error");
          setError(err instanceof ApiError ? err.message : String(err));
        }
      });
  }, []);

  const iterations = report?.iterations ?? [];
  const maxLatency = Math.max(0, ...iterations.map((e) => e.timing.item_wall_p95_s ?? 0));
  const maxTokens = Math.max(0, ...iterations.map((e) => e.tokens.prompt_total ?? 0));
  const first = iterations[0];
  const last = iterations[iterations.length - 1];

  return (
    <div style={{ marginBottom: 34 }}>
      <div style={{ marginBottom: 14 }}>
        <h2 style={{ fontSize: 22, fontWeight: 700, letterSpacing: -0.3, margin: "0 0 4px" }}>Improvement loop</h2>
        <div style={{ fontSize: 13, color: "var(--text-2)" }}>
          Five iterations over the frozen 61-item subset, every run rescored under the final scorer for apples-to-apples.
        </div>
      </div>

      {state === "loading" && <div style={{ fontSize: 13, color: "var(--text-3)" }}>Loading iteration trend…</div>}
      {state === "missing" && (
        <div style={{ fontSize: 13, color: "var(--text-3)" }}>
          No <code style={{ fontFamily: MONO }}>results/iterations/report_data.json</code> yet — run the improve-eval analysis
          pipeline to build the cumulative iteration dataset.
        </div>
      )}
      {state === "error" && <div style={{ fontSize: 13, color: "var(--red)" }}>Failed to load iteration trend: {error}</div>}

      {state === "ready" && iterations.length > 0 && (
        <Card style={{ overflow: "hidden" }}>
          {first && last && (
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 10,
                padding: "12px 20px",
                borderBottom: "1px solid var(--line)",
                background: "var(--surface-2)",
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>Correct of 61</span>
              <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700, color: "var(--text-3)" }}>{first.correct_of_61}</span>
              <span style={{ color: "var(--text-3)" }}>→</span>
              <span style={{ fontFamily: MONO, fontSize: 18, fontWeight: 700, color: cellColor(fraction(correctRatio(last))) }}>
                {last.correct_of_61}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 12.5, fontWeight: 600, color: "var(--green)" }}>
                +{last.correct_of_61 - first.correct_of_61}
              </span>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                across {iterations.length - 1} iterations · click a metric name below for its definition
              </span>
            </div>
          )}

          <div style={{ overflowX: "auto" }}>
            <div style={{ minWidth: 900 }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: TREND_GRID,
                  padding: "10px 20px",
                  borderBottom: "1px solid var(--line-strong)",
                  gap: 12,
                  background: "var(--surface-2)",
                }}
              >
                <span style={headerCellStyle}>Iteration</span>
                {TREND_COLS.map((col) => (
                  <span key={col.key} style={{ ...headerCellStyle, textAlign: "left" }}>
                    {col.header}
                    {col.sub && (
                      <span style={{ display: "block", fontWeight: 400, textTransform: "none", letterSpacing: 0, color: "var(--text-3)", fontSize: 10 }}>
                        {col.sub}
                      </span>
                    )}
                  </span>
                ))}
              </div>

              {iterations.map((entry, i) => (
                <div
                  key={entry.key}
                  style={{
                    display: "grid",
                    gridTemplateColumns: TREND_GRID,
                    padding: "12px 20px",
                    gap: 12,
                    alignItems: "center",
                    borderBottom: i < iterations.length - 1 ? "1px solid var(--line)" : "none",
                    background: entry.key === "baseline61" ? "color-mix(in srgb, var(--surface-2) 40%, transparent)" : "transparent",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text)" }}>{shortLabel(entry)}</div>
                    <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text-3)" }}>{entry.key}</div>
                  </div>
                  {TREND_COLS.map((col) => (
                    <div key={col.key}>{col.render(entry, { maxLatency, maxTokens })}</div>
                  ))}
                </div>
              ))}
            </div>
          </div>

          <div style={{ padding: "10px 20px", fontSize: 11.5, color: "var(--text-3)", background: "var(--surface-2)", borderTop: "1px solid var(--line)" }}>
            Judge means (groundedness / actionability / gold agreement, 1–5) are only run for the final iteration; “—” marks iterations
            with no judge pass.
          </div>
        </Card>
      )}
    </div>
  );
}

export function EvalsTab() {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const rows = comparison ? buildRows(comparison) : [];
  const selectedRow = rows.find((r) => r.key === selectedKey) ?? null;

  useEffect(() => {
    getEvalResults()
      .then((c) => {
        setComparison(c);
        setState("ready");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setState("missing");
        } else {
          setState("error");
          setError(err instanceof ApiError ? err.message : String(err));
        }
      });
  }, []);

  return (
    <section
      style={{
        maxWidth: selectedRow ? 1240 : 960,
        display: "grid",
        gridTemplateColumns: selectedRow ? "minmax(0,1fr) 320px" : "minmax(0,1fr)",
        gap: 28,
        alignItems: "start",
      }}
    >
      <div style={{ minWidth: 0, maxWidth: 960 }}>
      <ImprovementLoop />

      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 700, letterSpacing: -0.3, margin: "0 0 4px" }}>Baseline vs. agent</h2>
          <div style={{ fontSize: 13, color: "var(--text-2)" }}>
            Deterministic scores on the curated FinanceBench subset. Click a metric for detail.
          </div>
        </div>
        {comparison && (
          <div style={{ display: "flex", gap: 20, fontFamily: MONO, fontSize: 12.5, color: "var(--text-2)" }}>
            <span>
              <strong style={{ color: "var(--text)", fontSize: 16 }}>{comparison.subset.num_questions}</strong> questions
            </span>
            <span>
              <strong style={{ color: "var(--text)", fontSize: 16 }}>{comparison.subset.num_companies}</strong> companies
            </span>
          </div>
        )}
      </div>

      {state === "loading" && <div style={{ fontSize: 13, color: "var(--text-3)" }}>Loading…</div>}
      {state === "missing" && (
        <div style={{ fontSize: 13, color: "var(--text-3)" }}>
          No <code style={{ fontFamily: MONO }}>results/comparison.json</code> yet — run{" "}
          <code style={{ fontFamily: MONO }}>uv run evals/run.py --system baseline</code> and{" "}
          <code style={{ fontFamily: MONO }}>uv run evals/run.py --system agent</code> first.
        </div>
      )}
      {state === "error" && <div style={{ fontSize: 13, color: "var(--red)" }}>Failed to load eval results: {error}</div>}

      {state === "ready" && comparison && (
        <>
          <Card style={{ overflow: "hidden" }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0,1.5fr) 1fr 1fr",
                padding: "12px 20px",
                borderBottom: "1px solid var(--line-strong)",
                background: "var(--surface-2)",
              }}
            >
              <span style={headerCellStyle}>Metric</span>
              <span style={headerCellStyle}>{comparison.systems.baseline?.label ?? "Naive-RAG baseline"}</span>
              <span style={{ ...headerCellStyle, color: "var(--text)" }}>{comparison.systems.agent?.label ?? "Agent"}</span>
            </div>

            {rows.map((row, i) => {
              const active = row.key === selectedKey;
              return (
                <div
                  key={row.key}
                  onClick={() => setSelectedKey(active ? null : row.key)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedKey(active ? null : row.key);
                    }
                  }}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1.5fr) 1fr 1fr",
                    padding: "11px 20px 11px 17px",
                    borderBottom: "1px solid var(--line)",
                    borderLeft: `3px solid ${active ? "var(--accent)" : "transparent"}`,
                    alignItems: "center",
                    cursor: "pointer",
                    background: active
                      ? "var(--accent-soft)"
                      : i % 2 === 1
                        ? "color-mix(in srgb, var(--surface-2) 40%, transparent)"
                        : "transparent",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13.5, fontWeight: active ? 600 : 400, color: active ? "var(--accent-text)" : "var(--text)" }}>
                      {row.label}
                    </div>
                    {row.sub && <div style={{ fontSize: 11.5, color: "var(--text-3)", marginTop: 1 }}>{row.sub}</div>}
                  </div>
                  <MetricValue cell={row.cell(comparison.systems.baseline as SystemMetrics | undefined)} />
                  <MetricValue cell={row.cell(comparison.systems.agent as SystemMetrics | undefined)} />
                </div>
              );
            })}

            <div style={{ padding: "11px 20px", fontSize: 12, color: "var(--text-3)", background: "var(--surface-2)" }}>
              {comparison.systems.published_reference?.label ?? "Published FinanceBench reference"}: context only, not the same subset — no
              per-metric numbers shown by design.
            </div>
          </Card>

          <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {(["baseline", "agent"] as const)
              .filter((key) => comparison.systems[key]?.notes)
              .map((key) => (
                <div key={key} style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--text-2)", borderLeft: "2px solid var(--line-strong)", paddingLeft: 12 }}>
                  <strong style={{ color: "var(--text)" }}>{comparison.systems[key]?.label ?? key}.</strong> {comparison.systems[key]?.notes}
                </div>
              ))}
          </div>
        </>
      )}
      </div>

      {selectedRow && comparison && (
        <MetricDetailPanel row={selectedRow} comparison={comparison} onClose={() => setSelectedKey(null)} />
      )}
    </section>
  );
}
