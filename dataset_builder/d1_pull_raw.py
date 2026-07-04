"""D1 — Pull raw FinanceBench benchmark data.

Downloads the FinanceBench open-source subset (questions + gold answers +
gold evidence + justifications) and the per-document metadata, joins them,
and writes an enriched raw record per question. Optionally downloads the
filing PDFs referenced by each question.

Source: https://github.com/patronus-ai/financebench  (open-source subset, 150 Qs)

Outputs (per spec section 6, D1):
    data/raw/financebench.jsonl      one enriched record per usable question
    data/filings/{doc_name}.pdf      filing PDFs (unless --no-pdfs)
    data/raw/download_report.json    what was pulled, what was dropped and why

Design rules from the spec:
    * If a PDF link is dead, DROP the question and LOG it. Never silently skip.
    * Preserve document identity, page numbers (0-indexed in source), page text.

Usage:
    uv run dataset_builder/d1_pull_raw.py            # metadata + PDFs
    uv run dataset_builder/d1_pull_raw.py --no-pdfs  # metadata + availability check only
    uv run dataset_builder/d1_pull_raw.py --limit 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/patronus-ai/financebench/main"
OPEN_SOURCE_URL = f"{REPO_RAW}/data/financebench_open_source.jsonl"
DOC_INFO_URL = f"{REPO_RAW}/data/financebench_document_information.jsonl"
PDF_URL_TMPL = f"{REPO_RAW}/pdfs/{{doc_name}}.pdf"

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
FILINGS_DIR = ROOT / "data" / "filings"


def _fetch(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "diligence-agent/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:  # noqa: PERF203
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def _fetch_jsonl(url: str) -> list[dict]:
    text = _fetch(url).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _pdf_available(doc_name: str) -> tuple[bool, int, str]:
    """HEAD-ish check: fetch first bytes and confirm it is a real PDF."""
    url = PDF_URL_TMPL.format(doc_name=doc_name)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "diligence-agent/0.1", "Range": "bytes=0-1023"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            head = resp.read()
            size = int(resp.headers.get("Content-Range", "0-0/0").split("/")[-1] or 0)
            return head.startswith(b"%PDF"), size, url
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, 0, f"{url}  ({exc})"


def _download_pdf(doc_name: str) -> tuple[bool, int, str]:
    url = PDF_URL_TMPL.format(doc_name=doc_name)
    dest = FILINGS_DIR / f"{doc_name}.pdf"
    if dest.exists() and dest.stat().st_size > 0:
        return True, dest.stat().st_size, url
    try:
        data = _fetch(url)
        if not data.startswith(b"%PDF"):
            return False, 0, f"{url}  (not a PDF)"
        dest.write_bytes(data)
        return True, len(data), url
    except RuntimeError as exc:
        return False, 0, f"{url}  ({exc})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull raw FinanceBench data (D1).")
    ap.add_argument("--no-pdfs", action="store_true", help="Skip PDF download; only check availability.")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of questions (debug).")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    FILINGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[d1] fetching questions: {OPEN_SOURCE_URL}", file=sys.stderr)
    questions = _fetch_jsonl(OPEN_SOURCE_URL)
    print(f"[d1] fetching doc metadata: {DOC_INFO_URL}", file=sys.stderr)
    doc_info = _fetch_jsonl(DOC_INFO_URL)
    doc_meta = {d["doc_name"]: d for d in doc_info}

    if args.limit:
        questions = questions[: args.limit]

    # unique docs referenced by the questions we care about
    referenced_docs = sorted({q["doc_name"] for q in questions})

    # resolve PDF availability / download, per doc (dedup network work)
    doc_status: dict[str, dict] = {}
    for i, doc_name in enumerate(referenced_docs, 1):
        if args.no_pdfs:
            ok, size, url = _pdf_available(doc_name)
            action = "checked"
        else:
            ok, size, url = _download_pdf(doc_name)
            action = "downloaded"
        doc_status[doc_name] = {
            "doc_name": doc_name,
            "available": ok,
            "bytes": size,
            "url": url,
            "action": action,
            "meta_present": doc_name in doc_meta,
        }
        print(f"[d1] ({i}/{len(referenced_docs)}) {doc_name}: {'OK' if ok else 'MISSING'} "
              f"{size:,}B", file=sys.stderr)

    # write enriched records, dropping questions whose PDF is unavailable
    kept, dropped = [], []
    for q in questions:
        doc_name = q["doc_name"]
        meta = doc_meta.get(doc_name, {})
        status = doc_status[doc_name]
        record = {
            "question_id": q["financebench_id"],
            "company": q["company"],
            "doc_name": doc_name,
            "doc_type": meta.get("doc_type"),
            "doc_period": meta.get("doc_period"),
            "doc_link": meta.get("doc_link"),
            "gics_sector": meta.get("gics_sector"),
            "question_type": q.get("question_type"),
            "question_reasoning": q.get("question_reasoning"),
            "question": q["question"],
            "gold_answer": q["answer"],
            "justification": q.get("justification"),
            "evidence": q.get("evidence", []),
            "pdf_path": f"data/filings/{doc_name}.pdf" if status["available"] else None,
        }
        if status["available"]:
            kept.append(record)
        else:
            dropped.append({"question_id": q["financebench_id"], "doc_name": doc_name,
                            "reason": "pdf_unavailable", "url": status["url"]})

    out_path = RAW_DIR / "financebench.jsonl"
    with out_path.open("w") as f:
        for rec in kept:
            f.write(json.dumps(rec) + "\n")

    report = {
        "source": {"questions": OPEN_SOURCE_URL, "doc_info": DOC_INFO_URL},
        "questions_total": len(questions),
        "questions_kept": len(kept),
        "questions_dropped": len(dropped),
        "docs_referenced": len(referenced_docs),
        "docs_available": sum(1 for s in doc_status.values() if s["available"]),
        "docs_missing": sum(1 for s in doc_status.values() if not s["available"]),
        "pdfs_downloaded": not args.no_pdfs,
        "dropped": dropped,
        "doc_status": list(doc_status.values()),
    }
    (RAW_DIR / "download_report.json").write_text(json.dumps(report, indent=2))

    print(f"\n[d1] kept {len(kept)}/{len(questions)} questions across "
          f"{report['docs_available']}/{len(referenced_docs)} docs.", file=sys.stderr)
    print(f"[d1] wrote {out_path}", file=sys.stderr)
    print(f"[d1] wrote {RAW_DIR / 'download_report.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
