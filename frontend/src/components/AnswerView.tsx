/**
 * src/components/AnswerView.tsx
 *
 * Purpose:        Render an LLM answer with FACT / INFERENCE / UNKNOWN lines
 *                 highlighted and a confidence badge (build plan §50, §72).
 * Responsibility: Light client-side formatting only — colour the label prefixes,
 *                 keep everything else as-is (the backend already parsed the
 *                 labels; this is purely visual).
 */
import type { AskResult } from "../api/types";

const LABEL_RE = /^(\s*)(FACT|INFERENCE|UNKNOWN)(\s*:)/;

function renderLine(line: string, key: number) {
  const m = line.match(LABEL_RE);
  if (!m) return <div key={key}>{line || " "}</div>;
  return (
    <div key={key}>
      {m[1]}
      <span className={`label-${m[2]}`}>{m[2]}</span>
      {m[3]}
      {line.slice(m[0].length)}
    </div>
  );
}

export function AnswerView({ result }: { result: AskResult }) {
  const conf = result.confidence;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Answer</h2>
        <span className="row small">
          <span className={`pill ${conf === "HIGH" ? "ok" : conf === "LOW" ? "off" : ""}`}>
            confidence: {conf}
          </span>
          <span className="tag">{result.classification.type}</span>
          {result.timings?.total_s != null && <span className="tag">{result.timings.total_s}s</span>}
        </span>
      </div>
      <div className="answer">{result.answer.split("\n").map(renderLine)}</div>
    </div>
  );
}
