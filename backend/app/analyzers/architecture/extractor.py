"""
backend/app/analyzers/architecture/extractor.py

Purpose
-------
Derive a high-level architecture view of the project (build plan §39).

Responsibility
--------------
- Assign each class a ROLE from deterministic signals:
    name suffix (Service/Repository/Controller/Factory/Manager/Util/Job/Config),
    superclass/interface names, whether it generates SQL, whether it has a
    `main` method or is a Spark job, whether it is an enum/record/`*Constants`.
- Map roles to LAYERS (Service / Processing / Metadata / Data Access /
  Configuration / Domain / Entry Point / Other).
- Identify entry points, SQL-touching classes and external integrations.
- Emit `architecture.json` + `architecture.md` content and persist
  `architecture_components` rows.

Heuristic but transparent — every assignment records its evidence string.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from backend.app.models.database import get_connection, transaction

_ROLE_BY_SUFFIX = [
    (re.compile(r"(Controller|Resource|Endpoint|RestController)$"), "CONTROLLER", "Entry Point"),
    (re.compile(r"(Service|Manager|Facade|UseCase|Handler)$"), "SERVICE", "Service"),
    (re.compile(r"(Repository|Dao|DAO|Store|Mapper)$"), "REPOSITORY", "Data Access"),
    (re.compile(r"(Factory|Builder|Provider)$"), "FACTORY", "Processing"),
    (re.compile(r"(Job|Batch|Task|Runner|Processor|Pipeline)$"), "BATCH_JOB", "Processing"),
    (re.compile(r"(Config|Configuration|Settings|Properties)$"), "CONFIG", "Configuration"),
    (re.compile(r"(Util|Utils|Helper|Support)$"), "UTILITY", "Other"),
    (re.compile(r"(Constants|Const)$"), "CONSTANTS", "Configuration"),
    (re.compile(r"(Entity|Model|Dto|DTO|Record|Bean|Vo|VO)$"), "DOMAIN", "Domain"),
    (re.compile(r"(Metadata|Registry|Catalog)"), "METADATA", "Metadata"),
]
_SPARK_HINT = re.compile(r"\b(SparkSession|Dataset<|DataFrame|JavaRDD|sparkContext)\b")


@dataclass
class Component:
    class_id: int | None
    name: str
    fqn: str | None
    role: str
    layer: str
    file: str | None
    evidence: str


@dataclass
class Architecture:
    project: str
    components: list[Component] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    layers: dict[str, list[str]] = field(default_factory=dict)
    data_access: list[str] = field(default_factory=list)
    external_integrations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "project": self.project,
            "layers": self.layers,
            "entry_points": self.entry_points,
            "data_access": self.data_access,
            "external_integrations": self.external_integrations,
            "components": [
                {"name": c.name, "fqn": c.fqn, "role": c.role, "layer": c.layer,
                 "file": c.file, "evidence": c.evidence}
                for c in self.components
            ],
        }

    def to_markdown(self) -> str:
        lines = [f"# Architecture — {self.project}", ""]
        lines.append("## Layers\n")
        for layer, members in self.layers.items():
            lines.append(f"- **{layer}**: {', '.join(sorted(members)) or '(none)'}")
        lines.append("\n## Entry points\n")
        lines += [f"- {e}" for e in self.entry_points] or ["- (none detected)"]
        lines.append("\n## Data access\n")
        lines += [f"- {d}" for d in self.data_access] or ["- (none detected)"]
        if self.external_integrations:
            lines.append("\n## External integrations\n")
            lines += [f"- {x}" for x in self.external_integrations]
        lines.append("\n## Components\n")
        for c in sorted(self.components, key=lambda x: (x.layer, x.name)):
            lines.append(f"- `{c.name}` — {c.role} / {c.layer}  ({c.file})  — {c.evidence}")
        return "\n".join(lines)


class ArchitectureExtractor:
    def __init__(self, conn=None) -> None:
        self.conn = conn or get_connection()

    def _role_for(self, cls: dict, generates_sql: bool, has_main: bool, is_spark: bool) -> tuple[str, str, str]:
        name = cls["name"]
        parents = f'{cls["extends_name"] or ""} {cls["implements_names"] or ""}'
        if has_main:
            return "ENTRY_POINT", "Entry Point", "declares a public static void main"
        if is_spark:
            return "SPARK_JOB", "Processing", "references SparkSession/Dataset/RDD"
        for rx, role, layer in _ROLE_BY_SUFFIX:
            if rx.search(name):
                return role, layer, f"class name matches /{rx.pattern}/"
        if re.search(r"(Service|Repository|Dao)", parents):
            return "SERVICE", "Service", f"implements/extends {parents.strip()}"
        if generates_sql:
            return "REPOSITORY", "Data Access", "generates SQL"
        if cls["kind"] in ("enum", "record"):
            return "DOMAIN", "Domain", f"{cls['kind']} type"
        return "COMPONENT", "Other", "no strong role signal"

    def extract(self, project_id: int) -> Architecture:
        proj = self.conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
        arch = Architecture(project=proj["name"] if proj else str(project_id))

        sql_classes = {r["source_class"] for r in self.conn.execute(
            "SELECT DISTINCT source_class FROM sql_queries WHERE project_id=? AND source_class IS NOT NULL",
            (project_id,))}
        sql_classes |= {r["source_class"] for r in self.conn.execute(
            "SELECT DISTINCT source_class FROM dynamic_sql WHERE project_id=?", (project_id,))}

        main_classes = {r["cname"] for r in self.conn.execute(
            "SELECT c.name AS cname FROM methods m JOIN classes c ON c.id=m.class_id "
            "WHERE m.project_id=? AND m.name='main' AND m.is_static=1", (project_id,))}

        files = {r["id"]: r["path"] for r in self.conn.execute(
            "SELECT id, path FROM files WHERE project_id=?", (project_id,))}

        # detect spark by scanning imports
        spark_files = {r["file_id"] for r in self.conn.execute(
            "SELECT DISTINCT file_id FROM imports WHERE project_id=? AND imported LIKE 'org.apache.spark%'",
            (project_id,))}

        for cls in self.conn.execute("SELECT * FROM classes WHERE project_id=?", (project_id,)):
            gen_sql = cls["name"] in sql_classes
            has_main = cls["name"] in main_classes
            is_spark = cls["file_id"] in spark_files
            role, layer, why = self._role_for(dict(cls), gen_sql, has_main, is_spark)
            comp = Component(
                class_id=cls["id"], name=cls["name"], fqn=cls["fully_qualified_name"],
                role=role, layer=layer, file=files.get(cls["file_id"]), evidence=why,
            )
            arch.components.append(comp)
            arch.layers.setdefault(layer, []).append(cls["name"])
            if role in ("ENTRY_POINT", "CONTROLLER", "SPARK_JOB", "BATCH_JOB"):
                arch.entry_points.append(cls["name"])
            if role == "REPOSITORY":
                arch.data_access.append(cls["name"])

        for r in self.conn.execute(
            "SELECT DISTINCT imported FROM imports WHERE project_id=? AND ("
            "imported LIKE 'javax.jms%' OR imported LIKE 'org.springframework.kafka%' OR "
            "imported LIKE 'software.amazon%' OR imported LIKE 'com.amazonaws%' OR "
            "imported LIKE 'org.apache.http%' OR imported LIKE 'java.net.http%')", (project_id,)):
            arch.external_integrations.append(r["imported"])

        arch.entry_points = sorted(set(arch.entry_points))
        arch.data_access = sorted(set(arch.data_access))
        self._persist(project_id, arch)
        return arch

    def _persist(self, project_id: int, arch: Architecture) -> None:
        with transaction(self.conn) as cur:
            cur.execute("DELETE FROM architecture_components WHERE project_id=?", (project_id,))
            cur.executemany(
                "INSERT INTO architecture_components(project_id, class_id, class_name, fqn, role, layer, file, evidence) "
                "VALUES(?,?,?,?,?,?,?,?)",
                [(project_id, c.class_id, c.name, c.fqn, c.role, c.layer, c.file, c.evidence)
                 for c in arch.components],
            )
