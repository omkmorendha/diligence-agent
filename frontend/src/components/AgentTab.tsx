// Agent tab - DiliAgent document-review pipeline (v1-spec section 12).
// Uploads a memo, streams review progress, and renders the finished report.

import type { ChangeEvent, CSSProperties, DragEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  annotatedReviewUrl,
  createReview,
  getReview,
  getReviewReport,
  getReviewReportHtml,
  listReviews,
  runFullReview,
  streamReviewEvents,
} from "../api";
import type {
  Citation,
  ClaimValue,
  ReviewCard,
  ReviewReport,
  ReviewReportClaim,
  ReviewStatus,
  ReviewStatusResponse,
  ReviewSummary,
  TraceEvent,
  Verdict,
} from "../types";
import { Card, MONO, Pill, SectionLabel } from "../ui";

const ACCEPT = ".pdf,.docx,.md";
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const VERDICTS: Verdict[] = [
  "SUPPORTED",
  "CONTRADICTED",
  "PARTIALLY_SUPPORTED",
  "NOT_IN_CORPUS",
  "OUT_OF_SCOPE",
  "UNVERIFIABLE",
];

// The 11-company FinanceBench corpus and the fiscal years covered per company
// (derived from data/subset.json gold evidence). Shown so users know what is
// verifiable before they upload — an out-of-corpus memo yields only OUT_OF_SCOPE.
const CORPUS: { company: string; periods: string }[] = [
  { company: "Adobe", periods: "FY2015-17, FY2022" },
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

const STATUS_META: Record<ReviewStatus, [string, string]> = {
  queued: ["var(--text-3)", "var(--surface-2)"],
  running: ["var(--accent-text)", "var(--accent-soft)"],
  completed: ["var(--green)", "var(--green-soft)"],
  failed: ["var(--red)", "var(--red-soft)"],
  out_of_scope: ["var(--amber)", "var(--amber-soft)"],
};

const VERDICT_META: Record<Verdict, [string, string]> = {
  SUPPORTED: ["var(--green)", "var(--green-soft)"],
  CONTRADICTED: ["var(--red)", "var(--red-soft)"],
  PARTIALLY_SUPPORTED: ["var(--amber)", "var(--amber-soft)"],
  NOT_IN_CORPUS: ["var(--text-3)", "var(--surface-2)"],
  OUT_OF_SCOPE: ["var(--amber)", "var(--amber-soft)"],
  UNVERIFIABLE: ["var(--text-3)", "var(--surface-2)"],
};

const SUMMARY_FIELDS: { key: keyof ReviewSummary; label: string; color: string; bg: string }[] = [
  { key: "supported", label: "Supported", color: "var(--green)", bg: "var(--green-soft)" },
  { key: "contradicted", label: "Contradicted", color: "var(--red)", bg: "var(--red-soft)" },
  { key: "partially_supported", label: "Partial", color: "var(--amber)", bg: "var(--amber-soft)" },
  { key: "not_in_corpus", label: "Not in corpus", color: "var(--text-3)", bg: "var(--surface-2)" },
  { key: "out_of_scope", label: "Out of scope", color: "var(--amber)", bg: "var(--amber-soft)" },
  { key: "unverifiable", label: "Unverifiable", color: "var(--text-3)", bg: "var(--surface-2)" },
  { key: "skipped", label: "Skipped", color: "var(--text-3)", bg: "var(--surface-2)" },
  { key: "error", label: "Error", color: "var(--red)", bg: "var(--red-soft)" },
];

const buttonStyle: CSSProperties = {
  border: "none",
  borderRadius: 8,
  background: "var(--accent)",
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
  boxShadow: "var(--shadow)",
  padding: "8px 14px",
};

const subtleButtonStyle: CSSProperties = {
  border: "1px solid var(--line-strong)",
  borderRadius: 8,
  background: "var(--surface)",
  color: "var(--text-2)",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
  padding: "7px 12px",
};

function toErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
}

function formatStatus(status: ReviewStatus): string {
  return status.replace(/_/g, " ");
}

function formatVerdict(verdict: Verdict): string {
  return verdict.toLowerCase().replace(/_/g, " ");
}

function isVerdict(value: unknown): value is Verdict {
  return typeof value === "string" && VERDICTS.includes(value as Verdict);
}

function formatDate(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.valueOf()) ? ts : d.toLocaleString();
}

function formatFileSize(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatClaimValue(value: ClaimValue | null | undefined): string {
  if (!value) return "-";
  const hasValue = value.value !== null && value.value !== undefined;
  if (!hasValue && !value.unit) return "-";
  return [hasValue ? String(value.value) : null, value.unit].filter(Boolean).join(" ");
}

function payloadLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.map(payloadLabel).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function eventQuote(event: TraceEvent): string {
  const p = event.payload;
  return payloadLabel(p.quote ?? p.claim ?? p.claim_text ?? p.text ?? event.detail);
}

function eventVerdict(event: TraceEvent): Verdict | null {
  const value = event.payload.verdict;
  return isVerdict(value) ? value : null;
}

function StatusPill({ status }: { status: ReviewStatus }) {
  const [color, bg] = STATUS_META[status];
  return (
    <Pill color={color} bg={bg}>
      {formatStatus(status)}
    </Pill>
  );
}

function VerdictPill({ verdict }: { verdict: Verdict }) {
  const [color, bg] = VERDICT_META[verdict];
  return (
    <Pill color={color} bg={bg}>
      {formatVerdict(verdict)}
    </Pill>
  );
}

function CorpusCoverage() {
  return (
    <Card style={{ padding: 18 }}>
      <SectionLabel style={{ marginBottom: 8 }}>Corpus coverage</SectionLabel>
      <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.5, marginBottom: 14 }}>
        Claims are only verifiable for these 11 companies and fiscal years. Anything outside this corpus is reported as out
        of scope, not incorrect.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 8 }}>
        {CORPUS.map((c) => (
          <div
            key={c.company}
            style={{
              display: "grid",
              gap: 2,
              padding: "9px 10px",
              borderRadius: 9,
              background: "var(--surface-2)",
              border: "1px solid var(--line)",
            }}
          >
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>{c.company}</div>
            <div style={{ fontSize: 11, color: "var(--text-3)", fontFamily: MONO }}>{c.periods}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function SummaryChips({ summary }: { summary: ReviewSummary }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      <Pill color="var(--text)" bg="var(--surface-2)">
        {summary.total_claims} claims
      </Pill>
      {SUMMARY_FIELDS.map((field) => (
        <Pill key={field.key} color={field.color} bg={field.bg}>
          {field.label}: {summary[field.key]}
        </Pill>
      ))}
    </div>
  );
}

function ScopeBreakdown({ event }: { event: TraceEvent | null }) {
  if (!event) {
    return <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>Waiting for scope classification...</div>;
  }
  const entries = Object.entries(event.payload).filter(([, value]) => value !== null && value !== undefined && value !== "");
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {entries.length === 0 && <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>{event.detail || "Scope checked."}</span>}
      {entries.map(([key, value]) => (
        <code
          key={key}
          style={{
            fontFamily: MONO,
            fontSize: 11,
            color: "var(--text-2)",
            background: "var(--surface-2)",
            border: "1px solid var(--line)",
            borderRadius: 7,
            padding: "4px 7px",
          }}
        >
          {key}: {payloadLabel(value)}
        </code>
      ))}
    </div>
  );
}

function ProgressPanel({
  reviewId,
  status,
  events,
  report,
}: {
  reviewId: string | null;
  status: ReviewStatus | null;
  events: TraceEvent[];
  report: ReviewReport | null;
}) {
  const extractedEvents = events.filter((event) => event.type === "claim_extracted");
  const verdictEvents = events.filter((event) => event.type === "claim_verdict");
  const scopeEvent = [...events].reverse().find((event) => event.type === "scope_check") ?? null;
  const totalClaims = report?.summary.total_claims ?? Math.max(extractedEvents.length, verdictEvents.length);
  const currentStatus = status ?? (events.length > 0 ? "running" : "queued");

  if (!reviewId) {
    return (
      <Card style={{ padding: 20 }}>
        <SectionLabel>Review progress</SectionLabel>
        <div style={{ fontSize: 13, color: "var(--text-3)", lineHeight: 1.5 }}>
          Upload a document to see extraction, scope checks, and per-claim verdicts stream in live.
        </div>
      </Card>
    );
  }

  return (
    <Card style={{ padding: 20, display: "grid", gap: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <SectionLabel style={{ marginBottom: 4 }}>Review progress</SectionLabel>
          <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--text-3)", wordBreak: "break-all" }}>{reviewId}</div>
        </div>
        <StatusPill status={currentStatus} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
        {[
          { value: extractedEvents.length, label: "claims extracted" },
          { value: totalClaims > 0 ? `${verdictEvents.length}/${totalClaims}` : verdictEvents.length, label: "verdicts" },
          { value: events.length, label: "trace events" },
        ].map((tile) => (
          <div key={tile.label} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px" }}>
            <div style={{ fontFamily: MONO, fontSize: 18, fontWeight: 600 }}>{tile.value}</div>
            <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 2 }}>{tile.label}</div>
          </div>
        ))}
      </div>

      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 8 }}>
          Scope breakdown
        </div>
        <ScopeBreakdown event={scopeEvent} />
      </div>

      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 8 }}>
          Verdict ticker
        </div>
        {verdictEvents.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>Waiting for claim verdicts...</div>}
        <div style={{ display: "grid", gap: 8 }}>
          {verdictEvents.slice(-8).map((event, index) => {
            const verdict = eventVerdict(event);
            return (
              <div
                key={`${event.seq}-${index}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) auto",
                  gap: 10,
                  alignItems: "center",
                  border: "1px solid var(--line)",
                  borderRadius: 9,
                  padding: "9px 11px",
                  background: "var(--surface)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {eventQuote(event)}
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text-3)", marginTop: 2 }}>
                    {event.item_id ?? (event.payload.claim_id ? payloadLabel(event.payload.claim_id) : `event ${event.seq}`)}
                  </div>
                </div>
                {verdict ? <VerdictPill verdict={verdict} /> : <Pill color="var(--text-3)" bg="var(--surface-2)">pending</Pill>}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 8 }}>
          Claims extracted
        </div>
        {extractedEvents.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>Waiting for extracted claims...</div>}
        <div style={{ display: "grid", gap: 6 }}>
          {extractedEvents.slice(-6).map((event) => (
            <div key={event.seq} style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.45, borderLeft: "2px solid var(--accent)", paddingLeft: 10 }}>
              {eventQuote(event)}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function CitationLinks({ claim, citations }: { claim: ReviewReportClaim["claim"]; citations: Citation[] }) {
  if (citations.length === 0) return <span style={{ color: "var(--text-3)" }}>-</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {citations.map((citation, index) => {
        const company = claim.company || "unknown";
        const href = `/corpus/${encodeURIComponent(company)}/${encodeURIComponent(citation.doc_id)}/page/${citation.pdf_page}`;
        return (
          <a
            key={citation.citation_id}
            href={href}
            title={`${citation.doc_name} p${citation.page_label ?? citation.pdf_page}: ${citation.quote}`}
            style={{
              fontFamily: MONO,
              fontSize: 11,
              fontWeight: 600,
              color: "var(--accent-text)",
              background: "var(--accent-soft)",
              border: "1px solid var(--accent-line)",
              borderRadius: 6,
              textDecoration: "none",
              padding: "2px 6px",
            }}
          >
            {index + 1} p{citation.page_label ?? citation.pdf_page}
          </a>
        );
      })}
    </div>
  );
}

function ClaimTable({ report }: { report: ReviewReport }) {
  return (
    <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 12 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 820 }}>
        <thead>
          <tr style={{ background: "var(--surface-2)" }}>
            {["Claim", "Verdict", "Document value", "Corpus value", "Citations"].map((header) => (
              <th
                key={header}
                style={{
                  textAlign: "left",
                  padding: "10px 12px",
                  fontSize: 11,
                  color: "var(--text-3)",
                  textTransform: "uppercase",
                  letterSpacing: 0.6,
                  borderBottom: "1px solid var(--line)",
                }}
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {report.claims.map((row) => {
            const result = row.result ?? null;
            return (
              <tr key={row.claim.claim_id} style={{ borderBottom: "1px solid var(--line)" }}>
                <td style={{ padding: "12px", verticalAlign: "top", width: "38%" }}>
                  <div style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.45 }}>{row.claim.quote}</div>
                  <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text-3)", marginTop: 5 }}>
                    {row.claim.company || "unknown"} {row.claim.period ? `- ${row.claim.period}` : ""}
                  </div>
                </td>
                <td style={{ padding: "12px", verticalAlign: "top" }}>
                  {result ? <VerdictPill verdict={result.verdict} /> : <Pill color="var(--text-3)" bg="var(--surface-2)">pending</Pill>}
                  {result?.confidence && (
                    <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 6, textTransform: "capitalize" }}>{result.confidence} confidence</div>
                  )}
                </td>
                <td style={{ padding: "12px", verticalAlign: "top", fontFamily: MONO, fontSize: 12, color: "var(--text-2)" }}>
                  {formatClaimValue(result?.doc_value)}
                </td>
                <td style={{ padding: "12px", verticalAlign: "top", fontFamily: MONO, fontSize: 12, color: "var(--text-2)" }}>
                  {formatClaimValue(result?.corpus_value)}
                </td>
                <td style={{ padding: "12px", verticalAlign: "top" }}>
                  <CitationLinks claim={row.claim} citations={result?.citations ?? []} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ResultsView({
  reviewId,
  status,
  report,
  reportHtml,
  artifactError,
  fullError,
  runningFull,
  onRunFull,
}: {
  reviewId: string | null;
  status: ReviewStatusResponse | null;
  report: ReviewReport | null;
  reportHtml: string | null;
  artifactError: string | null;
  fullError: string | null;
  runningFull: boolean;
  onRunFull: () => void;
}) {
  if (!reviewId) return null;
  const isTerminal = status?.status === "completed" || status?.status === "out_of_scope" || status?.status === "failed";
  const verifiedClaims = report?.claims.filter((row) => row.result).length ?? 0;
  const totalClaims = report?.summary.total_claims ?? report?.claims.length ?? 0;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {status?.pilot && isTerminal && (
        <Card
          style={{
            padding: "13px 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            background: "var(--amber-soft)",
          }}
        >
          <div style={{ fontSize: 13, color: "var(--amber)", fontWeight: 600 }}>
            Pilot: {verifiedClaims} of {totalClaims || "N"} claims verified
          </div>
          <button onClick={onRunFull} disabled={runningFull} style={{ ...buttonStyle, opacity: runningFull ? 0.65 : 1 }}>
            {runningFull ? "Starting..." : "Run full review"}
          </button>
        </Card>
      )}

      {fullError && <div style={{ fontSize: 13, color: "var(--red)" }}>Failed to start full review: {fullError}</div>}
      {artifactError && <div style={{ fontSize: 13, color: "var(--red)" }}>Failed to load review artifacts: {artifactError}</div>}

      {report && (
        <Card style={{ padding: 20, display: "grid", gap: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
            <div>
              <SectionLabel style={{ marginBottom: 6 }}>Results</SectionLabel>
              <h2 style={{ fontSize: 21, letterSpacing: -0.25, margin: 0 }}>{report.filename}</h2>
              <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--text-3)", marginTop: 4 }}>
                {report.format.toUpperCase()} - {report.company_scope.join(", ") || "scope unknown"}
              </div>
            </div>
            <a href={annotatedReviewUrl(reviewId)} download style={{ ...subtleButtonStyle, textDecoration: "none" }}>
              Download annotated document
            </a>
          </div>
          <SummaryChips summary={report.summary} />
          <ClaimTable report={report} />
        </Card>
      )}

      {reportHtml && (
        <Card style={{ padding: 20 }}>
          <SectionLabel>HTML report</SectionLabel>
          <iframe
            title="Review report"
            srcDoc={reportHtml}
            sandbox=""
            style={{
              width: "100%",
              minHeight: 520,
              border: "1px solid var(--line)",
              borderRadius: 10,
              background: "#fff",
            }}
          />
        </Card>
      )}
    </div>
  );
}

function HistoryRail({
  reviews,
  activeReviewId,
  historyError,
  onSelect,
}: {
  reviews: ReviewCard[];
  activeReviewId: string | null;
  historyError: string | null;
  onSelect: (reviewId: string) => void;
}) {
  return (
    <Card style={{ padding: 18 }}>
      <SectionLabel style={{ marginBottom: 10 }}>Review history</SectionLabel>
      {historyError && <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 10 }}>Failed to load history: {historyError}</div>}
      {reviews.length === 0 && <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>No reviews yet.</div>}
      <div style={{ display: "grid", gap: 8 }}>
        {reviews.map((review) => {
          const active = review.review_id === activeReviewId;
          return (
            <button
              key={review.review_id}
              onClick={() => onSelect(review.review_id)}
              style={{
                textAlign: "left",
                background: "var(--surface)",
                border: `1px solid ${active ? "var(--accent-line)" : "var(--line)"}`,
                borderRadius: 10,
                padding: "10px 12px",
                cursor: "pointer",
                fontFamily: "inherit",
                boxShadow: active ? "0 0 0 3px var(--accent-soft)" : "none",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {review.filename}
                </span>
                <StatusPill status={review.status} />
              </div>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text-3)", wordBreak: "break-all" }}>{review.review_id}</div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, fontSize: 11, color: "var(--text-3)" }}>
                <span>{review.format.toUpperCase()}</span>
                <span>{review.pilot ? "pilot" : "full"}</span>
                <span>{formatDate(review.created_at)}</span>
              </div>
              {review.summary && (
                <div style={{ fontSize: 11, color: "var(--text-2)", marginTop: 6 }}>
                  {review.summary.supported} supported - {review.summary.contradicted} contradicted - {review.summary.total_claims} total
                </div>
              )}
            </button>
          );
        })}
      </div>
    </Card>
  );
}

export function AgentTab() {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [reviews, setReviews] = useState<ReviewCard[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [currentReviewId, setCurrentReviewId] = useState<string | null>(null);
  const [status, setStatus] = useState<ReviewStatusResponse | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [reportHtml, setReportHtml] = useState<string | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [fullError, setFullError] = useState<string | null>(null);
  const [runningFull, setRunningFull] = useState(false);
  const closeStreamRef = useRef<(() => void) | null>(null);

  const refreshReviews = useCallback(() => {
    listReviews()
      .then((items) => {
        setReviews(items);
        setHistoryError(null);
      })
      .catch((err) => setHistoryError(toErrorMessage(err)));
  }, []);

  const loadReviewArtifacts = useCallback(async (reviewId: string) => {
    setArtifactError(null);
    try {
      const nextStatus = await getReview(reviewId);
      setStatus(nextStatus);
      const shouldLoadArtifacts =
        nextStatus.status === "completed" || nextStatus.status === "out_of_scope" || nextStatus.status === "failed";
      if (!shouldLoadArtifacts) return;

      const [jsonResult, htmlResult] = await Promise.allSettled([
        getReviewReport(reviewId),
        getReviewReportHtml(reviewId),
      ]);
      if (jsonResult.status === "fulfilled") {
        setReport(jsonResult.value);
      } else if (nextStatus.status !== "failed") {
        setArtifactError(toErrorMessage(jsonResult.reason));
      }
      if (htmlResult.status === "fulfilled") {
        setReportHtml(htmlResult.value);
      }
    } catch (err) {
      setArtifactError(toErrorMessage(err));
    }
  }, []);

  const openReview = useCallback(
    (reviewId: string) => {
      closeStreamRef.current?.();
      setCurrentReviewId(reviewId);
      setStatus(null);
      setEvents([]);
      setReport(null);
      setReportHtml(null);
      setArtifactError(null);
      setFullError(null);
      void loadReviewArtifacts(reviewId);
      closeStreamRef.current = streamReviewEvents<TraceEvent>(
        reviewId,
        (event) =>
          setEvents((prev) => {
            if (prev.some((existing) => existing.seq === event.seq)) return prev;
            return [...prev, event].sort((a, b) => a.seq - b.seq);
          }),
        () => {
          refreshReviews();
          void loadReviewArtifacts(reviewId);
        },
      );
    },
    [loadReviewArtifacts, refreshReviews],
  );

  useEffect(() => {
    refreshReviews();
    return () => closeStreamRef.current?.();
  }, [refreshReviews]);

  const currentStatus = useMemo<ReviewStatus | null>(() => {
    if (status) return status.status;
    const card = reviews.find((review) => review.review_id === currentReviewId);
    if (card) return card.status;
    return currentReviewId ? "queued" : null;
  }, [currentReviewId, reviews, status]);

  function validateFile(file: File): string | null {
    const lower = file.name.toLowerCase();
    if (!lower.endsWith(".pdf") && !lower.endsWith(".docx") && !lower.endsWith(".md")) {
      return "Upload a PDF, DOCX, or Markdown file.";
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      return `File is ${formatFileSize(file.size)}. Reviews are limited to 20.0 MB.`;
    }
    return null;
  }

  async function uploadFile(file: File) {
    const validationError = validateFile(file);
    if (validationError) {
      setUploadError(validationError);
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const created = await createReview(file, true);
      refreshReviews();
      openReview(created.review_id);
    } catch (err) {
      setUploadError(toErrorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void uploadFile(file);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void uploadFile(file);
  }

  async function startFullReview() {
    if (!currentReviewId) return;
    setRunningFull(true);
    setFullError(null);
    try {
      await runFullReview(currentReviewId);
      refreshReviews();
      openReview(currentReviewId);
    } catch (err) {
      setFullError(toErrorMessage(err));
    } finally {
      setRunningFull(false);
    }
  }

  return (
    <section style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 330px", gap: 28, alignItems: "start" }}>
      <div style={{ minWidth: 0, display: "grid", gap: 20 }}>
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1 style={{ fontSize: 24, fontWeight: 700, letterSpacing: -0.35, margin: 0 }}>Review a document</h1>
            {currentStatus && <StatusPill status={currentStatus} />}
          </div>
          <div style={{ fontSize: 14, color: "var(--text-2)", lineHeight: 1.5, maxWidth: 760 }}>
            Upload a draft diligence document. DiliAgent extracts every material claim and verifies it against the
            FinanceBench filing corpus, then returns an annotated document with verdicts and citations.
          </div>
        </div>

        <Card style={{ padding: 4 }}>
          <label
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            style={{
              display: "grid",
              placeItems: "center",
              gap: 10,
              padding: "40px 24px",
              borderRadius: 10,
              border: `1.5px dashed ${dragging ? "var(--accent-line)" : "var(--line-strong)"}`,
              background: dragging ? "var(--accent-soft)" : "var(--surface-2)",
              textAlign: "center",
              cursor: uploading ? "default" : "pointer",
              transition: "background 120ms, border-color 120ms",
              opacity: uploading ? 0.72 : 1,
            }}
          >
            <div
              style={{
                width: 42,
                height: 42,
                borderRadius: 10,
                display: "grid",
                placeItems: "center",
                background: "var(--surface)",
                border: "1px solid var(--line)",
                color: "var(--text-3)",
                fontSize: 18,
                fontFamily: MONO,
              }}
            >
              UP
            </div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {uploading ? "Uploading review..." : "Drag a document here, or click to browse"}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-3)" }}>
              PDF, DOCX, or Markdown - up to 20 MB - starts as an 8-claim pilot
            </div>
            <input type="file" accept={ACCEPT} disabled={uploading} hidden onChange={handleInputChange} />
          </label>
        </Card>

        {uploadError && <div style={{ fontSize: 13, color: "var(--red)" }}>Upload failed: {uploadError}</div>}

        <ProgressPanel reviewId={currentReviewId} status={currentStatus} events={events} report={report} />

        <ResultsView
          reviewId={currentReviewId}
          status={status}
          report={report}
          reportHtml={reportHtml}
          artifactError={artifactError}
          fullError={fullError}
          runningFull={runningFull}
          onRunFull={startFullReview}
        />
      </div>

      <aside style={{ minWidth: 0, display: "grid", gap: 16 }}>
        <HistoryRail reviews={reviews} activeReviewId={currentReviewId} historyError={historyError} onSelect={openReview} />
        <CorpusCoverage />
      </aside>
    </section>
  );
}
