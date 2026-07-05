"""S3 — Corpus scope pre-check (spec sections 1.4, 8).

FROZEN CONTRACT — signatures must not change.

`corpus_registry()` builds `{company: {doc_ids, periods}}` once from
`data/index/*/meta.json` + `data/subset.json`. `scope_check` tags each claim
against that registry (with a small company-alias table) and stamps an immediate
`OUT_OF_SCOPE` verdict (via claim status/downstream result) for claims whose
company or period the corpus does not cover — no agent run for those.

Note on the frozen `Claim` schema: `Claim` carries no verdict field, so
`scope_check` records its decision by flipping `claim.status` to `"SKIPPED"` for
out-of-scope / unverifiable claims (they demonstrably skip the agent run). The
precise verdict + human-readable explanation is recoverable per claim via
`scope_verdict()`, which the report assembler uses to synthesise the
`OUT_OF_SCOPE` / `UNVERIFIABLE` `VerificationResult`.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Optional

from .. import config
from ..schemas import Claim, Verdict

# Explicit company-alias table for the 11 corpus companies (spec section 8:
# "small explicit alias table, extendable"). Keys are the canonical display
# names as they appear in data/subset.json; values are common variants. Matching
# is normalisation-tolerant on top of this table (corporate suffixes, "The",
# "&" vs "and", punctuation, case), so only genuinely distinct aliases are listed.
_ALIASES: dict[str, list[str]] = {
    "AMD": ["Advanced Micro Devices", "Advanced Micro Devices, Inc."],
    "Adobe": ["Adobe Inc.", "Adobe Systems", "Adobe Systems Incorporated"],
    "Best Buy": ["Best Buy Co.", "Best Buy Co., Inc."],
    "Boeing": ["The Boeing Company", "Boeing Co."],
    "General Mills": ["General Mills, Inc."],
    "Johnson & Johnson": ["J&J", "Johnson and Johnson", "JNJ"],
    "MGM Resorts": ["MGM Resorts International", "MGM", "MGM Resorts, Inc."],
    "Nike": ["Nike, Inc.", "NIKE, Inc."],
    "PepsiCo": ["PepsiCo, Inc.", "Pepsi", "Pepsi Co"],
    "Pfizer": ["Pfizer Inc.", "Pfizer, Inc."],
    "Verizon": ["Verizon Communications", "Verizon Communications Inc.", "Verizon Wireless"],
}

# Corporate suffixes stripped when normalising a company name to a match key.
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "plc", "ltd", "limited", "lp", "llc", "sa", "ag", "nv", "holdings",
}

_QUARTER_RE = re.compile(r"(\d{4})\s*Q\s*([1-4])", re.IGNORECASE)
_QUARTER_RE_ALT = re.compile(r"Q\s*([1-4])\D{0,8}(\d{4})", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


# --- company normalisation --------------------------------------------------
def _norm_key(name: str) -> str:
    """Lowercased, punctuation-folded, leading-'the'-dropped match key."""
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    toks = s.split()
    if toks and toks[0] == "the":
        toks = toks[1:]
    return " ".join(toks)


def _strip_suffix(key: str) -> str:
    toks = key.split()
    while toks and toks[-1] in _SUFFIXES:
        toks = toks[:-1]
    return " ".join(toks)


def _slugify(name: str) -> str:
    """Filesystem-safe company slug — mirrors ingest.slugify without importing
    ingest (which pulls in numpy / sentence-transformers)."""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower())
    return s.strip("_") or "unknown"


def _canonical_lookup(canonicals: set[str]) -> dict[str, str]:
    """Build {normalised_key: canonical} over canonicals + their aliases."""
    lookup: dict[str, str] = {}

    def add(key: str, canonical: str) -> None:
        for k in (key, _strip_suffix(key)):
            if k and k not in lookup:
                lookup[k] = canonical

    for canonical in canonicals:
        add(_norm_key(canonical), canonical)
        for alias in _ALIASES.get(canonical, []):
            add(_norm_key(alias), canonical)
    return lookup


def normalize_company(name: str, registry: Optional[dict] = None) -> Optional[str]:
    """Resolve a raw company mention to its canonical corpus name, or None.

    Canonicals default to the 11 alias-table keys; when a `registry` is given its
    keys are used (so the resolver tracks whatever the corpus actually holds)."""
    canonicals = set(registry) if registry else set(_ALIASES)
    lookup = _canonical_lookup(canonicals)
    key = _norm_key(name)
    if key in lookup:
        return lookup[key]
    stripped = _strip_suffix(key)
    if stripped in lookup:
        return lookup[stripped]
    return None


# --- registry construction --------------------------------------------------
def _period_token_year(period: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """Normalise a period string to (token, year). Handles 'FY2022', '2023Q1',
    'Q1 FY2023', '2023-05-30', 'May 26, 2023'. Token is 'YYYYQn' or 'FYYYYY'."""
    if not period:
        return (None, None)
    s = str(period)
    m = _QUARTER_RE.search(s)
    if m:
        return (f"{m.group(1)}Q{m.group(2)}", int(m.group(1)))
    m = _QUARTER_RE_ALT.search(s)
    if m:
        return (f"{m.group(2)}Q{m.group(1)}", int(m.group(2)))
    m = _YEAR_RE.search(s)
    if m:
        year = int(m.group(0))
        return (f"FY{year}", year)
    token = s.strip().upper()
    return (token or None, None)


def _period_from_doc_id(doc_id: str) -> str:
    """Derive a filing period from a doc_id when subset gold evidence is absent
    (index-only docs). Mirrors ingest._doc_type_and_period's fallback."""
    m = _QUARTER_RE.search(f"_{doc_id}_")
    if m:
        return f"{m.group(1)}Q{m.group(2)}".upper()
    m = re.search(r"_(\d{4})", doc_id)
    return f"FY{m.group(1)}" if m else ""


def _build_registry(subset_path, index_dir) -> dict:
    reg: dict[str, dict[str, set]] = {}

    def bucket(canonical: str) -> dict[str, set]:
        return reg.setdefault(canonical, {"doc_ids": set(), "periods": set()})

    # 1. data/subset.json — primary source, and the sole source in a fresh
    #    checkout where data/index/ has not been built yet.
    try:
        subset_items = json.loads(subset_path.read_text())
    except (OSError, json.JSONDecodeError):
        subset_items = []
    for item in subset_items:
        company = item.get("company")
        if not company:
            continue
        b = bucket(company)
        for ev in item.get("gold_evidence") or []:
            if ev.get("doc_id"):
                b["doc_ids"].add(ev["doc_id"])
            if ev.get("filing_period"):
                b["periods"].add(ev["filing_period"])

    # 2. data/index/*/meta.json — augment doc_ids/periods when the index exists.
    #    meta.json stores the company slug and doc_ids only, so periods are
    #    derived from each doc_id.
    slug_to_canonical = {_slugify(c): c for c in reg}
    if index_dir and index_dir.is_dir():
        for meta_path in sorted(index_dir.glob("*/meta.json")):
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            slug = meta.get("company") or meta_path.parent.name
            canonical = slug_to_canonical.get(slug)
            if canonical is None:
                # Index holds a company not in the subset: resolve via aliases,
                # else fall back to the slug directory name.
                canonical = normalize_company(slug.replace("_", " ")) or slug
                slug_to_canonical[slug] = canonical
            b = bucket(canonical)
            for doc_id in meta.get("doc_ids") or []:
                b["doc_ids"].add(doc_id)
                period = _period_from_doc_id(doc_id)
                if period:
                    b["periods"].add(period)

    return {
        company: {"doc_ids": sorted(v["doc_ids"]), "periods": sorted(v["periods"])}
        for company, v in sorted(reg.items())
    }


def corpus_registry() -> dict:
    """Return {company: {"doc_ids": [...], "periods": [...]}} for the RAG corpus.

    Built fresh from `config.SUBSET_PATH` (+ `config.INDEX_DIR` when present) on
    each call — cheap at corpus scale, and keeps unit tests that monkeypatch the
    config paths deterministic. Callers that verify many claims should hold the
    result and pass it into `scope_check` / `scope_verdict`."""
    return _build_registry(config.SUBSET_PATH, config.INDEX_DIR)


# --- scope checking ---------------------------------------------------------
def _period_covered(claim_period: Optional[str], periods: list[str]) -> bool:
    """Whether a claim period falls within a company's corpus coverage.

    Unparseable or missing periods are allowed through because extraction may not
    always tag a period. Once a year/quarter is parsed, the pre-check is strict:
    only explicitly covered filing periods or the same filing year are in scope.
    It must not infer continuous coverage between sparse FinanceBench years."""
    claim_token, claim_year = _period_token_year(claim_period)
    if claim_year is None:
        return True

    covered_tokens: set[str] = set()
    covered_years: set[int] = set()
    for p in periods:
        token, year = _period_token_year(p)
        if token:
            covered_tokens.add(token)
        if year is not None:
            covered_years.add(year)

    if claim_token and claim_token in covered_tokens:
        return True
    if claim_year in covered_years:
        return True
    return False


def scope_verdict(claim: Claim, registry: Optional[dict] = None) -> tuple[Optional[Verdict], str]:
    """Classify one claim against the corpus registry.

    Returns `(verdict, explanation)` where verdict is `OUT_OF_SCOPE` (unknown
    company, or period the corpus does not cover — covered periods named in the
    explanation), `UNVERIFIABLE` (pure-opinion judgment claim flagged by
    extraction), or `None` for a claim that should proceed to the verification
    agent. Authoritative per-claim decision; `scope_check` and the report
    assembler build on it."""
    reg = registry if registry is not None else corpus_registry()
    canonical = normalize_company(claim.company, reg)
    if canonical is None or canonical not in reg:
        covered = ", ".join(sorted(reg)) or "(none)"
        return (
            "OUT_OF_SCOPE",
            f"Company '{claim.company}' is not in the corpus. "
            f"Covered companies: {covered}.",
        )

    # Judgment/opinion claims are not factual statements the corpus can confirm
    # or contradict (spec §1.6, §8). Extraction tags these as claim_type
    # 'judgment'; there is no separate opinion boolean on the frozen Claim schema.
    # Smaller models can mislabel historical numeric checks (for example a past
    # guidance increase) as judgment, so let concrete numeric checks proceed.
    if claim.claim_type == "judgment" and not _has_concrete_numeric_check(claim):
        return (
            "UNVERIFIABLE",
            "Judgment or forward-looking opinion; not a factual claim the corpus "
            "can verify.",
        )

    periods = reg[canonical]["periods"]
    if not _period_covered(claim.period, periods):
        covered = ", ".join(periods) or "(none)"
        return (
            "OUT_OF_SCOPE",
            f"Period '{claim.period}' is outside corpus coverage for {canonical}. "
            f"Covered periods: {covered}.",
        )

    return (None, "")


def _has_concrete_numeric_check(claim: Claim) -> bool:
    text = f"{claim.quote or ''} {claim.question or ''}".lower()
    return bool(re.search(r"\d", text)) and any(
        marker in text
        for marker in (
            "$",
            "%",
            "percent",
            "percentage",
            "point",
            "points",
            "million",
            "billion",
            "increase",
            "decrease",
            "raised",
            "lowered",
            "repurchased",
            "amounted",
        )
    )


def scope_check(claims: list[Claim]) -> list[Claim]:
    """Tag claims in/out of corpus scope; out-of-scope claims skip the agent run.

    Mutates and returns the same claims: aliases are rewritten to canonical
    corpus company names before verification, and any claim resolving to
    `OUT_OF_SCOPE` or `UNVERIFIABLE` has `status` set to `"SKIPPED"` so the
    verification fan-out passes over it (the frozen Claim schema carries no
    verdict field; recover the precise verdict + explanation with
    `scope_verdict`)."""
    reg = corpus_registry()
    for claim in claims:
        canonical = normalize_company(claim.company, reg)
        if canonical is not None:
            claim.company = canonical
        verdict, _ = scope_verdict(claim, reg)
        if verdict is not None:
            claim.status = "SKIPPED"
    return claims


def out_of_scope_fraction(claims: list[Claim]) -> float:
    """Fraction of claims whose company/period the corpus does not cover.

    Counts `OUT_OF_SCOPE` only (not `UNVERIFIABLE`): the >80% fail-fast in
    spec §1.4/§8 is about corpus *coverage* — an all-opinion memo is a different
    condition. Returns 0.0 for an empty list."""
    if not claims:
        return 0.0
    reg = corpus_registry()
    out = sum(1 for c in claims if scope_verdict(c, reg)[0] == "OUT_OF_SCOPE")
    return out / len(claims)
