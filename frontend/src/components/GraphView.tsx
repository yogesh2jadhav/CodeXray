/**
 * src/components/GraphView.tsx
 *
 * Purpose:        Lightweight dependency-graph view (build plan §51) — the
 *                 in/out edges of one node, grouped by edge type. No canvas /
 *                 d3; a readable adjacency list is enough for v1 and keeps the
 *                 bundle small.
 * Responsibility: Render `GraphNeighbors`; clicking a neighbour re-centres via
 *                 `onSelect`.
 */
import type { GraphNeighbors } from "../api/types";

export function GraphView({
  data,
  onSelect,
}: {
  data: GraphNeighbors;
  onSelect: (node: string) => void;
}) {
  if (!data.node) return <div className="muted small">Node not found in the graph.</div>;

  const incoming = data.edges.filter((e) => e.to === data.node);
  const outgoing = data.edges.filter((e) => e.from === data.node);
  const byType = (list: typeof data.edges, dir: "from" | "to") => {
    const g: Record<string, string[]> = {};
    for (const e of list) (g[e.type] ||= []).push(e[dir]);
    return g;
  };

  const Col = ({ title, groups }: { title: string; groups: Record<string, string[]> }) => (
    <div style={{ flex: 1, minWidth: 240 }}>
      <h3>{title}</h3>
      {Object.keys(groups).length === 0 && <div className="muted small">—</div>}
      {Object.entries(groups).map(([type, nodes]) => (
        <div key={type} style={{ marginBottom: 8 }}>
          <span className="tag">{type}</span>
          <div>
            {[...new Set(nodes)].map((n) => (
              <a
                key={n}
                href="#"
                className="mono small"
                style={{ display: "block", padding: "1px 0" }}
                onClick={(e) => {
                  e.preventDefault();
                  onSelect(n);
                }}
              >
                {n}
              </a>
            ))}
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div className="card">
      <h2 className="mono">
        {data.kind}: {data.node}
      </h2>
      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <Col title="⟵ used by / callers" groups={byType(incoming, "from")} />
        <Col title="depends on / calls ⟶" groups={byType(outgoing, "to")} />
      </div>
    </div>
  );
}
