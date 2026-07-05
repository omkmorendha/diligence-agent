from __future__ import annotations

import importlib
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api, config
from app.schemas import (
    Claim,
    ClaimAnchor,
    DocModel,
    ReviewReport,
    ReviewReportClaim,
    ReviewSummary,
    TraceEvent,
    VerificationResult,
)


def _now() -> str:
    return "2026-07-05T00:00:00+00:00"


@pytest.fixture(autouse=True)
def isolated_reviews(tmp_path, monkeypatch):
    reviews_dir = tmp_path / "runs" / "reviews"
    monkeypatch.setattr(config, "REVIEWS_DIR", reviews_dir)
    monkeypatch.setattr(api.config, "REVIEWS_DIR", reviews_dir)
    with api._REVIEWS_LOCK:
        api._REVIEWS.clear()
    yield reviews_dir
    with api._REVIEWS_LOCK:
        api._REVIEWS.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


def _claim(status: str = "VERIFIED") -> Claim:
    return Claim(
        claim_id="c01",
        quote="PepsiCo revenue increased in FY2022.",
        claim_type="factual",
        company="PepsiCo",
        period="FY2022",
        question="Did PepsiCo revenue increase in FY2022?",
        status=status,
        anchor=ClaimAnchor(
            quote="PepsiCo revenue increased in FY2022.",
            char_start=0,
            char_end=36,
            line_start=1,
        ),
    )


def _supported_result() -> VerificationResult:
    return VerificationResult(
        claim_id="c01",
        verdict="SUPPORTED",
        explanation="Supported by the corpus.",
        confidence="high",
    )


def _write_fake_review(review_id: str, upload_path: str | Path, pilot: bool) -> ReviewReport:
    review_dir = config.REVIEWS_DIR / review_id
    review_dir.mkdir(parents=True, exist_ok=True)
    claim = _claim()
    report = ReviewReport(
        review_id=review_id,
        filename=Path(upload_path).name,
        format="md",
        company_scope=["PepsiCo"],
        summary=ReviewSummary(total_claims=1, supported=1),
        claims=[ReviewReportClaim(claim=claim, result=_supported_result())],
    )
    (review_dir / "report.json").write_text(json.dumps(report.model_dump(mode="json"), indent=2))
    (review_dir / "report.html").write_text("<html>DiliAgent report</html>")
    (review_dir / "annotated.md").write_text("annotated")
    event = TraceEvent(run_id=review_id, seq=1, ts=_now(), type="verdict", title="review completed")
    (review_dir / "trace.jsonl").write_text(event.model_dump_json() + "\n")
    meta_path = review_dir / "review.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"review_id": review_id}
    meta.update(
        {
            "status": "completed",
            "pilot": pilot,
            "completed_at": _now(),
            "summary": report.summary.model_dump(mode="json"),
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2))
    return report


def _wait_status(client: TestClient, review_id: str, status: str = "completed") -> dict:
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/reviews/{review_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"review {review_id} did not reach {status}; last={last}")


def test_create_review_and_fetch_artifacts(client, monkeypatch, isolated_reviews) -> None:
    calls = []

    def fake_run_review(review_id: str, upload_path: str | Path, pilot: bool) -> ReviewReport:
        calls.append((review_id, Path(upload_path), pilot))
        return _write_fake_review(review_id, upload_path, pilot)

    monkeypatch.setattr(api.review_runner, "run_review", fake_run_review)

    response = client.post(
        "/reviews",
        files={"file": ("memo.md", b"PepsiCo revenue increased in FY2022.\n", "text/markdown")},
    )

    assert response.status_code == 200
    review_id = response.json()["review_id"]
    status = _wait_status(client, review_id)
    assert status["summary"]["supported"] == 1
    assert calls and calls[0][2] is True
    assert calls[0][1] == isolated_reviews / review_id / "upload.md"
    assert not str(calls[0][1]).startswith(str(config.INDEX_DIR))

    listed = client.get("/reviews").json()
    assert any(row["review_id"] == review_id for row in listed)

    report = client.get(f"/reviews/{review_id}/report")
    assert report.status_code == 200
    assert report.json()["summary"]["supported"] == 1

    html = client.get(f"/reviews/{review_id}/report?format=html")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]

    annotated = client.get(f"/reviews/{review_id}/annotated")
    assert annotated.status_code == 200
    assert annotated.headers["content-type"].startswith("text/markdown")

    events = client.get(f"/reviews/{review_id}/events")
    assert events.status_code == 200
    assert "review completed" in events.text


def test_upload_validation_rejects_bad_magic_and_size(client, monkeypatch) -> None:
    response = client.post(
        "/reviews",
        files={"file": ("memo.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400
    assert "magic" in response.json()["detail"]

    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    monkeypatch.setattr(api.config, "MAX_UPLOAD_MB", 0)
    too_large = client.post(
        "/reviews",
        files={"file": ("memo.md", b"x", "text/markdown")},
    )
    assert too_large.status_code == 413


def test_one_review_at_a_time_returns_409(client, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocking_run_review(review_id: str, upload_path: str | Path, pilot: bool) -> ReviewReport:
        started.set()
        assert release.wait(5)
        return _write_fake_review(review_id, upload_path, pilot)

    monkeypatch.setattr(api.review_runner, "run_review", blocking_run_review)

    first = client.post(
        "/reviews",
        files={"file": ("first.md", b"PepsiCo revenue increased.\n", "text/markdown")},
    )
    assert first.status_code == 200
    assert started.wait(5)

    second = client.post(
        "/reviews",
        files={"file": ("second.md", b"PepsiCo revenue increased.\n", "text/markdown")},
    )
    assert second.status_code == 409

    release.set()
    _wait_status(client, first.json()["review_id"])


def test_promote_pilot_to_full(client, monkeypatch, isolated_reviews) -> None:
    review_id = "review_existing_1"
    review_dir = isolated_reviews / review_id
    review_dir.mkdir(parents=True)
    (review_dir / "upload.md").write_text("PepsiCo revenue increased.\n")
    (review_dir / "review.json").write_text(
        json.dumps(
            {
                "review_id": review_id,
                "filename": "memo.md",
                "format": "md",
                "status": "completed",
                "pilot": True,
                "created_at": _now(),
            }
        )
    )
    pilots = []

    def fake_run_review(review_id: str, upload_path: str | Path, pilot: bool) -> ReviewReport:
        pilots.append(pilot)
        return _write_fake_review(review_id, upload_path, pilot)

    monkeypatch.setattr(api.review_runner, "run_review", fake_run_review)
    response = client.post(f"/reviews/{review_id}/full")
    assert response.status_code == 200
    status = _wait_status(client, review_id)
    assert pilots == [False]
    assert status["pilot"] is False


def test_run_review_chains_mocked_stages(tmp_path, monkeypatch) -> None:
    rr = importlib.import_module("app.review.run_review")
    monkeypatch.setattr(rr.config, "REVIEWS_DIR", tmp_path / "reviews")
    upload = tmp_path / "memo.md"
    upload.write_text("PepsiCo revenue increased in FY2022.\n")
    docmodel = DocModel(
        doc_id="upload",
        format="md",
        filename="upload.md",
        canonical_text="PepsiCo revenue increased in FY2022.",
        blocks=[],
    )
    claim = _claim(status="PENDING")
    result = _supported_result()
    calls = []

    monkeypatch.setattr(rr, "parse_document", lambda path: calls.append("parse") or docmodel)
    monkeypatch.setattr(
        rr,
        "extract_claims",
        lambda model, pilot, trace=None: calls.append(("extract", pilot)) or [claim],
    )
    monkeypatch.setattr(rr, "scope_check", lambda claims: calls.append("scope") or claims)
    monkeypatch.setattr(rr, "corpus_registry", lambda: {})
    monkeypatch.setattr(rr, "scope_verdict", lambda claim, reg=None: (None, ""))
    monkeypatch.setattr(rr, "out_of_scope_fraction", lambda claims: 0.0)

    def fake_verify(review_id: str, claims: list[Claim], trace, workers: int):
        calls.append(("verify", [c.claim_id for c in claims], workers))
        assert claims == [claim]
        return [result]

    monkeypatch.setattr(rr, "verify_claims", fake_verify)
    monkeypatch.setitem(
        rr._ANNOTATORS,
        "md",
        lambda src, report, out: calls.append("annotate") or Path(out).write_text("annotated"),
    )
    monkeypatch.setattr(rr, "render_report_html", lambda report, model: calls.append("html") or "<html>ok</html>")

    report = rr.run_review("review_test", upload, pilot=True)

    review_dir = tmp_path / "reviews" / "review_test"
    assert report.summary.supported == 1
    assert (review_dir / "upload.md").exists()
    assert (review_dir / "docmodel.json").exists()
    assert (review_dir / "claims.json").exists()
    assert (review_dir / "report.json").exists()
    assert (review_dir / "annotated.md").read_text() == "annotated"
    assert (review_dir / "report.html").read_text() == "<html>ok</html>"
    assert (review_dir / "trace.jsonl").exists()
    assert (review_dir / "llm_calls.jsonl").exists()
    assert json.loads((review_dir / "review.json").read_text())["status"] == "completed"
    assert calls == ["parse", ("extract", True), "scope", ("verify", ["c01"], rr.config.REVIEW_WORKERS), "annotate", "html"]


def test_full_review_does_not_reuse_stale_pilot_result_for_changed_claim(tmp_path, monkeypatch) -> None:
    rr = importlib.import_module("app.review.run_review")
    monkeypatch.setattr(rr.config, "REVIEWS_DIR", tmp_path / "reviews")
    review_dir = tmp_path / "reviews" / "review_test"
    review_dir.mkdir(parents=True)
    upload = tmp_path / "memo.md"
    upload.write_text("PepsiCo revenue increased in FY2022.\n")
    old_claim = _claim(status="VERIFIED")
    old_claim.quote = "PepsiCo reported $600 million in restructuring costs."
    old_report = ReviewReport(
        review_id="review_test",
        filename="upload.md",
        format="md",
        company_scope=["PepsiCo"],
        summary=ReviewSummary(total_claims=1, supported=1),
        claims=[ReviewReportClaim(claim=old_claim, result=_supported_result())],
    )
    (review_dir / "report.json").write_text(json.dumps(old_report.model_dump(mode="json"), indent=2))

    docmodel = DocModel(
        doc_id="upload",
        format="md",
        filename="upload.md",
        canonical_text="PepsiCo revenue increased in FY2022.",
        blocks=[],
    )
    new_claim = _claim(status="PENDING")
    new_claim.quote = "PepsiCo revenue increased in FY2022."
    verified_result = VerificationResult(
        claim_id="c01",
        verdict="CONTRADICTED",
        explanation="Fresh full verification ran.",
        confidence="high",
    )
    calls = []

    monkeypatch.setattr(rr, "parse_document", lambda path: docmodel)
    monkeypatch.setattr(rr, "extract_claims", lambda model, pilot, trace=None: [new_claim])
    monkeypatch.setattr(rr, "scope_check", lambda claims: claims)
    monkeypatch.setattr(rr, "corpus_registry", lambda: {})
    monkeypatch.setattr(rr, "scope_verdict", lambda claim, reg=None: (None, ""))
    monkeypatch.setattr(rr, "out_of_scope_fraction", lambda claims: 0.0)

    def fake_verify(review_id: str, claims: list[Claim], trace, workers: int):
        calls.append(("verify", [claim.quote for claim in claims]))
        return [verified_result]

    monkeypatch.setattr(rr, "verify_claims", fake_verify)
    monkeypatch.setitem(rr._ANNOTATORS, "md", lambda src, report, out: Path(out).write_text("annotated"))
    monkeypatch.setattr(rr, "render_report_html", lambda report, model: "<html>ok</html>")

    report = rr.run_review("review_test", upload, pilot=False)

    assert calls == [("verify", ["PepsiCo revenue increased in FY2022."])]
    assert report.claims[0].result.verdict == "CONTRADICTED"


def test_run_review_fail_fast_out_of_scope(tmp_path, monkeypatch) -> None:
    rr = importlib.import_module("app.review.run_review")
    monkeypatch.setattr(rr.config, "REVIEWS_DIR", tmp_path / "reviews")
    upload = tmp_path / "memo.md"
    upload.write_text("Nvidia revenue increased in FY2022.\n")
    docmodel = DocModel(
        doc_id="upload",
        format="md",
        filename="upload.md",
        canonical_text="Nvidia revenue increased in FY2022.",
        blocks=[],
    )
    claim = _claim(status="PENDING")
    claim.company = "Nvidia"

    monkeypatch.setattr(rr, "parse_document", lambda path: docmodel)
    monkeypatch.setattr(rr, "extract_claims", lambda model, pilot, trace=None: [claim])
    monkeypatch.setattr(rr, "scope_check", lambda claims: claims)
    monkeypatch.setattr(rr, "corpus_registry", lambda: {})
    monkeypatch.setattr(rr, "scope_verdict", lambda claim, reg=None: ("OUT_OF_SCOPE", "Nvidia is not covered."))
    monkeypatch.setattr(rr, "out_of_scope_fraction", lambda claims: 1.0)
    monkeypatch.setattr(rr, "verify_claims", lambda *args, **kwargs: pytest.fail("verify should not run"))
    monkeypatch.setitem(
        rr._ANNOTATORS,
        "md",
        lambda src, report, out: Path(out).write_text("annotated"),
    )
    monkeypatch.setattr(rr, "render_report_html", lambda report, model: "<html>out of scope</html>")

    report = rr.run_review("review_oos", upload, pilot=True)

    review_dir = tmp_path / "reviews" / "review_oos"
    assert report.summary.out_of_scope == 1
    assert report.claims[0].result.verdict == "OUT_OF_SCOPE"
    assert json.loads((review_dir / "review.json").read_text())["status"] == "out_of_scope"
