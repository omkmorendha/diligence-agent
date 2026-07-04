// Evals tab (spec section 24, Step 16).
// Renders the comparison table from GET /evals/results (results/comparison.json).
// Rows: answer accuracy, accuracy by bucket, citation precision, citation provenance,
// arithmetic integrity, groundedness judge, actionability judge.
// Columns: published reference (labeled "Context only, not same subset"),
// naive-RAG baseline, agent. Green/red emphasis.

export function EvalsTab() {
  return (
    <section>
      <h2 style={{ fontSize: 16 }}>Evals</h2>
      <p style={{ color: "#666" }}>
        TODO(Step 16): comparison table (published reference | naive-RAG baseline | agent)
        from <code>GET /evals/results</code>. This is the tab the demo opens on.
      </p>
    </section>
  );
}
