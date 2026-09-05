/**
 * src/pages/ArchitecturePage.tsx
 *
 * Purpose:        Architecture view (build plan §39, §51) — layers, entry points,
 *                 data-access classes and a role table.
 * Responsibility: Render the `/architecture` payload; each component links to its
 *                 source file.
 */
import { api } from "../api/client";
import { useSource } from "../App";
import { Loading } from "../components/common";
import { useAsync } from "../hooks/useApi";

export function ArchitecturePage({ projectId }: { projectId: number }) {
  const arch = useAsync(() => api.architecture(projectId), [projectId]);
  const src = useSource();

  return (
    <div>
      <h1>Architecture</h1>
      <Loading loading={arch.loading} error={arch.error} data={arch.data}>
        {(a) => (
          <>
            <div className="grid">
              {Object.entries(a.layers).map(([layer, members]) => (
                <div className="card" key={layer}>
                  <h3 style={{ marginTop: 0 }}>{layer}</h3>
                  {members.map((m) => (
                    <div key={m} className="mono small">
                      {m}
                    </div>
                  ))}
                </div>
              ))}
            </div>

            <div className="row" style={{ gap: 24, alignItems: "flex-start" }}>
              <div>
                <h3>Entry points</h3>
                {a.entry_points.length ? a.entry_points.map((e) => <div key={e} className="mono small">{e}</div>) : <span className="muted small">none detected</span>}
              </div>
              <div>
                <h3>Data access</h3>
                {a.data_access.length ? a.data_access.map((e) => <div key={e} className="mono small">{e}</div>) : <span className="muted small">none</span>}
              </div>
              {a.external_integrations.length > 0 && (
                <div>
                  <h3>External integrations</h3>
                  {a.external_integrations.map((e) => <div key={e} className="mono small">{e}</div>)}
                </div>
              )}
            </div>

            <h2>Components</h2>
            <table>
              <thead>
                <tr>
                  <th>class</th>
                  <th>role</th>
                  <th>layer</th>
                  <th>why</th>
                </tr>
              </thead>
              <tbody>
                {a.components.map((c) => (
                  <tr key={c.name}>
                    <td className="mono">
                      {c.file ? (
                        <a href="#" onClick={(e) => { e.preventDefault(); src.open(c.file!, null); }}>
                          {c.name}
                        </a>
                      ) : (
                        c.name
                      )}
                    </td>
                    <td>
                      <span className="tag">{c.role}</span>
                    </td>
                    <td className="small">{c.layer}</td>
                    <td className="small muted">{c.evidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Loading>
    </div>
  );
}
