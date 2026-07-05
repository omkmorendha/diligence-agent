from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
spec = importlib.util.spec_from_file_location("eval_run", ROOT / "evals" / "run.py")
assert spec is not None and spec.loader is not None
eval_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_run)


def test_zero_variance_detects_identical_scores_only_with_multiple_items() -> None:
    assert eval_run._zero_variance_criteria({"groundedness": [5, 5], "actionability": [4, 5]}) == [
        "groundedness"
    ]


def test_zero_variance_does_not_flag_single_scored_item() -> None:
    assert eval_run._zero_variance_criteria({"groundedness": [5]}) == []


def test_coverage_fraction_and_zero_attempts() -> None:
    assert eval_run._coverage(3, 4) == 0.75
    assert eval_run._coverage(0, 0) is None
