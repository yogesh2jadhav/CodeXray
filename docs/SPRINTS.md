# CodeXray — Sprint tracker

Derived from the build plan §80/§86. Build in order; do not start the UI early.

| # | Sprint | Status | Key modules |
|---|--------|--------|-------------|
| 1 | Repository indexer (scan → Java AST → SQL parse → SQLite → search API) | ✅ done | `indexing/`, `analyzers/java`, `analyzers/sql`, `retrieval/search.py`, `api/routes.py` |
| 2 | Dynamic SQL analyzer (constant/variable/method-return/StringBuilder resolvers, metadata-query detection, `dynamic_sql*` tables, `trace_dynamic_sql`) | ⬜ todo | `analyzers/dynamic_sql/`, `analyzers/metadata/` |
| 3 | Local LLM (Ollama provider abstraction, PromptBuilder, ContextBuilder, ResponseParser, `/ask`) | ⬜ todo | `llm/` |
| 4 | Project graph + hybrid semantic retrieval (NetworkX, embeddings via local model, Qdrant, impact analysis, architecture extraction) | ⬜ todo | `graph/`, `retrieval/` |
| 5 | AI agent (tool calling loop, `max_iterations`, planners) | ⬜ todo | `agents/` |
| 6 | React + TypeScript UI (projects, chat+evidence, code viewer, dynamic-SQL trace, graph) | ⬜ todo | `frontend/` |
| 7 | Evaluation framework (~100 Q synthetic benchmark, model comparison) | ⬜ todo | `tests/eval/` |
| 8 | Git integration, runtime evidence | ⬜ todo | |
| 9 | Spark analysis (second project) | ⬜ todo | `analyzers/spark/` |

## Sprint 1 acceptance (all green — `pytest`)

- [x] Detect Java + SQL + config + doc files, hash for incremental re-index
- [x] Extract classes, methods, imports, statically-identifiable calls (tree-sitter, regex fallback)
- [x] Parse SQL from `.sql` files, inline Java literals, and `static final String` constants
- [x] Identify SQL tables (read/write), columns, joins, filters, bind parameters
- [x] Build a searchable symbol index; symbol / keyword / file / SQL / table-usage / caller search
- [x] Redact secrets in indexed config
- [x] Never abort a run on one bad file — failures recorded in `parse_failures`
- [x] Runs fully locally, read-only
