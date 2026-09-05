/**
 * src/api/client.ts
 *
 * Purpose:        Thin typed wrapper over the CodeXray FastAPI backend.
 * Responsibility: One function per endpoint the UI needs; consistent JSON error
 *                 handling (throws `ApiError` with the backend's detail); all
 *                 requests are same-origin `/api/...` (Vite proxies in dev).
 * Notes:          No caching / no state — components own that.
 */
import type {
  AgentResult, Architecture, AskResult, DynamicSqlSite, FileSource,
  GraphNeighbors, IndexReport, Project, ProjectStatus, SearchHit, SymbolHit,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body?.detail ?? body;
    const msg = typeof detail === "string" ? detail : detail?.message || `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, detail);
  }
  return body as T;
}

export const api = {
  listProjects: () => req<Project[]>("/projects"),
  createProject: (name: string, root_path: string) =>
    req<Project>("/projects", { method: "POST", body: JSON.stringify({ name, root_path }) }),
  index: (id: number, force = false) =>
    req<IndexReport>(`/projects/${id}/index`, { method: "POST", body: JSON.stringify({ force }) }),
  status: (id: number) => req<ProjectStatus>(`/projects/${id}/status`),

  search: (id: number, query: string, mode: string, limit = 20) =>
    req<SearchHit[] | SymbolHit[]>(`/projects/${id}/search`, {
      method: "POST", body: JSON.stringify({ query, mode, limit }),
    }),

  fileSource: (id: number, fileId: number) => req<FileSource>(`/projects/${id}/files/${fileId}/source`),
  files: (id: number, q?: string) =>
    req<{ id: number; path: string; language: string }[]>(`/projects/${id}/files${q ? `?q=${encodeURIComponent(q)}` : ""}`),

  ask: (id: number, question: string) =>
    req<AskResult>(`/projects/${id}/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  investigate: (id: number, question: string, llm_planning?: boolean) =>
    req<AgentResult>(`/projects/${id}/investigate`, {
      method: "POST", body: JSON.stringify({ question, llm_planning }),
    }),
  agentTools: () => req<{ name: string; description: string; args: Record<string, string> }[]>("/agent/tools"),
  llmHealth: () => req<{ provider: string; model: string; available: boolean; models_installed: string[] }>("/llm/health"),

  dynamicSql: (id: number) => req<DynamicSqlSite[]>(`/projects/${id}/dynamic-sql`),
  traceDynamicSql: (id: number, selector: string) =>
    req<{ selector: string; status: string; match_count: number; sites: DynamicSqlSite[] }>(
      `/projects/${id}/dynamic-sql/trace`, { method: "POST", body: JSON.stringify({ selector }) }),

  architecture: (id: number) => req<Architecture>(`/projects/${id}/architecture`),

  graph: (id: number, node: string) =>
    req<GraphNeighbors>(`/projects/${id}/graph?node=${encodeURIComponent(node)}`),
  callers: (id: number, symbol: string) =>
    req<{ symbol: string; callers: string[]; callees: string[] }>(
      `/projects/${id}/graph/callers?symbol=${encodeURIComponent(symbol)}`),
  impact: (id: number, symbol: string) =>
    req<Record<string, string[]>>(`/projects/${id}/impact-analysis`, {
      method: "POST", body: JSON.stringify({ symbol }),
    }),
};
