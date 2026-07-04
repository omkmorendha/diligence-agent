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
export interface SystemMetrics {
  answer_accuracy: number;
  citation_precision: number;
  citation_provenance: number;
  arithmetic_integrity: number;
  groundedness_judge?: number | null;
  actionability_judge?: number | null;
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

export interface PageResponse {
  company: string;
  doc_id: string;
  doc_name: string;
  page: number;
  text: string;
  spans: { citation_id: string; char_start: number; char_end: number }[];
}
