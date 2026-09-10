# Graph export — Neo4j & friends

CodeXray keeps the dependency graph in **SQLite + NetworkX** (build plan §19). This
exporter reshapes that graph for external visualisation / querying — it does not
change how CodeXray works, and none of it is required to run the app.

## What's in the graph

| Node label | From |
|------------|------|
| `Module` | first two package segments (`com.acme`) |
| `Package` | Java package |
| `Class` / `Interface` | classes in **your** code (JDBC/stdlib types filtered out) |
| `Method` | methods in your code — carries `purpose`, `class`, `file`, `line` |
| `File` | source file |
| `Table` | SQL table |
| `SqlQuery` | a parsed static query |
| `DynamicSql` | a reconstructed dynamic-SQL site — carries `status` (RESOLVED / PARTIALLY_RESOLVED / UNRESOLVED) |

Relationships: `CONTAINS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `CALLS`,
`GENERATES_SQL`, `READS_TABLE`, `WRITES_TABLE`, `METADATA_LOOKUP`.

## Neo4j

Run an instance any way you like — **Neo4j Desktop** (GUI, Windows/Mac), **Neo4j
Community** (ZIP, `bin/neo4j console`), or `docker compose up -d neo4j`. Note the
connection URI (Desktop shows it, e.g. `neo4j://127.0.0.1:7687`) and the password.

```bash
# load directly over bolt — best for a real codebase  (pip install neo4j)
python scripts/export_graph.py --project myapp --format cypher --load \
    --uri neo4j://127.0.0.1:7687 --user neo4j --password YOUR_PASSWORD

# or: write a file and run it with cypher-shell (ships with Neo4j, in bin/)
python scripts/export_graph.py --project myapp --format cypher --out graph.cypher
cypher-shell -a neo4j://127.0.0.1:7687 -u neo4j -p YOUR_PASSWORD -f graph.cypher
```

Small projects only: open the `.cypher` file and paste it into the Neo4j Desktop
**Query** tool (or the old Neo4j Browser). Don't paste thousands of statements —
use `--load`.

From the UI: **Graph** tab → *Export the whole graph* → **Neo4j (.cypher)** downloads
the file; run it as above.

`--uri` / `--user` / `--password` also read from `NEO4J_URI` / `NEO4J_USER` /
`NEO4J_PASSWORD` if you prefer env vars.

### Useful Cypher

```cypher
// module → package → class → method tree
MATCH (m:Module)-[:CONTAINS*1..3]->(x) RETURN * LIMIT 300;

// call flow from an entry point
MATCH p=(:Method {name:'CasingService.getClaimData'})-[:CALLS*1..6]->(:Method) RETURN p;

// every dynamic query and the metadata tables that feed it
MATCH (mth:Method)-[:GENERATES_SQL]->(d:DynamicSql)-[:METADATA_LOOKUP]->(t:Table)
RETURN mth.name, d.status, collect(t.name);

// classes by architecture layer
MATCH (c:Class) RETURN c.layer, collect(c.name) ORDER BY c.layer;

// what reads a table
MATCH (m:Method)-[:GENERATES_SQL]->(:SqlQuery)-[:READS_TABLE]->(:Table {name:'CLAIM_HEADER'})
RETURN DISTINCT m.name;
```

## Other targets

| Tool | Command |
|------|---------|
| Gephi / yEd / Cytoscape desktop | `--format graphml --out graph.graphml` |
| Graphviz | `--format dot --out graph.dot` → `dot -Tsvg graph.dot -o graph.svg` |
| cytoscape.js / d3 (web) | `--format json` (also `GET /api/projects/{id}/graph/export?format=json`) |

## API

```
GET  /api/projects/{id}/graph/export?format=cypher|graphml|dot|json
POST /api/projects/{id}/graph/export/neo4j   {"uri","user","password"}
```

## Include JDBC / framework types

By default only your code is exported. To keep external classes too, use the
`GraphExporter(project_id, include_external=True)` constructor (not yet exposed on
the CLI/API — add `--include-external` if you need it).
