/**
 * src/App.tsx
 *
 * Purpose:        App shell + routing + the shared "open source" overlay.
 * Responsibility:
 *   - Top bar (brand, LLM health pill).
 *   - Hash routes: "/" projects list; "/p/:id/*" the project workspace with a
 *     side-nav (Overview / Search / Chat / Agent / Dynamic SQL / Architecture /
 *     Graph — build plan §62).
 *   - `SourceContext.open(file, line)` renders a <CodeViewer> panel any page can
 *     trigger from an evidence link.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api/client";
import { CodeViewer } from "./components/CodeViewer";
import { useAsync } from "./hooks/useApi";
import { Link, segments, useRoute } from "./router";
import { ArchitecturePage } from "./pages/ArchitecturePage";
import { ChatPage } from "./pages/ChatPage";
import { DynamicSqlPage } from "./pages/DynamicSqlPage";
import { GraphPage } from "./pages/GraphPage";
import { InvestigatePage } from "./pages/InvestigatePage";
import { Overview } from "./pages/Overview";
import { Projects } from "./pages/Projects";
import { SearchPage } from "./pages/SearchPage";

interface SourceApi {
  open: (file: string, line: number | null) => void;
}
const SourceContext = createContext<SourceApi>({ open: () => {} });
export const useSource = () => useContext(SourceContext);

const TABS: [string, string][] = [
  ["", "Overview"],
  ["search", "Search"],
  ["chat", "Chat"],
  ["agent", "Agent"],
  ["dynamic-sql", "Dynamic SQL"],
  ["architecture", "Architecture"],
  ["graph", "Graph"],
];

function LlmPill() {
  const { data } = useAsync(() => api.llmHealth(), []);
  if (!data) return <span className="pill">llm …</span>;
  return (
    <span className={`pill ${data.available ? "ok" : "off"}`} title={data.models_installed?.join(", ")}>
      {data.provider}:{data.model} {data.available ? "●" : "○"}
    </span>
  );
}

function ProjectWorkspace({ projectId, tab }: { projectId: number; tab: string }) {
  const status = useAsync(() => api.status(projectId), [projectId]);
  const name = status.data?.project?.name ?? `project ${projectId}`;

  return (
    <div className="body">
      <nav className="sidenav">
        <div className="small muted" style={{ padding: "4px 10px 10px" }}>
          <Link to="/">← projects</Link>
        </div>
        <div style={{ padding: "0 10px 8px", fontWeight: 600 }}>{name}</div>
        {TABS.map(([slug, label]) => (
          <Link
            key={slug}
            to={`/p/${projectId}${slug ? `/${slug}` : ""}`}
            className={tab === slug ? "active" : ""}
          >
            {label}
          </Link>
        ))}
      </nav>
      <div className="content">
        {tab === "" && <Overview projectId={projectId} status={status} />}
        {tab === "search" && <SearchPage projectId={projectId} />}
        {tab === "chat" && <ChatPage projectId={projectId} />}
        {tab === "agent" && <InvestigatePage projectId={projectId} />}
        {tab === "dynamic-sql" && <DynamicSqlPage projectId={projectId} />}
        {tab === "architecture" && <ArchitecturePage projectId={projectId} />}
        {tab === "graph" && <GraphPage projectId={projectId} />}
      </div>
    </div>
  );
}

export function App() {
  const route = useRoute();
  const seg = segments(route);
  const [src, setSrc] = useState<{ file: string; line: number | null } | null>(null);
  // Close the source panel whenever the tab / project changes.
  useEffect(() => setSrc(null), [seg[1], seg[2]]);

  let view: ReactNode;
  if (seg[0] === "p" && seg[1]) {
    view = <ProjectWorkspace projectId={Number(seg[1])} tab={seg[2] ?? ""} />;
  } else {
    view = (
      <div className="content">
        <Projects />
      </div>
    );
  }

  return (
    <SourceContext.Provider value={{ open: (file, line) => setSrc({ file, line }) }}>
      <div className="app">
        <div className="topbar">
          <span className="brand">
            Code<span>Xray</span>
          </span>
          <span className="small muted">local project intelligence</span>
          <span className="spacer" />
          <LlmPill />
        </div>
        {view}
        {src && seg[0] === "p" && seg[1] && (
          <div style={{ position: "fixed", right: 0, bottom: 0, top: 49, width: "46%", overflow: "auto", background: "var(--bg)", borderLeft: "1px solid var(--border)", padding: 14 }}>
            <CodeViewer projectId={Number(seg[1])} file={src.file} line={src.line} onClose={() => setSrc(null)} />
          </div>
        )}
      </div>
    </SourceContext.Provider>
  );
}
