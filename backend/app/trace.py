"""Trace writer (spec sections 9-12).

Every run emits events that are (1) appended immediately to
`runs/{run_id}/trace.jsonl`, (2) held in an in-memory list, and (3) pushed to an
SSE queue. Never wait until completion to persist — the trace IS the product.

This module is intentionally small and dependency-light so every other module
(tools, agent, baseline) can emit events the same way.
"""

from __future__ import annotations

import json
import queue
from datetime import datetime, timezone
from typing import Any, Optional

from . import config
from .schemas import TraceEvent, TraceEventType


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceWriter:
    """Append-only trace for a single run, with a live SSE queue.

    Event ordering rules (spec section 12) are the caller's responsibility; this
    class only guarantees strictly increasing `seq` and immediate persistence.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._seq = 0
        self.events: list[TraceEvent] = []
        self.sse_queue: "queue.Queue[Optional[TraceEvent]]" = queue.Queue()
        self.run_dir = config.RUNS_DIR / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.run_dir / "trace.jsonl"

    def emit(
        self,
        type: TraceEventType,
        title: str,
        detail: str = "",
        item_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(
            schema_version=config.SCHEMA_VERSION,
            run_id=self.run_id,
            seq=self._seq,
            ts=_now_iso(),
            type=type,
            title=title,
            detail=detail,
            item_id=item_id,
            payload=payload or {},
        )
        # 1. persist immediately
        with self.trace_path.open("a") as f:
            f.write(event.model_dump_json() + "\n")
        # 2. in-memory
        self.events.append(event)
        # 3. SSE
        self.sse_queue.put(event)
        return event

    def close(self) -> None:
        """Signal end-of-stream to any live SSE consumer."""
        self.sse_queue.put(None)

    @staticmethod
    def read(run_id: str) -> list[TraceEvent]:
        """Load a completed trace from disk (for replay mode)."""
        path = config.RUNS_DIR / run_id / "trace.jsonl"
        if not path.exists():
            return []
        return [TraceEvent(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
