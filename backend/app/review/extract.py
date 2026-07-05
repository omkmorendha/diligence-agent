"""S2 — Claim extraction (spec section 7).

FROZEN CONTRACT — signature must not change.

One `llm.chat(json_mode=True)` pass over `DocModel.canonical_text` (chunked with
overlap if long, deduped across chunks) yields verifiable claims. Deterministic
post-processing anchors each `quote` in the DocModel (unanchorable claims dropped),
dedupes, sorts by (type priority, document order), and caps at
`MAX_CLAIMS_PER_REVIEW`; cap overflow is kept with `status="SKIPPED"`. When
`pilot` is True, only the first `PILOT_CLAIMS` by priority are returned active.

The frozen signature carries no `TraceWriter`, so an optional trailing `trace`
argument (default None) is accepted for `claim_extracted` emission (spec section
10) without breaking the `extract_claims(docmodel, pilot)` positional contract.
The count of dropped-unanchorable claims (needed for `review.json`, spec section
7) has no return channel either, so it is surfaced on the summary trace event.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .. import config, llm
from ..schemas import Claim, ClaimType, DocModel
from .parse import anchor_quote

# canonical_text longer than this is chunked; overlap keeps a sentence that
# straddles a boundary anchorable from at least one chunk (spec section 7).
CHUNK_CHAR_LIMIT = 24_000
CHUNK_OVERLAP = 2_000

# Extraction-time priority: numeric > factual > judgment (spec sections 1.3 / 7).
# Lower rank sorts first.
_TYPE_RANK: dict[ClaimType, int] = {"numeric": 0, "factual": 1, "judgment": 2}
_VALID_TYPES = set(_TYPE_RANK)

_SYSTEM_PROMPT = (
    "You are a meticulous financial-diligence analyst extracting the verifiable "
    "claims from a draft diligence document so each can be checked against a "
    "filing corpus. Respond with JSON only."
)

_INSTRUCTIONS = (
    "Extract every materially verifiable claim in the document text below. For each "
    "claim return a JSON object with these fields:\n"
    '  "quote": a VERBATIM span copied EXACTLY from the document text (do not '
    "paraphrase, reword, or fix typos) — this is anchored back to the source, so a "
    "non-verbatim quote is dropped;\n"
    '  "claim_type": one of "numeric" (asserts a specific figure/amount/percentage/'
    'ratio), "factual" (a checkable non-numeric statement of fact), or "judgment" '
    "(a pure opinion, characterization, or forward-looking/guidance statement that "
    "cannot be proven true or false against filings);\n"
    '  "company": the company the claim is about (e.g. "PepsiCo");\n'
    '  "period": the fiscal period if stated (e.g. "FY2022", "Q1 FY2023") else null;\n'
    '  "metric": the specific metric/subject in a few words (e.g. "restructuring '
    'costs") else null;\n'
    '  "question": a neutral verification question derived from the claim (e.g. '
    '"What were PepsiCo\'s restructuring costs in FY2022?").\n\n'
    'Respond with EXACTLY one JSON object: {"claims": [ {..}, {..} ]}. No prose, no '
    "code fence. Prefer numeric and factual claims; mark genuine opinions and "
    "forward-looking guidance as judgment. Do not invent claims not present in the text."
)


def _chunks(text: str) -> list[str]:
    """Split canonical_text into overlapping chunks when it exceeds the limit."""
    if len(text) <= CHUNK_CHAR_LIMIT:
        return [text]
    chunks: list[str] = []
    start = 0
    step = CHUNK_CHAR_LIMIT - CHUNK_OVERLAP
    while start < len(text):
        chunks.append(text[start : start + CHUNK_CHAR_LIMIT])
        if start + CHUNK_CHAR_LIMIT >= len(text):
            break
        start += step
    return chunks


def _response_text(response: Any) -> str:
    return response.choices[0].message.content or ""


def _parse_claims(text: str) -> list[dict[str, Any]]:
    """Parse the model response into a list of raw claim dicts. Tolerates a code
    fence and either a {"claims": [...]} object or a bare list."""
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    obj = json.loads(cleaned)
    if isinstance(obj, dict):
        claims = obj.get("claims", [])
    elif isinstance(obj, list):
        claims = obj
    else:
        raise ValueError("model output is not a JSON object or list")
    if not isinstance(claims, list):
        raise ValueError('"claims" is not a list')
    return [c for c in claims if isinstance(c, dict)]


def _extract_chunk(chunk_text: str) -> list[dict[str, Any]]:
    """One `llm.chat(json_mode=True)` pass over a chunk. Retries once on
    unparsable JSON (spec section 7), then gives up on that chunk."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"{_INSTRUCTIONS}\n\nDocument text:\n{chunk_text}"},
    ]
    text = ""
    for attempt in range(2):
        try:
            response = llm.chat(messages, json_mode=True)
            text = _response_text(response)
            return _parse_claims(text)
        except (json.JSONDecodeError, ValueError) as exc:
            if attempt == 0:
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That was not valid JSON ({exc}). Respond again with EXACTLY one "
                            'JSON object {"claims": [...]} matching the schema above, no prose, '
                            "no code fence."
                        ),
                    }
                )
                continue
            return []
    return []


def _coerce_type(value: Any) -> ClaimType:
    return value if value in _VALID_TYPES else "factual"


def _clean_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _derived_question(raw: dict[str, Any], company: str, period: Optional[str], metric: Optional[str]) -> str:
    """Prefer the model's question; otherwise synthesize one from the tags."""
    question = _clean_str(raw.get("question"))
    if question:
        return question
    subject = metric or "this figure"
    when = f" in {period}" if period else ""
    return f"What was {company}'s {subject}{when}?"


def _emit(trace: Any, type: str, title: str, detail: str = "", item_id: Optional[str] = None,
          payload: Optional[dict[str, Any]] = None) -> None:
    if trace is None:
        return
    trace.emit(type, title, detail=detail, item_id=item_id, payload=payload or {})


def extract_claims(docmodel: DocModel, pilot: bool, trace: Any = None) -> list[Claim]:
    """Extract, anchor, dedupe, prioritize and cap claims from a parsed document."""
    # --- S2: one LLM pass per chunk, collected across the whole document ---
    raw_claims: list[dict[str, Any]] = []
    for chunk in _chunks(docmodel.canonical_text):
        raw_claims.extend(_extract_chunk(chunk))

    # --- deterministic post-processing ---
    # 1. anchor every quote; drop and count the unanchorable (spec section 7).
    # 2. dedupe near-identical claims by anchored span (the same sentence proposed
    #    twice across overlapping chunks anchors to the identical canonical span),
    #    keeping the highest-priority type for that span.
    dropped_unanchorable = 0
    by_span: dict[tuple[int, int], tuple[Claim, int]] = {}
    for raw in raw_claims:
        quote = _clean_str(raw.get("quote"))
        if not quote:
            dropped_unanchorable += 1
            continue
        anchor = anchor_quote(docmodel, quote)
        if anchor is None:
            dropped_unanchorable += 1
            continue
        claim_type = _coerce_type(raw.get("claim_type"))
        rank = _TYPE_RANK[claim_type]
        company = _clean_str(raw.get("company")) or ""
        period = _clean_str(raw.get("period"))
        metric = _clean_str(raw.get("metric"))
        claim = Claim(
            claim_id="",  # assigned deterministically after sort
            quote=anchor.quote,  # the true canonical span, not the model's copy
            claim_type=claim_type,
            company=company,
            period=period,
            metric=metric,
            question=_derived_question(raw, company or "the company", period, metric),
            priority=rank + 1,
            status="PENDING",
            anchor=anchor,
        )
        key = (anchor.char_start, anchor.char_end)
        existing = by_span.get(key)
        if existing is None or rank < existing[1]:
            by_span[key] = (claim, rank)

    deduped = [claim for claim, _ in by_span.values()]
    n_deduped = len(raw_claims) - dropped_unanchorable - len(deduped)

    # 3. sort by (type priority, document order).
    ordered = sorted(
        deduped,
        key=lambda c: (_TYPE_RANK[c.claim_type], c.anchor.char_start if c.anchor else 0),
    )

    # 4. assign deterministic ids and stamp status: active up to the pilot/cap
    #    limit, everything else SKIPPED (cap overflow is kept, spec section 7).
    active_limit = config.PILOT_CLAIMS if pilot else config.MAX_CLAIMS_PER_REVIEW
    claims: list[Claim] = []
    active = 0
    for i, claim in enumerate(ordered, start=1):
        claim.claim_id = f"c{i:02d}"
        claim.status = "PENDING" if i <= active_limit else "SKIPPED"
        if claim.status == "PENDING":
            active += 1
        claims.append(claim)
        _emit(
            trace, "claim_extracted",
            f"{claim.claim_id}: {claim.claim_type} claim",
            detail=claim.quote,
            item_id=claim.claim_id,
            payload={
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "company": claim.company,
                "period": claim.period,
                "metric": claim.metric,
                "question": claim.question,
                "status": claim.status,
                "priority": claim.priority,
            },
        )

    _emit(
        trace, "claim_extracted", "claim extraction complete",
        detail=f"{active} active of {len(claims)} claims",
        payload={
            "extracted": len(raw_claims),
            "dropped_unanchorable": dropped_unanchorable,
            "deduped": n_deduped,
            "total_claims": len(claims),
            "active_claims": active,
            "skipped_claims": len(claims) - active,
            "pilot": pilot,
        },
    )
    return claims
