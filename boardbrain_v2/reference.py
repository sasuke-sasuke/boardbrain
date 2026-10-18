"""Explicit, local-only setup import. Normal app launch never imports files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading

from .ingest import import_source
from .store import Store, dumps


def import_reference_bundle(store: Store, manifest_path: Path) -> dict:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != "boardbrain-reference-1":
        raise ValueError("Unsupported reference bundle format.")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
        raise ValueError("A reference bundle needs 1–10 files.")
    prepared = []
    for item in entries:
        name = item["name"]
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid reference filename.")
        if item["role"] not in ("boardview", "schematic", "reference"):
            raise ValueError("Invalid reference role.")
        path = manifest_path.parent / name
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 25 * 1024 * 1024:
            raise ValueError(f"Reference file missing or too large: {name}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Reference file changed or incomplete: {name}")
        prepared.append((item, content))
    # Validate every original before creating anything. Stable ID makes setup
    # resumable without duplicate workspaces, even after an interrupted import.
    identifier = "R" + hashlib.sha256(dumps(manifest).encode()).hexdigest()[:20]
    cases = {c["id"]: c for c in store.list_cases()}
    if identifier in cases:
        case = cases[identifier]
        if case["kind"] != "reference" or any(case[k] != str(manifest.get(k, "")).strip() for k in ("board_id", "revision")):
            raise ValueError("This reference workspace was edited. Import the files through the app instead of rerunning setup.")
    else:
        case = store.create_case({k: manifest.get(k, "") for k in ("title", "device", "board_id", "revision")} | {"kind": "reference"}, identifier=identifier)
    for item, content in prepared:
        source = store.add_source(case["id"], item["name"], content, item["role"])
        if source["status"] not in ("ready", "parsed", "partial"):
            import_source(store, case["id"], source["id"], threading.Event(), lambda *_: None)
    return store.case(case["id"])
