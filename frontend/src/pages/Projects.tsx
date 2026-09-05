/**
 * src/pages/Projects.tsx
 *
 * Purpose:        Project selection + creation (build plan §3, §49).
 * Responsibility: List indexed projects, create a new one from a path, and open
 *                 a project workspace. Shows last-indexed time.
 */
import { useState } from "react";
import { api } from "../api/client";
import { ErrorBox, Loading } from "../components/common";
import { useAction, useAsync } from "../hooks/useApi";
import { navigate } from "../router";

export function Projects() {
  const list = useAsync(() => api.listProjects(), []);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const create = useAction(api.createProject);

  return (
    <div style={{ maxWidth: 760 }}>
      <h1>Projects</h1>

      <Loading loading={list.loading} error={list.error} data={list.data}>
        {(projects) =>
          projects.length === 0 ? (
            <div className="muted">No projects yet — add one below.</div>
          ) : (
            <div className="grid">
              {projects.map((p) => (
                <div className="card" key={p.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/p/${p.id}`)}>
                  <strong>{p.name}</strong>
                  <div className="small mono muted" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                    {p.root_path}
                  </div>
                  <div className="small muted">
                    {p.last_indexed ? `indexed ${new Date(p.last_indexed).toLocaleString()}` : "not indexed"}
                  </div>
                </div>
              ))}
            </div>
          )
        }
      </Loading>

      <div className="card" style={{ marginTop: 20 }}>
        <h2 style={{ marginTop: 0 }}>Add a project</h2>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ flex: "0 0 200px" }}>
            <label>name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-service" />
          </div>
          <div style={{ flex: 1 }}>
            <label>source path (absolute or repo-relative)</label>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="projects/synthetic-test-project"
            />
          </div>
          <button
            disabled={create.pending || !name || !path}
            onClick={async () => {
              const p = await create.run(name, path);
              navigate(`/p/${p.id}`);
            }}
          >
            {create.pending ? "creating…" : "create"}
          </button>
        </div>
        {create.error && <ErrorBox message={create.error} />}
      </div>
    </div>
  );
}
