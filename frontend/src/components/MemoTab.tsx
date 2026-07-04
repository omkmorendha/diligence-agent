// Memo tab (spec section 24, Steps 13/15).
// Renders the memo with inline citation markers. Clicking a citation opens a side
// panel that calls GET /corpus/{company}/{doc_id}/page/{n} and highlights the span.
// Per-item confidence shown as "2 inputs grounded / 0 assumed". Item statuses:
// answered / abstained / error.
//
// Build against src/fixtures/demo_memo.json first (Step 13), then wire up (Step 15).

export function MemoTab() {
  return (
    <section>
      <h2 style={{ fontSize: 16 }}>Memo</h2>
      <p style={{ color: "#666" }}>
        TODO(Step 13/15): rendered memo with clickable, document-aware citations and a
        source-page side panel. Build against <code>src/fixtures/demo_memo.json</code>.
      </p>
    </section>
  );
}
