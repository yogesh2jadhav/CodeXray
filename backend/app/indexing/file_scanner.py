"""
backend/app/indexing/file_scanner.py

Purpose
-------
Walk a project directory and produce a classified, hashed inventory of files.

Responsibility
--------------
- Recurse from the project root, skipping `scan.ignore_dirs` (.git, target, .venv…).
- Classify each file by extension into java / sql / config / doc / other.
- Skip files larger than `scan.max_file_bytes` (generated / vendored blobs).
- Compute a sha256 content hash — this is what drives incremental indexing
  (unchanged hash => the file is not re-parsed).

It performs no parsing and writes nothing; it just yields `ScannedFile` records.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterator

from backend.app.config.settings import ScanConfig, get_settings
from backend.app.models.records import Language, ScannedFile

_CHUNK = 1 << 20  # 1 MiB read buffer for hashing


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def classify(extension: str, scan: ScanConfig) -> Language:
    """Map a lower-case extension (with dot) to a Language bucket."""
    ext = extension.lower()
    if ext in scan.java_extensions:
        return Language.JAVA
    if ext in scan.sql_extensions:
        return Language.SQL
    if ext in scan.config_extensions:
        return Language.CONFIG
    if ext in scan.doc_extensions:
        return Language.DOC
    return Language.OTHER


class FileScanner:
    """Directory walker that yields classified, hashed files."""

    def __init__(self, scan: ScanConfig | None = None) -> None:
        self.scan = scan or get_settings().scan

    def scan_tree(self, root: str | Path) -> Iterator[ScannedFile]:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {root_path}")

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Prune ignored directories in-place so os.walk does not descend.
            dirnames[:] = [d for d in dirnames if d not in self.scan.ignore_dirs and not d.startswith(".")]

            for name in filenames:
                abs_path = Path(dirpath) / name
                try:
                    stat = abs_path.stat()
                except OSError:
                    continue
                if not abs_path.is_file() or abs_path.is_symlink():
                    continue

                ext = abs_path.suffix
                language = classify(ext, self.scan)

                # 'other' files that are also oversized are pure noise — drop them.
                if stat.st_size > self.scan.max_file_bytes and language in (Language.OTHER, Language.DOC):
                    continue

                yield ScannedFile(
                    rel_path=str(abs_path.relative_to(root_path)),
                    abs_path=str(abs_path),
                    language=language,
                    extension=ext.lower(),
                    size_bytes=stat.st_size,
                    content_hash=_sha256(abs_path),
                )

    def relevant_files(self, root: str | Path) -> list[ScannedFile]:
        """All files whose language is something the indexer cares about."""
        keep = {Language.JAVA, Language.SQL, Language.CONFIG, Language.DOC}
        return [f for f in self.scan_tree(root) if f.language in keep]
