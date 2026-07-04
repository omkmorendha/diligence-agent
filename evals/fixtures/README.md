# Eval fixtures (spec sections 19 + 25, Step 2)

Tiny, hand-authored `(subset_item, trace.jsonl, memo.json)` triples that drive the
deterministic scorer tests **before any real data or model is involved**. Build
these first — they are the TDD foundation.

Required fixtures (each should make exactly one scorer fire the intended way):

| # | fixture | exercises |
|---|---------|-----------|
| 1 | `correct_lookup/`            | answer accuracy = pass (string/numeric lookup) |
| 2 | `correct_calculation/`      | answer accuracy = pass + arithmetic integrity via `calculate` |
| 3 | `incorrect_calculation/`    | answer accuracy = fail (wrong derived number) |
| 4 | `missing_citation/`         | citation precision / material-claim-without-citation |
| 5 | `citation_unretrieved_chunk/` | citation provenance = fail (chunk never in a retrieval event) |
| 6 | `abstention/`               | abstention scored correct on a truly unanswerable item |
| 7 | `corrupted_swapped_citation/` | citation precision fail + judge calibration gate |
| 8 | `corrupted_wrong_number/`   | arithmetic integrity fail + judge calibration gate |

Each fixture dir contains:
```
subset_item.json   # includes gold fields the scorer needs
trace.jsonl        # minimal event sequence (plan -> retrieval -> ... -> item_answer)
memo.json          # the memo under test
expected.json      # expected per-metric scores, so tests assert exact values
```

Keep them minimal — one item each. They must be deterministic and model-free.
