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
- Curated FinanceBench subset: **28 questions across 4 companies** (AMD,
  Boeing, Johnson & Johnson, PepsiCo — `data/subset.json`), bucketed
  12 A_multi_input / 10 B_judgment / 6 C_lookup.
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

### Headline comparison (full curated subset, `results/comparison.json`)

| Metric | Baseline (naive RAG) | Agent |
|---|---|---|
| Answer accuracy | 0% | **13.6%** |
| Answer accuracy — A_multi_input | n/a (abstains on every item) | **25%** |
| Citation precision | 0% | **54.5%** |
| Citation provenance | 100% | 100% |
| Arithmetic integrity | 100%* | 90.9% |

\* Baseline's arithmetic integrity is trivially 100%: it abstains on nearly
every numeric question rather than claiming an unsupported number (0%
citation precision tells the more honest story — when it does answer, its
citations don't hold up). The agent claims real numeric values (91% traced to
a `calculate` call or a grounded citation) and is the only system that
produces a scoreable, correct answer on any A_multi_input question.

Per-bucket answer accuracy and the full breakdown are in
[`results/comparison.json`](./results/comparison.json) (rendered live by the
Evals tab). [`results/baseline.json`](./results/baseline.json) and
[`results/agent.json`](./results/agent.json) hold each system's raw
deterministic scores; `results/corrupted_memo_judge.json` holds the LLM-judge
calibration result.

### How to reproduce

```bash
cp .env.example .env && uv sync --project backend
uv run --project backend scripts/smoke_llm.py            # gate

uv run --project backend scripts/run_baseline.py          # 1 run per company
cd backend && uv run --project . -m app.agent --company "<Company>"  # per company

uv run --project backend evals/run.py --system baseline
uv run --project backend evals/run.py --system agent
uv run --project backend scripts/build_comparison.py       # -> results/comparison.json
```

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
results/         baseline.json, agent.json, comparison.json, corrupted_memo_judge.json
scripts/         smoke_llm.py, run_baseline.py, build_comparison.py
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
