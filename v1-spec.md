# DiliAgent — v1 Build Spec (Document Review Pipeline)

v1 turns the v0 question-answering system into **DiliAgent**: upload a draft
diligence document, and an agent pipeline verifies every material claim in it
against the FinanceBench filing corpus, then returns the original document
annotated with verdicts — incorrect numbers, contradicted statements, and
claims the corpus cannot support.

v0 (see `v0-spec.md`) stays intact and becomes the verification engine. v1 is
a layer on top of it, not a rewrite.

---

## 0. Product summary

The v1 workflow:

1. User uploads a document (PDF, DOCX, or Markdown) in the new **Agent** tab.
2. An **extraction agent** parses the document and extracts a list of
   verifiable **claims**, each anchored to a verbatim span in the source
   document, and derives a verification question per claim.
3. A bounded pool of **verification agents** — each an instance of the v0
   planning + multi-retrieval + calculator agent — answers those questions
   against the existing per-company RAG indexes and returns a structured
   **VerificationResult** JSON per claim.
4. An **annotation renderer** produces (a) the original document annotated in
   place — highlights colored by verdict, with evidence comments — and (b) an
   HTML review report rendered in the frontend, with citations that link into
   the existing corpus page viewer.

The output is auditable in the v0 sense: every verdict carries verified
citations into the corpus, every numeric comparison traces to a deterministic
calculator call, and the original document is never rewritten — only
annotated.

---

## 1. Issues with the v1 brief, and resolutions

These are the problems found in the original v1 idea, each with the decision
this spec encodes. Any of these can be overridden before build starts.

### 1.1 "Rewritten with annotations" — do not rewrite

The brief says the document "is rewritten with annotations". Rewriting a
diligence artifact through an LLM is disqualifying for this product's core
principle (v0 spec §2: every claim auditable): a rewrite can silently
introduce *new* errors into the exact document we claim to have checked, and
for PDF it is also technically lossy (layout, tables, fonts).

**Resolution:** annotate the original in place, never regenerate its prose.
PDF gets native highlight + comment annotations via `pymupdf` (full
fidelity — this is why PDF is the showcase format). DOCX gets run-level
highlighting plus review comments. Markdown gets inline `<mark>` spans plus a
footnoted appendix. All formats additionally get the same HTML review report.

### 1.2 Questions alone cannot drive annotation — extract claims first

The brief's step 3 generates "a long list of questions", and step 5 annotates
the document from the answers. A question ("What was PepsiCo's FY2022
restructuring cost?") has no location in the uploaded document — you cannot
highlight anything with it.

**Resolution:** the extraction agent's primary output is **claims**, each with
a verbatim source span (`quote` + anchor), a claim type, and a company/period
tag. The verification question is *derived from* each claim. This is the same
verbatim-quote discipline the v0 agent already uses for citations
(`_normalize_citation` / `_match_quote_offsets`), pointed at the uploaded
document instead of the corpus. Lesson already banked from building the test
docs: PDF text extraction yields typographic ligatures (`ﬁ`, `ﬂ`), so anchor
matching must fold NFKC in addition to the existing whitespace/unicode
tolerance.

### 1.3 "A long list of questions" — unbounded fan-out is unbounded cost

A v0 agent item costs up to 12 tool calls with an LLM call between each. A
10-page memo can yield 100+ extractable claims; at v0 cost per item that is a
runaway review. It also violates the established working rule: pilot first,
then full run, throttled.

**Resolution:** hard cap `MAX_CLAIMS_PER_REVIEW = 30`, with extraction-time
prioritization (numeric > factual > judgment; deduplicate near-identical
claims). Reviews start in **pilot mode** (first `PILOT_CLAIMS = 8` claims by
priority); the UI shows pilot results and offers "run full review" as an
explicit second step. Dropped claims are reported as `SKIPPED` in the report,
never silently.

### 1.4 The corpus only knows 11 companies and specific filing years

The RAG corpus is 11 companies at fixed periods (e.g. AMD FY2015/FY2022,
Boeing FY2018/FY2022, PepsiCo FY2022–FY2023Q1). Upload a memo about Nvidia —
or about PepsiCo FY2024 — and *every* claim is unverifiable. Without handling,
the pipeline burns a full agent run to discover this and reports a wall of
noise.

**Resolution:** a cheap **scope pre-check** after extraction: each claim is
tagged with company + period and checked against a corpus registry (built
from `data/index/` + `data/subset.json`). Claims outside the registry get an
immediate `OUT_OF_SCOPE` verdict with no agent run. If >80% of claims are out
of scope, the review stops after extraction and tells the user what the
corpus actually covers. The seeded test documents (§14) are deliberately
in-scope.

### 1.5 "Data missing from the corpus" — absence of evidence is not evidence of absence

A retrieval miss does not prove the corpus lacks the fact; it may prove the
query was bad. Flagging a correct claim as "missing" because retrieval was
lazy is the fastest way to lose user trust.

**Resolution:** `NOT_IN_CORPUS` is a *earned* verdict: the verification agent
must exhaust a minimum search budget (≥3 distinct queries, reformulated, plus
a `get_pages` probe of the most plausible section when retrieval scores are
weak) before it may return it, and the verdict carries the queries tried.
The report renders it as "could not verify against corpus", never as
"incorrect".

### 1.6 Judgment and opinion statements cannot be "incorrect"

"Boeing's business is subject to cyclicality" is checkable; "we view the
dividend as well covered" is an opinion. The brief's framing ("identifying
incorrect... statements") needs a verdict taxonomy, not a boolean.

**Resolution:** six verdicts:

| Verdict | Meaning | Rendered as |
|---|---|---|
| `SUPPORTED` | corpus confirms the claim (numeric within tolerance) | green |
| `CONTRADICTED` | corpus states otherwise; both values shown | red |
| `PARTIALLY_SUPPORTED` | direction right, magnitude/detail off; or mixed | amber |
| `NOT_IN_CORPUS` | in-scope, but exhaustive search found nothing | grey |
| `OUT_OF_SCOPE` | company/period not in corpus; no agent run | grey, dashed |
| `UNVERIFIABLE` | opinion/forward-looking; not a factual claim | uncolored, noted |

Only numeric and factual claims can be `CONTRADICTED`.

### 1.7 The v0 plumbing is not concurrency-safe — fix before "multiple agents"

`llm.py`'s usage sink is module-global and documented "one run per process";
`trace.py` seq allocation and the SSE queue were built for a single agent
thread. Fanning out verification agents without fixing this corrupts
`llm_calls.jsonl` and interleaves trace seq numbers.

**Resolution:** make the usage sink and trace emitter run-scoped objects with
lock-protected counters (mechanical change, but it is a prerequisite, so it
is milestone 1). Fan-out uses a `ThreadPoolExecutor` with
`REVIEW_WORKERS = 3` (the Vultr endpoint has bitten on rate limits before;
workers back off on 429/5xx).

### 1.8 DOCX is the weakest annotation target; Markdown annotation is inherently a rewrite

PDF annotation is lossless and native. DOCX comment support in `python-docx`
is version-dependent (real comments require ≥1.2; otherwise highlight +
appendix markers). Markdown has no annotation layer at all — inserting
`<mark>` tags *is* editing the file.

**Resolution:** accept the asymmetry and match the brief's stated focus: PDF
is the flagship path and the only format with a fidelity guarantee. DOCX:
highlights + `[R#]` markers + appendix (native comments when the library
supports it). Markdown: annotated copy with `<mark>` spans — an exception to
§1.1, acceptable because a text-diff of `.md` files is reviewable in a way a
regenerated PDF is not; the renderer inserts tags only, never alters
original bytes between them, and CI-style verification asserts that
stripping the inserted tags reproduces the original file byte-for-byte.

### 1.9 "Two tabs" vs the existing three

The current UI is three tabs (Run / Memo / Evals).

**Resolution:** two top-level tabs, **Agent** and **RAG**; the existing three
become sub-tabs under RAG unchanged. Agent is the new default tab.

### 1.10 Scanned PDFs

An uploaded PDF without a text layer cannot be claim-extracted or annotated
by span. **Resolution:** reject at upload with a clear error ("no extractable
text — scanned documents are not supported in v1"). No OCR in v1.

---

## 2. v1 boundaries

### Supported in v1

* Upload of one document per review: `.pdf`, `.docx`, `.md` (≤ 20 MB).
* Claim extraction with verbatim anchors, capped and prioritized.
* Scope pre-check against the 11-company corpus registry.
* Concurrent verification (bounded pool) reusing the v0 agent unchanged in
  behavior: same tools, same citation discipline, same calculator.
* Six-verdict taxonomy (§1.6) with per-claim citations into the corpus.
* Annotated PDF (native annots), annotated DOCX (highlights + markers),
  annotated Markdown (`<mark>` copy), HTML review report for all formats.
* Live SSE progress in the Agent tab; review artifacts on disk under
  `runs/reviews/{review_id}/`.
* Review eval: three seeded-error test documents + deterministic scorer.

### Not supported in v1

* OCR / scanned PDFs, XLSX/PPTX, multi-file uploads, URLs.
* Extending the corpus from the uploaded document (upload is subject, not
  source; it must never enter the retrieval index).
* Rewriting/paraphrasing document prose (except §1.8 Markdown marks).
* Cross-document consistency checks within the upload.
* Auth, multi-tenancy, persistence beyond the filesystem.

---

## 3. Naming

* Product name: **DiliAgent** (README title, frontend header/title tag).
* Code identifiers, paths, and repo name stay as-is; no churn renames.
* v0 terminology ("run", "memo") is unchanged; the new object is a
  **review** (`review_id = f"review_{slug(filename)}_{ts_ms}"`).

---

## 4. Stack additions

Everything from v0 §3 stands. New:

* `python-docx >= 1.1` — DOCX parse + annotate (real comments if ≥ 1.2).
* No new Markdown library: `.md` is treated as plain text with line/char
  offsets (claims anchor to character ranges; heading structure is not
  needed).
* No new PDF library: `pymupdf` already in `backend/pyproject.toml` does
  parse, span search (`page.search_for`), and annotation
  (`add_highlight_annot`, popup content, appended pages).
* LLM: unchanged — `moonshotai/Kimi-K2.6` via the Vultr/NVIDIA-compatible
  endpoint through `llm.chat()` only. Native tool protocol as selected by the
  smoke test.

---

## 5. Pipeline overview

```
upload ─▶ S1 parse ─▶ S2 extract claims ─▶ S3 scope check ─▶ S4 verify (pool)
                                                                  │
annotated.{pdf|docx|md} ◀─ S6 annotate ◀─ S5 assemble report ◀────┘
report.html / report.json
```

Stages S1, S3, S5, S6 are deterministic code. S2 is one LLM pass (JSON
mode). S4 is the v0 agent, N claims through `REVIEW_WORKERS` threads.

Artifacts per review under `runs/reviews/{review_id}/`:

```
upload.<ext>        original bytes, untouched
docmodel.json       parsed blocks + anchors
claims.json         list[Claim] after cap/prioritization
report.json         ReviewReport (claims + VerificationResults)
annotated.<ext>     annotated copy of the original
report.html         self-contained HTML report
trace.jsonl         TraceEvents (same schema as v0 runs)
llm_calls.jsonl     usage log (run-scoped sink)
review.json         meta: status, timings, counts
```

---

## 6. S1 — Document ingestion

`backend/app/review/parse.py`.

* **PDF**: `pymupdf` per page → text + word boxes. Reject if total extracted
  text < 200 chars ("no text layer"). Store per-block: `{page, text,
  char_start, char_end}`.
* **DOCX**: `python-docx` paragraphs in body order → `{para_index, text,
  char_start, char_end}` over a concatenated canonical text.
* **MD**: raw text → `{line_start, char_start, char_end}` blocks split on
  blank lines.

Common output — **DocModel**: `{doc_id, format, filename, canonical_text,
blocks[]}`. `canonical_text` is NFKC-normalized, whitespace-collapsed, with a
maintained offset map back to raw positions per format (the same trick
`_match_quote_offsets` uses, plus NFKC — see §1.2).

## 7. S2 — Claim extraction

`backend/app/review/extract.py`. One `llm.chat(json_mode=True)` pass over
`canonical_text` (chunked if > ~24k chars, with overlap; dedupe across
chunks). Output per claim:

```json
{
  "claim_id": "c07",
  "quote": "verbatim sentence from the document",
  "claim_type": "numeric | factual | judgment",
  "company": "PepsiCo",
  "period": "FY2022",
  "metric": "restructuring costs",
  "question": "What were PepsiCo's restructuring costs in FY2022?",
  "priority": 1
}
```

Deterministic post-processing: verify each `quote` anchors in the DocModel
(unanchorable claims are dropped and counted in `review.json`), dedupe,
sort by (type priority, document order), cap at `MAX_CLAIMS_PER_REVIEW`.
Claims cut by the cap are kept in `claims.json` with `status: "SKIPPED"`.

## 8. S3 + S4 — Scope check and verification fan-out

**Registry** (`backend/app/review/registry.py`): built once from
`data/index/*/meta.json` + `data/subset.json` → `{company: {doc_ids,
periods}}`. Claim company matching is normalized (aliases: "PepsiCo, Inc." →
PepsiCo, etc. — small explicit alias table, extendable).

* Claim company not in registry → `OUT_OF_SCOPE`, no agent run.
* Claim period not covered for that company → `OUT_OF_SCOPE` with the
  covered periods named in the explanation.
* `judgment` claims that are pure opinion/forward-looking (extraction flags
  them) → `UNVERIFIABLE`, no agent run.
* \>80% of claims out of scope → stop; review completes with status
  `out_of_scope` and a corpus-coverage message.

**Verification** (`backend/app/review/verify.py`): each surviving claim runs
through the v0 agent loop (`_run_item` machinery) with the claim's derived
question as an ad-hoc item for the claim's company. The item prompt includes
the claim's quoted value so the agent compares, not just answers. Result is
mapped deterministically from the agent's `ItemAnswer`:

```json
{
  "claim_id": "c07",
  "verdict": "CONTRADICTED",
  "doc_value": {"value": 600.0, "unit": "USD millions"},
  "corpus_value": {"value": 400.0, "unit": "USD millions"},
  "explanation": "The 8-K states the agreement increased by $400M, not $600M.",
  "citations": [ Citation... ],
  "calculation": CalculationResult | null,
  "queries_tried": ["..."],
  "confidence": "high | medium | low"
}
```

Numeric comparison uses the v0 tolerance rule (±1% relative default);
within tolerance → `SUPPORTED`, outside → `CONTRADICTED`, direction-only
match → `PARTIALLY_SUPPORTED`. `NOT_IN_CORPUS` requires the exhausted-search
budget (§1.5). Verdict mapping is code, not model choice, wherever a gold
comparison is computable.

Fan-out: `ThreadPoolExecutor(max_workers=REVIEW_WORKERS)` (default 3), retry
with exponential backoff on 429/5xx, per-review wall-clock cap
`REVIEW_TIMEOUT_S = 1800`. Prerequisite: run-scoped, lock-protected usage
sink and trace emitter (§1.7).

## 9. S5 + S6 — Report assembly and annotation

**ReviewReport** (`report.json`): review meta + summary counts by verdict +
`claims[]` each embedding its `VerificationResult` and document anchor.
Assembly is deterministic; no LLM call (same rule as v0 memo assembly).

**PDF** (`annotate_pdf.py`): for each claim, `page.search_for(quote)` (NFKC-
tolerant, with the §6 offset map as fallback) → `add_highlight_annot(rects)`
colored by verdict; annotation popup contains verdict, corpus value,
explanation, and citation (`doc_name` p. `pdf_page`). Appended **Review
Appendix** pages (via `fitz.Story`) list every claim, verdict, and citation,
including `SKIPPED`/`OUT_OF_SCOPE`. Original pages' content streams are
never modified — annotations and appended pages only.

**DOCX** (`annotate_docx.py`): split runs at anchor boundaries, apply
`WD_COLOR_INDEX` highlight per verdict, insert `[R1]`-style markers, append
a Review Appendix section; use native `add_comment` when python-docx ≥ 1.2.

**MD** (`annotate_md.py`): insert `<mark class="verdict-contradicted"
title="...">…</mark>` around anchored spans + footnote list appended.
Verifier asserts: stripping inserted tags reproduces the original bytes.

**HTML report** (`report_html.py`): self-contained (inline CSS), theme-aware;
document text with highlighted spans, click → verdict card with citations
linking to the existing `/corpus/{company}/{doc_id}/page/{n}` viewer. This is
what the frontend embeds for all three formats.

## 10. Schemas

New models in `backend/app/schemas.py`, mirrored in `frontend/src/types.ts`
(same manual-mirror convention as v0):

* `Verdict` literal (§1.6 six values, plus `SKIPPED` as a claim status, not
  a verdict).
* `ClaimAnchor {page?, para_index?, char_start, char_end, quote}`
* `Claim {claim_id, quote, claim_type, company, period?, metric?, question,
  priority, status, anchor}`
* `VerificationResult` (§8), reusing v0 `Citation` and `CalculationResult`
  unchanged.
* `ReviewReport {schema_version, review_id, filename, format, company_scope,
  summary{counts by verdict}, claims[]}`
* `ReviewCard / ReviewStatusResponse / CreateReviewResponse` API DTOs.

Trace: reuse `TraceEvent` with new `type` values `claim_extracted`,
`scope_check`, `claim_verdict`, `annotation`; `item_id` carries `claim_id`.
SSE consumers need no structural changes.

## 11. API

* `POST /reviews` — multipart upload (`file`), optional `pilot: bool = true`.
  Validates format/size/text-layer, creates review, starts background
  execution (thread, as v0 runs). → `{review_id, status}`.
* `POST /reviews/{id}/full` — promote a completed pilot review to a full run
  (verifies remaining claims, reusing pilot results).
* `GET /reviews` → `list[ReviewCard]`.
* `GET /reviews/{id}` → status + summary.
* `GET /reviews/{id}/events` — SSE, live or replay (same mechanics as
  `/runs/{id}/events`).
* `GET /reviews/{id}/report` → `report.json`; `?format=html` → `report.html`.
* `GET /reviews/{id}/annotated` → annotated file download
  (correct content-type per format).

Uploads are size-capped (20 MB), extension + magic-byte checked, stored only
under `runs/reviews/{id}/`, and never ingested into `data/index`.

## 12. Frontend

Top level: **Agent | RAG** tabs (Agent default).

* **RAG tab**: the existing Run / Memo / Evals tabs mounted unchanged as
  sub-tabs.
* **Agent tab**:
  * Dropzone (`.pdf .docx .md`, 20 MB) + corpus-coverage hint listing the 11
    companies/periods so users know what is verifiable.
  * On upload: pipeline progress from SSE — claims extracted, scope
    breakdown, then a per-claim verification ticker (k of n, verdict chips
    appearing live).
  * Results: summary chips by verdict; claim table (quote, verdict,
    corpus value vs doc value, citations that open the corpus page panel —
    reuse the Memo tab's citation panel component).
  * Embedded HTML report viewer; **Download annotated document** button.
  * Pilot banner: "Pilot: 8 of N claims verified — Run full review".

Styling follows the existing token/inline-style system; no new UI libraries.

## 13. Cost, concurrency, reliability

Config additions (`config.py`, all env-overridable):

```
MAX_CLAIMS_PER_REVIEW = 30
PILOT_CLAIMS          = 8
REVIEW_WORKERS        = 3
REVIEW_TIMEOUT_S      = 1800
MAX_UPLOAD_MB         = 20
NOT_IN_CORPUS_MIN_QUERIES = 3
```

* One review at a time per process (same informal constraint as v0 runs);
  `POST /reviews` returns 409 if one is executing.
* Usage sink and trace seq become run-scoped + locked (milestone 1).
* Worker-level retry/backoff on LLM 429/5xx; a claim that still fails gets
  verdict-less `status: "ERROR"` in the report rather than sinking the
  review.

## 14. Eval — seeded test documents and review scorer

Already built (this commit):

* `scripts/make_testdocs.py` — deterministic generator.
* `evals/testdocs/pepsico_memo.pdf` (10 claims), `boeing_memo.docx`
  (8 claims), `amd_memo.md` (7 claims) — 25 claims total: 11 accurate,
  10 corrupted, 4 fabricated, derived from `data/subset.json` gold answers
  for PepsiCo, Boeing, and AMD. Every `claim_text` appears verbatim in its
  document (ligature-folded check passes).
* `evals/testdocs/manifest.json` — ground truth per claim:
  `seeded_status ∈ {accurate, corrupted, fabricated}` and
  `expected_verdict ∈ {SUPPORTED, CONTRADICTED, NOT_IN_CORPUS}`.

New `evals/review_scorer.py`, deterministic, mirroring the v0 scorer style:

| Metric | Definition |
|---|---|
| Extraction recall | manifest claims matched (fuzzy, NFKC) by an extracted claim |
| Corrupted recall (**headline**) | corrupted claims flagged `CONTRADICTED` or `PARTIALLY_SUPPORTED` |
| False-flag rate | accurate claims flagged `CONTRADICTED` (target ~0) |
| Fabrication detection | fabricated claims → `NOT_IN_CORPUS` (not `CONTRADICTED`) |
| Verdict accuracy | exact `expected_verdict` match over all matched claims |
| Anchor precision | annotated span overlaps the claim sentence (PDF: rect vs quote rects) |
| Citation provenance | verdict citations verify against corpus pages (reuse v0 checker) |

Target for v1 done: corrupted recall ≥ 8/10, false-flag ≤ 1/11, fabrication
detection ≥ 3/4, on the PDF path end-to-end.

## 15. Build order

1. **Concurrency prerequisites** — run-scoped usage sink + locked trace
   emitter; regression: a 2-worker dummy run produces clean `llm_calls.jsonl`
   and monotonic seq.
2. **Parse + DocModel** (`.pdf/.docx/.md`) with anchor offset maps; unit
   tests against the three test docs (every manifest claim anchorable).
3. **Claim extraction + scope registry** — pilot on `amd_memo.md` (cheapest
   format) per the pilot-first rule; inspect `claims.json` by hand before
   wiring S4.
4. **Verification fan-out** — adapt `_run_item` for ad-hoc claims; verdict
   mapping; run pilot (8 claims) then the full PepsiCo PDF.
5. **Report + PDF annotation** — `report.json`, `annotated.pdf`,
   `report.html`; anchor-precision check.
6. **API + Agent tab** — upload → SSE → report viewer → download.
7. **DOCX + MD annotators**, review scorer, README/frontend rename to
   DiliAgent, targets from §14 met.

## 16. Risks

* **Extraction quality** is the new single point of failure: a missed claim
  is invisible downstream. Mitigation: extraction recall is a scored metric
  (§14), and the report lists dropped/unanchorable counts.
* **Verdict overconfidence** on judgment claims. Mitigation: code-side
  verdict mapping wherever numeric; `UNVERIFIABLE` default for opinions.
* **Quote anchoring on real-world PDFs** (multi-column, tables, ligatures,
  hyphenation). Mitigation: NFKC + offset-map fallback; claims that fail to
  anchor are still verified and reported, only appendix-listed instead of
  highlighted.
* **Endpoint rate limits** under fan-out. Mitigation: 3 workers, backoff,
  per-review token/wall-clock caps.
* **python-docx comment support** varies by version. Mitigation: feature-
  detect; markers + appendix are the guaranteed path.

## 17. Cut line

If time runs short, cut in this order (latest first): DOCX native comments →
MD annotator (HTML report still covers it) → `POST /reviews/{id}/full`
promotion (pilot-only ships) → Evals-tab integration of review scores. The
PDF path, the six-verdict taxonomy, and the scorer are never cut.
