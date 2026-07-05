# Diligence Agent

Diligence Agent is a FinanceBench-backed diligence memo generator.

It compares naive retrieve-then-answer RAG against a planning, multi-retrieval,
calculator-using agent on a curated FinanceBench subset.

The output is not a chat response. It is an auditable diligence memo where every
material claim is cited and every derived number is traceable to a deterministic
calculator call.

> Single retrieve-then-answer RAG is weak on multi-input financial diligence
> questions. A planning + multi-retrieval + calculator-using agent produces more
> accurate, better-cited, and more numerically traceable answers. The eval harness
> exists to quantify this thesis.

---

## Status

**v0 is complete** (spec `v0-spec.md` §25 Steps 0–17, all cut-line items kept —
see §26). See [`AMBIGUITIES.md`](./AMBIGUITIES.md) for open spec questions
resolved against the real dataset.

What exists and runs end-to-end:

- Frozen canonical schemas: `backend/app/schemas.py` ↔ `frontend/src/types.ts`.
- LLM adapter (`backend/app/llm.py`) against Vultr Serverless Inference — the
  single choke point for all model calls; `scripts/smoke_llm.py` is the
  pre-build gate.
- Curated FinanceBench subset: **61 questions across 11 companies** (Adobe,
  AMD, Best Buy, Boeing, General Mills, Johnson & Johnson, MGM Resorts, Nike,
  PepsiCo, Pfizer, Verizon — `data/subset.json`), bucketed
  34 A_multi_input / 14 B_judgment / 13 C_lookup.
- Naive-RAG baseline (`backend/app/baseline.py`) and the planning +
  multi-retrieval + calculator agent (`backend/app/agent.py`), both run to
  completion over every company in the subset (`scripts/run_baseline.py`,
  `backend/app/agent.py --company ...`).
- Deterministic eval harness (`evals/scorers.py` + `evals/run.py`): answer
  accuracy, citation precision, citation provenance, arithmetic integrity,
  trace shape, abstention — scored per item, per bucket, per system.
- Optional Tier-2 LLM judges (`evals/judges.py`, groundedness/actionability)
  behind a calibration gate (`results/corrupted_memo_judge.json`).
- FastAPI backend (`backend/app/api.py`): `POST /runs`, `GET /runs/{id}`,
  `GET /runs/{id}/events` (SSE, live or replay from `trace.jsonl`),
  `GET /runs`, `GET /runs/{id}/memo`, `GET /corpus/...`, `GET /evals/results`.
- React frontend (`frontend/`): Run tab (company picker, live/replay trace),
  Memo tab (clickable document-aware citations, source page side panel),
  Evals tab (baseline vs agent comparison table).

### Headline comparison (61-item subset, final scorer v4, `results/comparison.json`)

Baseline is naive retrieve-then-answer RAG; **Agent** is the final tuned agent
(iteration 5). Both scored under the same final deterministic scorer (v4).

| Metric | Baseline (naive RAG) | Agent (final) |
|---|---|---|
| Correct of 61 | 22 (36%) | **51 (84%)** |
| Answered | 29 / 61 (48%) | **60 / 61 (98%)** |
| Accuracy of answered | 75.9% | **85.0%** |
| — A_multi_input | 86.7% | 87.9% |
| — B_judgment | 50.0% | **78.6%** |
| — C_lookup | 75.0% | **84.6%** |
| Citation precision | 41.4% | **58.3%** |
| Citation provenance | 100% | 100% |
| Arithmetic integrity | 69.0% | **100%** |
| Trace shape | 44.3% | **77.0%** |

The honest headline is **correct-of-61: 22 → 51**. Baseline's 75.9%
"accuracy of answered" looks respectable only because it abstains on 32 of 61
answerable items — it declines the hard questions. The agent answers 60/61 and
is still more accurate on what it answers, every one of its 51 correct answers
carries verified citations (100% provenance), and every numeric claim traces to
a `calculate` call or a grounded citation (100% arithmetic integrity vs the
baseline's 69%).

Per-bucket answer accuracy and the full breakdown are in
[`results/comparison.json`](./results/comparison.json) (rendered live by the
Evals tab). [`results/baseline.json`](./results/baseline.json) and
[`results/agent.json`](./results/agent.json) hold each system's raw
deterministic scores (baseline = the `iter0-baseline-*` runs, agent = the
`iter5-agent-*` runs, both rescored under v4); `results/agent.json` also carries
the Tier-2 judge fields, and `results/corrupted_memo_judge.json` holds the
LLM-judge calibration result.

### Improvement loop

The agent was not tuned by hand-waving. It went through **five measured
iterations** of run → analyze → improve → re-run, each one a full 61-item agent
pass with per-item timing, token, tool-call, and failure-taxonomy
instrumentation. Every iteration was rescored under the *final* scorer (v4) so
the trend below is apples-to-apples.

| Stage | Correct / 61 | Accuracy of answered | Citation precision | p95 latency | Prompt tokens |
|---|---|---|---|---|---|
| Baseline (naive RAG) | 22 | 75.9% | 41.4% | 77 s | 0.13M |
| Iter 1 — unmodified agent | 37 | 84.1% | 61.4% | 634 s | 4.61M |
| Iter 2 — citation-spiral fix | 50 | 86.2% | 48.3% | 105 s | 3.20M |
| Iter 3 — precision hardening | 49 | 90.7% | 59.3% | 68 s | 3.84M |
| Iter 4 — coverage recovery | 50 | 84.8% | 61.0% | 73 s | 3.35M |
| **Iter 5 — stability (final)** | **51** | **85.0%** | **58.3%** | 94 s | 3.20M |

What each iteration changed (each plan was written from the *previous*
iteration's failure taxonomy):

- **Iter 1 — unmodified agent.** First measurement of the raw planning +
  multi-retrieval + calculator agent. Exposed a citation-rejection *budget
  spiral* (the model saw whitespace-collapsed snippets but citations were
  validated against raw PDF text, so every quote was rejected and each rejection
  burned a tool-call slot) plus gold-labeling artifacts — a 634 s p95 and 4.6M
  prompt tokens from the spiral.
- **Iter 2 — citation-spiral fix.** Whitespace-tolerant verbatim matcher,
  stopped charging rejected `record_answer` calls to the budget, let citations
  resolve against fetched page text. Answered 44 → 58, correct 37 → 50, p95
  634 s → 105 s.
- **Iter 3 — precision hardening.** Citation minimalism (cite only the pages
  that source the reported figures), canonical polarity/entity/choice scorer
  branches, and ratio/derivation fixes. Accuracy-of-answered peaked at 90.7%.
- **Iter 4 — coverage recovery.** Commit-nudge on the over-search/no-commit
  path, a tightened stall guard, and a case-insensitive verbatim matcher, plus
  honest annotations. Answered 54 → 59.
- **Iter 5 — stability (final).** Answer-format canonicalization rules (lead
  Yes/No; always populate value/unit for numeric answers), temperature 0.2 → 0.0
  (the endpoint does not honor `seed`; this cuts run-to-run churn), a generic
  geography synonym, and the DeepSeek judge. Correct 51/61, answered 60/61 with
  the single remaining abstention (`best_buy_02`) being a genuine
  budget-exhaustion case.

#### Scoring-version history (v0 → v4)

Analysis surfaced that most of the raw baseline-vs-iter1 gap was a *measurement*
artifact, not model quality, so the scorer was tightened alongside the agent.
Each change is a labeling-honesty fix (never a threshold loosened to flatter the
agent — the baseline benefits too: it goes 3 → 22 correct across the same
progression), and **all iterations were rescored retroactively under v4**.

| Scorer | Change | Why |
|---|---|---|
| v0 | string-match only | starting point: graded answers by normalized string equality to gold text |
| v1 | + parsed numeric golds | 37 numeric golds had `gold_value=null` and were graded as strings; parse value/unit so the ±1% numeric scorer applies |
| v2 | + polarity / canonical branches | add `gold_polarity` + `gold_canonical` and gated scorer branches for honest Yes/No, entity, and multiple-choice matching |
| v3 | + iter-4 annotations | 5 honest gold annotations to clear scorer false-negatives on substantively-correct answers |
| v4 | + geography synonym, signed-off golds | generic North America ↔ "United States and Canada" synonym; honest canonical annotations for `amd_05`/`boeing_07`; **`verizon_05` carries an explicit human sign-off** |

Under v0 the baseline scored 3/61 and iter 1 scored 13/61; under the final v4
they score 22 and 37. The per-iteration scoring snapshots
(`pre_gold_fix`, `pre_canonical_fix`, `pre_iter4_annots`, `pre_final_scorer`)
are preserved under `results/iterations/baseline61/` for audit.

#### Judge (Tier-2 LLM) note

The early judges were non-informative — a flat 5.0 with zero variance across all
61 items in both baseline and iter 1, because the verbatim-citation gate
engineered out the only mismatch groundedness could detect. Iter 5 ran a
`JUDGE_MODEL` bake-off and switched to **DeepSeek-V4-Flash** (gate PASS, 9/9
agreement with the prior judge, ~30× faster). The final agent's judge scores
carry real signal and non-zero variance: groundedness **4.72 / 5**,
gold-agreement **4.39 / 5**, actionability **3.48 / 5**, all at 100% coverage
(see the `*_judge` fields in `results/agent.json`).

#### Artifacts & how to re-run an iteration

Every iteration's staged artifacts live under
[`results/iterations/`](./results/iterations/) — per-iteration `metrics.json`
(timing/token/taxonomy), `per_item_scores.json`, the staged `runs/`, and
`code_state.diff` for traceability — with the cumulative trend, per-bucket,
taxonomy, timing, token, judge, churn-matrix, and scoring-version data rolled up
into [`results/iterations/report_data.json`](./results/iterations/report_data.json).

```bash
# one full iteration: run every company in the subset, stage, score, analyze
uv run --project backend scripts/run_iteration.py --iteration <N> --label "<what changed>"
uv run --project backend scripts/run_iteration.py --iteration 0 --system baseline --no-judges
uv run --project backend scripts/run_iteration.py --iteration <N> --skip-runs   # rescore-only

# standalone cross-run analysis over already-staged runs
uv run --project backend scripts/analyze_iteration.py --iteration <N> \
    --runs-dir results/iterations/iter<N>/runs \
    --scores  results/iterations/iter<N>/per_item_scores.json \
    --out     results/iterations/iter<N>/metrics.json
```

> **Index rebuild.** `data/index/` is gitignored. `backend/app/ingest.py`
> rebuilds it from the filings; all 11 subset companies are verified present in
> the index before any run.

### How to reproduce

```bash
cp .env.example .env && uv sync --project backend
uv run --project backend scripts/smoke_llm.py            # gate
uv run --project backend python -m app.ingest --all      # rebuild data/index/ (gitignored)

# full staged runs over all 11 subset companies (one process per company)
uv run --project backend scripts/run_iteration.py --iteration 0 --system baseline --no-judges
uv run --project backend scripts/run_iteration.py --iteration 5 --label "final"

# score the staged runs (no --judges keeps it deterministic) and build the comparison
uv run --project backend evals/run.py --system baseline \
    --runs-dir results/iterations/baseline61/runs
uv run --project backend evals/run.py --system agent \
    --runs-dir results/iterations/iter5/runs
uv run --project backend scripts/build_comparison.py       # -> results/comparison.json
```

`results/{baseline,agent}.json` are the rescored `iter0-baseline-*` and
`iter5-agent-*` runs under the final scorer; `build_comparison.py` reads those
two files plus `data/subset.json` and is pure aggregation (no LLM calls). The
one-shot per-company path (`scripts/run_baseline.py`, `python -m app.agent
--company "<Company>"`) still works for a single ad-hoc run.

### Demo trace

`runs/agent-pepsico-full/trace.jsonl` is the suggested replay for the demo
script (spec §27): a full 8-item PepsiCo run with multi-retrieval, `calculate`
tool calls, document-aware citations, and a rendered memo (5/8 items
answered with citations, 3 abstained). Replay it via
`GET /runs/agent-pepsico-full/events` (the Run tab can't tell live from
replay) or open it directly in the Memo tab.

---

## Layout

```
backend/app/     schemas, config, llm adapter, trace writer, tools, ingest, retrieval, baseline, agent, api
dataset_builder/ d1–d6: pull, parse-test, classify, verify, select subset, spot-check
evals/           run.py (scorers + comparison-ready results) + scorers.py + judges.py; fixtures/
frontend/        Vite + React + TS; Run/Memo/Evals tabs wired to the live API; types.ts mirror
data/            raw/, filings/, pages/, index/ (gitignored); subset.json (curated); profiles + reports
runs/            {run_id}/trace.jsonl + memo.json + memo.md (gitignored)
results/         baseline.json, agent.json, comparison.json, corrupted_memo_judge.json; iterations/ (per-iteration metrics + report_data.json)
scripts/         smoke_llm.py, run_baseline.py, build_comparison.py, run_iteration.py, analyze_iteration.py
```

---

## Setup

### Backend (Python 3.11+, `uv` — no pip)

```bash
cp .env.example .env          # add NVIDIA_API_KEY (LLM_MODEL default is confirmed)
uv sync --project backend
uv run --project backend scripts/smoke_llm.py    # gate: writes data/smoke_llm_result.json
```

The smoke test decides the tool-calling protocol (native vs json) once; the agent
loop never decides per call.

### Dataset (already pulled; re-run any time)

```bash
uv run --no-project dataset_builder/d1_pull_raw.py       # metadata + PDFs
uv run --no-project dataset_builder/characterize.py      # -> data/dataset_profile.json
```

### Run the backend API + frontend

```bash
cd backend && uv run --project . uvicorn app.api:app --reload   # http://localhost:8000
cd frontend && pnpm install && pnpm dev                          # http://localhost:5173
```

### Full eval run (Step 17 — reproduces `results/`)

See "How to reproduce" above.

---

### Docker Compose (serving/demo stack only)

One-command bring-up of the whole demo stack against the local dataset and
whatever runs already exist under `runs/`. This never ingests, builds the
dataset, or executes eval runs — that stays host-side via `uv run --project
backend ...` (see above).

```bash
cp .env.example .env      # add NVIDIA_API_KEY (Vultr endpoint) if not already done
docker compose up --build
```

- Frontend: <http://localhost:5173> (nginx, proxies `/runs`, `/companies`,
  `/corpus`, `/evals`, `/health` to the backend, including the SSE trace
  stream).
- Backend: <http://localhost:8000> (FastAPI/uvicorn).
- `data/` is bind-mounted read-only and `runs/`/`results/` are bind-mounted
  so the containers replay existing traces and serve corpus pages without
  rebuilding anything; `.env` is passed via `env_file`, never baked into
  either image.
- `docker compose down` then `docker compose up --build` again is idempotent.

---

## Build order (eval-first)

Built per `v0-spec.md` section 25 (Steps 0–17), schemas/fixtures/deterministic
eval harness **before** the agent. Nothing on the section 26 cut list was cut:
deterministic eval, baseline comparison, live/replay trace, citation
provenance, arithmetic integrity, the calculator, and document-aware
citations are all in place.

## Data source & credit

FinanceBench open-source subset (150 examples), Patronus AI —
<https://github.com/patronus-ai/financebench>. We do **not** create new gold
labels; we curate and human-audit a weekend-sized subset of FinanceBench's gold
answers and evidence.
