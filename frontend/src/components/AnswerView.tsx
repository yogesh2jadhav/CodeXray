/**
 * src/components/AnswerView.tsx
 *
 * Purpose:        Render an LLM answer as formatted Markdown (headings, bold,
 *                 code blocks, lists) with FACT / INFERENCE / UNKNOWN colouring
 *                 and a confidence badge (build plan §50, §72).
 * Responsibility: Header row (confidence + question type + latency) + the
 *                 <Markdown> body. The backend already parsed the labels; this
 *                 is purely presentation.
 */
import type { AskResult } from "../api/types";
import { Markdown } from "./Markdown";

export function AnswerView({ result }: { result: AskResult }) {
  const conf = result.confidence;
  return (
    <div className="card answer-card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Answer</h2>
        <span className="row small">
          <span className={`pill ${conf === "HIGH" ? "ok" : conf === "LOW" ? "off" : ""}`}>
            confidence: {conf}
          </span>
          <span className="tag">{result.classification.type}</span>
          {result.timings?.total_s != null && <span className="tag">{result.timings.total_s}s</span>}
        </span>
      </div>
      <Markdown text={result.answer} />
    </div>
  );
}
