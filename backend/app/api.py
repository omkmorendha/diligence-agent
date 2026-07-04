"""FastAPI app + SSE (spec section 23, Step 14).

Endpoints:
    POST /runs                                  start a run (agent or baseline)
    GET  /runs                                  list past runs (RunCard[])
    GET  /runs/{id}                             run status
    GET  /runs/{id}/events                      SSE: live queue OR replay from trace.jsonl
    GET  /runs/{id}/memo                        200 done / 202 running / 404 missing
    GET  /corpus/{company}/{doc_id}/page/{n}    raw page text + citation spans
    GET  /evals/results                         results/comparison.json

Replay mode sleeps 150-400ms between events; the frontend must not be able to
tell live from replay (spec section 11.7 / 23).

TODO(Step 14).
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Diligence Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# TODO(Step 14): implement the endpoints above. Keep this module thin — run
# orchestration lives in agent.py / baseline.py; scoring lives in evals/.
