-- backend/app/models/schema.sql
--
-- Purpose:      Relational data model for the CodeXray project index (build plan §20/§21).
-- Responsibility:
--   Defines every table used by the Sprint 1 indexer plus forward-declared tables
--   for later sprints (dynamic_sql, embeddings, dependencies, git). SQLite is the
--   v1 store; an optional NetworkX graph is layered on top in memory (no Neo4j).
-- Notes:
--   * All FKs cascade on project delete so re-indexing / removing a project is clean.
--   * `resolution_status` uses the plan's vocabulary: RESOLVED | PARTIALLY_RESOLVED | UNRESOLVED.
--   * Applied idempotently by database.py via "CREATE TABLE IF NOT EXISTS".

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------ projects
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    root_path     TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_indexed  TEXT
);

-- ------------------------------------------------------------------ analysis_runs
-- One row per index invocation; used by the UI to show progress / failure counts.
CREATE TABLE IF NOT EXISTS analysis_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT,
    mode           TEXT NOT NULL DEFAULT 'full',        -- full | incremental
    files_total    INTEGER NOT NULL DEFAULT 0,
    files_indexed  INTEGER NOT NULL DEFAULT 0,
    files_skipped  INTEGER NOT NULL DEFAULT 0,
    files_failed   INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'running'      -- running | ok | error
);

-- ------------------------------------------------------------------ files
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,               -- relative to project root
    abs_path     TEXT NOT NULL,
    language     TEXT NOT NULL,               -- java | sql | config | doc | other
    extension    TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    content_hash TEXT NOT NULL,               -- sha256; drives incremental indexing
    indexed_at   TEXT,
    UNIQUE (project_id, path)
);

-- ------------------------------------------------------------------ parse_failures
CREATE TABLE IF NOT EXISTS parse_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    parser      TEXT NOT NULL,                -- java | sql | scanner
    error       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------------ packages
CREATE TABLE IF NOT EXISTS packages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    file_id     INTEGER REFERENCES files(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------------ classes / interfaces
CREATE TABLE IF NOT EXISTS classes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id             INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id                INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    fully_qualified_name   TEXT,
    package                TEXT,
    visibility             TEXT,               -- public | protected | private | package
    kind                   TEXT NOT NULL DEFAULT 'class',   -- class | interface | enum | record
    extends_name           TEXT,
    implements_names       TEXT,               -- comma-separated
    line_start             INTEGER,
    line_end               INTEGER
);

-- ------------------------------------------------------------------ methods
CREATE TABLE IF NOT EXISTS methods (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    class_id     INTEGER REFERENCES classes(id) ON DELETE CASCADE,
    file_id      INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    signature    TEXT,
    return_type  TEXT,
    parameters   TEXT,                         -- "Type name, Type name"
    visibility   TEXT,
    is_static    INTEGER NOT NULL DEFAULT 0,
    line_start   INTEGER,
    line_end     INTEGER
);

-- ------------------------------------------------------------------ fields (incl. string constants that may hold SQL)
CREATE TABLE IF NOT EXISTS fields (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    class_id      INTEGER REFERENCES classes(id) ON DELETE CASCADE,
    file_id       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    type_name     TEXT,
    visibility    TEXT,
    is_static     INTEGER NOT NULL DEFAULT 0,
    is_final      INTEGER NOT NULL DEFAULT 0,
    string_value  TEXT,                        -- literal value if statically known
    looks_like_sql INTEGER NOT NULL DEFAULT 0,
    line_start    INTEGER
);

-- ------------------------------------------------------------------ imports
CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    imported    TEXT NOT NULL,
    is_static   INTEGER NOT NULL DEFAULT 0,
    is_wildcard INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------ calls (statically identifiable method invocations)
CREATE TABLE IF NOT EXISTS calls (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    caller_method_id   INTEGER REFERENCES methods(id) ON DELETE CASCADE,
    caller_file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    callee_qualifier   TEXT,                   -- receiver expr / type as written
    callee_name        TEXT NOT NULL,
    arg_count          INTEGER,
    line_number        INTEGER
);

-- ------------------------------------------------------------------ symbols (searchable index, build plan §18)
CREATE TABLE IF NOT EXISTS symbols (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                -- package | class | interface | method | field
    name         TEXT NOT NULL,               -- simple name
    qualified    TEXT NOT NULL,               -- e.g. CustomerService.processCustomer
    file_id      INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line_number  INTEGER,
    ref_id       INTEGER                       -- id in the kind's own table
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(project_id, name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(project_id, qualified);

-- ------------------------------------------------------------------ SQL model (build plan §16)
CREATE TABLE IF NOT EXISTS sql_queries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id           INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    source_class      TEXT,
    source_method     TEXT,
    origin            TEXT NOT NULL,           -- java_literal | java_constant | sql_file
    query_type        TEXT,                    -- SELECT | INSERT | UPDATE | DELETE | MERGE | DDL | UNKNOWN
    raw_sql           TEXT NOT NULL,
    normalized_sql    TEXT,
    line_number       INTEGER,
    resolution_status TEXT NOT NULL DEFAULT 'RESOLVED',
    parse_ok          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sql_tables (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id  INTEGER NOT NULL REFERENCES sql_queries(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    alias     TEXT,
    access    TEXT NOT NULL DEFAULT 'read'     -- read | write
);
CREATE INDEX IF NOT EXISTS idx_sql_tables_name ON sql_tables(name);

CREATE TABLE IF NOT EXISTS sql_columns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id  INTEGER NOT NULL REFERENCES sql_queries(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    table_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_sql_columns_name ON sql_columns(name);

CREATE TABLE IF NOT EXISTS sql_joins (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id  INTEGER NOT NULL REFERENCES sql_queries(id) ON DELETE CASCADE,
    join_type TEXT,
    target    TEXT,
    on_expr   TEXT
);

CREATE TABLE IF NOT EXISTS sql_conditions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id  INTEGER NOT NULL REFERENCES sql_queries(id) ON DELETE CASCADE,
    expr      TEXT
);

CREATE TABLE IF NOT EXISTS sql_parameters (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id  INTEGER NOT NULL REFERENCES sql_queries(id) ON DELETE CASCADE,
    marker    TEXT                              -- '?' or ':name'
);

-- ------------------------------------------------------------------ config entries (properties / yaml / xml flattened)
CREATE TABLE IF NOT EXISTS config_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT,                           -- redacted if secret-like
    is_secret   INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------ documents (RAG source text)
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    title       TEXT,
    content     TEXT
);

-- ================================================================== later sprints
-- Forward-declared so migrations stay additive. Populated from Sprint 2+.

CREATE TABLE IF NOT EXISTS dynamic_sql (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_method_id  INTEGER REFERENCES methods(id) ON DELETE CASCADE,
    source_file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    source_class      TEXT,
    source_method     TEXT,
    source_var        TEXT,
    line_number       INTEGER,
    expression        TEXT,
    sql_template      TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
    resolved_sql      TEXT,
    confidence        REAL,
    tables_json       TEXT,                    -- JSON array of table names parsed from the template
    columns_json      TEXT                     -- JSON array of column names
);

CREATE TABLE IF NOT EXISTS dynamic_sql_dependencies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dynamic_sql_id    INTEGER NOT NULL REFERENCES dynamic_sql(id) ON DELETE CASCADE,
    ordinal           INTEGER NOT NULL DEFAULT 0,
    dependency_type   TEXT NOT NULL,           -- CONSTANT|VARIABLE|METHOD_RETURN|METADATA_QUERY|CONFIGURATION|PARAMETER|UNKNOWN
    source_type       TEXT,
    source_id         INTEGER,
    value             TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
    evidence_json     TEXT                     -- JSON array of {detail,file,line,metadata_sql,metadata_tables}
);

CREATE TABLE IF NOT EXISTS dependencies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    edge_type   TEXT NOT NULL,                 -- CALLS|IMPORTS|EXTENDS|IMPLEMENTS|READS_TABLE|GENERATES_SQL|...
    src_kind    TEXT NOT NULL,
    src_id      INTEGER,
    src_label   TEXT NOT NULL,
    dst_kind    TEXT NOT NULL,
    dst_id      INTEGER,
    dst_label   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deps_src ON dependencies(project_id, src_label);
CREATE INDEX IF NOT EXISTS idx_deps_dst ON dependencies(project_id, dst_label);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_type  TEXT,                           -- class|method|sql|config|doc
    symbol      TEXT,
    line_start  INTEGER,
    line_end    INTEGER,
    content     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id   INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    project_id INTEGER,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,             -- float32 little-endian, `dim` values
    norm       REAL NOT NULL DEFAULT 1.0  -- pre-computed L2 norm for cosine
);
CREATE INDEX IF NOT EXISTS idx_embeddings_project ON embeddings(project_id);

-- Sprint 4: architecture roles/layers per class (build plan §39).
CREATE TABLE IF NOT EXISTS architecture_components (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    class_id    INTEGER REFERENCES classes(id) ON DELETE CASCADE,
    class_name  TEXT NOT NULL,
    fqn         TEXT,
    role        TEXT NOT NULL,            -- ENTRY_POINT | CONTROLLER | SERVICE | REPOSITORY | ...
    layer       TEXT NOT NULL,            -- Service | Processing | Metadata | Data Access | Config | ...
    file        TEXT,
    evidence    TEXT                       -- why this role was assigned
);
CREATE INDEX IF NOT EXISTS idx_arch_role ON architecture_components(project_id, role);

-- Flow explanations: one-line purpose per method, generated at index time
-- (heuristic — javadoc first sentence, else name + SQL/call signals).
CREATE TABLE IF NOT EXISTS method_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    method_id   INTEGER REFERENCES methods(id) ON DELETE CASCADE,
    qualified   TEXT NOT NULL,             -- Class.method
    summary     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'heuristic',   -- javadoc | heuristic
    UNIQUE (project_id, qualified)
);
CREATE INDEX IF NOT EXISTS idx_msum_q ON method_summaries(project_id, qualified);

-- Per-class technical design documents, generated on demand (not during a
-- normal index run) — see scripts/generate_class_docs.py. Fed into the
-- semantic chunker (chunk_type='class_doc') so Chat/Ask/Search can cite them.
CREATE TABLE IF NOT EXISTS class_docs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    class_id       INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    package        TEXT,
    markdown       TEXT NOT NULL,
    purpose_source TEXT NOT NULL DEFAULT 'heuristic',   -- llm | heuristic
    generated_at   TEXT NOT NULL,
    UNIQUE (project_id, class_id)
);
CREATE INDEX IF NOT EXISTS idx_class_docs_pkg ON class_docs(project_id, package);
