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

Later sprints (graph, embeddings, Ollama, agent, React UI) are scaffolded as empty
packages and tracked in `docs/SPRINTS.md`.

## Quick start

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r requirements.txt

# Index the bundled synthetic test project
python scripts/index_project.py --root projects/synthetic-test-project --name synthetic

# Run the API
uvicorn backend.app.main:app --reload --port 8000
# open http://localhost:8000/docs
```

## Design rules (do not violate)

- **Local only.** No source code or clinical data leaves the machine. No cloud LLM.
- **Deterministic first.** Parser → facts, graph → relationships, resolver → resolution,
  LLM → explanation. Unresolved dynamic SQL returns `UNRESOLVED`, never a guessed table.
- **Read-only.** v1 never modifies source, deletes files, or runs destructive SQL.
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
