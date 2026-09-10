/**
 * src/pages/GraphPage.tsx
 *
 * Purpose:        Dependency-graph explorer + impact analysis (build plan §41, §51).
 * Responsibility: Enter a node (Class, Class.method, TABLE, file); show its
 *                 in/out edges (GraphView) and, for a symbol, the impact-analysis
 *                 buckets. Clicking a neighbour re-centres.
 */
import { useState } from "react";
import { api } from "../api/client";
import { ErrorBox, Spinner } from "../components/common";
import { GraphView } from "../components/GraphView";
import { useAction } from "../hooks/useApi";

export function GraphPage({ projectId }: { projectId: number }) {
  const [node, setNode] = useState("");
  const g = useAction((n: string) => api.graph(projectId, n));
  const impact = useAction((n: string) => api.impact(projectId, n));

  const go = (n: string) => {
    setNode(n);
    g.run(n);
    impact.run(n).catch(() => {});
  };

  const exportUrl = (fmt: string) => `/api/projects/${projectId}/graph/export?format=${fmt}`;

  return (
    <div>
      <h1>Dependency Graph</h1>

      <div className="card small">
        <strong>Export the whole graph</strong> (modules · packages · classes · methods · tables · SQL) —{" "}
        <a href={exportUrl("cypher")}>Neo4j (.cypher)</a> ·{" "}
        <a href={exportUrl("graphml")}>GraphML (Gephi/yEd)</a> ·{" "}
        <a href={exportUrl("dot")}>Graphviz (.dot)</a> ·{" "}
        <a href={exportUrl("json")} target="_blank" rel="noreferrer">cytoscape JSON</a>
        <div className="muted" style={{ marginTop: 4 }}>
          Neo4j: start an instance (Neo4j Desktop or Community — no Docker needed), then{" "}
          <span className="mono">
            scripts/export_graph.py --project X --format cypher --load --uri neo4j://127.0.0.1:7687 --password …
          </span>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="text"
          value={node}
          placeholder="CustomerService.loadEligibleCustomers  ·  CUSTOMER  ·  MetadataService"
          onChange={(e) => setNode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && node && go(node)}
        />
        <button disabled={!node || g.pending} onClick={() => go(node)}>
          explore
        </button>
      </div>

      {(g.pending || impact.pending) && <Spinner />}
      {g.error && <ErrorBox message={g.error} />}

      {g.data && <GraphView data={g.data} onSelect={go} />}

      {impact.data && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Impact analysis</h2>
          <table>
            <tbody>
              {Object.entries(impact.data).map(([bucket, vals]) =>
                bucket === "target" ? null : (
                  <tr key={bucket}>
                    <th style={{ whiteSpace: "nowrap" }}>{bucket.replace(/_/g, " ")}</th>
                    <td className="mono small">
                      {Array.isArray(vals) && vals.length ? (vals as string[]).join(", ") : <span className="muted">—</span>}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
