import { Card, SectionLabel } from "../ui";

const flowSteps = [
  "Parsed filing pages",
  "Page-aware chunks",
  "Local embeddings",
  "Hybrid retrieval",
  "Grounded answer",
];

const evalPoints = [
  { label: "Dataset", value: "FinanceBench SEC filing QA" },
  { label: "Loop", value: "Run, score, inspect traces, patch" },
  { label: "Result", value: "Fewer guesses, clearer citations" },
];

function SignatureVisual() {
  return (
    <svg viewBox="0 0 920 360" width="100%" role="img" aria-label="DiliAgent orchestration diagram">
      <defs>
        <marker id="doc-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="var(--accent)" />
        </marker>
        <linearGradient id="agent-glow" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--accent-soft)" />
          <stop offset="100%" stopColor="var(--surface)" />
        </linearGradient>
      </defs>

      <rect x="1" y="1" width="918" height="358" rx="20" fill="var(--surface)" stroke="var(--line)" />
      <rect x="44" y="54" width="190" height="108" rx="18" fill="url(#agent-glow)" stroke="var(--accent-line)" />
      <text x="139" y="90" textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--text)">
        Orchestrator
      </text>
      <text x="139" y="116" textAnchor="middle" fontSize="12" fill="var(--text-2)">
        plans each claim
      </text>
      <text x="139" y="138" textAnchor="middle" fontSize="12" fill="var(--text-2)">
        dispatches agentic RAG
      </text>

      {[0, 1, 2].map((i) => (
        <g key={i}>
          <rect
            x={276}
            y={44 + i * 70}
            width="138"
            height="44"
            rx="10"
            fill="var(--surface-2)"
            stroke="var(--line)"
          />
          <text x="345" y={71 + i * 70} textAnchor="middle" fontSize="12" fill="var(--text)">
            Claim packet {i + 1}
          </text>
          <path
            d={`M234 ${108} C250 ${108}, 254 ${66 + i * 70}, 276 ${66 + i * 70}`}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="1.5"
            markerEnd="url(#doc-arrow)"
          />
        </g>
      ))}

      <rect x="476" y="44" width="178" height="174" rx="18" fill="var(--surface-2)" stroke="var(--line)" />
      <text x="565" y="74" textAnchor="middle" fontSize="13" fontWeight="700" fill="var(--text)">
        Verification swarm
      </text>
      {[
        [520, 112, "Lookup"],
        [612, 112, "Calc"],
        [520, 166, "Cite"],
        [612, 166, "Abstain"],
      ].map(([cx, cy, label]) => (
        <g key={label}>
          <circle cx={cx} cy={cy} r="25" fill="var(--surface)" stroke="var(--accent-line)" />
          <text x={cx} y={Number(cy) + 4} textAnchor="middle" fontSize="11" fill="var(--text-2)">
            {label}
          </text>
        </g>
      ))}
      <path d="M414 66 L476 112" fill="none" stroke="var(--accent)" strokeWidth="1.5" markerEnd="url(#doc-arrow)" />
      <path d="M414 136 L476 136" fill="none" stroke="var(--accent)" strokeWidth="1.5" markerEnd="url(#doc-arrow)" />
      <path d="M414 206 L476 166" fill="none" stroke="var(--accent)" strokeWidth="1.5" markerEnd="url(#doc-arrow)" />

      <rect x="472" y="250" width="190" height="58" rx="14" fill="var(--accent-soft)" stroke="var(--accent-line)" />
      <text x="567" y="273" textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--accent-text)">
        RAG corpus + tools
      </text>
      <text x="567" y="294" textAnchor="middle" fontSize="11" fill="var(--text-2)">
        search_filing / get_pages / calculate
      </text>
      <path d="M565 218 L565 250" fill="none" stroke="var(--accent)" strokeWidth="1.5" markerEnd="url(#doc-arrow)" />

      <rect x="718" y="82" width="150" height="62" rx="14" fill="var(--green-soft)" stroke="var(--line)" />
      <text x="793" y="108" textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--green)">
        Verified
      </text>
      <text x="793" y="128" textAnchor="middle" fontSize="11" fill="var(--text-2)">
        answer + quote
      </text>
      <rect x="718" y="168" width="150" height="62" rx="14" fill="var(--amber-soft)" stroke="var(--line)" />
      <text x="793" y="194" textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--amber)">
        Outstanding
      </text>
      <text x="793" y="214" textAnchor="middle" fontSize="11" fill="var(--text-2)">
        no safe answer
      </text>
      <path d="M654 131 C684 131, 688 113, 718 113" fill="none" stroke="var(--accent)" strokeWidth="1.5" markerEnd="url(#doc-arrow)" />
      <path d="M654 160 C684 160, 688 199, 718 199" fill="none" stroke="var(--accent)" strokeWidth="1.5" markerEnd="url(#doc-arrow)" />
    </svg>
  );
}

function RagFlowVisual() {
  return (
    <svg viewBox="0 0 920 236" width="100%" role="img" aria-label="Actual DiliAgent RAG flow">
      <defs>
        <marker id="rag-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="var(--text-3)" />
        </marker>
      </defs>
      <rect x="1" y="1" width="918" height="234" rx="18" fill="var(--surface)" stroke="var(--line)" />
      {flowSteps.map((step, i) => {
        const x = 36 + i * 176;
        return (
          <g key={step}>
            <rect x={x} y="54" width="132" height="78" rx="13" fill="var(--surface-2)" stroke="var(--line)" />
            <text x={x + 66} y="86" textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--text)">
              {step}
            </text>
            <text x={x + 66} y="110" textAnchor="middle" fontSize="10.5" fill="var(--text-2)">
              {i === 0 && "PDF text by page"}
              {i === 1 && "1000 chars / 150 overlap"}
              {i === 2 && "embeddings.npy + chunks"}
              {i === 3 && "semantic + BM25 + metadata"}
              {i === 4 && "chunk_id + quote"}
            </text>
            {i < flowSteps.length - 1 && (
              <path
                d={`M${x + 132} 93 L${x + 170} 93`}
                fill="none"
                stroke="var(--text-3)"
                strokeWidth="1.5"
                markerEnd="url(#rag-arrow)"
              />
            )}
          </g>
        );
      })}
      <rect x="216" y="162" width="488" height="38" rx="11" fill="var(--accent-soft)" stroke="var(--accent-line)" />
      <text x="460" y="186" textAnchor="middle" fontSize="12" fill="var(--accent-text)">
        Agent can escalate from snippets to full pages, then use calculate for grounded arithmetic.
      </text>
    </svg>
  );
}

function PdfBeforeAfterVisual() {
  const lines = [0, 1, 2, 3, 4];

  return (
    <svg viewBox="0 0 920 300" width="100%" role="img" aria-label="Before and after annotated PDF">
      <defs>
        <marker id="pdf-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="var(--accent)" />
        </marker>
      </defs>
      <rect x="1" y="1" width="918" height="298" rx="18" fill="var(--surface)" stroke="var(--line)" />
      <g>
        <rect x="82" y="42" width="240" height="214" rx="12" fill="var(--surface-2)" stroke="var(--line)" />
        <text x="202" y="76" textAnchor="middle" fontSize="14" fontWeight="700" fill="var(--text)">
          Before
        </text>
        {lines.map((line) => (
          <rect
            key={line}
            x="118"
            y={104 + line * 24}
            width={line === 2 ? 142 : 168}
            height="8"
            rx="4"
            fill="var(--text-3)"
            opacity="0.45"
          />
        ))}
        <rect x="118" y="214" width="112" height="8" rx="4" fill="var(--text-3)" opacity="0.35" />
        <text x="202" y="238" textAnchor="middle" fontSize="11" fill="var(--text-3)">
          unreviewed diligence draft
        </text>
      </g>

      <path d="M362 150 L558 150" fill="none" stroke="var(--accent)" strokeWidth="2" markerEnd="url(#pdf-arrow)" />
      <text x="460" y="132" textAnchor="middle" fontSize="12" fill="var(--accent-text)">
        DiliAgent review
      </text>

      <g>
        <rect x="598" y="42" width="240" height="214" rx="12" fill="var(--surface-2)" stroke="var(--line)" />
        <text x="718" y="76" textAnchor="middle" fontSize="14" fontWeight="700" fill="var(--text)">
          After
        </text>
        {lines.map((line) => (
          <rect
            key={line}
            x="634"
            y={104 + line * 24}
            width={line === 2 ? 142 : 168}
            height="8"
            rx="4"
            fill="var(--text-3)"
            opacity="0.4"
          />
        ))}
        <rect x="634" y="212" width="112" height="8" rx="4" fill="var(--text-3)" opacity="0.35" />
        <rect x="618" y="98" width="6" height="36" rx="3" fill="var(--green)" />
        <rect x="762" y="92" width="66" height="24" rx="12" fill="var(--green-soft)" stroke="var(--line)" />
        <text x="795" y="108" textAnchor="middle" fontSize="9.5" fontWeight="700" fill="var(--green)">
          VERIFIED
        </text>
        <rect x="618" y="146" width="6" height="36" rx="3" fill="var(--amber)" />
        <rect x="762" y="142" width="74" height="24" rx="12" fill="var(--amber-soft)" stroke="var(--line)" />
        <text x="799" y="158" textAnchor="middle" fontSize="9.5" fontWeight="700" fill="var(--amber)">
          NEEDS CITE
        </text>
        <rect x="618" y="194" width="6" height="36" rx="3" fill="var(--red)" />
        <rect x="762" y="190" width="70" height="24" rx="12" fill="var(--red-soft)" stroke="var(--line)" />
        <text x="797" y="206" textAnchor="middle" fontSize="9.5" fontWeight="700" fill="var(--red)">
          OUT OF SCOPE
        </text>
        <text x="718" y="238" textAnchor="middle" fontSize="11" fill="var(--text-3)">
          annotated PDF with diligence markers
        </text>
      </g>
    </svg>
  );
}

export function DocumentationTab() {
  return (
    <div style={{ display: "grid", gap: 22 }}>
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 0.9fr) minmax(320px, 1.4fr)",
          gap: 22,
          alignItems: "center",
        }}
      >
        <div style={{ display: "grid", gap: 14 }}>
          <SectionLabel style={{ marginBottom: 0 }}>Documentation</SectionLabel>
          <div style={{ fontSize: 32, lineHeight: 1.08, fontWeight: 700, letterSpacing: -0.8 }}>
            Due-diligence verification, shown as a review system.
          </div>
          <div style={{ fontSize: 15, color: "var(--text-2)", lineHeight: 1.55, maxWidth: 540 }}>
            DiliAgent breaks a memo into claims, dispatches specialized verification
            agents, retrieves filing evidence, and returns verdicts with citations.
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {["Agentic RAG", "FinanceBench", "Annotated PDF"].map((label) => (
              <span
                key={label}
                style={{
                  padding: "5px 10px",
                  borderRadius: 999,
                  background: "var(--surface-2)",
                  border: "1px solid var(--line)",
                  color: "var(--text-2)",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                {label}
              </span>
            ))}
          </div>
        </div>
        <SignatureVisual />
      </section>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 18,
        }}
      >
        <Card style={{ padding: 20, display: "grid", gap: 14 }}>
          <SectionLabel style={{ marginBottom: 0 }}>Actual RAG flow</SectionLabel>
          <RagFlowVisual />
          <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.55 }}>
            Filings are parsed into page-aware chunks, embedded locally, and searched
            with a semantic/BM25/metadata blend. The agent can fetch full pages,
            run grounded calculations, and cite only retrieved chunk IDs with
            verbatim quotes.
          </div>
        </Card>

        <Card style={{ padding: 20, display: "grid", gap: 14 }}>
          <SectionLabel style={{ marginBottom: 0 }}>Document output</SectionLabel>
          <PdfBeforeAfterVisual />
          <div style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.55 }}>
            The presentation target is simple: upload an unannotated PDF and receive
            the same document annotated with due-diligence markers, citations, and
            outstanding items.
          </div>
        </Card>
      </section>

      <section
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(260px, 0.9fr) minmax(320px, 1.1fr)",
          gap: 18,
        }}
      >
        <Card style={{ padding: 20 }}>
          <SectionLabel>Models on Vultr</SectionLabel>
          <div style={{ display: "grid", gap: 10, fontSize: 13.5, color: "var(--text-2)", lineHeight: 1.55 }}>
            <div>
              Development used NVIDIA Nemotron and Kimi models through Vultr-hosted
              access, making iteration fast enough to run many eval cycles.
            </div>
            <div>
              Nemotron models provided very good speed and value for quick development
              and were able to do evals at a level comparable to GLM 5.2 and Kimi K2.6.
            </div>
          </div>
        </Card>

        <Card style={{ padding: 20 }}>
          <SectionLabel>Eval-driven development</SectionLabel>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
            {evalPoints.map((point) => (
              <div
                key={point.label}
                style={{
                  padding: 13,
                  borderRadius: 10,
                  background: "var(--surface-2)",
                  border: "1px solid var(--line)",
                  display: "grid",
                  gap: 6,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-3)", textTransform: "uppercase" }}>
                  {point.label}
                </div>
                <div style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.35 }}>{point.value}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 14, fontSize: 13.5, color: "var(--text-2)", lineHeight: 1.55 }}>
            FinanceBench is a benchmark of financial questions grounded in company
            filings. Scoring each run against it made improvements concrete: better
            retrieval, stricter calculation rules, citation checks, and abstentions
            where a less measured workflow would invite guesses.
          </div>
        </Card>
      </section>
    </div>
  );
}
