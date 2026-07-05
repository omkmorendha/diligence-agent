"""Canonical schemas (spec sections 7-22).

The backend schema is canonical; `frontend/src/types.ts` mirrors it manually.
Freeze these before building dependent code.

CRITICAL — no hidden gold leakage (spec section 2.2):
    The agent prompt receives ONLY item_id, company, question. Everything else on
    a SubsetItem (gold_answer, gold_value, gold_evidence, bucket, expected_formula,
    expected_inputs, predicted_baseline_failure) is eval-only. Use
    `agent_visible_item()` to strip a SubsetItem before it reaches any agent/baseline
    prompt.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

# --- shared literals ---
DocType = Literal["10k", "10q", "8k", "other"]
Unit = Literal["USD millions", "percent", "ratio", "text", "other"]
Bucket = Literal["A_multi_input", "B_judgment", "C_lookup"]
AnswerStatus = Literal["answered", "abstained"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
VerdictBadge = Literal["strong", "mixed", "failed", "unknown"]
Strategy = Literal["single_lookup", "multi_input_computation", "judgment"]
ToolName = Literal["search_filing", "get_pages", "calculate", "record_answer", "flag_outstanding"]
TraceEventType = Literal[
    "plan", "scratchpad", "retrieval", "tool_call", "tool_result",
    "decision", "citation", "item_answer", "verdict", "error",
    # v1 review pipeline (spec section 10)
    "claim_extracted", "scope_check", "claim_verdict", "annotation",
]

# --- v1 review pipeline literals (spec sections 1.6, 10) ---
DocFormat = Literal["pdf", "docx", "md"]
Verdict = Literal[
    "SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED",
    "NOT_IN_CORPUS", "OUT_OF_SCOPE", "UNVERIFIABLE",
]
ClaimType = Literal["numeric", "factual", "judgment"]
# SKIPPED = cut by MAX_CLAIMS cap; ERROR = verification failed after retries (spec section 13).
ClaimStatus = Literal["PENDING", "VERIFIED", "SKIPPED", "ERROR"]
VerificationConfidence = Literal["high", "medium", "low"]
ReviewStatus = Literal["queued", "running", "completed", "failed", "out_of_scope"]


# --- section 8: subset item -------------------------------------------------
class GoldEvidence(BaseModel):
    doc_id: str
    doc_name: str
    doc_type: DocType
    filing_period: str
    pdf_page: int
    page_label: str
    evidence_text: str


class Tolerance(BaseModel):
    relative: Optional[float] = 0.01
    absolute: Optional[float] = None


class SubsetItem(BaseModel):
    """A curated benchmark item. Gold/eval fields must be stripped before any agent prompt."""

    item_id: str
    question_id: str
    company: str
    question: str
    # --- gold / eval-only fields below (never shown to agent or baseline) ---
    gold_answer: str
    gold_value: Optional[float] = None
    gold_unit: Unit = "text"
    # --- IMP3-1 (results/iterations/iter2/improvement_plan.json): human-reviewable
    # CANONICAL gold annotations, sourced from data/gold_annotations.json and merged in
    # by d5_select_subset.py. Derived from question + gold_answer TEXT ONLY (never from
    # any agent output). They ONLY ADD pass opportunities in scorers.answer_accuracy
    # (gated on presence, above the exact-string fallback); gold_answer stays canonical.
    #   gold_polarity   -> the yes/no answer to an unambiguous yes/no question.
    #   gold_canonical  -> canonical entity/category (str), entity list (str[]), or one
    #                      of {operating,investing,financing} for cash-flow-source choices.
    gold_polarity: Optional[Literal["yes", "no"]] = None
    gold_canonical: Optional[Union[str, list[str]]] = None
    gold_evidence: list[GoldEvidence] = Field(default_factory=list)
    bucket: Bucket = "C_lookup"
    expected_formula: Optional[str] = None
    expected_inputs: list[str] = Field(default_factory=list)
    predicted_baseline_failure: bool = False
    answer_verifiable_from_evidence: bool = True
    unit_or_period_ambiguity: bool = False
    demo_candidate: bool = False
    human_reviewed: bool = False
    tolerance: Tolerance = Field(default_factory=Tolerance)


class AgentVisibleItem(BaseModel):
    """The ONLY fields an agent/baseline prompt may see (spec section 8)."""

    item_id: str
    company: str
    question: str


def agent_visible_item(item: SubsetItem) -> AgentVisibleItem:
    """Strip a SubsetItem down to the agent-visible surface. Single choke point."""
    return AgentVisibleItem(item_id=item.item_id, company=item.company, question=item.question)


# --- section 13: chunk / retrieval -----------------------------------------
class Chunk(BaseModel):
    chunk_id: str  # "company_slug:doc_id:p{page}:c{chunk_index}"
    company: str
    doc_id: str
    doc_name: str
    doc_type: DocType
    filing_period: str
    page: int
    text: str
    score: float
    char_start: int
    char_end: int


# --- section 11: citation ---------------------------------------------------
class Citation(BaseModel):
    citation_id: str
    claim: Optional[str] = None
    doc_id: str
    doc_name: str
    doc_type: Optional[DocType] = None
    filing_period: Optional[str] = None
    pdf_page: int
    page_label: Optional[str] = None
    chunk_id: str
    quote: str
    char_start: int
    char_end: int
    source_event_seq: Optional[int] = None


# --- section 13: calculate --------------------------------------------------
class FinancialInput(BaseModel):
    value: float
    unit: Unit
    period: str
    citation_id: str


class CalculationResult(BaseModel):
    expression: str
    inputs: dict[str, FinancialInput]
    value: float
    unit: Optional[str] = None
    rounding: Optional[str] = None
    steps: Optional[str] = None


# --- section 11 / 17: item answer ------------------------------------------
class Confidence(BaseModel):
    grounded_inputs: int = 0
    assumed_inputs: int = 0


class ItemAnswer(BaseModel):
    item_id: str
    question: Optional[str] = None
    answer: str
    value: Optional[float] = None
    unit: Unit = "text"
    citations: list[Citation] = Field(default_factory=list)
    status: AnswerStatus = "answered"
    confidence: Confidence = Field(default_factory=Confidence)


# --- section 9-11: trace event ---------------------------------------------
class TraceEvent(BaseModel):
    schema_version: str = "0.1"
    run_id: str
    seq: int
    ts: str  # ISO8601
    type: TraceEventType
    title: str
    detail: str = ""
    item_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


# --- section 17: memo -------------------------------------------------------
class MemoSummary(BaseModel):
    items_total: int = 0
    items_answered: int = 0
    items_abstained: int = 0
    citations_total: int = 0
    calculate_calls: int = 0


class MemoItem(BaseModel):
    item_id: str
    question: str
    answer: str
    value: Optional[float] = None
    unit: Unit = "text"
    citations: list[Citation] = Field(default_factory=list)
    status: AnswerStatus = "answered"
    confidence: Confidence = Field(default_factory=Confidence)


class Memo(BaseModel):
    run_id: str
    company: str
    status: Literal["completed", "failed"] = "completed"
    created_at: str
    completed_at: Optional[str] = None
    items: list[MemoItem] = Field(default_factory=list)
    summary: MemoSummary = Field(default_factory=MemoSummary)


# --- section 22: eval results ----------------------------------------------
class BucketAccuracy(BaseModel):
    answer_accuracy: float = 0.0


class SystemMetrics(BaseModel):
    answer_accuracy: float = 0.0
    citation_precision: float = 0.0
    citation_provenance: float = 0.0
    arithmetic_integrity: float = 0.0
    trace_shape: Optional[float] = None
    abstention_correct_rate: Optional[float] = None
    groundedness_judge: Optional[float] = None
    actionability_judge: Optional[float] = None
    by_bucket: dict[str, BucketAccuracy] = Field(default_factory=dict)


class SubsetSummary(BaseModel):
    num_questions: int
    num_companies: int
    bucket_counts: dict[str, int]


class Comparison(BaseModel):
    created_at: str
    subset: SubsetSummary
    systems: dict[str, Any]


# --- section 23: API contract ----------------------------------------------
class CompanyChecklist(BaseModel):
    """Company picker + checklist preview source for the Run tab (spec section 24).
    Not itemized in the section 23 endpoint list, but the frontend needs a
    gold-free (`AgentVisibleItem`) source to populate the picker before a run
    starts. Derived from `data/subset.json` via `agent_visible_item()` -- same
    no-gold-leakage guarantee as the agent/baseline prompts."""

    company: str
    items: list[AgentVisibleItem] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    company: str
    item_ids: Optional[list[str]] = None  # omitted => all items for company
    system: Literal["agent", "baseline"] = "agent"


class CreateRunResponse(BaseModel):
    run_id: str
    status: RunStatus = "queued"


class RunStatusResponse(BaseModel):
    run_id: str
    company: str
    status: RunStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class RunCard(BaseModel):
    run_id: str
    company: str
    status: RunStatus
    created_at: str
    items_total: int = 0
    items_answered: int = 0
    items_abstained: int = 0
    verdict_badge: VerdictBadge = "unknown"


class PageResponse(BaseModel):
    company: str
    doc_id: str
    doc_name: str
    page: int
    text: str
    spans: list[dict[str, Any]] = Field(default_factory=list)


# --- section 6: DocModel (parsed upload) -----------------------------------
class DocBlock(BaseModel):
    """One parsed block of the source document. `char_start`/`char_end` index into
    the DocModel `canonical_text`; the per-format anchor fields (page for PDF,
    para_index for DOCX, line_start for MD) are populated only for that format."""

    text: str
    char_start: int
    char_end: int
    page: Optional[int] = None
    para_index: Optional[int] = None
    line_start: Optional[int] = None


class DocModel(BaseModel):
    doc_id: str
    format: DocFormat
    filename: str
    canonical_text: str
    blocks: list[DocBlock] = Field(default_factory=list)


# --- section 10: claims + verification -------------------------------------
class ClaimAnchor(BaseModel):
    """Verbatim source span for a claim. Offsets index into DocModel.canonical_text;
    the per-format field (page/para_index/line_start) locates the raw position."""

    quote: str
    char_start: int
    char_end: int
    page: Optional[int] = None
    para_index: Optional[int] = None
    line_start: Optional[int] = None


class Claim(BaseModel):
    claim_id: str
    quote: str
    claim_type: ClaimType
    company: str
    period: Optional[str] = None
    metric: Optional[str] = None
    question: str
    priority: int = 1
    status: ClaimStatus = "PENDING"
    anchor: Optional[ClaimAnchor] = None


class ClaimValue(BaseModel):
    """A numeric value + unit extracted from the document or the corpus."""

    value: Optional[float] = None
    unit: Optional[str] = None


class VerificationResult(BaseModel):
    claim_id: str
    verdict: Verdict
    doc_value: Optional[ClaimValue] = None
    corpus_value: Optional[ClaimValue] = None
    explanation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    calculation: Optional[CalculationResult] = None
    queries_tried: list[str] = Field(default_factory=list)
    confidence: VerificationConfidence = "medium"


# --- section 9: review report ----------------------------------------------
class ReviewSummary(BaseModel):
    """Counts by verdict + claim status (spec section 9)."""

    total_claims: int = 0
    supported: int = 0
    contradicted: int = 0
    partially_supported: int = 0
    not_in_corpus: int = 0
    out_of_scope: int = 0
    unverifiable: int = 0
    skipped: int = 0
    error: int = 0


class ReviewReportClaim(BaseModel):
    """A claim paired with its verification result inside the assembled report."""

    claim: Claim
    result: Optional[VerificationResult] = None


class ReviewReport(BaseModel):
    schema_version: str = "0.1"
    review_id: str
    filename: str
    format: DocFormat
    company_scope: list[str] = Field(default_factory=list)
    summary: ReviewSummary = Field(default_factory=ReviewSummary)
    claims: list[ReviewReportClaim] = Field(default_factory=list)


# --- section 11: review API DTOs -------------------------------------------
class ReviewCard(BaseModel):
    review_id: str
    filename: str
    format: DocFormat
    status: ReviewStatus
    created_at: str
    pilot: bool = True
    summary: Optional[ReviewSummary] = None


class ReviewStatusResponse(BaseModel):
    review_id: str
    filename: str
    format: DocFormat
    status: ReviewStatus
    pilot: bool = True
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[ReviewSummary] = None


class CreateReviewResponse(BaseModel):
    review_id: str
    status: ReviewStatus = "queued"
