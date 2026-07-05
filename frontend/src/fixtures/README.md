# Frontend sample data (spec section 25, Step 13)

Build the Run and Memo tabs against static sample data **before** wiring real
SSE, so the UI can be developed without a running backend or model:

- Trace JSONL — one full run's trace events (plan → retrievals → citations →
  calculate → item_answer → verdict). Same schema the SSE stream emits.
- Memo JSON — the matching rendered memo (schema in `../types.ts`, `Memo`).
- Review report JSON — static document-review report data for the Agent tab
  results view.
- Review report HTML — self-contained HTML rendering of the same review report
  for styling/layout work.

Populate these from the best real replay trace once the backend produces one
(spec section 17 / Step 17). Until then they can be hand-authored minimal examples.
