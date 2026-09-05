/**
 * src/pages/ChatPage.tsx
 *
 * Purpose:        One-shot Q&A over the project (build plan §50) — the Sprint 3
 *                 `/ask` pipeline: retrieved evidence + local LLM.
 * Responsibility: Ask a question, render the labelled answer + the structured
 *                 evidence list (clickable to source). Shows a 503 hint when the
 *                 local model is offline.
 */
import { useState } from "react";
import { api } from "../api/client";
import { useSource } from "../App";
import { AnswerView } from "../components/AnswerView";
import { ErrorBox, Spinner } from "../components/common";
import { EvidenceList } from "../components/Evidence";
import { useAction } from "../hooks/useApi";

const SAMPLES = [
  "Explain this project architecture.",
  "How is the table name for loadEligibleCustomers determined?",
  "Where is customer eligibility calculated?",
];

export function ChatPage({ projectId }: { projectId: number }) {
  const [q, setQ] = useState("");
  const ask = useAction((question: string) => api.ask(projectId, question));
  const src = useSource();

  return (
    <div>
      <h1>Chat</h1>
      <div className="card">
        <textarea value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a technical question about this project…" />
        <div className="row" style={{ marginTop: 8 }}>
          <button disabled={!q || ask.pending} onClick={() => ask.run(q)}>
            {ask.pending ? "thinking…" : "Ask"}
          </button>
          {SAMPLES.map((s) => (
            <button key={s} className="secondary small" onClick={() => setQ(s)}>
              {s.length > 34 ? s.slice(0, 34) + "…" : s}
            </button>
          ))}
        </div>
      </div>

      {ask.pending && <Spinner label="retrieving evidence + querying local model…" />}
      {ask.error && <ErrorBox message={ask.error} />}

      {ask.data && (
        <>
          <AnswerView result={ask.data} />
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Evidence</h2>
            <EvidenceList items={ask.data.evidence} onOpen={src.open} />
          </div>
          {(ask.data.unknowns.length > 0 || ask.data.inferences.length > 0) && (
            <div className="card small">
              {ask.data.inferences.length > 0 && (
                <>
                  <h3>Inferences</h3>
                  <ul>{ask.data.inferences.map((x, i) => <li key={i}>{x}</li>)}</ul>
                </>
              )}
              {ask.data.unknowns.length > 0 && (
                <>
                  <h3>Unknowns</h3>
                  <ul>{ask.data.unknowns.map((x, i) => <li key={i}>{x}</li>)}</ul>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
