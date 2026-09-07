"""
backend/app/indexing/indexer.py

Purpose
-------
Orchestrate a full or incremental index run for one project (build plan §55, §70).

Responsibility
--------------
- Scan the project tree (FileScanner).
- For each relevant file, decide (via content hash) whether it needs work.
- Route by language:
    java   -> parse_java -> symbols -> SQL-in-Java extraction
    sql    -> SQL statement extraction
    config -> flatten + secret redaction
    doc    -> store text for RAG
- Persist through IndexWriter.
- Isolate failures per file: one unparuseable file is logged to `parse_failures`
  and the run continues.
- Return an `IndexReport` with counts for the API / CLI to display.

This module contains the control flow only; every real transformation lives in
`analyzers/*` or the other `indexing/*` modules.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.analyzers.architecture.extractor import ArchitectureExtractor
from backend.app.analyzers.dynamic_sql.analyzer import DynamicSqlAnalyzer
from backend.app.analyzers.java.java_parser import parse_java
from backend.app.config.settings import get_settings
from backend.app.graph.builder import GraphBuilder
from backend.app.indexing.config_extractor import extract_config
from backend.app.indexing.file_scanner import FileScanner
from backend.app.indexing.index_writer import IndexWriter
from backend.app.indexing.sql_extractor import extract_from_java_file, extract_from_sql_file
from backend.app.indexing.symbol_extractor import extract_symbols
from backend.app.models.records import Language

log = logging.getLogger("codexray.indexer")


@dataclass
class IndexReport:
    project_id: int
    project_name: str
    mode: str
    files_total: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    dynamic_sql_sites: int = 0
    graph_edges: int = 0
    semantic_chunks: int = 0
    architecture_components: int = 0
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "mode": self.mode,
            "files_total": self.files_total,
            "files_indexed": self.files_indexed,
            "files_skipped": self.files_skipped,
            "files_failed": self.files_failed,
            "dynamic_sql_sites": self.dynamic_sql_sites,
            "graph_edges": self.graph_edges,
            "semantic_chunks": self.semantic_chunks,
            "architecture_components": self.architecture_components,
            "failures": self.failures[:50],
        }


class ProjectIndexer:
    def __init__(self, writer: IndexWriter | None = None) -> None:
        self.settings = get_settings()
        self.writer = writer or IndexWriter()
        self.scanner = FileScanner(self.settings.scan)

    def index_project(self, name: str, root: str | Path, *, force: bool = False) -> IndexReport:
        root = str(Path(root).resolve())
        incremental = self.settings.index.incremental and not force
        project_id = self.writer.upsert_project(name, root)

        files = self.scanner.relevant_files(root)
        mode = "incremental" if incremental else "full"
        report = IndexReport(project_id=project_id, project_name=name, mode=mode, files_total=len(files))
        run_id = self.writer.start_run(project_id, mode, len(files))

        for sf in files:
            try:
                file_id, changed = self.writer.register_file(project_id, sf, incremental)
                if incremental and not changed:
                    report.files_skipped += 1
                    continue

                self.writer.clear_file_data(project_id, file_id)
                self._index_one(project_id, file_id, sf)
                self.writer.mark_indexed(file_id)
                report.files_indexed += 1
            except Exception as exc:  # never abort the whole run for one file
                log.exception("Indexing failed for %s", sf.rel_path)
                parser = {"java": "java", "sql": "sql"}.get(sf.language.value, "scanner")
                self.writer.record_failure(project_id, sf.rel_path, parser, repr(exc))
                report.files_failed += 1
                report.failures.append({"file": sf.rel_path, "error": repr(exc)})

        # Sprint 2: interprocedural dynamic-SQL reconstruction over the whole
        # project (not per file), rebuilt wholesale each run.
        if self.settings.dynamic_sql.enabled:
            try:
                java_files = [(f.rel_path, f.abs_path) for f in files if f.language == Language.JAVA]
                records = DynamicSqlAnalyzer().analyze_project(java_files)
                self.writer.clear_dynamic_sql(project_id)
                report.dynamic_sql_sites = self.writer.write_dynamic_sql(project_id, records)
            except Exception as exc:  # never fail the run for the optional stage
                log.exception("dynamic-SQL analysis stage failed")
                self.writer.record_failure(project_id, "<project>", "dynamic_sql", repr(exc))

        # Sprint 4: dependency graph, semantic index and architecture view are all
        # derived whole-project views — rebuilt after the facts are in place.
        if self.settings.graph.enabled:
            try:
                report.graph_edges = GraphBuilder(self.writer.conn).build(project_id)
            except Exception:
                log.exception("graph build stage failed")
                self.writer.record_failure(project_id, "<project>", "graph", "graph build failed")

        try:
            from backend.app.retrieval.semantic import SemanticIndex
            report.semantic_chunks = SemanticIndex(conn=self.writer.conn).build(project_id)
        except Exception:
            log.exception("semantic index stage failed")
            self.writer.record_failure(project_id, "<project>", "semantic", "semantic index failed")

        try:
            arch = ArchitectureExtractor(self.writer.conn).extract(project_id)
            report.architecture_components = len(arch.components)
        except Exception:
            log.exception("architecture extraction stage failed")
            self.writer.record_failure(project_id, "<project>", "architecture", "architecture extraction failed")

        try:
            from backend.app.analyzers.summarizer import MethodSummarizer
            MethodSummarizer(self.writer.conn).run(project_id)
        except Exception:
            log.exception("method-summary stage failed")
            self.writer.record_failure(project_id, "<project>", "summarizer", "method summaries failed")

        status = "ok" if report.files_failed == 0 else "error"
        self.writer.finish_run(run_id, report.files_indexed, report.files_skipped, report.files_failed, status)
        self.writer.mark_project_indexed(project_id)
        return report

    # ------------------------------------------------------------------ routing
    def _index_one(self, project_id: int, file_id: int, sf) -> None:
        text = Path(sf.abs_path).read_text(encoding="utf-8", errors="replace")

        if sf.language == Language.JAVA:
            java = parse_java(text)
            if java.error:
                self.writer.record_failure(project_id, sf.rel_path, java.parser, java.error)
            symbols = extract_symbols(java)
            self.writer.write_java(project_id, file_id, java, symbols)
            sql_occ = extract_from_java_file(text, java)
            if sql_occ:
                self.writer.write_sql(project_id, file_id, sql_occ)

        elif sf.language == Language.SQL:
            self.writer.write_sql(project_id, file_id, extract_from_sql_file(text))

        elif sf.language == Language.CONFIG:
            self.writer.write_config(project_id, file_id, extract_config(text, sf.extension))

        elif sf.language == Language.DOC:
            self.writer.write_document(project_id, file_id, Path(sf.rel_path).name, text)
