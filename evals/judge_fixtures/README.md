# evals/judge_fixtures/

Calibration fixtures for the **LLM judges** (`evals/judges.py`) that must live OUTSIDE
`evals/fixtures/` so the deterministic `score_fixtures()` gate (`evals/run.py --fixtures`,
currently 8/8) can never discover them. `score_fixtures()` only walks `evals/fixtures/*`
that contain an `expected.json`; these directories deliberately have no `expected.json`
and are read only by the judge calibration gate, which makes live LLM calls.

## period_mismatch_wrong_year/

The "operating-region" hard-negative (IMP-5). A well-formed memo item that answers an
**FY2023** revenue question by citing the verbatim **FY2022** comparative figure — the
number is genuinely present in the citation, but for the wrong fiscal year.

The old flat 1/3/5 groundedness rubric scored this a 5 (the citation supports the number),
which is exactly the blind spot that produced iter1's zero-variance judge. The redesigned
rubric's period-mismatch anchor must score it **<= 3**. `run_calibration_gate()` asserts
this via `HARD_NEGATIVE_GROUNDEDNESS_MAX = 3`.

## long_multiclaim_grounded/

The groundedness floor case (IMP3-3). This is a long, correct, multi-claim memo item
whose purpose is to catch judge truncation regressions: the judge must return a valid
groundedness score instead of exhausting its response budget and producing a null
`judge_error`.

`run_calibration_gate()` asserts this via `LONG_CALIBRATION_GROUNDEDNESS_MIN = 3`.
Unlike the corrupted and period-mismatch fixtures, this is a **floor**: the answer is
intended to be grounded, so scores below the floor or missing scores fail calibration.

The gate makes LLM calls, so it is not run in the deterministic CI path — the orchestrator
must re-run it live (`uv run --project backend evals/judges.py --calibrate`) after any
prompt change to confirm the corrupted and period-mismatch fixtures stay at/below their
ceilings and the long grounded fixture stays at/above its floor.
