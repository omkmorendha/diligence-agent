// Manual mirror of backend/app/schemas.py (spec section 7). The backend schema is
// canonical; keep this in sync by hand. Spec sections 8-23.

export type DocType = "10k" | "10q" | "8k" | "other";
export type Unit = "USD millions" | "percent" | "ratio" | "text" | "other";
export type Bucket = "A_multi_input" | "B_judgment" | "C_lookup";
export type AnswerStatus = "answered" | "abstained";
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type VerdictBadge = "strong" | "mixed" | "failed" | "unknown";
export type Strategy = "single_lookup" | "multi_input_computation" | "judgment";
export type ToolName =
  | "search_filing"
  | "get_pages"
  | "calculate"
  | "record_answer"
  | "flag_outstanding";
export type TraceEventType =
  | "plan"
  | "scratchpad"
  | "retrieval"
  | "tool_call"
  | "tool_result"
  | "decision"
  | "citation"
  | "item_answer"
  | "verdict"
  | "error";

// --- section 9: trace event ---
export interface TraceEvent {
  schema_version: string;
  run_id: string;
  seq: number;
  ts: string;
  type: TraceEventType;
  title: string;
  detail: string;
  item_id: string | null;
  payload: Record<string, unknown>;
}

// --- section 13: retrieval chunk ---
export interface Chunk {
  chunk_id: string;
  company: string;
  doc_id: string;
  doc_name: string;
  doc_type: DocType;
  filing_period: string;
  page: number;
  text?: string;
  snippet?: string;
  score: number;
  char_start?: number;
  char_end?: number;
}

// --- section 11: citation ---
export interface Citation {
  citation_id: string;
  claim?: string;
  doc_id: string;
  doc_name: string;
  doc_type?: DocType;
  filing_period?: string;
  pdf_page: number;
  page_label?: string;
  chunk_id: string;
  quote: string;
  char_start: number;
  char_end: number;
  source_event_seq?: number;
}

// --- section 13: calculate ---
export interface FinancialInput {
  value: number;
  unit: Unit;
  period: string;
  citation_id: string;
}

// --- section 11 / 17: item answer ---
export interface Confidence {
  grounded_inputs: number;
  assumed_inputs: number;
}

export interface ItemAnswer {
  item_id: string;
  question?: string;
  answer: string;
  value?: number | null;
  unit: Unit;
  citations: Citation[];
  status: AnswerStatus;
  confidence: Confidence;
}

// --- section 17: memo ---
export interface MemoItem extends ItemAnswer {
  question: string;
}

export interface MemoSummary {
  items_total: number;
  items_answered: number;
  items_abstained: number;
  citations_total: number;
  calculate_calls: number;
}

export interface Memo {
  run_id: string;
  company: string;
  status: "completed" | "failed";
  created_at: string;
  completed_at?: string | null;
  items: MemoItem[];
  summary: MemoSummary;
}

// --- section 22: eval results ---
// Judge fields mirror the scorer output in results/agent.json: each judge score
// carries a *_coverage companion (share of items the judge actually scored), and
// judge_zero_variance flags a degenerate judge pass (all items same score).
export interface SystemMetrics {
  answer_accuracy: number;
  citation_precision: number;
  citation_provenance: number;
  arithmetic_integrity: number;
  trace_shape?: number | null;
  abstention_correct_rate?: number | null;
  groundedness_judge?: number | null;
  groundedness_judge_coverage?: number | null;
  actionability_judge?: number | null;
  actionability_judge_coverage?: number | null;
  gold_agreement_judge?: number | null;
  gold_agreement_judge_coverage?: number | null;
  judge_zero_variance?: boolean | null;
  by_bucket: Record<string, { answer_accuracy: number }>;
  label?: string;
  notes?: string;
}

export interface Comparison {
  created_at: string;
  subset: {
    num_questions: number;
    num_companies: number;
    bucket_counts: Record<string, number>;
  };
  systems: Record<string, SystemMetrics>;
}

// --- improvement-loop cumulative dataset ---
// Mirror of results/iterations/report_data.json (GET /evals/iterations), built by
// the analysis pipeline. baseline61 + iter1..iter5, all rescored under the final
// (v4) scorer. Only the fields the EvalsTab trend view consumes are typed strictly;
// the report also carries score_versions / churn / plans which the tab does not read.
export interface IterationAggregate {
  answer_accuracy: number;
  citation_precision: number;
  citation_provenance: number;
  arithmetic_integrity: number;
  trace_shape?: number | null;
  abstention_correct_rate?: number | null;
  n_items: number;
  answered: number;
  abstained: number;
}

export interface IterationJudges {
  groundedness_judge?: number;
  groundedness_judge_coverage?: number;
  actionability_judge?: number;
  actionability_judge_coverage?: number;
  gold_agreement_judge?: number;
  gold_agreement_judge_coverage?: number;
  judge_zero_variance?: boolean;
}

export interface IterationTiming {
  item_wall_p50_s?: number;
  item_wall_p95_s?: number;
  item_wall_mean_s?: number;
  mean_llm_call_s?: number;
  total_llm_s?: number;
  run_wall_max_s?: number;
}

export interface IterationTokens {
  prompt_total: number;
  completion_total: number;
  by_purpose?: Record<string, unknown>;
}

export interface IterationEntry {
  key: string;
  label: string;
  aggregate: IterationAggregate;
  correct_of_61: number;
  by_bucket: Record<string, { answer_accuracy: number }>;
  timing: IterationTiming;
  tokens: IterationTokens;
  judges: IterationJudges;
}

export interface IterationsReport {
  iterations: IterationEntry[];
}

// --- section 8: curated benchmark item ---
// Mirror of schemas.py SubsetItem. The gold/eval-only fields (including the
// gold_polarity / gold_canonical annotation additions) NEVER reach this
// frontend at runtime — every API surface strips a SubsetItem down to
// AgentVisibleItem first (no-gold-leakage). Typed here only to keep the mirror
// faithful to the canonical schema.
export interface Tolerance {
  relative?: number | null;
  absolute?: number | null;
}

export interface GoldEvidence {
  doc_id: string;
  doc_name: string;
  doc_type: DocType;
  filing_period: string;
  pdf_page: number;
  page_label: string;
  evidence_text: string;
}

export interface SubsetItem {
  item_id: string;
  question_id: string;
  company: string;
  question: string;
  // --- gold / eval-only fields below (never shown to agent or baseline) ---
  gold_answer: string;
  gold_value?: number | null;
  gold_unit: Unit;
  gold_polarity?: "yes" | "no" | null;
  gold_canonical?: string | string[] | null;
  gold_evidence: GoldEvidence[];
  bucket: Bucket;
  expected_formula?: string | null;
  expected_inputs: string[];
  predicted_baseline_failure: boolean;
  answer_verifiable_from_evidence: boolean;
  unit_or_period_ambiguity: boolean;
  demo_candidate: boolean;
  human_reviewed: boolean;
  tolerance: Tolerance;
}

// --- section 8: agent-visible checklist item (gold fields stripped) ---
export interface AgentVisibleItem {
  item_id: string;
  company: string;
  question: string;
}

// --- section 24 "Run tab" company picker + checklist preview (not itemized in
// section 23's endpoint list, but GET /companies serves this) ---
export interface CompanyChecklist {
  company: string;
  items: AgentVisibleItem[];
}

// --- section 23: API ---
export interface RunCard {
  run_id: string;
  company: string;
  status: RunStatus;
  created_at: string;
  items_total: number;
  items_answered: number;
  items_abstained: number;
  verdict_badge: VerdictBadge;
}

export interface CreateRunRequest {
  company: string;
  item_ids?: string[];
  system?: "agent" | "baseline";
}

export interface CreateRunResponse {
  run_id: string;
  status: RunStatus;
}

export interface RunStatusResponse {
  run_id: string;
  company: string;
  status: RunStatus;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
}

export interface PageResponse {
  company: string;
  doc_id: string;
  doc_name: string;
  page: number;
  text: string;
  spans: { run_id: string; item_id: string; citation_id: string; char_start: number; char_end: number }[];
}
