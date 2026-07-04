// Evals tab (spec section 24, Step 16).
// Renders the comparison table from GET /evals/results (results/comparison.json).
// Rows: answer accuracy, accuracy by bucket, citation precision, citation provenance,
// arithmetic integrity, groundedness judge, actionability judge.
// Columns: published reference (labeled "Context only, not same subset"),
// naive-RAG baseline, agent. Green/red emphasis.

import { useEffect, useState } from "react";
import { ApiError, getEvalResults } from "../api";
import type { Comparison, SystemMetrics } from "../types";

// Fixed column order (spec section 24); only columns present in the response render.
const SYSTEM_ORDER: { key: string; label: string; sublabel?: string }[] = [
  { key: "published_reference", label: "Published reference", sublabel: "Context only, not same subset" },
  { key: "baseline", label: "Naive-RAG baseline" },
  { key: "agent", label: "Agent" },
];

type Fraction = { kind: "fraction"; value: number | null | undefined };
type Score5 = { kind: "score5"; value: number | null | undefined };

function fraction(value: number | null | undefined): Fraction {
  return { kind: "fraction", value };
}
function score5(value: number | null | undefined): Score5 {
  return { kind: "score5", value };
}

interface Row {
  label: string;
  cell: (metrics: SystemMetrics | undefined) => Fraction | Score5;
}

function bucketRows(bucketKeys: string[]): Row[] {
  return bucketKeys.map((bucket) => ({
    label: `Answer accuracy — ${bucket}`,
    cell: (m) => fraction(m?.by_bucket?.[bucket]?.answer_accuracy),
  }));
}

function buildRows(bucketKeys: string[]): Row[] {
  return [
    { label: "Answer accuracy", cell: (m) => fraction(m?.answer_accuracy) },
    ...bucketRows(bucketKeys),
    { label: "Citation precision", cell: (m) => fraction(m?.citation_precision) },
    { label: "Citation provenance", cell: (m) => fraction(m?.citation_provenance) },
    { label: "Arithmetic integrity", cell: (m) => fraction(m?.arithmetic_integrity) },
    { label: "Groundedness (judge, 1–5)", cell: (m) => score5(m?.groundedness_judge) },
    { label: "Actionability (judge, 1–5)", cell: (m) => score5(m?.actionability_judge) },
  ];
}

// Green/red emphasis (spec section 24): thresholds are relative to each metric's own
// scale (fraction 0-1 vs judge 1-5), not compared across rows.
function cellColor(cell: Fraction | Score5): string | undefined {
  if (cell.value == null) return undefined;
  const ratio = cell.kind === "fraction" ? cell.value : (cell.value - 1) / 4;
  if (ratio >= 0.7) return "#1c7a3c";
  if (ratio >= 0.4) return "#a06a1c";
  return "#c0392b";
}

function formatCell(cell: Fraction | Score5): string {
  if (cell.value == null) return "—";
  return cell.kind === "fraction" ? `${Math.round(cell.value * 100)}%` : cell.value.toFixed(1);
}

export function EvalsTab() {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

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
    <section>
      <h2 style={{ fontSize: 16 }}>Evals</h2>

      {state === "loading" && <div style={{ fontSize: 13, color: "#888" }}>Loading…</div>}
      {state === "missing" && (
        <div style={{ fontSize: 13, color: "#888" }}>
          No <code>results/comparison.json</code> yet — run <code>uv run evals/run.py --system baseline</code> and{" "}
          <code>uv run evals/run.py --system agent</code> first.
        </div>
      )}
      {state === "error" && <div style={{ fontSize: 13, color: "#c0392b" }}>Failed to load eval results: {error}</div>}

      {state === "ready" && comparison && (
        <>
          <div style={{ fontSize: 13, color: "#666", marginBottom: 12 }}>
            {comparison.subset.num_questions} questions · {comparison.subset.num_companies} companies · created{" "}
            {comparison.created_at}
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "6px 10px", borderBottom: "2px solid #ddd" }}>Metric</th>
                  {SYSTEM_ORDER.filter((s) => comparison.systems[s.key]).map((s) => (
                    <th key={s.key} style={{ textAlign: "left", padding: "6px 10px", borderBottom: "2px solid #ddd" }}>
                      <div>{comparison.systems[s.key]?.label ?? s.label}</div>
                      {s.sublabel && (
                        <div style={{ fontSize: 11, fontWeight: 400, color: "#888", textTransform: "none" }}>
                          {s.sublabel}
                        </div>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {buildRows(Object.keys(comparison.subset.bucket_counts)).map((row) => (
                  <tr key={row.label}>
                    <td style={{ padding: "6px 10px", borderBottom: "1px solid #eee", color: "#333" }}>{row.label}</td>
                    {SYSTEM_ORDER.filter((s) => comparison.systems[s.key]).map((s) => {
                      const metrics = comparison.systems[s.key] as SystemMetrics | undefined;
                      if (s.key === "published_reference") {
                        // No per-subset numbers for this column by design (spec section 22) --
                        // the header sublabel already carries "context only, not same subset".
                        return (
                          <td key={s.key} style={{ padding: "6px 10px", borderBottom: "1px solid #eee", color: "#bbb" }}>
                            —
                          </td>
                        );
                      }
                      const cell = row.cell(metrics);
                      return (
                        <td
                          key={s.key}
                          style={{
                            padding: "6px 10px",
                            borderBottom: "1px solid #eee",
                            fontWeight: 600,
                            color: cellColor(cell),
                          }}
                        >
                          {formatCell(cell)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {SYSTEM_ORDER.filter((s) => s.key !== "published_reference" && comparison.systems[s.key]?.notes).map(
            (s) => (
              <div key={s.key} style={{ fontSize: 12, color: "#888", marginTop: 10 }}>
                <strong>{comparison.systems[s.key]?.label ?? s.label}:</strong> {comparison.systems[s.key]?.notes}
              </div>
            ),
          )}
        </>
      )}
    </section>
  );
}
