import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError, getLatestEvalIteration } from "../api";
import type { IterativeEvalReport } from "../types";
import { Card, MONO } from "../ui";

const METRIC_LABELS: Record<string, string> = {
  answer_accuracy: "Accuracy",
  citation_precision: "Citation precision",
  citation_provenance: "Citation provenance",
  arithmetic_integrity: "Arithmetic",
  trace_shape: "Trace shape",
  abstention_correct_rate: "Abstention",
};

const COLORS = ["var(--accent)", "var(--green)", "var(--amber)", "var(--red)", "var(--text-3)", "var(--text-2)"];

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metric(metrics: Record<string, unknown>, key: string): number | null {
  return asNumber(metrics[key]);
}

function pct(value: unknown): number | null {
  const n = asNumber(value);
  return n == null ? null : Math.round(n * 1000) / 10;
}

function fmtPct(value: unknown): string {
  const n = pct(value);
  return n == null ? "-" : `${n.toFixed(1)}%`;
}

function fmtSeconds(value: unknown): string {
  const n = asNumber(value);
  if (n == null) return "-";
  if (n < 60) return `${n.toFixed(1)}s`;
  return `${(n / 60).toFixed(1)}m`;
}

function SectionTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <h3 style={{ margin: 0, fontSize: 16, letterSpacing: -0.2 }}>{title}</h3>
      {sub && <div style={{ marginTop: 3, fontSize: 12.5, color: "var(--text-2)" }}>{sub}</div>}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <div style={{ fontSize: 13, color: "var(--text-3)" }}>{message}</div>;
}

function TrendChart({ report }: { report: IterativeEvalReport }) {
  const data = report.iterations.map((iteration) => ({
    iteration: `i${iteration.iteration}`,
    Accuracy: pct(metric(iteration.metrics, "answer_accuracy")),
    Citations: pct(metric(iteration.metrics, "citation_precision")),
    Arithmetic: pct(metric(iteration.metrics, "arithmetic_integrity")),
    "Trace shape": pct(metric(iteration.metrics, "trace_shape")),
  }));

  return (
    <Card>
      <SectionTitle
        title="Metric Trends"
        sub="Per-iteration deterministic scores; use the cumulative card below to distinguish variance from steady movement."
      />
      <div style={{ height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 18, left: -12, bottom: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="iteration" stroke="var(--text-3)" tick={{ fontSize: 12 }} />
            <YAxis stroke="var(--text-3)" tick={{ fontSize: 12 }} domain={[0, 100]} unit="%" />
            <Tooltip
              formatter={(value) => [`${Number(value).toFixed(1)}%`, ""]}
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8 }}
            />
            <Legend />
            {["Accuracy", "Citations", "Arithmetic", "Trace shape"].map((name, i) => (
              <Line key={name} type="monotone" dataKey={name} stroke={COLORS[i]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function StageChart({ report }: { report: IterativeEvalReport }) {
  const data = report.iterations.map((iteration) => ({
    iteration: `i${iteration.iteration}`,
    ...iteration.stage_seconds,
  }));
  const stages = Array.from(new Set(report.iterations.flatMap((iteration) => Object.keys(iteration.stage_seconds))));

  return (
    <Card>
      <SectionTitle title="Stage Duration Waterfall" sub="Stacked by trace-derived stage buckets. Exact LLM-call latency remains a missing metric." />
      {stages.length === 0 ? (
        <EmptyState message="No stage timing data available yet." />
      ) : (
        <div style={{ height: 285 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 18, left: -12, bottom: 0 }}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis dataKey="iteration" stroke="var(--text-3)" tick={{ fontSize: 12 }} />
              <YAxis stroke="var(--text-3)" tick={{ fontSize: 12 }} tickFormatter={(v) => fmtSeconds(v)} />
              <Tooltip
                formatter={(value, name) => [fmtSeconds(value), String(name)]}
                contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8 }}
              />
              <Legend />
              {stages.map((stage, i) => (
                <Bar key={stage} dataKey={stage} stackId="stage" fill={COLORS[i % COLORS.length]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

function BottleneckCharts({ report }: { report: IterativeEvalReport }) {
  const bucketData = Object.entries(report.bottlenecks.failures_by_bucket ?? {}).map(([bucket, failures]) => ({ bucket, failures }));
  const companyData = Object.entries(report.bottlenecks.failures_by_company ?? {}).map(([company, failures]) => ({ company, failures }));

  return (
    <Card>
      <SectionTitle title="Accuracy Bottlenecks" sub="Counts of failed scoring checks across all completed iteration runs." />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div style={{ height: 230 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bucketData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis dataKey="bucket" stroke="var(--text-3)" tick={{ fontSize: 11 }} />
              <YAxis allowDecimals={false} stroke="var(--text-3)" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8 }} />
              <Bar dataKey="failures" fill="var(--amber)" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div style={{ height: 230 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={companyData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis dataKey="company" stroke="var(--text-3)" tick={{ fontSize: 11 }} />
              <YAxis allowDecimals={false} stroke="var(--text-3)" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8 }} />
              <Bar dataKey="failures" fill="var(--red)" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </Card>
  );
}

function SummaryCards({ report }: { report: IterativeEvalReport }) {
  const overall = report.overall;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 12 }}>
      {["answer_accuracy", "citation_precision", "arithmetic_integrity", "trace_shape"].map((key) => (
        <Card key={key} style={{ padding: 14 }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: 0.7 }}>{METRIC_LABELS[key]}</div>
          <div style={{ marginTop: 8, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: "var(--text)" }}>{fmtPct(overall[key])}</div>
        </Card>
      ))}
    </div>
  );
}

function RegressionCards({ report }: { report: IterativeEvalReport }) {
  const latest = report.regressions[report.regressions.length - 1];
  const deltas = latest?.deltas ?? [];
  return (
    <Card>
      <SectionTitle title="Improvements And Regressions" sub="Iteration-over-iteration deltas, with the latest transition shown first." />
      {deltas.length === 0 ? (
        <EmptyState message="Need at least two completed iterations to compute deltas." />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 10 }}>
          {deltas.map((delta) => {
            const color = delta.delta > 0 ? "var(--green)" : delta.delta < 0 ? "var(--red)" : "var(--text-3)";
            return (
              <div key={delta.metric} style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12 }}>
                <div style={{ fontSize: 12.5, color: "var(--text)" }}>{METRIC_LABELS[delta.metric] ?? delta.metric}</div>
                <div style={{ marginTop: 8, fontFamily: MONO, color, fontWeight: 700 }}>
                  {delta.delta >= 0 ? "+" : ""}
                  {(delta.delta * 100).toFixed(1)} pts
                </div>
                <div style={{ marginTop: 4, fontSize: 11.5, color: "var(--text-3)" }}>
                  {fmtPct(delta.previous)} to {fmtPct(delta.current)}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function FailureList({ report }: { report: IterativeEvalReport }) {
  const failures = report.bottlenecks.repeated_failure_items ?? [];
  return (
    <Card>
      <SectionTitle title="Repeated Failure Clusters" sub="Items that repeatedly fail answer, citation, arithmetic, or trace-shape scoring." />
      {failures.length === 0 ? (
        <EmptyState message="No repeated failure clusters found in the available report." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {failures.slice(0, 6).map((failure) => (
            <div key={`${failure.item_id}-${failure.metric}`} style={{ borderTop: "1px solid var(--line)", paddingTop: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <strong style={{ fontSize: 12.5 }}>{failure.item_id}</strong>
                <span style={{ fontFamily: MONO, fontSize: 12, color: "var(--red)" }}>{failure.failures} failures</span>
              </div>
              <div style={{ marginTop: 3, fontSize: 12, color: "var(--text-2)" }}>{failure.metric}</div>
              <div style={{ marginTop: 3, fontSize: 12, color: "var(--text-3)", lineHeight: 1.45 }}>{failure.question}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function MissingMetrics({ report }: { report: IterativeEvalReport }) {
  return (
    <Card>
      <SectionTitle title="Useful Metrics Not Yet Available" sub="These are intentionally reported instead of silently omitting gaps." />
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {report.missing_metrics.map((metric) => (
          <div key={metric.metric} style={{ borderLeft: "2px solid var(--line-strong)", paddingLeft: 12 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>
              {metric.metric} <span style={{ color: metric.status === "partial" ? "var(--amber)" : "var(--red)" }}>({metric.status})</span>
            </div>
            <div style={{ marginTop: 3, fontSize: 12, color: "var(--text-2)", lineHeight: 1.5 }}>{metric.reason}</div>
            <div style={{ marginTop: 3, fontSize: 11.5, color: "var(--text-3)", lineHeight: 1.5 }}>{metric.needed_instrumentation}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function IterationAnalyticsTab() {
  const [report, setReport] = useState<IterativeEvalReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getLatestEvalIteration()
      .then((data) => {
        setReport(data);
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

  const latestCumulative = useMemo(
    () => (report && report.cumulative.length > 0 ? report.cumulative[report.cumulative.length - 1].metrics : null),
    [report],
  );

  return (
    <section style={{ maxWidth: 1180 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 700, letterSpacing: -0.3, margin: "0 0 4px" }}>Iterative eval analytics</h2>
          <div style={{ fontSize: 13, color: "var(--text-2)" }}>
            Repeated heavier-model runs, cumulative scoring, regressions, bottlenecks, and trace-derived timing.
          </div>
        </div>
        {report && (
          <div style={{ fontFamily: MONO, fontSize: 12, color: "var(--text-2)", textAlign: "right" }}>
            <div>{report.experiment_id}</div>
            <div>{report.model ?? "model unknown"}</div>
          </div>
        )}
      </div>

      {state === "loading" && <EmptyState message="Loading iterative eval analytics..." />}
      {state === "missing" && (
        <EmptyState message="No results/iterations/latest.json yet. Run `uv run --project backend scripts/run_iterative_eval.py --iterations 5`." />
      )}
      {state === "error" && <div style={{ fontSize: 13, color: "var(--red)" }}>Failed to load iteration analytics: {error}</div>}

      {state === "ready" && report && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <SummaryCards report={report} />
          {latestCumulative && (
            <Card style={{ padding: 14, fontSize: 12.5, color: "var(--text-2)" }}>
              Cumulative through iteration {report.cumulative[report.cumulative.length - 1]?.through_iteration}: accuracy{" "}
              <strong style={{ color: "var(--text)" }}>{fmtPct(metric(latestCumulative, "answer_accuracy"))}</strong>, citation precision{" "}
              <strong style={{ color: "var(--text)" }}>{fmtPct(metric(latestCumulative, "citation_precision"))}</strong>, arithmetic{" "}
              <strong style={{ color: "var(--text)" }}>{fmtPct(metric(latestCumulative, "arithmetic_integrity"))}</strong>.
            </Card>
          )}
          <TrendChart report={report} />
          <StageChart report={report} />
          <BottleneckCharts report={report} />
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <RegressionCards report={report} />
            <FailureList report={report} />
          </div>
          <MissingMetrics report={report} />
        </div>
      )}
    </section>
  );
}
