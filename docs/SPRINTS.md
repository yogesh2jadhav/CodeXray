# CodeXray — Sprint tracker

Derived from the build plan §80/§86. Build in order; do not start the UI early.

| # | Sprint | Status | Key modules |
|---|--------|--------|-------------|
| 1 | Repository indexer (scan → Java AST → SQL parse → SQLite → search API) | ✅ done | `indexing/`, `analyzers/java`, `analyzers/sql`, `retrieval/search.py`, `api/routes.py` |
| 2 | Dynamic SQL analyzer (constant/variable/method-return/StringBuilder resolvers, metadata-query detection, `dynamic_sql*` tables, `trace_dynamic_sql`) | ✅ done | `analyzers/dynamic_sql/`, `analyzers/metadata/` |
| 3 | Local LLM (Ollama provider abstraction, PromptBuilder, ContextBuilder, ResponseParser, `/ask`) | ✅ done | `llm/` |
| 4 | Project graph + hybrid semantic retrieval (NetworkX, embeddings via local model, Qdrant, impact analysis, architecture extraction) | ⬜ todo | `graph/`, `retrieval/` |
| 5 | AI agent (tool calling loop, `max_iterations`, planners) | ⬜ todo | `agents/` |
| 6 | React + TypeScript UI (projects, chat+evidence, code viewer, dynamic-SQL trace, graph) | ⬜ todo | `frontend/` |
| 7 | Evaluation framework (~100 Q synthetic benchmark, model comparison) | ⬜ todo | `tests/eval/` |
| 8 | Git integration, runtime evidence | ⬜ todo | |
| 9 | Spark analysis (second project) | ⬜ todo | `analyzers/spark/` |

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
