"""FastAPI app + SSE (spec section 23, Step 14).

Endpoints:
    POST /runs                                  start a run (agent or baseline)
    GET  /runs                                  list past runs (RunCard[])
    GET  /runs/{id}                             run status
    GET  /runs/{id}/events                      SSE: live queue OR replay from trace.jsonl
    GET  /runs/{id}/memo                        200 done / 202 running / 404 missing / 500 failed
    GET  /corpus/{company}/{doc_id}/page/{n}    raw page text + citation spans
    GET  /evals/results                         results/comparison.json

Replay mode sleeps 150-400ms between events; the frontend must not be able to
tell live from replay (spec section 11.7 / 23).

Run bookkeeping: a run created via POST /runs is tracked in-memory (`_RUNS`) for
the lifetime of this process AND mirrored to `runs/{run_id}/run.json` on every
status transition, so GET /runs and GET /runs/{id} keep working after a
restart. Runs that exist on disk but were never created through this API (e.g.
produced directly by `agent.py`/`baseline.py` during earlier build steps, or by
another process sharing the `runs/` directory) have no `run.json`; their status
is synthesized from `memo.json` (if present) or the tail of `trace.jsonl`
otherwise. Directories with no trace at all are skipped.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import config
from .agent import run_agent
from .baseline import run_baseline
from .ingest import slugify
from .schemas import (
    CompanyChecklist,
    CreateRunRequest,
    CreateRunResponse,
    IterativeEvalReport,
    PageResponse,
    RunCard,
    RunStatusResponse,
    SubsetItem,
    TraceEvent,
    VerdictBadge,
    agent_visible_item,
)
from .trace import TraceWriter

app = FastAPI(title="Diligence Agent", version="0.1.0")

# Dev convenience: the Vite dev server proxies same-origin, but keep CORS open
# so the frontend can also be pointed at the API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Company picker + checklist preview (spec section 24 "Run tab"; not itemized
# in section 23 but required to populate the picker before POST /runs)
# --------------------------------------------------------------------------
@app.get("/companies", response_model=list[CompanyChecklist])
def list_companies() -> list[CompanyChecklist]:
    if not config.SUBSET_PATH.exists():
        raise HTTPException(status_code=404, detail=f"{config.SUBSET_PATH} not found (built in Step 8)")

    try:
        raw = json.loads(config.SUBSET_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"corrupt {config.SUBSET_PATH}: {exc}") from exc

    by_company: dict[str, list[Any]] = {}
    for row in raw:
        item = agent_visible_item(SubsetItem(**row))
        by_company.setdefault(item.company, []).append(item)

    return [
        CompanyChecklist(company=company, items=items)
        for company, items in sorted(by_company.items())
    ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_dir(run_id: str) -> Path:
    return config.RUNS_DIR / run_id


def _meta_path(run_id: str) -> Path:
    return _run_dir(run_id) / "run.json"


def _dir_iso_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return _now_iso()


# --------------------------------------------------------------------------
# In-process run registry
# --------------------------------------------------------------------------
class _RunRecord:
    """Live bookkeeping for a run started by this process."""

    def __init__(self, run_id: str, company: str, system: str, item_ids: Optional[list[str]]) -> None:
        self.run_id = run_id
        self.company = company
        self.system = system
        self.item_ids = item_ids
        self.status = "queued"
        self.created_at = _now_iso()
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.error: Optional[str] = None
        self.trace: Optional[TraceWriter] = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "company": self.company,
            "system": self.system,
            "item_ids": self.item_ids,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


_RUNS: dict[str, _RunRecord] = {}
_RUNS_LOCK = threading.Lock()


def _persist_meta(record: _RunRecord) -> None:
    _run_dir(record.run_id).mkdir(parents=True, exist_ok=True)
    _meta_path(record.run_id).write_text(json.dumps(record.to_meta(), indent=2))


def _execute_run(record: _RunRecord, trace: TraceWriter) -> None:
    record.status = "running"
    record.started_at = _now_iso()
    _persist_meta(record)
    try:
        fn = run_agent if record.system == "agent" else run_baseline
        fn(record.run_id, record.company, record.item_ids, trace)
        record.status = "completed"
    except Exception as exc:  # noqa: BLE001 - surface any failure as a failed run, never a crash
        record.status = "failed"
        record.error = str(exc)
        try:
            trace.emit("error", "Run failed", detail=str(exc))
        except Exception:
            pass
    finally:
        record.completed_at = _now_iso()
        _persist_meta(record)
        trace.close()


@app.post("/runs", response_model=CreateRunResponse)
def create_run(req: CreateRunRequest) -> CreateRunResponse:
    ts_ms = int(time.time() * 1000)
    slug = slugify(req.company)
    with _RUNS_LOCK:
        run_id = f"{req.system}_{slug}_{ts_ms}"
        while run_id in _RUNS or _run_dir(run_id).exists():
            ts_ms += 1
            run_id = f"{req.system}_{slug}_{ts_ms}"
        record = _RunRecord(run_id, req.company, req.system, req.item_ids)
        trace = TraceWriter(run_id)  # creates runs/{run_id}/ immediately
        record.trace = trace
        _RUNS[run_id] = record
    _persist_meta(record)

    thread = threading.Thread(target=_execute_run, args=(record, trace), daemon=True)
    thread.start()

    return CreateRunResponse(run_id=run_id, status="queued")


# --------------------------------------------------------------------------
# Status synthesis for runs not (or no longer) tracked in-memory
# --------------------------------------------------------------------------
_PLAN_COMPANY_RE = re.compile(r"for ([A-Za-z0-9&.,'()\- ]+?)\.\s*$")


def _company_from_events(events: list[TraceEvent]) -> Optional[str]:
    for event in events:
        if event.type == "plan":
            match = _PLAN_COMPANY_RE.search(event.detail or "")
            if match:
                return match.group(1).strip()
    return None


def _synthesize_meta(run_id: str) -> Optional[dict[str, Any]]:
    """Best-effort status for a run directory with no run.json (e.g. produced
    outside this API during earlier build steps)."""
    run_dir = _run_dir(run_id)
    memo_path = run_dir / "memo.json"
    trace_path = run_dir / "trace.jsonl"

    if memo_path.exists():
        try:
            memo = json.loads(memo_path.read_text())
        except (json.JSONDecodeError, OSError):
            memo = {}
        return {
            "run_id": run_id,
            "company": memo.get("company", "unknown"),
            "status": memo.get("status", "completed"),
            "created_at": memo.get("created_at") or _dir_iso_mtime(run_dir),
            "started_at": memo.get("created_at"),
            "completed_at": memo.get("completed_at"),
            "error": None,
        }

    if trace_path.exists() and trace_path.stat().st_size > 0:
        events = TraceWriter.read(run_id)
        if not events:
            return None
        last = events[-1]
        if last.type == "verdict":
            status, error = "completed", None
        elif last.type == "error":
            status, error = "failed", last.detail
        else:
            status, error = "failed", "run ended without a verdict (interrupted trace)"
        return {
            "run_id": run_id,
            "company": _company_from_events(events) or "unknown",
            "status": status,
            "created_at": events[0].ts,
            "started_at": events[0].ts,
            "completed_at": last.ts,
            "error": error,
        }

    return None


def _get_run_meta(run_id: str) -> Optional[dict[str, Any]]:
    with _RUNS_LOCK:
        record = _RUNS.get(run_id)
    if record is not None:
        return record.to_meta()

    meta_path = _meta_path(run_id)
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text())
            return {
                "run_id": data.get("run_id", run_id),
                "company": data.get("company", "unknown"),
                "status": data.get("status", "failed"),
                "created_at": data.get("created_at") or _dir_iso_mtime(_run_dir(run_id)),
                "started_at": data.get("started_at"),
                "completed_at": data.get("completed_at"),
                "error": data.get("error"),
            }
        except (json.JSONDecodeError, OSError):
            pass

    return _synthesize_meta(run_id)


def _counts_from_trace(events: list[TraceEvent]) -> tuple[int, int, int]:
    items_total = 0
    for event in events:
        if event.type == "plan":
            items = event.payload.get("items")
            if isinstance(items, list) and items:
                items_total = len(items)
    answered = sum(1 for e in events if e.type == "item_answer" and e.payload.get("status") == "answered")
    abstained = sum(1 for e in events if e.type == "item_answer" and e.payload.get("status") == "abstained")
    return items_total, answered, abstained


def _verdict_badge(status: str, items_total: int, items_answered: int) -> VerdictBadge:
    if status == "failed":
        return "failed"
    if status != "completed" or items_total == 0:
        return "unknown"
    ratio = items_answered / items_total
    if ratio >= 0.75:
        return "strong"
    if ratio >= 0.4:
        return "mixed"
    return "failed"


def _run_card(run_id: str) -> Optional[RunCard]:
    meta = _get_run_meta(run_id)
    if meta is None:
        return None

    memo_path = _run_dir(run_id) / "memo.json"
    items_total = items_answered = items_abstained = 0
    if memo_path.exists():
        try:
            memo = json.loads(memo_path.read_text())
            summary = memo.get("summary", {})
            items_total = summary.get("items_total", 0)
            items_answered = summary.get("items_answered", 0)
            items_abstained = summary.get("items_abstained", 0)
        except (json.JSONDecodeError, OSError):
            pass
    else:
        events = TraceWriter.read(run_id)
        items_total, items_answered, items_abstained = _counts_from_trace(events)

    return RunCard(
        run_id=run_id,
        company=meta["company"],
        status=meta["status"],
        created_at=meta["created_at"],
        items_total=items_total,
        items_answered=items_answered,
        items_abstained=items_abstained,
        verdict_badge=_verdict_badge(meta["status"], items_total, items_answered),
    )


@app.get("/runs", response_model=list[RunCard])
def list_runs() -> list[RunCard]:
    run_ids: set[str] = set()
    with _RUNS_LOCK:
        run_ids.update(_RUNS.keys())
    if config.RUNS_DIR.is_dir():
        run_ids.update(p.name for p in config.RUNS_DIR.iterdir() if p.is_dir())

    cards = [card for card in (_run_card(run_id) for run_id in run_ids) if card is not None]
    cards.sort(key=lambda c: c.created_at, reverse=True)
    return cards


@app.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    meta = _get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return RunStatusResponse(
        run_id=meta["run_id"],
        company=meta["company"],
        status=meta["status"],
        created_at=meta["created_at"],
        started_at=meta.get("started_at"),
        completed_at=meta.get("completed_at"),
        error=meta.get("error"),
    )


# --------------------------------------------------------------------------
# SSE: live queue while running, replay (with sleeps) once complete
# --------------------------------------------------------------------------
def _sse(event: TraceEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


def _live_stream(trace: TraceWriter) -> Iterator[str]:
    while True:
        event = trace.sse_queue.get()  # blocks; StreamingResponse runs this in a threadpool
        if event is None:
            break
        yield _sse(event)


def _replay_stream(run_id: str) -> Iterator[str]:
    events = TraceWriter.read(run_id)
    for i, event in enumerate(events):
        if i > 0:
            time.sleep(random.uniform(0.15, 0.4))
        yield _sse(event)


@app.get("/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    with _RUNS_LOCK:
        record = _RUNS.get(run_id)

    if record is not None and record.status in ("queued", "running") and record.trace is not None:
        generator: Iterator[str] = _live_stream(record.trace)
    else:
        meta = _get_run_meta(run_id)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        generator = _replay_stream(run_id)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/runs/{run_id}/memo")
def get_memo(run_id: str) -> JSONResponse:
    meta = _get_run_meta(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")

    memo_path = _run_dir(run_id) / "memo.json"
    if memo_path.exists():
        try:
            return JSONResponse(content=json.loads(memo_path.read_text()), status_code=200)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"corrupt memo.json for '{run_id}': {exc}") from exc

    if meta["status"] == "failed":
        return JSONResponse(
            content={"run_id": run_id, "status": "failed", "error": meta.get("error") or "run failed"},
            status_code=500,
        )

    # queued / running / cancelled: not ready yet
    return JSONResponse(content={"run_id": run_id, "status": meta["status"]}, status_code=202)


# --------------------------------------------------------------------------
# Corpus page viewer
# --------------------------------------------------------------------------
def _spans_for_page(doc_id: str, page: int) -> list[dict[str, Any]]:
    """Citation spans on this doc/page across every persisted run memo, so the
    side panel (spec section 24) can highlight the relevant quote regardless of
    which run's citation the frontend opened the panel from."""
    spans: list[dict[str, Any]] = []
    if not config.RUNS_DIR.is_dir():
        return spans

    # citation_id is assigned per-item by the agent/baseline (e.g. "citation_001",
    # "citation_002", ...) and is scoped to that item only — it is NOT globally
    # unique. It collides across different items within a single run's memo
    # (each item restarts its own citation_001, citation_002, ...) and, a
    # fortiori, across different runs. The only combination that uniquely
    # identifies a citation is (run_id, item_id, citation_id), so dedup (and
    # key returned spans) on that triple, and include run_id/item_id in each
    # span so the frontend can disambiguate exactly which citation it is.
    seen: set[tuple[str, str, str]] = set()
    for run_dir in config.RUNS_DIR.iterdir():
        run_id = run_dir.name
        memo_path = run_dir / "memo.json"
        if not memo_path.exists():
            continue
        try:
            memo = json.loads(memo_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for item in memo.get("items", []):
            item_id = item.get("item_id")
            for citation in item.get("citations", []):
                if citation.get("doc_id") != doc_id or citation.get("pdf_page") != page:
                    continue
                citation_id = citation.get("citation_id")
                key = (run_id, item_id, citation_id)
                if key in seen:
                    continue
                seen.add(key)
                spans.append(
                    {
                        "run_id": run_id,
                        "item_id": item_id,
                        "citation_id": citation_id,
                        "char_start": citation.get("char_start"),
                        "char_end": citation.get("char_end"),
                    }
                )
    return spans


@app.get("/corpus/{company}/{doc_id}/page/{n}", response_model=PageResponse)
def get_page(company: str, doc_id: str, n: int) -> PageResponse:
    doc_path = config.PAGES_DIR / slugify(company) / f"{doc_id}.json"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"no parsed pages for {company}/{doc_id}")

    try:
        doc = json.loads(doc_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"corrupt page file for {doc_id}: {exc}") from exc

    page = next((p for p in doc.get("pages", []) if p.get("page") == n), None)
    if page is None:
        raise HTTPException(
            status_code=404,
            detail=f"page {n} not found in {doc_id} ({doc.get('num_pages', '?')} pages)",
        )

    return PageResponse(
        company=company,
        doc_id=doc_id,
        doc_name=doc.get("doc_name", doc_id),
        page=n,
        text=page.get("text", ""),
        spans=_spans_for_page(doc_id, n),
    )


# --------------------------------------------------------------------------
# Evals tab
# --------------------------------------------------------------------------
@app.get("/evals/results")
def get_eval_results() -> JSONResponse:
    path = config.RESULTS_DIR / "comparison.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="results/comparison.json not found; run `evals/run.py --system agent` (and "
            "--system baseline) first",
        )
    try:
        return JSONResponse(content=json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"corrupt results/comparison.json: {exc}") from exc


def _iterations_dir() -> Path:
    return config.RESULTS_DIR / "iterations"


def _read_iteration_report(path: Path) -> IterativeEvalReport:
    try:
        return IterativeEvalReport.model_validate_json(path.read_text())
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"corrupt iterative eval report {path}: {exc}") from exc


@app.get("/evals/iterations")
def list_eval_iterations() -> list[dict[str, Any]]:
    base = _iterations_dir()
    if not base.exists():
        return []

    experiments: list[dict[str, Any]] = []
    for manifest_path in sorted(base.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        experiments.append(
            {
                "experiment_id": manifest.get("experiment_id", manifest_path.parent.name),
                "status": manifest.get("status", "unknown"),
                "created_at": manifest.get("created_at"),
                "completed_at": manifest.get("completed_at"),
                "model": manifest.get("model"),
                "tool_protocol": manifest.get("tool_protocol"),
                "run_selection": manifest.get("run_selection", {}),
            }
        )
    return experiments


@app.get("/evals/iterations/latest", response_model=IterativeEvalReport)
def get_latest_eval_iteration() -> IterativeEvalReport:
    path = _iterations_dir() / "latest.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="results/iterations/latest.json not found; run scripts/run_iterative_eval.py first",
        )
    return _read_iteration_report(path)


@app.get("/evals/iterations/{experiment_id}", response_model=IterativeEvalReport)
def get_eval_iteration(experiment_id: str) -> IterativeEvalReport:
    path = _iterations_dir() / experiment_id / "latest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"iterative eval experiment {experiment_id!r} not found")
    return _read_iteration_report(path)
