/**
 * src/pages/SearchPage.tsx
 *
 * Purpose:        Multi-mode code search (build plan §49) — keyword / symbol /
 *                 sql / file / semantic / hybrid.
 * Responsibility: Fire the query, render a normalised result list, and let the
 *                 user open a hit in the source panel.
 */
import { useState } from "react";
import { api } from "../api/client";
import { useSource } from "../App";
import { ErrorBox, Spinner } from "../components/common";
import { useAction } from "../hooks/useApi";

const MODES = ["hybrid", "keyword", "symbol", "sql", "semantic", "file"];

export function SearchPage({ projectId }: { projectId: number }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("hybrid");
  const search = useAction((query: string, m: string) => api.search(projectId, query, m, 25));
  const src = useSource();

  const rows = ((search.data as unknown) as Record<string, unknown>[] | null) ?? [];

  const field = (r: Record<string, unknown>, keys: string[]) => {
    for (const k of keys) if (r[k] != null) return r[k] as string | number;
    return undefined;
  };

  return (
    <div>
      <h1>Search</h1>
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 130 }} value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
        <input
          type="text"
          value={q}
          placeholder="e.g. where is customer eligibility calculated"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && q && search.run(q, mode)}
          style={{ flex: 1 }}
        />
        <button disabled={!q || search.pending} onClick={() => search.run(q, mode)}>
          search
        </button>
      </div>

      {search.pending && <Spinner />}
      {search.error && <ErrorBox message={search.error} />}

      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>type</th>
              <th>symbol / label</th>
              <th>file</th>
              <th>score / detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const file = field(r, ["file", "path"]) as string | undefined;
              const line = field(r, ["line", "line_number", "line_start"]) as number | undefined;
              return (
                <tr key={i}>
                  <td>
                    <span className="tag">{field(r, ["type", "kind", "chunk_type"]) ?? mode}</span>
                  </td>
                  <td className="mono">{field(r, ["symbol", "qualified", "label", "name"]) ?? "—"}</td>
                  <td className="mono small">
                    {file ? (
                      <a href="#" onClick={(e) => { e.preventDefault(); src.open(file, line ?? null); }}>
                        {file}
                        {line ? `:${line}` : ""}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="small muted">
                    {field(r, ["score"]) != null ? `${Number(field(r, ["score"])).toFixed(2)} ` : ""}
                    {(field(r, ["detail", "snippet", "raw_sql"]) as string | undefined)?.slice(0, 120)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {search.data && rows.length === 0 && <div className="muted">No results.</div>}
    </div>
  );
}
