/**
 * src/pages/DynamicSqlPage.tsx
 *
 * Purpose:        The dedicated Dynamic SQL viewer (build plan §52).
 * Responsibility: List every reconstructed construction site; a search box that
 *                 runs `trace_dynamic_sql(selector)` (Class.method / table / SQL
 *                 fragment). Each site renders as a full trace (template → deps →
 *                 metadata lookups → tables/columns → status).
 */
import { useState } from "react";
import { api } from "../api/client";
import { useSource } from "../App";
import { ErrorBox, Loading, Spinner, StatusBadge } from "../components/common";
import { DynamicSqlTrace } from "../components/DynamicSqlTrace";
import { useAction, useAsync } from "../hooks/useApi";

export function DynamicSqlPage({ projectId }: { projectId: number }) {
  const all = useAsync(() => api.dynamicSql(projectId), [projectId]);
  const [sel, setSel] = useState("");
  const trace = useAction((s: string) => api.traceDynamicSql(projectId, s));
  const src = useSource();

  const sites = trace.data?.sites ?? all.data ?? [];

  return (
    <div>
      <h1>Dynamic SQL</h1>
      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="text"
          value={sel}
          placeholder="trace: Class.method, table name, or SQL fragment"
          onChange={(e) => setSel(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sel && trace.run(sel)}
        />
        <button disabled={!sel || trace.pending} onClick={() => trace.run(sel)}>
          trace
        </button>
        {trace.data && (
          <button className="secondary" onClick={() => { trace.setData(null); setSel(""); }}>
            clear
          </button>
        )}
      </div>

      {trace.pending && <Spinner />}
      {trace.error && <ErrorBox message={trace.error} />}
      {trace.data && (
        <div className="card small">
          selector <span className="mono">{trace.data.selector}</span> — <StatusBadge status={trace.data.status} /> ·{" "}
          {trace.data.match_count} match(es)
        </div>
      )}

      <Loading loading={all.loading && !trace.data} error={all.error} data={sites}>
        {(list) =>
          list.length === 0 ? (
            <div className="muted">No dynamic SQL construction sites{trace.data ? " for this selector" : ""}.</div>
          ) : (
            <>{list.map((s) => <DynamicSqlTrace key={s.id ?? `${s.source_class}.${s.source_method}`} site={s} onOpen={src.open} />)}</>
          )
        }
      </Loading>
    </div>
  );
}
