# D5 subset selection report

Selected **40** questions across **7** companies (target: 40 questions, <=8 per company).

## Bucket composition

- A_multi_input: 19 (ideal ~20)
- B_judgment: 12 (ideal ~10)
- C_lookup: 9 (ideal ~10)

C_lookup questions are scarce in the D3/D4-verified pool for the highest-eligible-count companies on this corpus, so the achieved composition may undershoot the ideal 20/10/10 split on scarce buckets in favor of B; this is the actual FinanceBench distribution, not a selection bug (see AMBIGUITIES.md section 6 and data/verified.jsonl bucket_d3 counts).

## Per-company breakdown

| company | selected | eligible pool | A | B | C | baseline-failures |
|---|---|---|---|---|---|---|
| PepsiCo | 8 | 9 | 5 | 2 | 1 | 6 |
| Boeing | 7 | 7 | 2 | 4 | 1 | 6 |
| Johnson & Johnson | 7 | 7 | 2 | 2 | 3 | 4 |
| AMD | 6 | 6 | 3 | 2 | 1 | 5 |
| MGM Resorts | 6 | 6 | 1 | 2 | 3 | 3 |
| Adobe | 5 | 5 | 5 | 0 | 0 | 5 |
| Verizon | 1 | 5 | 1 | 0 | 0 | 1 |

## Selection policy

Eligible pool = `data/verified.jsonl` rows with `include=true` and `disagreement=false` (i.e. excludes everything in `data/disputes.jsonl` -- those require `human_reviewed: true` per the fallback policy, which is D6's job and has not happened yet). Companies ranked by (eligible count desc, baseline-failure count desc, name asc), then included until the global target of 40 questions is reached. Fully included companies clear >= 2 baseline-failure questions and >= 4 eligible questions; the final company may be partially included to hit exactly 40. Per-company selection targets a 4:2:2 (A:C:B) split of the 8-slot cap, falls back to filling remaining slots in A, then C, then B order when a bucket is short, and drops down from B, then C, then A when a company has more than 8 eligible questions.

