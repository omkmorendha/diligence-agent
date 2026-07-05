"""Central configuration (spec sections 3, 4).

Loads settings from a gitignored `.env` at the repo root. The API key is NEVER
hardcoded. The selected tool-calling protocol is decided ONCE by the smoke test
(scripts/smoke_llm.py -> data/smoke_llm_result.json) and read here — never
decided per call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# --- paths ---
DATA_DIR = ROOT / "data"
FILINGS_DIR = DATA_DIR / "filings"
PAGES_DIR = DATA_DIR / "pages"
RAW_DIR = DATA_DIR / "raw"
INDEX_DIR = DATA_DIR / "index"  # embedded chunk index (ingest.py), gitignored
RUNS_DIR = ROOT / "runs"
RESULTS_DIR = ROOT / "results"
SUBSET_PATH = DATA_DIR / "subset.json"
SMOKE_RESULT_PATH = DATA_DIR / "smoke_llm_result.json"

# --- LLM (spec section 3) ---
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "moonshotai/Kimi-K2.6")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_SEED = int(os.environ.get("LLM_SEED", "42"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16384"))
# Optional speed knob for reasoning models: "none" -> 0 reasoning tokens. Kimi-K2.6
# is non-reasoning so this is a no-op today; kept for models that honor it. Sent via
# extra_body in llm.chat (see llm.py). Leave unset (None) to keep a model's default.
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT") or None
# Optional cheaper/faster model for the Tier-2 LLM judges only (evals/judges.py).
# Unset -> judges use LLM_MODEL. The agent/baseline never read this.
JUDGE_MODEL = os.environ.get("JUDGE_MODEL") or None

# --- embeddings (spec section 3) ---
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# --- agent loop caps (spec section 15) ---
MAX_TOOL_CALLS_PER_ITEM = 12
RETRIEVAL_DEFAULT_K = 6
# A rejected final action (record_answer/flag_outstanding whose citation could
# not be resolved) no longer burns the retrieval budget (IMP-1: break the
# citation-rejection budget spiral). This bounds a persistently-invalid citation
# so it can't loop forever now that rejections are "free" against MAX_TOOL_CALLS:
# after this many *consecutive* rejected final attempts the item is force-abstained.
MAX_CONSECUTIVE_FINAL_REJECTIONS = 4

# --- hybrid retrieval (IMP3-5, results/iterations/iter2/improvement_plan.json) ---
# The dense-only lexical rerank is a bare token-overlap ratio: a rare exact term
# like "restructuring" counts the same as a stopword-common one, so the note that
# actually holds the answer (pepsico_06's $411M restructuring charge was NEVER
# retrieved) can be blurred out by MiniLM. RETRIEVAL_HYBRID_BM25 swaps that flat
# overlap for an IDF-weighted BM25 lexical component (still blended UNDER the dense
# cosine, same weight budget) so rare exact terms and the right source document
# surface. Behind a flag so it can be A/B'd against dense-only recall per the
# plan's risk guard ("keep behind a config flag ... so it does not regress items
# that currently pass"). k1/b are the standard BM25 saturation / length-norm knobs.
RETRIEVAL_HYBRID_BM25 = os.environ.get("RETRIEVAL_HYBRID_BM25", "1") not in ("0", "false", "False", "")
RETRIEVAL_BM25_K1 = float(os.environ.get("RETRIEVAL_BM25_K1", "1.5"))
RETRIEVAL_BM25_B = float(os.environ.get("RETRIEVAL_BM25_B", "0.75"))

# --- search stall guard (IMP3-5) ---
# When the model fixates on near-identical searches that surface no new evidence,
# stop the retrieval churn and nudge it to change strategy or abstain. verizon_04
# fired 11 near-identical "interest rate caps swaptions" queries (consecutive
# token-Jaccard up to 0.92) while the needed cross-currency-swap table was ALREADY
# retrieved -- pure budget burn. A search is "stalled" when it returns zero NEW
# chunk_ids OR its query is a near-duplicate (token-Jaccard >= threshold) of the
# previous one; after MAX_REPEATS consecutive stalls we inject a tool-result hint.
# The plan's risk guard mandates "zero-new-chunks OR high Jaccard, not repeated
# intent alone", and a hint (never a hard stop) so it can't cut legitimate
# multi-query research.
SEARCH_STALL_JACCARD_THRESHOLD = float(os.environ.get("SEARCH_STALL_JACCARD_THRESHOLD", "0.6"))
SEARCH_STALL_MAX_REPEATS = int(os.environ.get("SEARCH_STALL_MAX_REPEATS", "3"))
# Additional stall arm (IMP4-2): the model varies surface tokens across
# near-duplicate-intent queries (boeing_01 cycled "Note 21/22/23"), so the
# Jaccard/zero-new-chunks arms miss the loop while the SAME dominant document is
# re-retrieved over and over (pepsico_02 re-hit one 8-K 61x). Latch the stall
# once one doc_id has been the dominant search result this many times.
SEARCH_STALL_DOC_REPEATS = int(os.environ.get("SEARCH_STALL_DOC_REPEATS", "4"))

# --- commit nudge (IMP4-2) ---
# The over-search/never-commit regression: the model burned all 12 successful
# searches with ZERO record_answer attempts while the answer-bearing evidence was
# already retrieved (boeing_01, mgm_resorts_01, pepsico_02, pfizer_03). Once this
# many successful searches land with no record_answer attempt yet, push a
# commit-nudge on the tool-result-hint channel (never an abstain suggestion) so
# the model drafts its answer from evidence in hand instead of hunting forever.
SEARCH_COMMIT_NUDGE_THRESHOLD = int(os.environ.get("SEARCH_COMMIT_NUDGE_THRESHOLD", "6"))

# --- eval tolerances (spec section 20) ---
DEFAULT_RELATIVE_TOLERANCE = 0.01
DEFAULT_PAGE_SLACK = 1

SCHEMA_VERSION = "0.1"


def selected_tool_protocol() -> str:
    """Return 'native' or 'json'. Prefers the smoke-test result, then env, then 'json'."""
    if SMOKE_RESULT_PATH.exists():
        try:
            data = json.loads(SMOKE_RESULT_PATH.read_text())
            proto = data.get("selected_tool_protocol")
            if proto in ("native", "json"):
                return proto
        except (json.JSONDecodeError, OSError):
            pass
    return os.environ.get("TOOL_PROTOCOL", "json")


def require_api_key() -> str:
    if not NVIDIA_API_KEY:
        raise RuntimeError(
            "NVIDIA_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return NVIDIA_API_KEY
