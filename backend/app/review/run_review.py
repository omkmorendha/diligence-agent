"""Review orchestrator — drives S1..S6 (spec section 5).

FROZEN CONTRACT — signature must not change.

parse -> extract -> scope_check -> verify -> assemble_report -> annotate, writing
all artifacts under `runs/reviews/{review_id}/` (docmodel.json, claims.json,
report.json, annotated.<ext>, report.html, trace.jsonl, llm_calls.jsonl,
review.json) and returning the assembled `ReviewReport`. If >80% of claims are
out of scope, stops after scope check with status `out_of_scope` (spec section 8).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config, llm
from ..ingest import slugify
from ..schemas import ReviewReport
from ..trace import TraceWriter
from .annotate_docx import annotate_docx
from .annotate_md import annotate_md
from .annotate_pdf import annotate_pdf
from .extract import extract_claims
from .parse import parse_document
from .registry import corpus_registry, out_of_scope_fraction, scope_check, scope_verdict
from .report import assemble_report
from .report_html import render_report_html
from .verify import verify_claims


_ANNOTATORS = {
    "pdf": annotate_pdf,
    "docx": annotate_docx,
    "md": annotate_md,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_dir(review_id: str) -> Path:
    return config.REVIEWS_DIR / review_id


def _review_meta_path(review_id: str) -> Path:
    return _review_dir(review_id) / "review.json"


def _write_json(path: Path, value: Any) -> None:
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json")
    elif isinstance(value, list):
        data = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in value]
    else:
        data = value
    path.write_text(json.dumps(data, indent=2) + "\n")


def _read_existing_meta(review_id: str) -> dict[str, Any]:
    path = _review_meta_path(review_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_meta(review_id: str, **updates: Any) -> None:
    review_dir = _review_dir(review_id)
    review_dir.mkdir(parents=True, exist_ok=True)
    meta = _read_existing_meta(review_id)
    meta.setdefault("review_id", review_id)
    meta.setdefault("created_at", _now_iso())
    meta.update({k: v for k, v in updates.items() if v is not None})
    _review_meta_path(review_id).write_text(json.dumps(meta, indent=2) + "\n")


def _copy_upload(upload_path: Path, review_dir: Path) -> Path:
    suffix = upload_path.suffix.lower()
    if suffix == ".markdown":
        suffix = ".md"
    dst = review_dir / f"upload{suffix}"
    if upload_path.resolve() != dst.resolve():
        shutil.copyfile(upload_path, dst)
    return dst


def _scope_results(claims, trace: TraceWriter):
    """Convert deterministic scope decisions into VerificationResults.

    `scope_check` uses Claim.status="SKIPPED" because the frozen Claim schema has
    no verdict field. For report accounting, scope decisions are real verdicts, so
    the orchestrator stamps active scoped claims as VERIFIED and returns synthetic
    VerificationResults for them.
    """
    from ..schemas import VerificationResult

    active_before_scope = {claim.claim_id for claim in claims if claim.status == "PENDING"}
    scoped = scope_check(claims)
    reg = corpus_registry()
    results = []
    for claim in scoped:
        verdict, explanation = scope_verdict(claim, reg)
        if verdict is None or claim.claim_id not in active_before_scope:
            continue
        claim.status = "VERIFIED"
        result = VerificationResult(
            claim_id=claim.claim_id,
            verdict=verdict,
            explanation=explanation,
            confidence="high" if verdict == "OUT_OF_SCOPE" else "medium",
        )
        results.append(result)
        trace.emit(
            "scope_check",
            f"{claim.claim_id}: {verdict}",
            detail=explanation,
            item_id=claim.claim_id,
            payload={"claim_id": claim.claim_id, "verdict": verdict},
        )
    trace.emit(
        "scope_check",
        "scope check complete",
        payload={
            "out_of_scope_fraction": out_of_scope_fraction(scoped),
            "scoped_claims": len(results),
        },
    )
    return scoped, results


def _claim_reuse_key(claim) -> tuple[str, str, str, str, str, str]:
    """Stable key for safely reusing pilot results across a full promotion.

    Extraction is re-run for full reviews, and claim IDs are assigned by extraction
    order. Reuse only when the material claim identity matches, not merely `c01`.
    """
    return (
        claim.quote,
        claim.question,
        claim.company,
        claim.period or "",
        claim.metric or "",
        claim.claim_type,
    )


def _load_existing_results(review_dir: Path):
    """Load prior pilot results when a review is promoted to full mode."""
    from ..schemas import Claim, VerificationResult

    path = review_dir / "report.json"
    if not path.exists():
        return {}
    try:
        report = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    results = {}
    for row in report.get("claims", []):
        claim = row.get("claim") if isinstance(row, dict) else None
        result = row.get("result") if isinstance(row, dict) else None
        if not isinstance(claim, dict) or not isinstance(result, dict) or not result.get("claim_id"):
            continue
        try:
            parsed_claim = Claim.model_validate(claim)
            parsed = VerificationResult.model_validate(result)
        except ValueError:
            continue
        results[_claim_reuse_key(parsed_claim)] = parsed
    return results


def _mark_verified(claims, results, trace: TraceWriter) -> None:
    by_id = {result.claim_id: result for result in results}
    for claim in claims:
        result = by_id.get(claim.claim_id)
        if result is None:
            continue
        claim.status = "VERIFIED"
        trace.emit(
            "claim_verdict",
            f"{claim.claim_id}: {result.verdict}",
            detail=result.explanation,
            item_id=claim.claim_id,
            payload={"claim_id": claim.claim_id, "verdict": result.verdict},
        )


def _annotate(upload_path: Path, report: ReviewReport, review_dir: Path, trace: TraceWriter) -> Path:
    annotator = _ANNOTATORS[report.format]
    out = review_dir / f"annotated.{report.format}"
    annotator(upload_path, report, out)
    trace.emit(
        "annotation",
        f"annotated {report.format.upper()}",
        detail=out.name,
        payload={"path": out.name, "format": report.format},
    )
    return out


def run_review(review_id: str, upload_path: str | Path, pilot: bool) -> ReviewReport:
    """Run the full review pipeline for one upload and return the assembled report."""
    review_dir = _review_dir(review_id)
    review_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceWriter(review_id, run_dir=review_dir)
    llm_calls_path = review_dir / "llm_calls.jsonl"
    llm_calls_path.touch(exist_ok=True)
    started_at = _now_iso()
    source_upload = Path(upload_path)
    if not source_upload.exists():
        raise FileNotFoundError(f"upload not found: {source_upload}")

    _write_meta(review_id, status="running", pilot=pilot, started_at=started_at, error=None)
    llm.set_usage_sink(llm.jsonl_usage_sink(llm_calls_path))
    llm.set_run_context(run_id=review_id, system="review", pilot=pilot)

    try:
        upload = _copy_upload(source_upload, review_dir)
        _write_meta(review_id, filename=upload.name, format=upload.suffix.lower().lstrip("."))

        trace.emit("plan", "review started", detail=upload.name, payload={"pilot": pilot})

        # S1 parse
        docmodel = parse_document(upload)
        _write_json(review_dir / "docmodel.json", docmodel)
        _write_meta(review_id, filename=docmodel.filename, format=docmodel.format)
        trace.emit(
            "decision",
            "parsed upload",
            detail=f"{len(docmodel.blocks)} blocks",
            payload={"format": docmodel.format, "blocks": len(docmodel.blocks)},
        )

        # S2 extract
        claims = extract_claims(docmodel, pilot, trace=trace)
        _write_json(review_dir / "claims.json", claims)

        # S3 scope check and fail-fast
        claims, results = _scope_results(claims, trace)
        fraction = out_of_scope_fraction(claims)
        status = "completed"
        if fraction > 0.8:
            status = "out_of_scope"
            for claim in claims:
                if claim.status == "PENDING":
                    claim.status = "SKIPPED"
            trace.emit(
                "decision",
                "review stopped out of scope",
                detail=f"{fraction:.0%} of claims are outside corpus coverage",
                payload={"out_of_scope_fraction": fraction},
            )
        else:
            # S4 verify surviving active claims through the frozen verify_claims contract.
            if not pilot:
                prior_results = _load_existing_results(review_dir)
                reused = []
                for claim in claims:
                    result = prior_results.get(_claim_reuse_key(claim))
                    if claim.status == "PENDING" and result is not None:
                        reused.append(result.model_copy(update={"claim_id": claim.claim_id}))
                if reused:
                    results.extend(reused)
                    _mark_verified(claims, reused, trace)
            pending = [claim for claim in claims if claim.status == "PENDING"]
            verified = verify_claims(review_id, pending, trace, config.REVIEW_WORKERS) if pending else []
            results.extend(verified)
            _mark_verified(claims, verified, trace)
            verified_ids = {result.claim_id for result in verified}
            for claim in pending:
                if claim.claim_id not in verified_ids:
                    claim.status = "ERROR"

        _write_json(review_dir / "claims.json", claims)

        # S5 report
        report = assemble_report(review_id, docmodel, claims, results)
        _write_json(review_dir / "report.json", report)

        # S6 annotate + HTML report
        annotated = _annotate(upload, report, review_dir, trace)
        html = render_report_html(report, docmodel)
        (review_dir / "report.html").write_text(html)

        completed_at = _now_iso()
        trace.emit("verdict", "review completed", payload={"status": status})
        _write_meta(
            review_id,
            status=status,
            pilot=pilot,
            completed_at=completed_at,
            error=None,
            annotated=annotated.name,
            summary=report.summary.model_dump(mode="json"),
        )
        return report
    except Exception as exc:
        completed_at = _now_iso()
        try:
            trace.emit("error", "review failed", detail=str(exc))
        except Exception:
            pass
        _write_meta(review_id, status="failed", pilot=pilot, completed_at=completed_at, error=str(exc))
        raise
    finally:
        llm.set_usage_sink(None)
        llm.clear_call_context()
        trace.close()


def _new_review_id(path: Path) -> str:
    ts_ms = int(time.time() * 1000)
    return f"review_{slugify(path.stem)}_{ts_ms}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a DiliAgent document review.")
    parser.add_argument("path", help="PDF, DOCX, or Markdown upload to review")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pilot", action="store_true", help="run the pilot claim subset (default)")
    mode.add_argument("--full", action="store_true", help="run the full claim set")
    args = parser.parse_args(argv)

    upload_path = Path(args.path)
    review_id = _new_review_id(upload_path)
    report = run_review(review_id, upload_path, pilot=not args.full)
    print(json.dumps({"review_id": review_id, "claims": report.summary.total_claims}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
