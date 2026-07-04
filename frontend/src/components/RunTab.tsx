// Run tab (spec section 24, Steps 13/15).
// Components: company picker, checklist preview, run button, live vertical timeline,
// past-runs sidebar, status badge. Timeline card types: plan, scratchpad, retrieval,
// tool_call, tool_result, decision, citation, item_answer, verdict, error.
// Consume events via EventSource on GET /runs/{id}/events (live queue OR replay —
// the frontend must not be able to tell them apart).
//
// Build against src/fixtures/demo_trace.jsonl first (Step 13), then wire to the
// backend (Step 15).

export function RunTab() {
  return (
    <section>
      <h2 style={{ fontSize: 16 }}>Run</h2>
      <p style={{ color: "#666" }}>
        TODO(Step 13/15): company picker, checklist preview, run button, and the live
        trace timeline. Build against <code>src/fixtures/demo_trace.jsonl</code> first.
      </p>
    </section>
  );
}
