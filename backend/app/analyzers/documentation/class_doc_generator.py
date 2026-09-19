"""
backend/app/analyzers/documentation/class_doc_generator.py

Purpose
-------
Generate one detailed "technical design document" per Java class — a level of
detail below `ProjectDocGenerator`'s project/module summaries — so class-level
facts can be embedded into the semantic index and cited by Chat/Ask/Agent.

Responsibility
--------------
- Precompute every project-wide fact exactly ONCE (`_precompute`), then build
  each class's document from in-memory dict lookups only. Calling this once
  per class in a 1700-class project must never re-run whole-project queries
  per class — that pitfall is why this class exists separately from
  `ProjectDocGenerator`, whose `generate()` is only ever called a handful of
  times (once per module), not once per class.
- Deterministic sections (fields, constructors, methods, SQL, one-hop call
  hierarchy, data flow, imports, sibling classes) come only from facts already
  in the index. A Spark-specific section is added only for classes whose file
  imports `org.apache.spark.*`, built by name-matching the `calls` table
  against known Spark API method names — no new tree-sitter parsing.
- Narrative sections (Purpose/Overview/Key Responsibilities/High-Level
  Workflow) ask the local LLM ONCE per class, fed only the facts already
  gathered here (never raw source), and fall back to heuristic template text
  if the model is unavailable or its response doesn't parse. Generating one
  class's narrative must never raise — a failure just degrades to heuristic.
- Exception handling, logging-framework usage and external service calls are
  not extracted anywhere in CodeXray yet; the document says so rather than
  guessing.

Read-only against the index except `persist()`, which upserts into
`class_docs`. Callers (scripts/generate_class_docs.py) own re-embedding.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.app.analyzers.architecture.extractor import ArchitectureExtractor
from backend.app.models.database import get_connection

log = logging.getLogger("codexray.classdocgen")

_JDK_COLLECTION_PREFIXES = (
    "List", "Map", "Set", "Collection", "Optional", "Queue", "Deque", "Stack",
    "ArrayList", "HashMap", "HashSet", "LinkedList", "TreeMap", "TreeSet",
    "ConcurrentHashMap", "Vector", "Iterable",
)
_JDK_PRIMITIVE_OR_BOXED = {
    "int", "long", "short", "byte", "char", "boolean", "float", "double", "void",
    "Integer", "Long", "Short", "Byte", "Character", "Boolean", "Float", "Double",
    "String", "Object", "BigDecimal", "BigInteger",
}

_SPARK_TRANSFORMATIONS = {"map", "filter", "flatmap", "join", "groupby", "reducebykey",
                           "union", "distinct", "sortby", "repartition", "coalesce"}
_SPARK_ACTIONS = {"collect", "count", "take", "write", "save", "foreach", "reduce", "first"}
_SPARK_PERSISTENCE = {"persist", "cache", "unpersist"}
_SPARK_BROADCAST = {"broadcast"}

_MAX_ONE_HOP_CALLEES = 25

_NARRATIVE_RE = re.compile(
    r"###\s*PURPOSE\s*(.*?)###\s*OVERVIEW\s*(.*?)###\s*RESPONSIBILITIES\s*(.*?)###\s*WORKFLOW\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_key_structure(type_name: str | None) -> bool:
    if not type_name:
        return False
    base = type_name.split("<", 1)[0].strip()
    simple = base.rsplit(".", 1)[-1]
    if any(simple.startswith(p) for p in _JDK_COLLECTION_PREFIXES):
        return True
    if simple in _JDK_PRIMITIVE_OR_BOXED or simple.startswith(("java.", "javax.")):
        return False
    return simple[:1].isupper()   # custom / DTO-looking type


@dataclass
class ClassDoc:
    class_id: int
    name: str
    package: str | None
    fqn: str | None
    kind: str
    extends_name: str | None
    implements_names: str | None
    static_fields: list[dict] = field(default_factory=list)
    instance_fields: list[dict] = field(default_factory=list)
    key_data_structures: list[dict] = field(default_factory=list)
    constructors: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    main_method: str | None = None
    call_hierarchy: dict[str, list[str]] = field(default_factory=dict)   # method name -> one-hop callees
    sql_queries: list[dict] = field(default_factory=list)
    tables_read: list[str] = field(default_factory=list)
    tables_written: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    siblings: list[str] = field(default_factory=list)
    arch_role: str | None = None
    arch_layer: str | None = None
    spark_section: dict | None = None
    purpose: str = ""
    overview: str = ""
    responsibilities: list[str] = field(default_factory=list)
    workflow: str = ""
    purpose_source: str = "heuristic"
    generated_at: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"# {self.name}",
            "",
            f"_Package:_ `{self.package or '(default)'}`  ·  _Kind:_ `{self.kind}`"
            + (f"  ·  _Extends:_ `{self.extends_name}`" if self.extends_name else "")
            + (f"  ·  _Implements:_ `{self.implements_names}`" if self.implements_names else ""),
            "",
            f"_Generated by CodeXray on {self.generated_at} ({self.purpose_source} narrative). "
            f"Structural facts come from the deterministic index; re-run after re-indexing to refresh._",
            "",
            "## Purpose", "", self.purpose or "_not available_", "",
            "## Overview", "", self.overview or "_not available_", "",
            "## Architecture role", "",
        ]
        lines.append(f"`{self.arch_role}` in the **{self.arch_layer}** layer." if self.arch_role
                     else "_not classified_")
        lines.append("")

        lines += ["## Key responsibilities", ""]
        lines += ([f"- {r}" for r in self.responsibilities] or ["- _not available_"])
        lines.append("")

        lines += ["## Class variables / static variables", ""]
        if self.static_fields:
            lines.append("**Static:**")
            lines += [f"- `{f['type_name'] or '?'} {f['name']}`" for f in self.static_fields]
        if self.instance_fields:
            lines.append("**Instance:**")
            lines += [f"- `{f['type_name'] or '?'} {f['name']}`" for f in self.instance_fields]
        if not self.static_fields and not self.instance_fields:
            lines.append("_none_")
        lines.append("")

        lines += ["## Key data structures", ""]
        lines += ([f"- `{d['type_name']} {d['name']}`" for d in self.key_data_structures]
                  or ["- _none flagged_"])
        lines.append("")

        lines += ["## Constructors", ""]
        lines += ([f"- `{c['signature'] or c['name'] + '(...)'}`" for c in self.constructors]
                  or ["- _none declared_"])
        lines.append("")

        lines += ["## Methods", "", "| method | visibility | summary |", "|---|---|---|"]
        for m in self.methods:
            sig = m["signature"] or f'{m["name"]}()'
            lines.append(f"| `{sig}` | {m['visibility'] or ''} | {m.get('summary') or ''} |")
        lines.append("")
        if self.main_method:
            lines.append(f"**Main method (primary entry point):** `{self.main_method}`")
            lines.append("")

        lines += [
            "## Method call hierarchy", "",
            "_One hop only — direct callees per method. For a deep recursive trace of a specific "
            f"method, use the Flow tab (or ask CodeXray to \"trace the flow of {self.name}.<method>\")._",
            "",
        ]
        shown_hierarchy = {m: callees for m, callees in self.call_hierarchy.items() if callees}
        if shown_hierarchy:
            lines += [f"- `{m}` → {', '.join(callees)}" for m, callees in shown_hierarchy.items()]
        else:
            lines.append("_no outgoing calls recorded_")
        lines.append("")

        lines += ["## SQL queries", ""]
        lines += ([f"- `{q['query_type']}` ({q['resolution_status']}): {q['tables']}"
                   for q in self.sql_queries] or ["_none_"])
        lines.append("")

        lines += [
            "## Data flow (tables)", "",
            f"- **Reads:** {', '.join(self.tables_read) or '_none_'}",
            f"- **Writes:** {', '.join(self.tables_written) or '_none_'}",
            "",
        ]

        lines += ["## Imports", ""]
        lines += ([f"- `{i}`" for i in self.imports] or ["- _none_"])
        lines.append("")

        lines += ["## Package structure / sibling classes", ""]
        lines += ([f"- `{s}`" for s in self.siblings] or ["- _none_"])
        lines.append("")

        lines += ["## High-level workflow", "", self.workflow or "_not available_", ""]

        if self.spark_section:
            s = self.spark_section
            lines += ["## Spark usage", ""]
            for label, key in (("Transformations", "transformations"), ("Actions", "actions"),
                                ("Persistence", "persistence"), ("Broadcast", "broadcast"),
                                ("Delta/Parquet writes", "delta_parquet")):
                vals = sorted({v for v in s.get(key, []) if v})
                if vals:
                    lines.append(f"- **{label}:** {', '.join(vals)}")
            lines.append("")

        lines += [
            "## Not yet extracted", "",
            "CodeXray does not yet detect exception-handling patterns, logging-framework usage, "
            "or external HTTP/service calls for this class — these are not modeled in the index.",
            "",
        ]
        return "\n".join(lines)


class ClassDocGenerator:
    def __init__(self, project_id: int, conn=None) -> None:
        self.pid = project_id
        self.conn = conn or get_connection()
        self._ready = False

    # -------------------------------------------------------------- precompute
    def precompute(self) -> None:
        if self._ready:
            return
        c, pid = self.conn, self.pid

        self.classes = [dict(r) for r in c.execute("SELECT * FROM classes WHERE project_id=?", (pid,))]
        self.by_package: dict[str, list[str]] = defaultdict(list)
        for cl in self.classes:
            self.by_package[cl["package"] or ""].append(cl["name"])

        self.fields_by_class: dict[int, list[dict]] = defaultdict(list)
        for r in c.execute("SELECT * FROM fields WHERE project_id=?", (pid,)):
            self.fields_by_class[r["class_id"]].append(dict(r))

        self.methods_by_class: dict[int, list[dict]] = defaultdict(list)
        for r in c.execute("SELECT * FROM methods WHERE project_id=? ORDER BY line_start", (pid,)):
            self.methods_by_class[r["class_id"]].append(dict(r))

        sql_rows = c.execute(
            "SELECT q.*, GROUP_CONCAT(t.name || ':' || t.access) AS tables_agg "
            "FROM sql_queries q LEFT JOIN sql_tables t ON t.query_id=q.id "
            "WHERE q.project_id=? GROUP BY q.id", (pid,),
        ).fetchall()
        self.sql_by_source_class: dict[str, list[dict]] = defaultdict(list)
        for r in sql_rows:
            self.sql_by_source_class[r["source_class"] or ""].append(dict(r))

        self.imports_by_file: dict[int, list[dict]] = defaultdict(list)
        for r in c.execute("SELECT * FROM imports WHERE project_id=?", (pid,)):
            self.imports_by_file[r["file_id"]].append(dict(r))

        arch = ArchitectureExtractor(c).extract(pid)
        self.arch_by_class_name = {comp.name: comp for comp in arch.components}

        self.summaries_by_qualified = {
            r["qualified"]: r["summary"] for r in c.execute(
                "SELECT qualified, summary FROM method_summaries WHERE project_id=?", (pid,))
        }

        self.calls_by_caller_method_id: dict[int, list[dict]] = defaultdict(list)
        for r in c.execute("SELECT * FROM calls WHERE project_id=?", (pid,)):
            self.calls_by_caller_method_id[r["caller_method_id"]].append(dict(r))

        self.dep_counts_by_dst_label: dict[str, int] = defaultdict(int)
        for r in c.execute(
            "SELECT dst_label, COUNT(*) AS n FROM dependencies WHERE project_id=? AND dst_kind='method' "
            "GROUP BY dst_label", (pid,),
        ):
            self.dep_counts_by_dst_label[r["dst_label"]] = r["n"]

        self._ready = True

    def target_classes(self, package_prefix: str | None) -> list[dict]:
        if not package_prefix:
            return list(self.classes)
        return [cl for cl in self.classes if (cl["package"] or "").startswith(package_prefix)]

    # -------------------------------------------------------------- per-class
    def build_one(self, cls: dict, *, use_llm: bool) -> ClassDoc:
        cid, name = cls["id"], cls["name"]
        methods_all = self.methods_by_class.get(cid, [])
        constructors = [m for m in methods_all if m["return_type"] is None and m["name"] == name]
        ctor_ids = {m["id"] for m in constructors}
        methods = [dict(m, summary=self.summaries_by_qualified.get(f'{name}.{m["name"]}'))
                   for m in methods_all if m["id"] not in ctor_ids]

        fields_all = self.fields_by_class.get(cid, [])
        static_fields = [f for f in fields_all if f["is_static"]]
        instance_fields = [f for f in fields_all if not f["is_static"]]
        key_data_structures = [f for f in fields_all if _looks_like_key_structure(f["type_name"])]

        call_hierarchy: dict[str, list[str]] = {}
        for m in methods_all:
            callees, seen = [], set()
            for call in self.calls_by_caller_method_id.get(m["id"], []):
                cn = call["callee_name"]
                if cn and cn not in seen:
                    seen.add(cn)
                    callees.append(cn)
                if len(callees) >= _MAX_ONE_HOP_CALLEES:
                    break
            if callees:
                call_hierarchy[m["name"]] = callees

        sql_queries, tables_read, tables_written = [], [], []
        for q in self.sql_by_source_class.get(name, []):
            parsed = []
            for entry in (q["tables_agg"] or "").split(","):
                if not entry:
                    continue
                tname, _, access = entry.partition(":")
                parsed.append(f"{tname}({access or 'read'})")
                (tables_written if access == "write" else tables_read).append(tname)
            sql_queries.append({"query_type": q["query_type"], "resolution_status": q["resolution_status"],
                                 "tables": ", ".join(parsed) or "(none)"})

        imports = sorted({i["imported"] for i in self.imports_by_file.get(cls["file_id"], [])})
        is_spark = any(imp.startswith("org.apache.spark") for imp in imports)

        siblings = sorted(n for n in self.by_package.get(cls["package"] or "", []) if n != name)

        comp = self.arch_by_class_name.get(name)
        arch_role, arch_layer = (comp.role, comp.layer) if comp else (None, None)

        main_method = self._main_method(name, methods)
        spark_section = self._spark_section(methods_all, fields_all) if is_spark else None

        doc = ClassDoc(
            class_id=cid, name=name, package=cls["package"], fqn=cls["fully_qualified_name"],
            kind=cls["kind"], extends_name=cls["extends_name"], implements_names=cls["implements_names"],
            static_fields=static_fields, instance_fields=instance_fields,
            key_data_structures=key_data_structures, constructors=constructors, methods=methods,
            main_method=main_method, call_hierarchy=call_hierarchy, sql_queries=sql_queries,
            tables_read=sorted(set(tables_read)), tables_written=sorted(set(tables_written)),
            imports=imports, siblings=siblings, arch_role=arch_role, arch_layer=arch_layer,
            spark_section=spark_section,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        doc.purpose, doc.overview, doc.responsibilities, doc.workflow, doc.purpose_source = \
            self._narrative(doc, use_llm=use_llm)
        return doc

    def _main_method(self, name: str, methods: list[dict]) -> str | None:
        for m in methods:
            if m["name"] == "main" and m["is_static"]:
                return f'{name}.main{m["signature"] or "()"}'
        public_methods = [m for m in methods if (m["visibility"] or "public") == "public"] or methods
        if not public_methods:
            return None
        best = max(public_methods, key=lambda m: self.dep_counts_by_dst_label.get(f'{name}.{m["name"]}', 0))
        return f'{name}.{best["name"]}'

    def _spark_section(self, methods_all: list[dict], fields_all: list[dict]) -> dict | None:
        out: dict[str, list[str]] = {"transformations": [], "actions": [], "persistence": [],
                                      "broadcast": [], "delta_parquet": []}
        for m in methods_all:
            for call in self.calls_by_caller_method_id.get(m["id"], []):
                cn = (call["callee_name"] or "").lower()
                cq = (call["callee_qualifier"] or "").lower()
                if cn in _SPARK_TRANSFORMATIONS:
                    out["transformations"].append(call["callee_name"])
                if cn in _SPARK_ACTIONS:
                    out["actions"].append(call["callee_name"])
                if cn in _SPARK_PERSISTENCE:
                    out["persistence"].append(call["callee_name"])
                if cn in _SPARK_BROADCAST:
                    out["broadcast"].append(call["callee_name"])
                if "parquet" in cn or "parquet" in cq or "delta" in cn or "delta" in cq:
                    out["delta_parquet"].append(call["callee_name"] or call["callee_qualifier"])
        for f in fields_all:
            sv = (f.get("string_value") or "").lower()
            if ".parquet(" in sv or 'format("delta")' in sv or "format('delta')" in sv:
                out["delta_parquet"].append(f["name"])
        if not any(out.values()):
            return None
        return out

    # ------------------------------------------------------------- narrative
    def _facts_text(self, doc: ClassDoc) -> str:
        method_names = ", ".join(m["name"] for m in doc.methods[:20]) or "(none)"
        return (
            f"Class: {doc.name} (kind={doc.kind}, package={doc.package})\n"
            f"Extends: {doc.extends_name or '(none)'}; Implements: {doc.implements_names or '(none)'}\n"
            f"Architecture role: {doc.arch_role or 'unclassified'} / layer: {doc.arch_layer or 'unclassified'}\n"
            f"Fields: {len(doc.static_fields)} static, {len(doc.instance_fields)} instance\n"
            f"Constructors: {len(doc.constructors)}\n"
            f"Methods ({len(doc.methods)}): {method_names}\n"
            f"Main method: {doc.main_method or 'unclear'}\n"
            f"SQL — reads: {', '.join(doc.tables_read) or 'none'}; writes: {', '.join(doc.tables_written) or 'none'}\n"
            f"Sibling classes in package: {', '.join(doc.siblings[:15]) or 'none'}\n"
            f"Spark usage: {'yes' if doc.spark_section else 'no'}"
        )

    def _heuristic_narrative(self, doc: ClassDoc) -> tuple[str, str, list[str], str]:
        role_txt = f"a {doc.arch_role.replace('_', ' ').lower()}" if doc.arch_role else "a component"
        purpose = f"`{doc.name}` is {role_txt} in package `{doc.package or '(default)'}`."
        overview = (
            f"{doc.name} is a {doc.kind} with {len(doc.static_fields)} static and "
            f"{len(doc.instance_fields)} instance field(s), {len(doc.constructors)} constructor(s), and "
            f"{len(doc.methods)} method(s)."
            + (f" It reads from {', '.join(doc.tables_read)}." if doc.tables_read else "")
            + (f" It writes to {', '.join(doc.tables_written)}." if doc.tables_written else "")
        )
        responsibilities = []
        if doc.arch_layer:
            responsibilities.append(f"Operates within the {doc.arch_layer} layer as a {doc.arch_role}.")
        if doc.tables_read or doc.tables_written:
            responsibilities.append(
                f"Reads {', '.join(doc.tables_read) or 'no tables'} and writes "
                f"{', '.join(doc.tables_written) or 'no tables'}."
            )
        if doc.methods:
            responsibilities.append(f"Exposes methods including {', '.join(m['name'] for m in doc.methods[:5])}.")
        if not responsibilities:
            responsibilities.append("No strong deterministic signal for responsibilities beyond its structure.")
        main_short = doc.main_method.split(".")[-1].split("(")[0] if doc.main_method else None
        if main_short and doc.call_hierarchy.get(main_short):
            workflow = (f"Execution typically starts at `{doc.main_method}`, which calls "
                        f"{', '.join(doc.call_hierarchy[main_short][:6])}.")
        else:
            workflow = "Not enough deterministic signal to describe a workflow."
        return purpose, overview, responsibilities, workflow

    def _parse_narrative(self, text: str) -> tuple[str, str, list[str], str] | None:
        m = _NARRATIVE_RE.search(text or "")
        if not m:
            return None
        purpose, overview, resp_block, workflow = (g.strip() for g in m.groups())
        if not purpose or not overview:
            return None
        responsibilities = [
            line.lstrip("-*• ").strip() for line in resp_block.splitlines()
            if line.strip().lstrip("-*• ").strip()
        ]
        if not responsibilities:
            return None
        return purpose, overview, responsibilities, workflow or "Not described."

    def _narrative(self, doc: ClassDoc, *, use_llm: bool) -> tuple[str, str, list[str], str, str]:
        heuristic = self._heuristic_narrative(doc)
        if not use_llm:
            return (*heuristic, "heuristic")
        try:
            from backend.app.config.settings import get_settings
            from backend.app.llm.provider import get_provider
            provider = get_provider(get_settings().llm)
            resp = provider.generate(
                "You write per-class technical documentation for a Java codebase. Using ONLY the "
                "facts given — never invent behavior beyond them — answer in exactly this format:\n"
                "### PURPOSE\n<1-2 sentences>\n### OVERVIEW\n<short paragraph>\n"
                "### RESPONSIBILITIES\n- <item>\n- <item>\n### WORKFLOW\n<short paragraph or numbered steps>\n"
                "No text outside these four sections.",
                f"Facts:\n{self._facts_text(doc)}\n\nWrite the four sections.",
                temperature=0.1, max_tokens=400,
            )
            parsed = self._parse_narrative(resp.text)
            return (*(parsed or heuristic), "llm" if parsed else "heuristic")
        except Exception as exc:   # LLMUnavailable or anything else — never fail generation
            log.info("LLM narrative skipped for %s: %s", doc.name, exc)
            return (*heuristic, "heuristic")

    # --------------------------------------------------------------- driving
    def generate_all(self, *, package_prefix: str | None = None, limit: int | None = None,
                      force: bool = False, use_llm: bool = True):
        """Yield a `ClassDoc` for each target class not already in `class_docs`
        (unless `force`), scoped to `package_prefix` and capped by `limit`."""
        self.precompute()
        existing: set[int] = set()
        if not force:
            existing = {r["class_id"] for r in self.conn.execute(
                "SELECT class_id FROM class_docs WHERE project_id=?", (self.pid,))}
        targets = [cl for cl in self.target_classes(package_prefix) if cl["id"] not in existing]
        if limit is not None:
            targets = targets[:limit]
        for cl in targets:
            yield self.build_one(cl, use_llm=use_llm)

    def persist(self, doc: ClassDoc) -> None:
        self.conn.execute(
            "INSERT INTO class_docs (project_id, class_id, package, markdown, purpose_source, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, class_id) DO UPDATE SET "
            "package=excluded.package, markdown=excluded.markdown, "
            "purpose_source=excluded.purpose_source, generated_at=excluded.generated_at",
            (self.pid, doc.class_id, doc.package, doc.to_markdown(), doc.purpose_source, doc.generated_at),
        )
        self.conn.commit()
