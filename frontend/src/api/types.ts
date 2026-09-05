/**
 * src/api/types.ts
 *
 * Purpose:        Shared TypeScript shapes for the CodeXray API responses.
 * Responsibility: Mirror the FastAPI payloads the UI consumes (projects, status,
 *                 search hits, evidence, dynamic-SQL sites, agent results,
 *                 architecture). Kept intentionally loose where the backend
 *                 returns pass-through dicts.
 */

export interface Project {
  id: number;
  name: string;
  root_path: string;
  created_at?: string;
  last_indexed?: string | null;
}

export interface ProjectStatus {
  project: Project;
  last_run: Record<string, unknown> | null;
  counts: Record<string, number>;
}

export interface IndexReport {
  project_id: number;
  files_total: number;
  files_indexed: number;
  files_skipped: number;
  files_failed: number;
  dynamic_sql_sites: number;
  graph_edges: number;
  semantic_chunks: number;
  architecture_components: number;
  failures: { file: string; error: string }[];
}

export interface Evidence {
  kind: string;
  detail: string;
  file: string | null;
  line: number | null;
}

export interface SymbolHit {
  kind: string;
  name: string;
  qualified: string;
  file: string;
  line_number: number | null;
  score?: number;
}

export interface SearchHit {
  type?: string;
  label?: string;
  symbol?: string;
  file?: string | null;
  line?: number | null;
  score?: number;
  detail?: string;
  signals?: string[];
}

export interface DynDependency {
  ordinal: number;
  dependency_type: string;
  source_type: string | null;
  value: string | null;
  resolution_status: string;
  evidence: {
    detail: string;
    file?: string | null;
    line?: number | null;
    metadata_sql?: string;
    metadata_tables?: string[];
  }[];
}

export interface DynamicSqlSite {
  id: number;
  source_class: string;
  source_method: string;
  source_var: string;
  file: string;
  line: number | null;
  sql_template: string;
  resolved_sql: string | null;
  status: string;
  confidence: number;
  tables: string[];
  columns: string[];
  dependencies: DynDependency[];
}

export interface AskResult {
  question: string;
  answer: string;
  facts: string[];
  inferences: string[];
  unknowns: string[];
  cited_refs: { file: string; line: number | null }[];
  confidence: string;
  classification: { type: string; retrieval_modes: string[]; symbols: string[]; tables: string[] };
  evidence: Evidence[];
  llm: Record<string, unknown>;
  timings: Record<string, number>;
}

export interface ToolTrace {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  data: unknown;
  evidence: Evidence[];
  error: string | null;
}

export interface AgentResult extends AskResult {
  plan: { tool: string; args: Record<string, unknown>; reason: string }[];
  tool_trace: ToolTrace[];
  iterations: number;
}

export interface Architecture {
  project: string;
  layers: Record<string, string[]>;
  entry_points: string[];
  data_access: string[];
  external_integrations: string[];
  components: { name: string; fqn: string | null; role: string; layer: string; file: string | null; evidence: string }[];
}

export interface GraphNeighbors {
  node: string | null;
  kind?: string;
  edges: { from: string; to: string; type: string }[];
}

export interface FileSource {
  path: string;
  language: string;
  content: string;
}
