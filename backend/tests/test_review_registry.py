"""Corpus registry + scope pre-check (spec sections 1.4, 8).

These guard: the registry builds `{company: {doc_ids, periods}}` from
`data/subset.json` (the sole source in a fresh checkout where `data/index/` is
absent) and augments it from `data/index/*/meta.json` when present; the alias
table resolves every one of the 11 companies' common variants; and `scope_check`
tags unknown-company / uncovered-period claims OUT_OF_SCOPE and pure-opinion
judgment claims UNVERIFIABLE while leaving in-scope claims to reach the agent.
"""

from __future__ import annotations

import json

import pytest

from app import config
from app.review.registry import (
    corpus_registry,
    normalize_company,
    out_of_scope_fraction,
    scope_check,
    scope_verdict,
)
from app.schemas import Claim

# The 11 canonical corpus companies (data/subset.json).
CORPUS_COMPANIES = {
    "AMD", "Adobe", "Best Buy", "Boeing", "General Mills", "Johnson & Johnson",
    "MGM Resorts", "Nike", "PepsiCo", "Pfizer", "Verizon",
}


def _claim(company: str, *, claim_type: str = "numeric", period=None, status="PENDING") -> Claim:
    return Claim(
        claim_id="c01",
        quote="q",
        claim_type=claim_type,
        company=company,
        period=period,
        question="?",
        status=status,
    )


# --- fixtures ---------------------------------------------------------------
@pytest.fixture
def subset_only(tmp_path, monkeypatch):
    """A minimal subset.json fixture + a guaranteed-absent index dir, so the
    registry must fall back to subset.json alone (spec section 8)."""
    subset = [
        {
            "company": "PepsiCo",
            "gold_evidence": [
                {"doc_id": "PEPSICO_2022_10K", "filing_period": "FY2022"},
                {"doc_id": "PEPSICO_2023_8K_dated-2023-05-30", "filing_period": "2023-05-30"},
                {"doc_id": "PEPSICO_2023Q1_EARNINGS", "filing_period": "2023Q1"},
            ],
        },
        {
            "company": "Boeing",
            "gold_evidence": [
                {"doc_id": "BOEING_2018_10K", "filing_period": "FY2018"},
                {"doc_id": "BOEING_2022_10K", "filing_period": "FY2022"},
            ],
        },
        {"company": "AMD", "gold_evidence": [{"doc_id": "AMD_2015_10K", "filing_period": "FY2015"}]},
    ]
    subset_path = tmp_path / "subset.json"
    subset_path.write_text(json.dumps(subset))
    monkeypatch.setattr(config, "SUBSET_PATH", subset_path)
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "does_not_exist_index")
    return subset_path


# --- registry construction --------------------------------------------------
def test_registry_from_real_subset_has_11_companies() -> None:
    reg = corpus_registry()
    assert set(reg) == CORPUS_COMPANIES
    pep = reg["PepsiCo"]
    assert "PEPSICO_2022_10K" in pep["doc_ids"]
    assert "FY2022" in pep["periods"]
    # doc_ids / periods are sorted lists, not sets.
    assert pep["doc_ids"] == sorted(pep["doc_ids"])
    assert isinstance(pep["periods"], list)


def test_registry_falls_back_to_subset_when_index_absent(subset_only) -> None:
    reg = corpus_registry()
    assert set(reg) == {"PepsiCo", "Boeing", "AMD"}
    assert reg["Boeing"]["doc_ids"] == ["BOEING_2018_10K", "BOEING_2022_10K"]
    assert reg["Boeing"]["periods"] == ["FY2018", "FY2022"]


def test_registry_augments_from_index_meta(subset_only, tmp_path, monkeypatch) -> None:
    index_dir = tmp_path / "index"
    (index_dir / "amd").mkdir(parents=True)
    # meta.json stores the company *slug* and doc_ids only; periods are derived.
    (index_dir / "amd" / "meta.json").write_text(
        json.dumps({"company": "amd", "doc_ids": ["AMD_2015_10K", "AMD_2022_10K"]})
    )
    monkeypatch.setattr(config, "INDEX_DIR", index_dir)
    reg = corpus_registry()
    assert "AMD_2022_10K" in reg["AMD"]["doc_ids"]
    # period derived from the doc_id even though it is not in the subset evidence.
    assert "FY2022" in reg["AMD"]["periods"]


# --- company alias resolution -----------------------------------------------
@pytest.mark.parametrize(
    "raw,canonical",
    [
        ("PepsiCo, Inc.", "PepsiCo"),
        ("PEPSICO", "PepsiCo"),
        ("The Boeing Company", "Boeing"),
        ("Boeing Co.", "Boeing"),
        ("Advanced Micro Devices", "AMD"),
        ("Advanced Micro Devices, Inc.", "AMD"),
        ("Johnson & Johnson", "Johnson & Johnson"),
        ("J&J", "Johnson & Johnson"),
        ("Johnson and Johnson", "Johnson & Johnson"),
        ("MGM Resorts International", "MGM Resorts"),
        ("MGM", "MGM Resorts"),
        ("Nike, Inc.", "Nike"),
        ("Adobe Inc.", "Adobe"),
        ("Best Buy Co., Inc.", "Best Buy"),
        ("General Mills, Inc.", "General Mills"),
        ("Pfizer Inc.", "Pfizer"),
        ("Verizon Communications", "Verizon"),
    ],
)
def test_alias_resolution(raw, canonical) -> None:
    assert normalize_company(raw) == canonical


def test_unknown_company_resolves_to_none() -> None:
    assert normalize_company("Nvidia") is None
    assert normalize_company("Tesla, Inc.") is None
    assert normalize_company("") is None


# --- scope_verdict: company -------------------------------------------------
def test_out_of_scope_unknown_company_names_covered_companies(subset_only) -> None:
    verdict, explanation = scope_verdict(_claim("Nvidia"))
    assert verdict == "OUT_OF_SCOPE"
    assert "Nvidia" in explanation
    # explanation lists what the corpus actually covers
    assert "PepsiCo" in explanation and "Boeing" in explanation


def test_in_scope_company_via_alias(subset_only) -> None:
    verdict, explanation = scope_verdict(_claim("PepsiCo, Inc.", period="FY2022"))
    assert verdict is None
    assert explanation == ""


# --- scope_verdict: period --------------------------------------------------
def test_out_of_scope_period_names_covered_periods(subset_only) -> None:
    # PepsiCo covers FY2022 / 2023; FY2010 is well before earliest coverage.
    verdict, explanation = scope_verdict(_claim("PepsiCo", period="FY2010"))
    assert verdict == "OUT_OF_SCOPE"
    assert "FY2022" in explanation  # names the covered periods


def test_missing_period_is_in_scope(subset_only) -> None:
    verdict, _ = scope_verdict(_claim("PepsiCo", period=None))
    assert verdict is None


def test_forward_looking_period_within_horizon_is_in_scope(subset_only) -> None:
    # Boeing corpus is FY2018/FY2022; a FY2022 10-K discusses 2023 production,
    # and PepsiCo FY2024 sits one year past latest coverage. Both must reach the
    # agent (earning CONTRADICTED / NOT_IN_CORPUS) rather than being buried
    # OUT_OF_SCOPE by the pre-check.
    assert scope_verdict(_claim("Boeing", period="2023"))[0] is None
    assert scope_verdict(_claim("PepsiCo", period="FY2024"))[0] is None


def test_far_future_period_is_out_of_scope(subset_only) -> None:
    assert scope_verdict(_claim("PepsiCo", period="FY2035"))[0] == "OUT_OF_SCOPE"


# --- scope_verdict: judgment ------------------------------------------------
def test_judgment_claim_is_unverifiable(subset_only) -> None:
    verdict, explanation = scope_verdict(_claim("Boeing", claim_type="judgment", period="FY2022"))
    assert verdict == "UNVERIFIABLE"
    assert explanation


def test_unknown_company_beats_judgment(subset_only) -> None:
    # An unknown company is reported OUT_OF_SCOPE even for a judgment claim —
    # naming the corpus is more useful than "opinion".
    verdict, _ = scope_verdict(_claim("Nvidia", claim_type="judgment"))
    assert verdict == "OUT_OF_SCOPE"


# --- scope_check: in-place annotation ---------------------------------------
def test_scope_check_marks_and_skips(subset_only) -> None:
    claims = [
        _claim("PepsiCo", period="FY2022"),                       # in scope
        _claim("Nvidia", period="FY2022"),                        # out: company
        _claim("PepsiCo", period="FY2010"),                       # out: period
        _claim("Boeing", claim_type="judgment", period="FY2022"),  # unverifiable
    ]
    returned = scope_check(claims)
    assert returned is claims  # annotated in place
    assert claims[0].status == "PENDING"
    assert claims[1].status == "SKIPPED"
    assert claims[2].status == "SKIPPED"
    assert claims[3].status == "SKIPPED"


# --- out_of_scope_fraction --------------------------------------------------
def test_out_of_scope_fraction(subset_only) -> None:
    claims = [
        _claim("PepsiCo", period="FY2022"),   # in scope
        _claim("Boeing", period="FY2018"),    # in scope
        _claim("Nvidia"),                     # out of scope
        _claim("Tesla"),                      # out of scope
    ]
    assert out_of_scope_fraction(claims) == 0.5


def test_out_of_scope_fraction_excludes_unverifiable(subset_only) -> None:
    # UNVERIFIABLE (opinion) does not count toward the coverage-based fail-fast.
    claims = [_claim("PepsiCo", claim_type="judgment", period="FY2022")]
    assert out_of_scope_fraction(claims) == 0.0


def test_out_of_scope_fraction_empty() -> None:
    assert out_of_scope_fraction([]) == 0.0


def test_out_of_scope_fraction_all_unknown(subset_only) -> None:
    claims = [_claim("Nvidia"), _claim("Tesla"), _claim("SpaceX")]
    assert out_of_scope_fraction(claims) == 1.0
