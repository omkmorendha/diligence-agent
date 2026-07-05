# D5 subset selection report

Selected **61** questions across **11** companies (expanded top-company mode: 11 qualifying companies x <=8 each).

## Bucket composition

- A_multi_input: 34 (expanded target ~44)
- B_judgment: 14 (expanded target ~22)
- C_lookup: 13 (expanded target ~22)

C_lookup questions are scarce in the D3/D4-verified pool for the highest-eligible-count companies on this corpus, so the achieved composition undershoots the 11 companies x per-company 4:2:2 target (44/22/22) split on A and C in favor of B; this is the actual FinanceBench distribution, not a selection bug (see AMBIGUITIES.md section 6 and data/verified.jsonl bucket_d3 counts).

## Per-company breakdown

| company | selected | eligible pool | A | B | C | baseline-failures |
|---|---|---|---|---|---|---|
| PepsiCo | 8 | 9 | 5 | 2 | 1 | 6 |
| Boeing | 7 | 7 | 2 | 4 | 1 | 6 |
| Johnson & Johnson | 7 | 7 | 2 | 2 | 3 | 4 |
| AMD | 6 | 6 | 3 | 2 | 1 | 5 |
| MGM Resorts | 6 | 6 | 1 | 2 | 3 | 3 |
| Adobe | 5 | 5 | 5 | 0 | 0 | 5 |
| Verizon | 5 | 5 | 5 | 0 | 0 | 5 |
| Best Buy | 5 | 5 | 3 | 1 | 1 | 4 |
| General Mills | 4 | 4 | 4 | 0 | 0 | 4 |
| Nike | 4 | 4 | 2 | 1 | 1 | 3 |
| Pfizer | 4 | 4 | 2 | 0 | 2 | 2 |

## Selection policy

Eligible pool = `data/verified.jsonl` rows with `include=true` and `disagreement=false` (i.e. excludes everything in `data/disputes.jsonl` -- those require `human_reviewed: true` per the fallback policy, which is D6's job and has not happened yet). Companies ranked by (eligible count desc, baseline-failure count desc, name asc). Expanded mode selected the top 11 qualifying companies (each with >= 2 baseline-failure questions and >= 4 eligible questions) using the same company ranking and per-company 4:2:2 (A:C:B) allocation as fallback mode. Per-company selection targets a 4:2:2 (A:C:B) split of the 8-slot cap, falls back to filling remaining slots in A, then C, then B order when a bucket is short, and drops down from B, then C, then A when a company has more than 8 eligible questions.

