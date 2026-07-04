// Tiny shared UI primitives for the Ledger redesign. Everything else stays inline
// in the tab components, per the repo's existing convention.
import type { CSSProperties, ReactNode } from "react";

export const MONO = "var(--mono)";

/** Uppercase 11px section label, e.g. "PAST RUNS", "TRACE · run-id". */
export function SectionLabel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div
      style={{
        fontSize: 12,
        fontWeight: 600,
        color: "var(--text-3)",
        textTransform: "uppercase",
        letterSpacing: 0.7,
        marginBottom: 12,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Soft pill, e.g. status / verdict badges. */
export function Pill({
  color,
  bg,
  children,
  style,
}: {
  color: string;
  bg: string;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 600,
        color,
        background: bg,
        borderRadius: 20,
        padding: "3px 10px",
        textTransform: "capitalize",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {children}
    </span>
  );
}

/** Card surface. */
export function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: 12,
        boxShadow: "var(--shadow)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Styled <select> with a custom arrow (theme.css strips the native appearance). */
export const selectStyle: CSSProperties = {
  fontSize: 13,
  fontFamily: "inherit",
  padding: "7px 30px 7px 11px",
  border: "1px solid var(--line-strong)",
  borderRadius: 8,
  background: "var(--surface)",
  color: "var(--text)",
  cursor: "pointer",
  backgroundImage:
    "linear-gradient(45deg, transparent 50%, var(--text-3) 50%), linear-gradient(135deg, var(--text-3) 50%, transparent 50%)",
  backgroundPosition: "calc(100% - 16px) 55%, calc(100% - 11px) 55%",
  backgroundSize: "5px 5px, 5px 5px",
  backgroundRepeat: "no-repeat",
};

/** Inline "Show/Hide …" expander trigger. */
export function ExpanderButton({ open, showLabel, hideLabel, onClick }: {
  open: boolean;
  showLabel: string;
  hideLabel: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontSize: 12,
        fontWeight: 500,
        color: "var(--accent-text)",
        background: "none",
        border: "none",
        cursor: "pointer",
        padding: 0,
        fontFamily: "inherit",
      }}
    >
      {open ? hideLabel : showLabel}
    </button>
  );
}
