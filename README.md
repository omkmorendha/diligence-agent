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

This repo is at **Step 0 (scaffold) + dataset pulled/characterized**. See
[`v0-spec.md`](./v0-spec.md) for the full build spec and
[`AMBIGUITIES.md`](./AMBIGUITIES.md) for open spec questions resolved against the
real dataset.

What exists and runs today:

- Repo scaffold (directory tree + module stubs, each pointing at its build step).
- Frozen canonical schemas: `backend/app/schemas.py` ↔ `frontend/src/types.ts`.
- LLM adapter (`backend/app/llm.py`) — the single choke point for all model calls.
- LLM smoke test (`scripts/smoke_llm.py`) — the pre-build gate (needs an API key).
- Safe deterministic `calculate` tool (`backend/app/tools.py`).
- **Dataset D1 puller + characterizer**, already run against real FinanceBench:
  - `data/raw/financebench.jsonl` — 150 questions, 84 docs, joined with metadata.
  - `data/filings/*.pdf` — all 84 filing PDFs (158 MB, gitignored).
  - `data/raw/download_report.json` — 150/150 questions kept, 0 dead links.
  - `data/dataset_profile.json` — companies, buckets, feasibility.

Everything else (agent loop, baseline, retrieval, ingest, eval scorers, API, UI)
is a documented stub keyed to the spec's build steps.

---

## Layout

```
backend/app/     schemas, config, llm adapter, trace writer, tools; agent/baseline/api/retrieval/ingest stubs
dataset_builder/ d1 (done) + characterize (done); d2–d6 stubs
evals/           run.py + scorers.py + judges.py stubs; fixtures/ (Step 2 checklist)
frontend/        Vite + React + TS; three-tab shell; types.ts mirror
data/            raw/, filings/, pages/, subset.json (curated); profiles + reports
runs/            {run_id}/trace.jsonl + memo.json + memo.md (gitignored)
results/         baseline.json, agent.json, comparison.json
scripts/         smoke_llm.py
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

### Frontend (`pnpm` — no npm)

```bash
cd frontend
pnpm install
pnpm dev            # http://localhost:5173 (proxies /runs, /corpus, /evals to :8000)
```

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

Follow `v0-spec.md` section 25. The schemas, fixtures, and deterministic eval
harness come **before** the agent. Never cut: deterministic eval, baseline
comparison, live/replay trace, citation provenance, arithmetic integrity, the
calculator, or document-aware citations.

## Data source & credit

FinanceBench open-source subset (150 examples), Patronus AI —
<https://github.com/patronus-ai/financebench>. We do **not** create new gold
labels; we curate and human-audit a weekend-sized subset of FinanceBench's gold
answers and evidence.
