# Frontend fixtures (spec section 25, Step 13)

Build the Run and Memo tabs against static fixtures **before** wiring real SSE, so
the UI is demoable without a running backend or model:

- `demo_trace.jsonl` — one full run's trace events (plan → retrievals → citations →
  calculate → item_answer → verdict). Same schema the SSE stream emits.
- `demo_memo.json` — the matching rendered memo (schema in `../types.ts`, `Memo`).
- `demo_review_report.json` — static document-review report fixture for the Agent
  tab results view.
- `demo_review_report.html` — self-contained HTML rendering of the same review
  report for styling/layout work.

Populate these from the best real replay trace once the backend produces one
(spec section 17 / Step 17). Until then they can be hand-authored minimal examples.
