from __future__ import annotations
import os
import hashlib
import re
from typing import Dict, Any, List, Tuple
import fitz  # PyMuPDF
from .config import SETTINGS
from .chunking import chunk_text
from .oai import embed_text
from .rag import upsert_text_chunks

TEXT_EXTS = {".txt", ".md", ".csv", ".tsv"}
PDF_EXTS = {".pdf"}

def infer_doc_type(path: str) -> str:
    p = path.lower()
    if "schem" in p or "schematic" in p:
        return "schematic"
    if "boardview" in p or "flexbv" in p:
        return "boardview"
    if "datasheet" in p:
        return "datasheet"
    if "manual" in p:
        return "manual"
    if "log" in p or "repairdesk" in p:
        return "log"
    return "note"


_RE_BOARD_ID = re.compile(r"\b\d{3}-\d{5}\b")
_RE_MODEL = re.compile(r"\bA\d{4}\b", re.IGNORECASE)


def infer_board_id(path: str) -> str | None:
    m = _RE_BOARD_ID.search(path)
    return m.group(0) if m else None


def infer_model(path: str) -> str | None:
    m = _RE_MODEL.search(path)
    return m.group(0).upper() if m else None


def rel_source_file(path: str) -> str:
    """Return a stable, human-readable source identifier relative to KB_RAW_DIR."""
    try:
        return os.path.relpath(path, SETTINGS.kb_raw_dir)
    except Exception:
        return os.path.basename(path)

def infer_device_family(path: str) -> str | None:
    """Infer device family from kb_raw subfolders, e.g. kb_raw/MacBook/A2338/820-02020/..."""
    rel = rel_source_file(path).replace("\\", "/")
    parts = [p for p in rel.split("/") if p and p not in (".", "..")]
    if not parts:
        return None
    # Accept a simple family name like MacBook/iPhone/iPad/Console/WindowsLaptop/PC/Other
    fam = parts[0]
    if re.fullmatch(r"[A-Za-z0-9_-]{2,32}", fam):
        return fam
    return None

def ingest_pdf(path: str) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    doc = fitz.open(path)
    for i in range(len(doc)):
        page = doc[i]
        text = (page.get_text("text") or "").strip()
        if not text:
            continue  # v1: skip image-only pages
        for j, chunk in enumerate(chunk_text(text)):
            meta = {
                "source_path": path,
                "source_file": rel_source_file(path),
                "page": i + 1,
                "chunk": j,
                "doc_type": infer_doc_type(path),
                "board_id": infer_board_id(path),
                "model": infer_model(path),
            "device_family": infer_device_family(path),
            }
            out.append((chunk, meta))
    doc.close()
    return out

def ingest_text_file(path: str) -> List[Tuple[str, Dict[str, Any]]]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    out: List[Tuple[str, Dict[str, Any]]] = []
    for j, ch in enumerate(chunk_text(text)):
        meta = {
            "source_path": path,
            "source_file": rel_source_file(path),
            "page": None,
            "chunk": j,
            "doc_type": infer_doc_type(path),
            "board_id": infer_board_id(path),
            "model": infer_model(path),
            "device_family": infer_device_family(path),
        }
        out.append((ch, meta))
    return out

def main() -> None:
    os.makedirs(SETTINGS.kb_raw_dir, exist_ok=True)
    all_items: List[Tuple[str, Dict[str, Any]]] = []

    for root, _, files in os.walk(SETTINGS.kb_raw_dir):
        for fn in files:
            path = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext in PDF_EXTS:
                all_items.extend(ingest_pdf(path))
            elif ext in TEXT_EXTS:
                all_items.extend(ingest_text_file(path))

    if not all_items:
        print("No ingestible text found. Tip: many schematics are image-only. Use schematic screenshots as CASE evidence in the app for v1.")
        return

    BATCH = 64
    for start in range(0, len(all_items), BATCH):
        batch = all_items[start:start+BATCH]
        docs = [b[0] for b in batch]
        metas = [b[1] for b in batch]
        embeds = embed_text(docs)
        ids = []
        for d, m in zip(docs, metas):
            key = f"{m['source_file']}|{m.get('page')}|{m.get('chunk')}|{hashlib.sha1(d.encode('utf-8')).hexdigest()}"
            ids.append(hashlib.sha1(key.encode("utf-8")).hexdigest())
        upsert_text_chunks(ids=ids, embeddings=embeds, documents=docs, metadatas=metas)
        print(f"Ingested {start+len(batch)}/{len(all_items)} chunks")

    print("Done. KB ready.")

if __name__ == "__main__":
    main()
