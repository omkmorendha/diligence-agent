# Cumulative Loop Narrative — 5-Iteration Improve/Eval Cycle

*Source of record: `results/iterations/report_data.json` (all numbers below are drawn from it). Curated subset: 61 items across 11 companies, three buckets (A_multi_input 34, B_judgment 14, C_lookup 13). Every iteration was rescored under the final scorer (v4) for apples-to-apples comparison; the primary metric is TOTAL CORRECT of 61.*

## 1. Executive summary

Five iterations moved the agent from **22/61 correct** (naive single-call RAG baseline) to **51/61 correct** — an 85% accuracy on the 60 of 61 items it chose to answer (only `best_buy_02` remains abstained). The trend was **22 → 37 → 50 → 49 → 50 → 51**: one giant jump when the agent was first run at all (baseline→iter1, +15), a second giant jump from the iter1 analysis (iter1→iter2, +13), then three iterations of trench warfare (49/50/51) where each gain was partly offset by endpoint-driven churn.

The single most important finding of the whole loop landed in **iteration 1's analysis**: the headline "29.5% accuracy" (v0 string-match) was almost entirely two *mechanical* defects, not agent reasoning quality:

1. **A citation-rejection budget spiral (agent bug).** `search_filing` showed the model a whitespace-collapsed snippet, but the citation validator did an exact `find()` against raw newline/`\xa0`-laden PDF text, so copied quotes could never match. Every rejected `record_answer` was still charged to the 12-call budget, so the agent burned out mid-task. This one bug produced **all 102 `citation_quote_not_verbatim` rejections** and **all 17 answerable abstentions** in iter1, and drove the **633.82 s p95 wall** and **4.61 M prompt tokens**.
2. **A gold-labeling measurement artifact (eval bug).** 44/61 golds had `gold_value=null`, so numeric and prose answers were graded by exact string-match instead of ±1% numeric / canonical matching. Most "wrong" answers were substantively correct and failed only on terse-vs-verbose formatting.

Fixing the spiral and the annotations was independent and compounding. Alongside correctness, latency collapsed: **p95 wall 634 s (iter1) → 93.6 s (iter5)**, mean wall **107.8 s → 34.0 s**, arithmetic integrity **0.69 → 1.00 and held**, and trace-shape **0.44 → 0.77**.

| metric | baseline | iter1 | iter2 | iter3 | iter4 | iter5 |
|---|---|---|---|---|---|---|
| correct of 61 | 22 | 37 | 50 | 49 | 50 | 51 |
| answered / abstained | 29 / 32 | 44 / 17 | 58 / 3 | 54 / 7 | 59 / 2 | 60 / 1 |
| answer_accuracy (of answered) | 0.759 | 0.841 | 0.862 | 0.907 | 0.847 | 0.850 |
| citation_precision | 0.414 | 0.614 | 0.483 | 0.593 | 0.610 | 0.583 |
| arithmetic_integrity | 0.690 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| trace_shape | 0.443 | 0.574 | 0.738 | 0.754 | 0.771 | 0.771 |
| p50 / p95 wall (s) | 23.8 / 77.3 | 34.5 / 633.8 | 41.1 / 105.2 | 22.5 / 68.1 | 21.2 / 72.6 | 22.2 / 93.6 |
| prompt tokens | 0.13 M | 4.61 M | 3.20 M | 3.84 M | 3.35 M | 3.20 M |
| verbatim-cite rejections | — | 102 | 18 | 18 | 17 | 14 |
| budget-exhausted items | 0 | 17 | 3 | 5 | 2 | 1 |

## 2. Iteration by iteration

Each round is framed as *what the analysis found → what was changed (agent / eval-measurement / judge / config) → measured effect → what it teaches*.

### Baseline — naive RAG (22/61)
- **Found:** the "no agent" floor. One `search_filing` + one answer per item, mean **2 tool calls/item**.
- **Effect:** 29 answered / **32 abstained**, all answerable (abstention-correct 0%); arithmetic integrity **0.69**, trace-shape **0.44**, citation_precision 0.41.
- **Teaches:** single-shot RAG cannot derive or verify; it is the reference point every later gain is measured against.

### Iteration 1 — unmodified agent (37/61, +15)
- **Found (the loop's cornerstone analysis):** the two mechanical root causes of §1 — the citation-rejection **budget spiral** (agent) and the **gold-labeling artifact** (eval) — plus a **dead judge** (groundedness/actionability flat 5.0, zero variance across all 61 items including 17 known-bad abstentions).
- **Changed:** nothing yet — this is the diagnostic run; it produced the iter1 improvement plan (IMP-1…IMP-5).
- **Effect:** running the tool-using agent alone added +15; arithmetic integrity jumped to **1.00** (calculator), trace-shape 0.44→**0.57**. But the spiral capped it: **17 answerable abstentions** all from budget exhaustion, **102** verbatim-cite rejections, **633.82 s p95**, **4.61 M** prompt tokens.
- **Teaches:** measure before you optimize — what looked like a reasoning ceiling was two bugs and a non-informative judge.

### Iteration 2 — citation-spiral fix (50/61, +13)
- **Found:** IMP-1's spiral fix and the annotation gap were independent and compounding.
- **Changed:** *agent* — whitespace-tolerant verbatim matcher, stop charging rejected `record_answer` to budget, resolve citations against fetched pages (IMP-1). *Eval-measurement* — parse numeric golds and add `gold_polarity`/`gold_canonical` branches (IMP-2 / IMP3-1).
- **Effect:** verbatim rejections **102 → 18**, budget-exhausted items **17 → 3**, answered **44 → 58**, p95 **634 → 105 s**; churn **+14 / −1** (lost `verizon_04`). Citation_precision dipped 0.614→0.483 — a denominator shift (14 formerly-abstained items became answered) plus over-citation, not a grounding regression.
- **Teaches:** the highest-ROI fix was simultaneously a behavior, latency, and token win; both the agent fix and the pure rescore had to land together for measured accuracy to track true content accuracy.

### Iteration 3 — precision hardening (49/61, −1)
- **Found:** citation-minimalism recovers over-citation, but stricter quoting risks non-commitment.
- **Changed:** *agent/prompt* — cite only the source page(s); stricter verbatim quoting; get-pages-only-citable-when-quoted.
- **Effect:** `answer_accuracy` of answered peaked at **0.907** and citation_precision recovered to **0.593**, but TOTAL CORRECT slipped to 49 (**+4 / −5**). The 5 losses (`amd_05`, `boeing_01`, `mgm_resorts_01`, `pepsico_02`, `pfizer_03`) shared one signature: **all 12 searches spent, zero `record_answer` attempts, zero rejections** — the raised "citable" bar made the model hunt for a cleaner quote until budget ran out.
- **Teaches:** the classic precision/coverage see-saw — tightening grounding rules can make a capable model *never commit*, and more budget would only buy more wasted searches.

### Iteration 4 — coverage recovery (50/61, +1)
- **Found:** the sharpest analysis of the loop. Byte-identical `plan` prompts (identical `prompt_tokens` on all 11 companies) produced **different completion tokens in every case** (0/11 identical, ~2× swings). **Temperature 0.2 + seed 42 is effectively non-deterministic on the Vultr/Kimi-K2.6 endpoint — the seed is not honored.**
- **Changed:** *agent* — commit-nudge on the over-search/no-commit path + a tightened stall guard.
- **Effect:** exactly the 5 iter3 stall losses came back (**+5**) but **−4 lost** to endpoint churn: `adobe_01`/`boeing_02` lost their leading "Yes/No" token, `jj_04` left `value=null`, `jj_01` surfaced a Kenvue-stripped 8-K COGS instead of the 10-K.
- **Teaches:** past a point, run-to-run variance — not agent skill — sets the ceiling, and the remedy is config/format canonicalization, not more reasoning.

### Iteration 5 — stability (51/61, +1)
- **Found:** with the seed dead, temperature is the only live determinism lever; residual flips are format/sampling artifacts.
- **Changed:** *config* — temperature **0.2 → 0.0** (IMP5-2). *Agent/prompt* — answer-format canonicalization (lead Yes/No; always populate value+unit, IMP5-1). *Eval-measurement* — geography synonym + honest `amd_05`/`boeing_07` annotations + `verizon_05` sign-off (IMP5-3). *Judge* — model switch to DeepSeek-V4-Flash (IMP5-4).
- **Effect:** answered reached **60/61**, budget-exhausted down to a single item (`best_buy_02`); churn **+3 / −2** (`amd_02`, `pfizer_02` flipped correct→wrong via residual sampling). Across iter2–iter5, **40 items were correct in every run** and only **14 flipped correct↔non-correct at least once**.
- **Teaches:** stabilization shrinks the churn band but does not close it — the last +1 was net of two determinism-driven regressions.

## 3. Metric integrity

**Scoring-version history (`score_versions`).** The scorer was hardened four times; every iteration was retroactively rescored under each version so comparisons stay apples-to-apples. On the baseline the ladder was **v0 string-match 3 → v1 parsed numeric golds 8 → v2 polarity/canonical 18 → v3 iter4 annotations 22 → v4 final (geography + signed-off golds) 22**. The same ladder on iter2: **v1 24 → v2 46 → v3 49 → v4 50**; on iter1: **v0 13 → v4 37**. The v0→v2 gap (3→18 on baseline, 13→32 on iter1) *is* the measurement artifact from §1 — most of the early "wins" were the scorer learning to read correct answers, not the agent improving.

**Honesty rules for annotations.** Annotations only ever added *branches* above the exact-string fallback (numeric ±1% gated on `gold_value`+`memo.value`; polarity gated on `gold_polarity`; canonical gated on `gold_canonical`); the fallback was never relaxed, so genuinely-wrong items keep failing. `johnson_johnson_06` (gold_polarity = No) stayed wrong across all six runs precisely because its annotation is correct and correctly failing.

**Adversarial audits and sign-offs.** `verizon_05` was carried as a human-signed-off gold (correct iter1→iter5). The reverted case is the load-bearing one: for **`pepsico_03`** the orchestrator proposed a `North America` canonical annotation, but the guard caught that the gold lists **multiple geographies** — crediting "North America" would have scored an *incomplete* answer as correct. The annotation was **reverted**; `pepsico_03` remains `wrong_text` in every iteration (baseline abstain → wrong_text iter1–iter5) and stays a genuine failure. This is the loop's proof that the annotation process could *deny* itself an easy point.

## 4. Model behavior findings (Kimi-K2.6)

- **Hidden reasoning channel truncates the judge.** The flat groundedness=5.0 / actionability=5.0 was not saturation but a *measurement bug*: Kimi-K2.6 spends its completion budget in a hidden reasoning channel, nulling 34/61 groundedness and 32/61 actionability scores non-randomly (exactly the long multi-claim answers where variance lives). Proven live: `adobe_01` returned empty at `max_tokens` 4000/8000 but valid JSON at 16000. Raising `max_tokens` made the judge honest — iter5 judges: **groundedness 4.72, actionability 3.48, gold_agreement 4.39** (coverage 1.0 each, `judge_zero_variance = false`).
- **Seed not honored → churn.** As in §2/iter4: identical prompts, ~2× completion-token swings, 0/11 reproducible. Temperature 0.0 was the mitigation.
- **Over-search / never-commit under strict citation rules.** Tightening the "citable" bar (iter3) produced 5 items that exhausted 12 searches with zero commits and zero rejections — a fixation failure distinct from retrieval recall.
- **Commit-nudge effect.** A single prompt nudge flipped those 5 back to answered in iter4 (some as calibrated abstentions turned answers, e.g. `amd_05`, `boeing_07`).
- **Judge bake-off.** The judge model was switched to **DeepSeek-V4-Flash** (IMP5-4): gate PASS, **9/9 agreement** with the incumbent, **~30× faster**.

## 5. Remaining failure inventory (10 iter5 non-correct) and a 6th iteration

| item | class | one-line cause |
|---|---|---|
| `pepsico_06` | wrong_value | "on the face of the income statement" interpretation gap — retrieved Note 3 ($411 M) but the question wants the face value; slowest item at 343 s, 3 numeric rejections. Genuine. |
| `boeing_06` | wrong_text | effective-tax-rate **sign convention** on a pre-tax loss — magnitudes match gold, signs flip. Genuine. |
| `johnson_johnson_06` | wrong_text | genuine wrong-growth-basis; gold_polarity = No, annotation correct and correctly failing (16 calls, 4 rejections). |
| `pepsico_03` | wrong_text | reverted geography annotation — gold lists multiple geographies, answer incomplete. Genuine (see §3). |
| `pfizer_04` | wrong_text | Upjohn-spinoff retrieval miss / contested-stale gold; persistent wrong_text every run. |
| `boeing_07` | wrong_text | cited 89% of BDS segment vs gold's 40% of total — partly off **plus** an annotation-coverage gap. |
| `boeing_02` | wrong_text | branch/polarity selection; citation_precision also fails. |
| `best_buy_02` | abstained | the last budget-exhausted item — 12 searches, 1 flag, 0 commits; abstained since iter1. |
| `amd_02` | wrong_text | **regression** — correct baseline→iter4, flipped by residual sampling nondeterminism at T=0.0. |
| `pfizer_02` | wrong_text | **regression** — correct iter1→iter4, flipped by residual sampling nondeterminism. |

**A 6th iteration would target three clusters.** (a) *Residual nondeterminism* — `amd_02` and `pfizer_02` are correct-answer flips, not skill losses; the lever is majority-vote / self-consistency over 3 samples since the seed is dead. (b) *Genuine reasoning/interpretation misses* — `pepsico_06` (face-vs-note reading), `boeing_06` (tax-sign convention), `pfizer_04` (retrieval of the spinoff filing): each needs a targeted rule or a document-priority fix, not a global prompt change. (c) *Coverage/annotation edges* — `best_buy_02` needs the stall guard to actually fire, and `boeing_07`/`boeing_02` are half annotation-gap, half partially-wrong answers that should be split before crediting. `johnson_johnson_06` and `pepsico_03` are correctly-failing genuine errors and should be left alone — chasing them would mean weakening a scorer, which the loop explicitly refused to do.
