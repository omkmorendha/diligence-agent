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
LLM_MODEL = os.environ.get("LLM_MODEL", "z-ai/glm-5.2")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_SEED = int(os.environ.get("LLM_SEED", "42"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16384"))

# --- embeddings (spec section 3) ---
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# --- agent loop caps (spec section 15) ---
MAX_TOOL_CALLS_PER_ITEM = 12
RETRIEVAL_DEFAULT_K = 6

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
