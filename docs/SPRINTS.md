# CodeXray — Sprint tracker

Derived from the build plan §80/§86. Build in order; do not start the UI early.

| # | Sprint | Status | Key modules |
|---|--------|--------|-------------|
| 1 | Repository indexer (scan → Java AST → SQL parse → SQLite → search API) | ✅ done | `indexing/`, `analyzers/java`, `analyzers/sql`, `retrieval/search.py`, `api/routes.py` |
| 2 | Dynamic SQL analyzer (constant/variable/method-return/StringBuilder resolvers, metadata-query detection, `dynamic_sql*` tables, `trace_dynamic_sql`) | ✅ done | `analyzers/dynamic_sql/`, `analyzers/metadata/` |
| 3 | Local LLM (Ollama provider abstraction, PromptBuilder, ContextBuilder, ResponseParser, `/ask`) | ✅ done | `llm/` |
| 4 | Project graph + hybrid semantic retrieval (NetworkX, local embeddings, impact analysis, architecture extraction) | ✅ done | `graph/`, `retrieval/`, `analyzers/architecture/` |
| 5 | AI agent (tool calling loop, `max_iterations`, planners) | ✅ done | `agents/` |
| 6 | React + TypeScript UI (projects, chat+evidence, code viewer, dynamic-SQL trace, graph) | ⬜ todo | `frontend/` |
| 7 | Evaluation framework (~100 Q synthetic benchmark, model comparison) | ⬜ todo | `tests/eval/` |
| 8 | Git integration, runtime evidence | ⬜ todo | |
| 9 | Spark analysis (second project) | ⬜ todo | `analyzers/spark/` |

## Sprint 5 acceptance (all green — `pytest tests/test_sprint5.py`)

- [x] `agents/tools.py` — 19 read-only tools (search_code/symbol, get_file/class/method,
      find_callers/callees/references, trace_call_path, get_class_dependencies, search_sql,
      get_sql_query, find_table/column_usage, trace_dynamic_sql, trace_metadata_dependency,
      impact_analysis, get_project_architecture, search_documents) + `ToolRegistry`
- [x] `agents/planner.py` — deterministic §27-style seed plan per question type
- [x] `agents/agent.py` — loop: classify → plan → run seed tools → optional LLM tool
      requests (JSON protocol, `agent.max_iterations`) → synthesis with FACT/INFERENCE/
      UNKNOWN rules → parse. Degrades to the seed plan when the model can't plan (echo /
      weak model); never invents tool output
- [x] `GraphService.impact_analysis` handles class targets (aggregates over methods)
- [x] API: `POST /projects/{id}/investigate`, `GET /agent/tools`; 503 when the model is down
- [x] Live vs qwen2.5-coder:7b: "trace how table name + columns are determined end to end"
      → full metadata-chain answer from tool evidence, ~30 s

## Sprint 4 acceptance (all green — `pytest tests/test_sprint4.py`)

- [x] `GraphBuilder` — SQLite index → typed edges in `dependencies` (CONTAINS/IMPORTS/EXTENDS/IMPLEMENTS/CALLS/GENERATES_SQL/READS_TABLE/WRITES_TABLE/METADATA_LOOKUP)
- [x] `GraphService` (NetworkX) — callers / callees / call_path / table_consumers / class_dependencies / neighbors
- [x] `impact_analysis(symbol)` (§41) — direct + indirect callers, reachable SQL + tables, tests, explicit unknowns for partially-resolved dynamic SQL
- [x] Semantic retrieval: `Chunker` (semantic units §35) → `Embedder` (`OllamaEmbedder` nomic-embed-text + offline `HashingEmbedder`) → `SqliteVectorStore` (numpy cosine) → `SemanticIndex`
- [x] `hybrid_search` — symbol + keyword + semantic + call-graph proximity, configurable weights (§24, §36), deterministic
- [x] `ArchitectureExtractor` (§39) — role/layer per class, entry points, data access; `architecture.json` + `.md`; persisted to `architecture_components`
- [x] Wired into the indexer (graph/semantic/architecture post-passes) and the ContextBuilder (IMPACT / SEMANTIC / ARCHITECTURE sections)
- [x] API: `/graph`, `/graph/callers`, `/graph/path`, `/impact-analysis`, `/architecture` (+ `?format=md`), `/search` modes `semantic`|`hybrid`, `/semantic/status`
- [x] Live: 40 chunks embedded via nomic-embed-text; impact of `loadEligibleCustomers` → `processCustomer` + `META_TABLE_REGISTRY`/`META_COLUMN_REGISTRY` + dynamic-SQL unknowns

## Sprint 3 acceptance (all green — `pytest tests/test_sprint3.py`)

- [x] `LLMProvider` abstraction; `OllamaProvider` (local `/api/chat`, CPU, no CUDA) + offline `EchoProvider`
- [x] `get_provider()` factory driven by `llm.provider` (env `CODEXRAY_LLM_PROVIDER`)
- [x] `question_classifier` — deterministic type + retrieval modes (§24), symbol/table extraction
- [x] `ContextBuilder` + `ContextBudgetManager` — structured evidence sections by priority within `llm.context_char_budget` (§25, §71)
- [x] `PromptBuilder` — technical-lead system prompt with FACT/INFERENCE/UNKNOWN + "never invent a table" (§29, §61, §72)
- [x] `ResponseParser` — label split, cited `file:line`, coarse confidence
- [x] `AskService` pipeline → `POST /api/projects/{id}/ask`; `GET /api/llm/health`; 503 (never cloud) when the engine is down
- [x] Verified live against `qwen2.5-coder:7b`: "how is the table name determined" → PARTIALLY_RESOLVED answer tracing `META_TABLE_REGISTRY`, ~13 s CPU

## Sprint 2 acceptance (all green — `pytest tests/test_sprint2.py`)

- [x] Resolvers: Constant, Variable, MethodReturn (interprocedural, depth-bounded), StringConcatenation, StringBuilder
- [x] `MetadataQueryDetector` — a method that runs `SELECT ... FROM <registry>` and returns a table/column value
- [x] Reconstruct SQL as a **template** with `{slot}` placeholders for unknown parts
- [x] End-to-end §83 trace: `CustomerService.loadEligibleCustomers` →
      `SELECT {columns} FROM {table} WHERE STATUS = ?` with deps
      `METADATA_QUERY(META_COLUMN_REGISTRY)`, `METADATA_QUERY(META_TABLE_REGISTRY)`, 3×`CONSTANT`
- [x] Status vocabulary RESOLVED / PARTIALLY_RESOLVED / UNRESOLVED; **never** invents a table (§29)
- [x] Persisted to `dynamic_sql` + `dynamic_sql_dependencies` (with evidence JSON)
- [x] `GET /projects/{id}/dynamic-sql` and `POST /projects/{id}/dynamic-sql/trace` (the `trace_dynamic_sql` tool)

## Sprint 1 acceptance (all green — `pytest`)

- [x] Detect Java + SQL + config + doc files, hash for incremental re-index
- [x] Extract classes, methods, imports, statically-identifiable calls (tree-sitter, regex fallback)
- [x] Parse SQL from `.sql` files, inline Java literals, and `static final String` constants
- [x] Identify SQL tables (read/write), columns, joins, filters, bind parameters
- [x] Build a searchable symbol index; symbol / keyword / file / SQL / table-usage / caller search
- [x] Redact secrets in indexed config
- [x] Never abort a run on one bad file — failures recorded in `parse_failures`
- [x] Runs fully locally, read-only
