# CodeXray — Local Project Intelligence AI

CodeXray is a **fully local** AI system that understands large enterprise Java + SQL
codebases and answers hard technical questions like a technical lead who has studied
the project. It combines deterministic static analysis (Java AST, SQL parsing, symbol
and reference indexing, dynamic-SQL reconstruction, dependency graph) with semantic
retrieval and a **local** LLM (Ollama). The LLM is the reasoning layer only — never the
source of truth.

> Build plan: `docs/Local_Project_Intelligence_AI_Full_Build_Plan.md` (source of truth for scope).

## Status — Sprint 1 (Repository Indexer) ✅

Implemented in this iteration:

| Component | File | Purpose |
|-----------|------|---------|
| File scanner | `backend/app/indexing/file_scanner.py` | Walk a project, classify files, hash them, skip junk/`.venv`/build dirs |
| Java parser | `backend/app/analyzers/java/java_parser.py` | AST extraction (tree-sitter, regex fallback) of packages, classes, methods, imports, calls, string constants |
| SQL parser | `backend/app/analyzers/sql/sql_parser.py` | Parse SQL (sqlglot) → type, tables, columns, joins, filters, params |
| SQL extractor | `backend/app/indexing/sql_extractor.py` | Find SQL literals inside Java + `.sql` files and hand them to the SQL parser |
| Symbol index | `backend/app/indexing/symbol_extractor.py` | Build searchable symbols (class / method / package / field) |
| Index writer | `backend/app/indexing/index_writer.py` | Persist everything to SQLite |
| Indexer | `backend/app/indexing/indexer.py` | Orchestrates a full/incremental index run, records failures, never aborts on one bad file |
| Search | `backend/app/retrieval/search.py` | Symbol / keyword / file / SQL / table search over the index |
| API | `backend/app/api/routes.py` | FastAPI endpoints for projects, indexing, files, symbols, SQL, search |
| Schema | `backend/app/models/schema.sql` | Relational data model (§20/§21 of the plan) |

## Status — Sprint 2 (Dynamic SQL Analyzer) ✅

| Component | File | Purpose |
|-----------|------|---------|
| Expression IR | `backend/app/analyzers/dynamic_sql/expression.py` | `StringExpr` / `Part` — reconstructed-SQL parts, each with its own status + evidence; renders a `{slot}` template |
| Project model | `backend/app/analyzers/dynamic_sql/project_model.py` | Cross-file constant + method + field-type index (tree-sitter) for interprocedural resolution |
| Evaluator | `backend/app/analyzers/dynamic_sql/evaluator.py` | Recursive expr engine: literals, `+`, identifiers, `Class.CONST`, `String.join`, StringBuilder, method-call inlining (depth-bounded) |
| Resolvers | `backend/app/analyzers/dynamic_sql/resolvers.py` | Named facades: Constant / Variable / MethodReturn / StringConcatenation / StringBuilder / DependencyBuilder |
| Metadata detector | `backend/app/analyzers/metadata/metadata_detector.py` | Recognises a method that runs `SELECT … FROM <registry>` and returns a table/column value (Patterns C/D) |
| Template resolver | `backend/app/analyzers/dynamic_sql/sql_template_resolver.py` | Parts → template + resolved SQL (iff fully known) + tables/columns + confidence |
| Analyzer | `backend/app/analyzers/dynamic_sql/analyzer.py` | Whole-project driver; `trace()` backs the `trace_dynamic_sql` tool |

Wired into the indexer (whole-project stage after per-file indexing), persisted to
`dynamic_sql` / `dynamic_sql_dependencies`, exposed at
`GET/POST /api/projects/{id}/dynamic-sql[/trace]`.

## Status — Sprint 3 (Local LLM) ✅

| Component | File | Purpose |
|-----------|------|---------|
| Provider abstraction | `backend/app/llm/provider.py` | `LLMProvider` interface + `get_provider()` factory (build plan §30) |
| Ollama provider | `backend/app/llm/ollama_provider.py` | local `/api/chat`, CPU, no CUDA; `LLMUnavailable` → 503 with fix-it text |
| Echo provider | `backend/app/llm/echo_provider.py` | offline deterministic stub — echoes the assembled evidence, used by tests/CI |
| Question classifier | `backend/app/llm/question_classifier.py` | type + retrieval modes (§24), symbol/table extraction — no LLM call |
| Context builder | `backend/app/llm/context_builder.py` | structured evidence sections + `ContextBudgetManager` (§25, §71) |
| Prompt builder | `backend/app/llm/prompt_builder.py` | technical-lead system prompt, FACT/INFERENCE/UNKNOWN, "never invent a table" (§72) |
| Response parser | `backend/app/llm/response_parser.py` | label split, cited `file:line`, confidence |
| Ask service | `backend/app/llm/client.py` | classify → context → prompt → provider → parse |

`POST /api/projects/{id}/ask` · `GET /api/llm/health`. Model configurable via
`config/config.yaml` (`llm.*`) or `CODEXRAY_LLM_*`. `CODEXRAY_LLM_PROVIDER=echo`
gives an offline evidence-only response.

## Status — Sprint 4 (Graph + Semantic Retrieval) ✅

| Component | File | Purpose |
|-----------|------|---------|
| Graph builder | `backend/app/graph/builder.py` | SQLite index → typed edges in `dependencies` (§57) |
| Graph service | `backend/app/graph/service.py` | NetworkX; callers/callees/path/table-consumers/class-deps/**impact_analysis** (§19, §41) |
| Chunker | `backend/app/retrieval/chunker.py` | semantic units — class/method/sql/dynamic_sql/config/doc (§35) |
| Embeddings | `backend/app/retrieval/embeddings.py` | `OllamaEmbedder` (nomic-embed-text) + offline `HashingEmbedder` (§33) |
| Vector store | `backend/app/retrieval/vector_store.py` | `SqliteVectorStore` — float32 BLOBs + numpy cosine (§34) |
| Semantic index | `backend/app/retrieval/semantic.py` | build + search (§23, §58) |
| Hybrid search | `backend/app/retrieval/hybrid.py` | symbol + keyword + semantic + graph proximity, weighted (§24, §36) |
| Architecture | `backend/app/analyzers/architecture/extractor.py` | roles/layers, `architecture.json` + `.md` (§39) |

API: `/graph`, `/graph/callers`, `/graph/path`, `/impact-analysis`, `/architecture`
(`?format=md`), `/search` modes `semantic`+`hybrid`, `/semantic/status`. All three
build as indexer post-passes. `CODEXRAY_EMBEDDING_PROVIDER=hashing` runs offline.

**Graph export** — the whole graph (modules · packages · classes · methods · tables · SQL)
to **Neo4j** (`.cypher`), GraphML, Graphviz `.dot`, or cytoscape JSON:
`scripts/export_graph.py --project X --format cypher [--load]`,
`GET /api/projects/{id}/graph/export?format=…`, or the **Graph** tab's export links.
`docker compose up -d neo4j` for a local instance. See `docs/GRAPH_EXPORT.md`.

## Status — Sprint 5 (Investigation Agent) ✅

| Component | File | Purpose |
|-----------|------|---------|
| Tools | `backend/app/agents/tools.py` | 19 read-only tools wrapping search / graph / dynamic-SQL / architecture / semantic (§26, §28) |
| Planner | `backend/app/agents/planner.py` | deterministic §27-style seed tool sequence per question type |
| Agent | `backend/app/agents/agent.py` | classify → plan → run tools → optional LLM tool loop (`agent.max_iterations`) → synthesise → parse (§60) |

`POST /api/projects/{id}/investigate` returns the answer + the plan + the full
tool trace + aggregated evidence. `GET /api/agent/tools` lists the catalogue.
The agent degrades to its deterministic seed plan when the model can't drive the
loop, and never fabricates tool output.

## Status — Sprint 6 (Web UI) ✅

React + TypeScript + Vite under `frontend/` — no UI framework, ~52 kB gzipped.

```bash
cd frontend && npm install && npm run dev     # http://localhost:5173 (proxies /api → :8000)
npm run build                                  # static bundle in frontend/dist/
```

Screens: Projects · Overview (counts + re-index) · Search (keyword/symbol/sql/semantic/hybrid) ·
Chat (`/ask` + evidence) · Agent (`/investigate` — plan, tool trace, evidence) ·
Dynamic SQL viewer (§52) · Architecture (§39) · Graph + impact (§41). Click any
`file:line` to open the source panel, scrolled and highlighted (§50).

## Status — Sprint 7 (Evaluation Framework) ✅

`eval/` + `scripts/run_eval.py` — a ground-truth benchmark for guarding regressions
and comparing local models (build plan §66-68).

```bash
python scripts/run_eval.py --project synthetic --mode retrieval               # deterministic, fast, model-free
python scripts/run_eval.py --project synthetic --mode ask   --model qwen2.5-coder:7b
python scripts/run_eval.py --project synthetic --mode agent --compare qwen2.5-coder:7b,qwen3:8b
```

- `eval/dataset/synthetic.yaml` — 60 cases across 7 categories, each with expected
  evidence + structured facts + answer content + **forbidden** content
- Metrics: retrieval / evidence / sql-resolution / dependency accuracy,
  answer_correctness, label_adherence, **hallucination_rate**, latency
- Baseline (`docs/EVAL_BASELINE.md`): 60/60, all deterministic metrics 1.00;
  `qwen2.5-coder:7b` on dynamic-SQL cases → 0.00 hallucination

## Answer modes (Chat & Agent)

Phrase the question and CodeXray adapts the format:

| Ask like this | You get |
|---|---|
| "Explain `X` **line by line**" | the full method reproduced with `/* … */` comments above each block |
| "**Trace the flow** of `X` **end to end**" / "follow the nested calls" | `GraphService.call_tree` walks every nested *project* method (JDBC/stdlib filtered), and the LLM narrates the whole flow step-by-step + an arrow diagram + which tables are read/written + open questions. Bounded by `flow.max_depth`/`flow.max_methods`; truncation is always stated. |
| anything else | the normal structured or agentic answer |

Deep enterprise flows: the deterministic call tree is exact; the model narrates it.
If a flow exceeds `flow.max_methods` the trace says so — narrow it ("trace `X` to `Y`").

## Project documentation generator (build plan §37, §61)

Auto-generate a `project_summary.md` from the index — purpose, architecture, key
classes with one-line purposes, SQL/dynamic-SQL picture, non-secret config,
external dependencies, and known hotspots (heavily-depended-on methods,
unresolved dynamic SQL, parse failures):

```bash
python scripts/generate_docs.py --project myapp --out project_summary.md
python scripts/generate_docs.py --project myapp --llm     # + a local-LLM purpose paragraph
```
Or `GET /api/projects/{id}/documentation` (`?format=json`, `?llm=true`), or the
**Overview** tab's documentation links. Purely deterministic by default; the
`--llm` paragraph degrades to a heuristic sentence if no model is available.

Next sprints (git, Spark) are tracked in `docs/SPRINTS.md`.

## Quick start

Requires **Python 3.10–3.13** and **Node 18+**. Ollama optional (see offline mode).

**macOS / Linux**
```bash
python3 -m venv backend/.venv && source backend/.venv/bin/activate
pip install -r requirements.txt
python scripts/index_project.py --root projects/synthetic-test-project --name synthetic
uvicorn backend.app.main:app --reload --port 8000        # http://localhost:8000/docs
```

**Windows (PowerShell)**
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\index_project.py --root projects\synthetic-test-project --name synthetic
uvicorn backend.app.main:app --reload --port 8000
```

**Web UI** (separate terminal)
```bash
cd frontend && npm install && npm run dev                 # http://localhost:5173
```
Set `CODEXRAY_API_URL=http://localhost:<port>` if the API isn't on 8000.

**Fully offline** (no model server): `set/export CODEXRAY_LLM_PROVIDER=echo` and
`CODEXRAY_EMBEDDING_PROVIDER=hashing` — everything except real LLM answers works.

> Java AST parsing uses `tree-sitter` + `tree-sitter-java` (wheels for 3.10–3.13). If neither
> installs, the indexer automatically falls back to a regex Java parser.

## Design rules (do not violate)

- **Local only.** No source code or clinical data leaves the machine. No cloud LLM.
- **Deterministic first.** Parser → facts, graph → relationships, resolver → resolution,
  LLM → explanation. Unresolved dynamic SQL returns `UNRESOLVED`, never a guessed table.
- **Read-only.** v1 never modifies source, deletes files, or runs destructive SQL.
- **Test code is excluded** from the index by default (no `src/test`, `*Test.java`,
  `*IT.java`, `*Spec.java` in any result). Set `scan.index_tests: true` (or
  `CODEXRAY_INDEX_TESTS=true`) only if you want test-impact analysis.
- **Everything configurable** via `config/config.yaml` (see `.env.example`).
- **Incremental.** Unchanged files (by hash) are not re-parsed.

## Layout

```
backend/app/
  api/         FastAPI routes
  agents/      (later) tool-calling agent
  analyzers/   java/ + sql/ static analysis
  config/      typed settings loader
  graph/       (later) NetworkX dependency graph
  indexing/    scanner + extractors + writer + orchestrator
  llm/         (later) Ollama provider abstraction
  models/      SQLite schema + connection
  retrieval/   hybrid search
projects/      indexed source projects (synthetic-test-project bundled)
data/          SQLite index, vectors, graphs, cache (git-ignored)
scripts/       CLI entry points
tests/         pytest suite
```
