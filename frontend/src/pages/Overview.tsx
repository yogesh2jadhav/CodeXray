/**
 * src/pages/Overview.tsx
 *
 * Purpose:        Project dashboard + indexing control (build plan §49, §74).
 * Responsibility: Show index counts (files / classes / methods / SQL / dynamic
 *                 SQL / graph edges / semantic chunks / parse failures), the last
 *                 run, and a "Re-index" button that reports the IndexReport.
 */
import { api } from "../api/client";
import { ErrorBox, Loading, Spinner } from "../components/common";
import { useAction } from "../hooks/useApi";
import type { ProjectStatus } from "../api/types";

interface StatusHook {
  data: ProjectStatus | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function Overview({ projectId, status }: { projectId: number; status: StatusHook }) {
  const reindex = useAction((force: boolean) => api.index(projectId, force));

  return (
    <div style={{ maxWidth: 820 }}>
      <h1>Overview</h1>

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
