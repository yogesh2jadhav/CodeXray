"""
backend/app/indexing/config_extractor.py

Purpose
-------
Flatten configuration files (.properties / .yaml / .yml / .json / .xml) into
key/value `ConfigEntry` records, masking anything that looks like a secret.

Responsibility
--------------
- Parse each supported format into a flat dotted-key map.
- Redact values whose key matches `security.secret_key_patterns`
  (passwords, tokens, API keys, connection strings) when
  `security.redact_secrets` is on — the plan forbids exposing these (§47).
- Never raise on malformed config; return what parsed.

The resolved (non-secret) values feed Sprint 2's "SQL/table/column from
configuration" pattern (build plan §14).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from backend.app.config.settings import get_settings
from backend.app.models.records import ConfigEntry


def _secret_matchers() -> list[re.Pattern[str]]:
    sec = get_settings().security
    if not sec.redact_secrets:
        return []
    return [re.compile(p, re.IGNORECASE) for p in sec.secret_key_patterns]


def _is_secret(key: str, matchers: list[re.Pattern[str]]) -> bool:
    return any(m.search(key) for m in matchers)


def _flatten(obj, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, "" if obj is None else str(obj)))
    return out


def extract_config(text: str, extension: str) -> list[ConfigEntry]:
    matchers = _secret_matchers()
    pairs: list[tuple[str, str]] = []

    try:
        if extension == ".properties":
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "!")):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                elif ":" in line:
                    k, _, v = line.partition(":")
                else:
                    continue
                pairs.append((k.strip(), v.strip()))
        elif extension in (".yaml", ".yml"):
            import yaml
            pairs = _flatten(yaml.safe_load(text) or {})
        elif extension == ".json":
            pairs = _flatten(json.loads(text))
        elif extension == ".xml":
            root = ET.fromstring(text)
            for el in root.iter():
                if el.text and el.text.strip():
                    pairs.append((el.tag, el.text.strip()))
                for ak, av in el.attrib.items():
                    pairs.append((f"{el.tag}@{ak}", av))
    except Exception:
        return []

    entries: list[ConfigEntry] = []
    for key, value in pairs:
        if not key:
            continue
        secret = _is_secret(key, matchers)
        entries.append(ConfigEntry(key=key, value="***REDACTED***" if secret else value, is_secret=secret))
    return entries
