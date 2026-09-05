/**
 * src/components/Evidence.tsx
 *
 * Purpose:        Render the structured evidence list from an ask/investigate
 *                 result (build plan §50) — clicking an item opens the source.
 * Responsibility: List {kind, detail, file, line}; emit `onOpen(file, line)` so
 *                 the parent can show the CodeViewer.
 */
import type { Evidence } from "../api/types";

export function EvidenceList({
  items,
  onOpen,
}: {
  items: Evidence[];
  onOpen: (file: string, line: number | null) => void;
}) {
  if (!items.length) return <div className="muted small">No evidence collected.</div>;
  return (
    <div>
      {items.map((e, i) => (
        <div className="evidence-item" key={i}>
          <span className="kind">{e.kind}</span>
          <span style={{ flex: 1 }}>{e.detail}</span>
          {e.file && (
            <a
              href="#"
              className="mono small"
              onClick={(ev) => {
                ev.preventDefault();
                onOpen(e.file!, e.line);
              }}
            >
              {e.file}
              {e.line ? `:${e.line}` : ""}
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
