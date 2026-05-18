"""v0.16 — MCP external-server catalog.

Persists a list of external MCP servers the user has registered with
LocalFlow at ``<home>/mcp_clients.json``. Each entry has a name +
shell command (e.g., ``npx @modelcontextprotocol/server-filesystem
/some/dir``). The probe command spawns the server, lists its tools,
and updates the catalog with the inventory.

No tool execution machinery in v0.16 — see app/mcp/client.py for the
scope note. The catalog is intentionally simple JSON so users can
hand-edit it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CatalogEntry:
    name: str
    command: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    last_probed_ok: bool | None = None
    last_probed_error: str | None = None


@dataclass
class Catalog:
    entries: list[CatalogEntry] = field(default_factory=list)


def catalog_path(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / "mcp_clients.json"
    env = os.environ.get("LOCALFLOW_HOME")
    base = Path(env) if env else (Path.home() / ".localflow")
    return base / "mcp_clients.json"


def load(home: Path | None = None) -> Catalog:
    path = catalog_path(home)
    if not path.exists():
        return Catalog()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Catalog()
    entries = []
    for item in raw.get("entries", []) or []:
        if not isinstance(item, dict):
            continue
        if "name" not in item or "command" not in item:
            continue
        entries.append(
            CatalogEntry(
                name=str(item["name"]),
                command=str(item["command"]),
                tools=list(item.get("tools") or []),
                last_probed_ok=item.get("last_probed_ok"),
                last_probed_error=item.get("last_probed_error"),
            )
        )
    return Catalog(entries=entries)


def save(catalog: Catalog, home: Path | None = None) -> None:
    path = catalog_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": [
            {
                "name": e.name,
                "command": e.command,
                "tools": list(e.tools),
                "last_probed_ok": e.last_probed_ok,
                "last_probed_error": e.last_probed_error,
            }
            for e in catalog.entries
        ]
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def add_entry(catalog: Catalog, name: str, command: str) -> CatalogEntry:
    for existing in catalog.entries:
        if existing.name == name:
            existing.command = command
            return existing
    new = CatalogEntry(name=name, command=command)
    catalog.entries.append(new)
    return new


def remove_entry(catalog: Catalog, name: str) -> bool:
    before = len(catalog.entries)
    catalog.entries = [e for e in catalog.entries if e.name != name]
    return len(catalog.entries) != before
