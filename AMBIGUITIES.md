# Open questions in `v0-spec.md`, resolved against the real dataset

This is the payoff of the scaffold: with FinanceBench actually pulled
(`data/raw/financebench.jsonl`, `data/dataset_profile.json`, `data/raw/download_report.json`)
we can settle the spec's open risks with numbers instead of guesses.

Legend: ✅ resolved by data · 🔧 design decision (recommendation given) · ⏳ pending the LLM smoke test (needs `NVIDIA_API_KEY`).

---

## ✅ 1. Is the ideal subset composition feasible? (spec §5, §31 "dataset shape risk")

**Yes — the ideal 4 companies × ~8 questions = ~32 is achievable; no fallback needed.**

Measured over the 150-question open subset:

- **150 questions, 32 companies, 84 documents.** All questions retained.
- **7 companies have ≥8 usable questions:** PepsiCo (11), Amcor (9), Johnson & Johnson (9), 3M (8), AMD (8), Best Buy (8), Boeing (8). We only need 4.
- Heuristic bucket totals across all 150: **A_multi_input 67 · B_judgment 52 · C_lookup 31.**

**Caveat on bucket targets (§5 wants ~16 A / ~8 B / ~8 C):** A is abundant, but
`C_lookup` is the scarcest bucket (31 total), so per-company C availability drives
selection. Recommended demo companies (good A+C mix, recognizable names):

| Company | A_multi_input | C_lookup | B_judgment |
|---------|---------------|----------|------------|
| AMD     | 4 | 2 | 2 |
| 3M      | 3 | 3 | 2 |
| Best Buy| 3 | 2 | 3 |
| PepsiCo | 2 | 4 | 5 |

These four alone yield ~12 A / ~11 C / ~12 B before D3/D4 filtering — comfortably
above the ~16A/8C target once B is trimmed. **The fallback policy (§D5) does not
need to trigger.** (Bucket numbers are the heuristic preview; D3 is authoritative.)

---

## ✅ 2. PDF availability & parsing risk (spec §31 "PDF parsing risk", §6 D1/D2)

**All filings are available and are real PDFs — zero dead links.**

- `d1_pull_raw.py` fetched **84/84 referenced documents** from the FinanceBench
  GitHub `pdfs/` folder; every one begins with `%PDF` and downloaded cleanly
  (158 MB total). `download_report.json`: `questions_kept: 150 / 150`.
- PDFs are served as plain files on `raw.githubusercontent.com` (not Git LFS
  pointers), so no LFS/`git lfs` step is needed.
- **Parse risk is lower than the spec fears:** FinanceBench already ships
  `evidence_text` and `evidence_text_full_page` per question — clean extracted
  text we can use as (a) a parse-quality ground truth for D2, and (b) a fallback
  page-text source if `pymupdf`/`pdfplumber` struggle on a given page.

D2 (`d2_parse_test.py`) is still worth running to build `data/pages/` and flag
table-heavy pages, but the "some PDFs may parse poorly → veto companies" risk is
largely retired by the presence of gold page text.

---

## ✅ 3. Page-number convention: 0-indexed vs 1-indexed vs printed label (spec §8, §20) — DECIDED

**Decided and encoded in `d5_select_subset.py` (`to_pdf_page()` /
`gold_evidence_from_raw()`).** This was the sharpest un-stated ambiguity and it
directly affects citation precision (±1 page slack). Three different page numbers
coexist:

- FinanceBench `evidence_page_num` is **0-indexed** into the PDF.
- The spec's `subset.json` uses `pdf_page: 61` and `page_label: "61"`.
- The printed page footer is a *third* number.

Concrete example (3M_2018_10K cash-flow question): `evidence_page_num = 59`, but
the printed footer on that page reads **60** (= 59 + 1). So all three can differ.

**Adopted convention (in code, single choke point):**
- `pdf_page` = **1-indexed PDF page** = `evidence_page_num + 1` (what a human sees
  as "page N of the PDF" and what `get_pages`/the corpus endpoint use).
- `page_label` = the **printed footer label** when D2 can extract it, else
  `str(pdf_page)`.
- Citation scoring compares `pdf_page` with ±1 slack, which absorbs the common
  off-by-one between PDF index and printed label.
- The raw `evidence_page_num` is preserved as `evidence_page_num_raw` for audit.

---

## 🔧 4. `doc_id` vs `doc_name`, `filing_period`, and `doc_type` mapping (spec §8, §13)

FinanceBench provides `doc_name` (e.g. `3M_2018_10K`), `doc_type`, and
`doc_period` (an int year). The spec's schemas reference `doc_id`, `doc_name`,
`filing_period`, and a `DocType` enum of `10k | 10q | 8k | other`.

**Recommendations:**
- `doc_id` = `doc_name` (already unique and stable). No separate id needed.
- `filing_period` = derived string, e.g. `FY2018` for annual, `2023Q1` for
  quarter/earnings (parse from `doc_name`/`doc_period`).
- **`doc_type` mapping needs a rule:** the dataset's `document_types` are
  `10k: 112, 10q: 15, 8k: 9, Earnings: 14`. The `Earnings` type (press-release /
  earnings PDFs) is **not** in the spec enum. Map `Earnings → other` (or extend the
  enum). Note ~14 questions ride on earnings docs, so decide before D5 selection;
  the safe demo path prefers 10-K/10-Q evidence and avoids `Earnings` docs.

---

## 🔧 5. Extracting `gold_value` / `gold_unit` from string answers (spec §8, §20)

FinanceBench `answer` is free text: sometimes `"$1577.00"`, sometimes a ratio,
often prose. The spec's numeric-tolerance scoring needs `gold_value` + `gold_unit`.

**Recommendation:** in D5, parse `gold_value`/`gold_unit` only for numeric answers
(regex for currency/percent/ratio); leave `gold_value = null`,
`gold_unit = "text"` for prose answers and score those by normalized string match
(§20). This aligns cleanly with the A/C = numeric, B = prose split.

---

## 🔧 6. Bucketing FinanceBench's native labels onto A/B/C (spec §6 D3)

The mapping is genuinely non-trivial and justifies the D3 LLM pass:

- `question_reasoning` is **null for 50/150** questions, and several are ambiguous
  disjunctions like `"Numerical reasoning OR Logical reasoning"` or
  `"Logical reasoning (based on numerical reasoning) OR ..."`.
- `characterize.py` ships a documented **heuristic preview** (numerical→A,
  logical/interpretation→B, extraction→C, with a `question_type` fallback) purely
  to size the subset. **D3 remains authoritative** and must not be skipped —
  roughly a third of rows can't be bucketed from the native labels alone.

No change to the spec; just confirming D3 is load-bearing, not optional.

---

## ✅ 7. "Unanswerable" abstention question requirement (spec §D5 fallback, §20)

**The open subset contains no natively unanswerable questions** — every one has
gold evidence (evidence-count distribution: 115 have 1 passage, 31 have 2, 4 have
3; none have 0). Per the §D5 fallback ("if no true unanswerable question exists,
omit the unanswerable requirement"), **omit it**, or add one clearly-marked
synthetic item excluded from headline accuracy. Abstention behavior is still
exercised by evidence-insufficient retrieval failures at run time.

---

## ⏳ 8. Native tool-calling support (spec §3, §4)

- **Model id `z-ai/glm-5.2`: confirmed available** on the NVIDIA catalog (GLM
  family; owner-confirmed). Kept as the `LLM_MODEL` default in `.env.example` /
  `config.py`, and env-overridable if the endpoint ever needs a different slug.
- **Native vs JSON tool protocol: still pending, blocked on an API key.** Unknown
  until `scripts/smoke_llm.py` runs. The scaffold defaults `TOOL_PROTOCOL=json`
  (the safe fallback); the smoke test overwrites the choice in
  `data/smoke_llm_result.json`, which `config.py` reads. **Do not build the agent
  loop until the smoke test has run.**

---

## 🔧 9. Minor / under-specified, with defaults chosen in the scaffold

| Topic | Spec gap | Default taken |
|-------|----------|---------------|
| Embedding index format (§3) | "NumPy on disk or memory" | one `.npy` matrix + a `chunks.jsonl` sidecar per company (documented in `ingest.py`) |
| `chunk_id` shape (§13) | given as a format string | encoded verbatim: `company_slug:doc_id:p{page}:c{chunk_index}` |
| Agent tool-call cap (§15) | "approximately 12" | `MAX_TOOL_CALLS_PER_ITEM = 12` in `config.py` |
| Default retrieval `k` (§13) | 6 | `RETRIEVAL_DEFAULT_K = 6` |
| Numeric tolerance (§20) | ±1% relative | `DEFAULT_RELATIVE_TOLERANCE = 0.01`, per-item override via `tolerance` |
| Backend/eval package wiring | not stated | one `uv` project at `backend/`; `evals/` and `dataset_builder/` import `app.*` and run via `uv run --project backend` |

---

## Bottom line

The two biggest scheduled risks in §31 — **dataset shape** and **PDF parsing** —
are **retired by data**: the ideal 4×8 subset is feasible and all 84 filings are
clean, available PDFs with gold page text as a safety net. The **page-indexing
convention is now decided and encoded in D5** (`pdf_page = evidence_page_num + 1`).
The remaining open items are (a) two small **convention decisions** still to encode
in D5 (doc_type/period mapping, gold-value parsing), and (b) the **LLM tool-protocol
smoke test**, which is the one genuine unknown and is gated on an API key.
