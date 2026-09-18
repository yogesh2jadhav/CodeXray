/**
 * src/pages/Overview.tsx
 *
 * Purpose:        Project dashboard + indexing control (build plan §49, §74).
 * Responsibility: Show index counts (files / classes / methods / SQL / dynamic
 *                 SQL / graph edges / semantic chunks / parse failures), the last
 *                 run, and a "Re-index" button that reports the IndexReport.
 */
import { useState } from "react";
import { api } from "../api/client";
import { ErrorBox, Loading, Spinner } from "../components/common";
import { useAction, useAsync } from "../hooks/useApi";
import type { ProjectStatus } from "../api/types";

interface StatusHook {
  data: ProjectStatus | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function Overview({ projectId, status }: { projectId: number; status: StatusHook }) {
  const reindex = useAction((force: boolean) => api.index(projectId, force));
  const large = (status.data?.counts.classes ?? 0) > 300;
  const [pkg, setPkg] = useState("");
  const modules = useAsync(
    () => (large ? api.documentationModules(projectId) : Promise.resolve([])),
    [projectId, large],
  );

  return (
    <div style={{ maxWidth: 820 }}>
      <h1>Overview</h1>

      <div className="card small">
        <strong>Project documentation</strong> — an auto-generated summary (purpose, architecture,
        key classes, SQL/dynamic-SQL picture, config, dependencies, hotspots) built from this index —{" "}
        <a href={`/api/projects/${projectId}/documentation`}>Markdown</a> ·{" "}
        <a href={`/api/projects/${projectId}/documentation?llm=true`}>Markdown + LLM purpose paragraph</a> ·{" "}
        <a href={`/api/projects/${projectId}/documentation?format=json`} target="_blank" rel="noreferrer">JSON</a>

        {large && (
          <div style={{ marginTop: 8 }}>
            <div className="muted">
              {status.data?.counts.classes} classes — a single document can't show everything. Pick a
              module for a focused doc:
            </div>
            <div className="row" style={{ marginTop: 4 }}>
              <select value={pkg} onChange={(e) => setPkg(e.target.value)} style={{ maxWidth: 320 }}>
                <option value="">{modules.loading ? "loading modules…" : "choose a module"}</option>
                {(modules.data ?? []).map((m) => (
                  <option key={m.module} value={m.module}>
                    {m.module} ({m.class_count})
                  </option>
                ))}
              </select>
              {pkg && (
                <a href={`/api/projects/${projectId}/documentation?package=${encodeURIComponent(pkg)}`}>
                  download {pkg} doc
                </a>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="row" style={{ marginBottom: 12 }}>
        <button disabled={reindex.pending} onClick={async () => { await reindex.run(false); status.reload(); }}>
          {reindex.pending ? "indexing…" : "Re-index (incremental)"}
        </button>
        <button className="secondary" disabled={reindex.pending} onClick={async () => { await reindex.run(true); status.reload(); }}>
          Full re-index
        </button>
        {reindex.pending && <Spinner label="running analyzers…" />}
      </div>
      {reindex.error && <ErrorBox message={reindex.error} />}
      {reindex.data && (
        <div className="card small">
          indexed {reindex.data.files_indexed}/{reindex.data.files_total} · skipped {reindex.data.files_skipped} ·{" "}
          <span className={reindex.data.files_failed ? "err" : ""}>failed {reindex.data.files_failed}</span> · dynamic SQL{" "}
          {reindex.data.dynamic_sql_sites} · graph edges {reindex.data.graph_edges} · semantic chunks{" "}
          {reindex.data.semantic_chunks}
          {reindex.data.failures.length > 0 && (
            <ul>
              {reindex.data.failures.map((f, i) => (
                <li key={i} className="err mono">
                  {f.file}: {f.error}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <Loading loading={status.loading} error={status.error} data={status.data}>
        {(s) => (
          <>
            <div className="card">
              <div className="small mono muted">{s.project.root_path}</div>
              <div className="small muted">
                {s.project.last_indexed ? `last indexed ${new Date(s.project.last_indexed).toLocaleString()}` : "never indexed"}
              </div>
            </div>
            <div className="grid">
              {Object.entries(s.counts).map(([k, v]) => (
                <div className="card" key={k} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 24, fontWeight: 700 }} className={k === "parse_failures" && v > 0 ? "err" : ""}>
                    {v}
                  </div>
                  <div className="small muted">{k.replace(/_/g, " ")}</div>
                </div>
              ))}
            </div>
            {s.last_run && (
              <>
                <h2>Last analysis run</h2>
                <pre className="code">{JSON.stringify(s.last_run, null, 2)}</pre>
              </>
            )}
          </>
        )}
      </Loading>
    </div>
  );
}
