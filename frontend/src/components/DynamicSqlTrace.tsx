/**
 * src/components/DynamicSqlTrace.tsx
 *
 * Purpose:        Visualise one reconstructed dynamic-SQL site (build plan §52):
 *                 source method -> template -> dependencies (constants / metadata
 *                 lookups / parameters) -> tables / columns -> status.
 * Responsibility: Pure rendering of a `DynamicSqlSite`; `onOpen` for source jumps.
 */
import type { DynamicSqlSite } from "../api/types";
import { StatusBadge } from "./common";

export function DynamicSqlTrace({
  site,
  onOpen,
}: {
  site: DynamicSqlSite;
  onOpen: (file: string, line: number | null) => void;
}) {
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong className="mono">
          {site.source_class}.{site.source_method}
        </strong>
        <span className="small">
          <StatusBadge status={site.status} /> · confidence {site.confidence} ·{" "}
          <a
            href="#"
            className="mono"
            onClick={(e) => {
              e.preventDefault();
              onOpen(site.file, site.line);
            }}
          >
            {site.file}:{site.line}
          </a>
        </span>
      </div>

      <h3>Template</h3>
      <pre className="code">{site.sql_template}</pre>
      {site.resolved_sql && (
        <>
          <h3>Resolved SQL</h3>
          <pre className="code">{site.resolved_sql}</pre>
        </>
      )}

      <h3>Dependencies</h3>
      {site.dependencies.map((d, i) => (
        <div className={`dep ${d.dependency_type}`} key={i}>
          <span className="tag">{d.dependency_type}</span>
          <span className={`status-${d.resolution_status} small`}>{d.resolution_status}</span>{" "}
          <span className="mono">{d.value}</span>
          {d.evidence
            .filter((e) => e.metadata_sql)
            .map((e, j) => (
              <div key={j} className="small muted" style={{ marginLeft: 12 }}>
                ↳ metadata: <span className="mono">{e.metadata_sql}</span>
                {e.metadata_tables?.length ? ` → ${e.metadata_tables.join(", ")}` : ""}
              </div>
            ))}
        </div>
      ))}

      <h3>Tables / Columns</h3>
      <div className="small">
        <span className="muted">tables:</span> {site.tables.join(", ") || "—"}
        <span className="muted" style={{ marginLeft: 16 }}>
          columns:
        </span>{" "}
        {site.columns.join(", ") || "—"}
      </div>
    </div>
  );
}
