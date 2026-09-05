/**
 * src/pages/InvestigatePage.tsx
 *
 * Purpose:        The Sprint 5 tool-calling agent (build plan §26-28, §60).
 * Responsibility: Ask a question, show the deterministic plan, the full tool
 *                 trace (each call, ok/error, args), the labelled answer and the
 *                 aggregated evidence. Toggle for LLM follow-up planning.
 */
import { useState } from "react";
import { api } from "../api/client";
import { useSource } from "../App";
import { AnswerView } from "../components/AnswerView";
import { ErrorBox, Spinner } from "../components/common";
import { EvidenceList } from "../components/Evidence";
import { useAction } from "../hooks/useApi";

export function InvestigatePage({ projectId }: { projectId: number }) {
  const [q, setQ] = useState("");
  const [llmPlanning, setLlmPlanning] = useState(true);
  const run = useAction((question: string) => api.investigate(projectId, question, llmPlanning));
  const src = useSource();

  return (
    <div>
      <h1>Agent</h1>
      <div className="card">
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. Trace this process from the entry point to the database."
        />
        <div className="row" style={{ marginTop: 8 }}>
          <button disabled={!q || run.pending} onClick={() => run.run(q)}>
            {run.pending ? "investigating…" : "Investigate"}
          </button>
          <label className="row small" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={llmPlanning}
              style={{ width: "auto" }}
              onChange={(e) => setLlmPlanning(e.target.checked)}
            />
            &nbsp;LLM follow-up tool calls
          </label>
        </div>
      </div>

      {run.pending && <Spinner label="planning → running read-only tools → synthesising…" />}
      {run.error && <ErrorBox message={run.error} />}

      {run.data && (
        <>
          <AnswerView result={run.data} />

          <div className="card">
            <h2 style={{ marginTop: 0 }}>
              Plan <span className="small muted">({run.data.iterations} LLM follow-up rounds)</span>
            </h2>
            <ol className="small">
              {run.data.plan.map((c, i) => (
                <li key={i}>
                  <span className="mono">{c.tool}</span>({JSON.stringify(c.args)}) — <span className="muted">{c.reason}</span>
                </li>
              ))}
            </ol>
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Tool trace</h2>
            {run.data.tool_trace.map((t, i) => (
              <details key={i} className="small">
                <summary>
                  <span className={t.ok ? "" : "err"}>{t.ok ? "✓" : "✗"}</span>{" "}
                  <span className="mono">{t.tool}</span> <span className="muted">{JSON.stringify(t.args)}</span>
                  {t.error ? <span className="err"> — {t.error}</span> : ""}
                </summary>
                <pre className="code">{JSON.stringify(t.data, null, 1)}</pre>
              </details>
            ))}
          </div>

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Evidence</h2>
            <EvidenceList items={run.data.evidence} onOpen={src.open} />
          </div>
        </>
      )}
    </div>
  );
}
