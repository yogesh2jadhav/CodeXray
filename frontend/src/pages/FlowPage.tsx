/**
 * src/pages/FlowPage.tsx
 *
 * Purpose:        Recursive call-flow explorer (build plan §43, §83).
 * Responsibility: Take a `Class.method` entry point + a depth, fetch the
 *                 interprocedural call tree, and render it as a collapsible tree
 *                 — each node shows the method, its one-line purpose, the SQL /
 *                 tables it touches, and a link to source. Recursion and
 *                 depth/size truncation are shown, not hidden.
 */
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { FlowStep } from "../api/types";
import { useSource } from "../App";
import { ErrorBox, Spinner } from "../components/common";
import { useAction } from "../hooks/useApi";

interface Node extends FlowStep {
  children: Node[];
}

function nest(steps: FlowStep[]): Node[] {
  const roots: Node[] = [];
  const stack: Node[] = [];
  for (const s of steps) {
    const node: Node = { ...s, children: [] };
    while (stack.length && stack[stack.length - 1].depth >= s.depth) stack.pop();
    if (stack.length) stack[stack.length - 1].children.push(node);
    else roots.push(node);
    stack.push(node);
  }
  return roots;
}

function SqlBadges({ sql }: { sql: FlowStep["sql"] }) {
  const items: [string, string[]][] = [
    ["reads", sql.reads],
    ["writes", sql.writes],
    ["metadata", sql.metadata_tables],
  ];
  const any = items.some(([, v]) => v.length) || sql.sql_kinds.length;
  if (!any) return null;
  return (
    <span className="small" style={{ marginLeft: 6 }}>
      {items.map(([label, vals]) =>
        vals.length ? (
          <span key={label} className="tag" title={`${label}: ${vals.join(", ")}`}>
            {label} {vals.join(",")}
          </span>
        ) : null,
      )}
      {sql.sql_kinds.map((k) => (
        <span key={k} className="tag">{k} SQL</span>
      ))}
    </span>
  );
}

function TreeNode({ node, depth }: { node: Node; depth: number }) {
  const [open, setOpen] = useState(depth < 3);
  const src = useSource();
  const hasKids = node.children.length > 0;
  return (
    <div style={{ marginLeft: depth ? 16 : 0 }}>
      <div className="flow-node">
        <span
          className="flow-toggle"
          onClick={() => hasKids && setOpen((o) => !o)}
          style={{ cursor: hasKids ? "pointer" : "default", opacity: hasKids ? 1 : 0.25 }}
        >
          {hasKids ? (open ? "▾" : "▸") : "•"}
        </span>
        <span className="mono" style={{ fontWeight: 600 }}>{node.method}</span>
        {node.recursion && <span className="tag" style={{ color: "var(--warn)" }}>recursion</span>}
        <SqlBadges sql={node.sql} />
        {node.file && (
          <a
            href="#"
            className="mono small"
            style={{ marginLeft: 6 }}
            onClick={(e) => { e.preventDefault(); src.open(node.file!, node.line); }}
          >
            {node.file.split("/").pop()}:{node.line}
          </a>
        )}
        {node.purpose && <div className="flow-purpose small muted">{node.purpose}</div>}
      </div>
      {open && node.children.map((c, i) => <TreeNode key={`${c.method}-${i}`} node={c} depth={depth + 1} />)}
    </div>
  );
}

export function FlowPage({ projectId }: { projectId: number }) {
  const [method, setMethod] = useState("");
  const [depth, setDepth] = useState(4);
  const run = useAction((m: string, d: number) => api.flow(projectId, m, d));
  const tree = useMemo(() => (run.data ? nest(run.data.steps) : []), [run.data]);

  return (
    <div>
      <h1>Flow</h1>
      <div className="card small muted">
        Enter a starting method (<span className="mono">Class.method</span> or just the method name).
        CodeXray walks every nested project method it calls.
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="text"
          value={method}
          placeholder="CustomerService.loadEligibleCustomers"
          onChange={(e) => setMethod(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && method && run.run(method, depth)}
          style={{ flex: 1 }}
        />
        <label className="small" style={{ margin: 0 }}>
          depth
          <input
            type="number"
            min={1}
            max={10}
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            style={{ width: 56, marginLeft: 6 }}
          />
        </label>
        <button disabled={!method || run.pending} onClick={() => run.run(method, depth)}>
          trace
        </button>
      </div>

      {run.pending && <Spinner />}
      {run.error && <ErrorBox message={run.error} />}

      {run.data && !run.data.found && (
        <ErrorBox message={`Method "${method}" not found in the call graph. Try the fully-qualified Class.method.`} />
      )}

      {run.data?.found && (
        <div className="card">
          <div className="row small muted" style={{ justifyContent: "space-between" }}>
            <span>
              root <span className="mono">{run.data.root}</span> · {run.data.step_count} methods · depth cap{" "}
              {run.data.max_depth}
            </span>
            {(run.data.truncated.depth || run.data.truncated.size) && (
              <span className="err">
                bounded — deeper/other calls exist beyond this
                {run.data.truncated.size ? " (method cap hit — raise flow.max_methods or narrow the trace)" : ""}
              </span>
            )}
          </div>
          <div style={{ marginTop: 10 }}>
            {tree.map((n, i) => <TreeNode key={`${n.method}-${i}`} node={n} depth={0} />)}
          </div>
        </div>
      )}
    </div>
  );
}
