from __future__ import annotations
import os
import hashlib
import re
import json
import io
from typing import Dict, Any, List, Tuple
from contextlib import redirect_stderr
import fitz  # PyMuPDF
from .config import SETTINGS
from .chunking import chunk_text
from .oai import embed_text
from .rag import upsert_text_chunks
from .components import extract_refdes_tokens
from .netlist import extract_known_nets_from_texts, load_netlist, write_netlist_cache
from .net_refs import build_net_refs_from_texts, write_net_refs_cache, load_net_refs
from .boardview import parse_boardview, write_boardview_cache, detect_boardview_format

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

def infer_evidence_source(path: str) -> str:
    p = path.lower()
    if any(tok in p for tok in ("community", "reddit", "forum", "stackexchange", "stack overflow", "youtube", "discord")):
        return "community"
    doc_type = infer_doc_type(path)
    if doc_type in ("schematic", "datasheet", "manual"):
        return "schematic"
    if doc_type == "boardview":
        return "boardview"
    return "note"


_RE_BOARD_ID = re.compile(r"\b\d{3}-\d{5}(?:_\d{3}-\d{5})?\b")
_RE_BOARD_ID_MULTI = re.compile(r"\b\d{3}-\d{5}_\d{3}-\d{5}\b")
_RE_MODEL = re.compile(r"\bA\d{4}\b", re.IGNORECASE)


def infer_board_id(path: str) -> str | None:
    p = path.strip()
    m = _RE_BOARD_ID_MULTI.search(p)
    if m:
        return m.group(0)
    m = _RE_BOARD_ID.search(p)
    if m:
        return m.group(0)
    try:
        rel = os.path.relpath(p, SETTINGS.kb_raw_dir).replace("\\", "/")
        parts = [x for x in rel.split("/") if x]
        if len(parts) >= 3:
            candidate = parts[2]
            ignore = {
                "schematic",
                "boardview",
                "boardview_screens",
                "notes",
                "photos",
                "reference",
                "silkscreen",
                "attachments",
            }
            if candidate.lower() not in ignore and any(ch.isdigit() for ch in candidate):
                return candidate
    except Exception:
        pass
    return None


def infer_model(path: str) -> str | None:
    m = _RE_MODEL.search(path.strip())
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
    try:
        fitz.TOOLS.set_verbosity(0)
    except Exception:
        pass
    def _suppress_stderr():
        import contextlib
        import os
        import sys
        @contextlib.contextmanager
        def _ctx():
            fd = None
            try:
                fd = os.dup(2)
                with open(os.devnull, "w") as devnull:
                    os.dup2(devnull.fileno(), 2)
                    yield
            except Exception:
                yield
            finally:
                try:
                    if fd is not None:
                        os.dup2(fd, 2)
                        os.close(fd)
                except Exception:
                    pass
        return _ctx()
    try:
        with open(path, "rb") as f:
            header = f.read(5)
        if header != b"%PDF-":
            print(f"[pdf] skipped {path} reason=header_mismatch")
            return out
    except Exception as e:
        print(f"[pdf] skipped {path} reason={e}")
        return out
    err_buf = io.StringIO()
    try:
        with _suppress_stderr():
            with redirect_stderr(err_buf):
                doc = fitz.open(path)
    except Exception as e:
        print(f"[pdf] skipped {path} reason={e}")
        return out
    for i in range(len(doc)):
        try:
            with _suppress_stderr():
                page = doc[i]
                text = (page.get_text("text") or "").strip()
        except Exception:
            continue
        if not text:
            continue  # v1: skip image-only pages
        for j, chunk in enumerate(chunk_text(text)):
            meta = {
                "source_path": path,
                "source_file": rel_source_file(path),
                "page": i + 1,
                "chunk": j,
                "doc_type": infer_doc_type(path),
                "evidence_source": infer_evidence_source(path),
                "board_id": infer_board_id(path),
                "model": infer_model(path),
                "device_family": infer_device_family(path),
            }
            out.append((chunk, meta))
    try:
        doc.close()
    except Exception:
        pass
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
            "evidence_source": infer_evidence_source(path),
            "board_id": infer_board_id(path),
            "model": infer_model(path),
            "device_family": infer_device_family(path),
        }
        out.append((ch, meta))
    return out

def main() -> None:
    os.makedirs(SETTINGS.kb_raw_dir, exist_ok=True)
    all_items: List[Tuple[str, Dict[str, Any]]] = []
    component_counts: Dict[str, Dict[str, int]] = {}
    net_ref_texts: Dict[str, List[str]] = {}
    boardview_candidates: List[str] = []
    boardview_reports: Dict[str, Dict[str, Any]] = {}
    boardview_force = os.getenv("BOARDVIEW_FORCE", "0").strip() == "1"

    def _kb_paths_for_board(board_id: str) -> List[str]:
        matches = []
        if not board_id:
            return matches
        for root, dirs, _ in os.walk(SETTINGS.kb_raw_dir):
            path = root.replace("\\", "/")
            if board_id in path:
                matches.append(root)
        return sorted(set(matches))

    def _find_boardview_candidates(board_id: str, family: str | None, model: str | None) -> List[str]:
        if not board_id:
            return []
        roots = []
        if family:
            fam_root = os.path.join(SETTINGS.kb_raw_dir, family)
            if os.path.isdir(fam_root):
                roots.append(fam_root)
        if not roots:
            roots = [SETTINGS.kb_raw_dir]
        candidates = []
        for r in roots:
            for root, _, files in os.walk(r):
                path = root.replace("\\", "/")
                if board_id not in path:
                    continue
                if "boardview" not in path.lower():
                    continue
                for fn in files:
                    if fn.startswith("."):
                        continue
                    full = os.path.join(root, fn)
                    if board_id not in full:
                        continue
                    candidates.append(full)
        return sorted(set(candidates))

    def _choose_boardview_file(board_id: str, candidates: List[str]) -> str | None:
        if not candidates:
            return None
        family = None
        if candidates:
            parts = candidates[0].replace("\\", "/").split("/")
            if "kb_raw" in parts:
                idx = parts.index("kb_raw")
                if idx + 1 < len(parts):
                    family = parts[idx + 1]
        exts = []
        for p in candidates:
            ext = os.path.splitext(p)[1].lower()
            exts.append((p, ext))
        if family and family.lower() == "iphone":
            pref = [".pcb", ".bvr", ".brd", ".tvw"]
        else:
            pref = [".bvr", ".tvw", ".pcb", ".brd"]
        for ext in pref:
            matches = [p for p, e in exts if e == ext]
            if matches:
                return sorted(matches)[0]
        return sorted(candidates)[0]

    def _choose_boardview_files(board_id: str, candidates: List[str]) -> List[str]:
        if not candidates:
            return []
        family = None
        if candidates:
            parts = candidates[0].replace("\\", "/").split("/")
            if "kb_raw" in parts:
                idx = parts.index("kb_raw")
                if idx + 1 < len(parts):
                    family = parts[idx + 1]
        exts = []
        for p in candidates:
            ext = os.path.splitext(p)[1].lower()
            exts.append((p, ext))
        if family and family.lower() == "iphone":
            pref = [".pcb", ".bvr", ".brd", ".tvw"]
        else:
            pref = [".bvr", ".tvw", ".pcb", ".brd"]
        for ext in pref:
            matches = [p for p, e in exts if e == ext]
            if matches:
                return sorted(matches)
        return sorted(candidates)

    for root, _, files in os.walk(SETTINGS.kb_raw_dir):
        for fn in files:
            path = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            if fn.startswith("."):
                continue
            if ext in PDF_EXTS and not boardview_force:
                items = ingest_pdf(path)
                all_items.extend(items)
                board_id = infer_board_id(path) or ""
                if board_id:
                    for chunk, _ in items:
                        counts = extract_refdes_tokens(chunk)
                        for ref, ct in counts.items():
                            component_counts.setdefault(board_id, {})
                            component_counts[board_id][ref] = component_counts[board_id].get(ref, 0) + ct
                        net_ref_texts.setdefault(board_id, []).append(chunk)
            elif ext in TEXT_EXTS and not boardview_force:
                items = ingest_text_file(path)
                all_items.extend(items)
                board_id = infer_board_id(path) or ""
                if board_id:
                    for chunk, _ in items:
                        counts = extract_refdes_tokens(chunk)
                        for ref, ct in counts.items():
                            component_counts.setdefault(board_id, {})
                            component_counts[board_id][ref] = component_counts[board_id].get(ref, 0) + ct
                        net_ref_texts.setdefault(board_id, []).append(chunk)
            elif "boardview" in path.lower():
                if infer_board_id(path):
                    boardview_candidates.append(path)

    def _bv_priority(p: str) -> tuple:
        ext = os.path.splitext(p)[1].lower()
        return (0 if ext == ".bvr" else 1, p)
    boardview_candidates = sorted(set(boardview_candidates), key=_bv_priority)
    def _parser_id(parser: str | None) -> str:
        if not parser:
            return "unknown"
        if parser.upper() == "BVRAW_FORMAT_3":
            return "bvraw3"
        return parser.lower()
    if boardview_candidates:
        print(f"Found {len(boardview_candidates)} boardview candidate file(s).")
        for p in boardview_candidates:
            try:
                size = os.path.getsize(p)
            except Exception:
                size = -1
            ext = os.path.splitext(p)[1].lower()
            print(f"[boardview] found: {p} (size={size} bytes, ext={ext})")
    else:
        print("No boardview files detected under kb_raw/.../boardview/")

    boardview_done: set[str] = set()
    boardview_by_board: Dict[str, List[str]] = {}
    for path in boardview_candidates:
        board_id = infer_board_id(path) or ""
        if not board_id:
            continue
        boardview_by_board.setdefault(board_id, []).append(path)

    for board_id, paths in boardview_by_board.items():
        family = infer_device_family(paths[0]) if paths else None
        model = infer_model(paths[0]) if paths else None
        scoped = _find_boardview_candidates(board_id, family, model)
        paths_sorted = scoped if scoped else sorted(paths, key=_bv_priority)
        report = {
            "detected_boardview_files": [],
            "selected_boardview_file": None,
            "parser_used": None,
            "parse_status": "fail",
            "parse_error": None,
            "outputs_written": {
                "netlist_path": None,
                "net_refs_path": None,
                "boardview_cache_path": None,
                "components_path": None,
            },
            "counts": {
                "nets_count_from_boardview": 0,
                "refs_pairs_count_from_boardview": 0,
                "components_count_from_boardview": 0,
            },
        }
        for p in paths_sorted:
            try:
                size = os.path.getsize(p)
            except Exception:
                size = -1
            report["detected_boardview_files"].append(
                {"path": p, "size_bytes": size, "ext": os.path.splitext(p)[1].lower()}
            )
        selected_files: List[str] = []
        if "_" in board_id:
            selected_files = _choose_boardview_files(board_id, paths_sorted)
        if not selected_files:
            selected = _choose_boardview_file(board_id, paths_sorted)
            selected_files = [selected] if selected else []
        parser_used = None
        if selected_files:
            try:
                with open(selected_files[0], "rb") as f:
                    head = f.read(256)
                parser_used = detect_boardview_format(selected_files[0], head)
            except Exception:
                parser_used = "unknown"
        if not selected_files:
            report["parse_error"] = "no_boardview_candidates"
            boardview_reports[board_id] = report
            continue
        report["selected_boardview_file"] = selected_files[0] if selected_files else None
        report["selected_boardview_files"] = selected_files
        report["parser_used"] = parser_used
        if len(selected_files) > 1:
            print(f"[boardview] selected (multi): {len(selected_files)} files")
            for p in selected_files:
                print(f"[boardview] selected: {p}")
        else:
            print(f"[boardview] selected: {selected_files[0]} (parser={parser_used})")
        if parser_used in (None, "unknown"):
            report["parse_error"] = "unsupported_format"
            report["parse_status"] = "unsupported_format"
            report["parser_used"] = parser_used or "unknown"
            report["source_reason"] = "boardview_unsupported"
            boardview_reports[board_id] = report
            print(f"[boardview] parse failed: {selected_files[0]} (unsupported format)")
            continue
        nets: set[str] = set()
        net_to_refs: Dict[str, List[Dict[str, Any]]] = {}
        components: set[str] = set()
        parse_errors: List[str] = []
        formats: List[str] = []
        parse_status = "success"
        sub_ids = board_id.split("_") if "_" in board_id else []

        def _merge_refs(dest: Dict[str, Dict[str, Dict[str, Any]]], src: Dict[str, List[Dict[str, Any]]], sub_board: str | None) -> None:
            for net, refs in (src or {}).items():
                dest.setdefault(net, {})
                for r in refs:
                    if isinstance(r, dict):
                        ref = (r.get("refdes") or "").upper()
                        entry = dict(r)
                    else:
                        ref = str(r).upper()
                        entry = {"refdes": ref}
                    if not ref:
                        continue
                    if sub_board:
                        entry.setdefault("sub_board", sub_board)
                    dest[net].setdefault(ref, entry)

        merged_refs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for selected in selected_files:
            sub_board = None
            if sub_ids:
                for part in sub_ids:
                    if part in os.path.basename(selected):
                        sub_board = part
                        break
            try:
                n, r, meta = parse_boardview(selected)
            except Exception as e:
                parse_errors.append(f"{os.path.basename(selected)}: {e}")
                parse_status = "partial_success"
                continue
            nets.update(n or [])
            _merge_refs(merged_refs, r or {}, sub_board)
            comps = meta.get("components") or []
            components.update([c for c in comps if c])
            fmt = meta.get("format") or parser_used or "unknown"
            formats.append(fmt)
            if meta.get("parse_status") == "partial_success":
                parse_status = "partial_success"

        if not nets:
            report["parse_error"] = "; ".join(parse_errors) if parse_errors else "parse_failed"
            report["parse_status"] = "fail"
            report["source_reason"] = "boardview_parse_failed"
            boardview_reports[board_id] = report
            print(f"[boardview] parse failed: {selected_files[0]} ({report['parse_error']})")
            continue

        parser_used = "+".join(sorted(set(formats))) if formats else (parser_used or "unknown")
        parser_id = _parser_id(parser_used)
        net_to_refs = {n: list(refs.values()) for n, refs in merged_refs.items()}
        meta = {
            "format": parser_used,
            "source": f"boardview_{parser_id}",
            "source_paths": selected_files,
            "source_files": [rel_source_file(p) for p in selected_files],
            "board_id": board_id,
            "model": infer_model(selected_files[0]) or "",
            "parse_status": parse_status,
        }
        if parse_errors:
            meta["parse_error"] = "; ".join(parse_errors)
        boardview_cache_path = write_boardview_cache(board_id, nets, net_to_refs, meta)
        net_meta = dict(meta)
        net_meta["source"] = f"boardview_{parser_id}"
        net_meta["net_count"] = len(nets)
        net_meta["pp_net_count"] = len([n for n in nets if n.startswith("PP")])
        net_meta["signal_net_count"] = len([n for n in nets if not n.startswith("PP")])
        netlist_path = write_netlist_cache(board_id, nets, net_meta)
        refs_pairs_count = sum(len(v) for v in net_to_refs.values())
        refs_meta = {
            "source": f"boardview_{parser_id}",
            "board_id": board_id,
            "model": infer_model(selected) or "",
            "net_count": len(net_to_refs),
            "pairs_count": refs_pairs_count,
        }
        net_refs_path = write_net_refs_cache(board_id, net_to_refs, refs_meta)
        components = sorted(components) if components else meta.get("components") or sorted({r for refs in net_to_refs.values() for r in [d.get("refdes") for d in refs if d.get("refdes")]})
        boardview_pairs = refs_pairs_count
        if board_id in net_ref_texts and refs_pairs_count < 20:
            known_nets = set(nets)
            known_refdes = set(components)
            text_refs, text_meta = build_net_refs_from_texts(
                net_ref_texts.get(board_id, []),
                known_nets,
                known_refdes,
            )
            if text_meta.get("pairs_count", 0) > refs_pairs_count:
                net_to_refs = text_refs
                refs_meta = {
                    "source": f"boardview_{parser_id}+kb_text",
                    "source_reason": "boardview_partial_kb_text_refs",
                    "board_id": board_id,
                    "model": infer_model(selected) or "",
                    "net_count": len(net_to_refs),
                    "pairs_count": text_meta.get("pairs_count", 0),
                    "boardview_pairs_count": boardview_pairs,
                    "kb_text_pairs_count": text_meta.get("pairs_count", 0),
                }
                net_refs_path = write_net_refs_cache(board_id, net_to_refs, refs_meta)
        prefix_histogram: Dict[str, int] = {}
        for ref in components:
            if ref.startswith("FB"):
                prefix = "FB"
            elif ref.startswith("TP"):
                prefix = "TP"
            else:
                prefix = ref[:1]
            prefix_histogram[prefix] = prefix_histogram.get(prefix, 0) + 1
        comp_dir = os.path.join(SETTINGS.data_dir, "components")
        os.makedirs(comp_dir, exist_ok=True)
        comp_path = os.path.join(comp_dir, f"{board_id}.json")
        import datetime
        comp_payload = {
            "board_id": board_id,
            "components": components,
            "refdes": components,
            "component_count": len(components),
            "prefix_histogram": prefix_histogram,
            "source": f"boardview_{parser_id}",
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }
        with open(comp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(comp_payload, indent=2))
        parse_status = meta.get("parse_status") or "success"
        report["parse_status"] = parse_status
        if meta.get("parse_error"):
            report["parse_error"] = meta.get("parse_error")
        if parse_status == "partial_success":
            report["source_reason"] = "boardview_partial"
        else:
            report["source_reason"] = "boardview_success"
        report["parser_used"] = parser_used
        report["counts"]["nets_count_from_boardview"] = len(nets)
        report["counts"]["refs_pairs_count_from_boardview"] = boardview_pairs
        if refs_meta.get("source_reason") == "boardview_partial_kb_text_refs":
            report["counts"]["refs_pairs_count_from_kb_text"] = refs_meta.get("kb_text_pairs_count", 0)
        report["counts"]["components_count_from_boardview"] = len(components)
        report["outputs_written"]["netlist_path"] = netlist_path
        report["outputs_written"]["net_refs_path"] = net_refs_path
        report["outputs_written"]["boardview_cache_path"] = boardview_cache_path
        report["outputs_written"]["components_path"] = comp_path
        boardview_reports[board_id] = report
        boardview_done.add(board_id)
        print(f"[boardview] parse success: {board_id} ({len(nets)} nets, {refs_meta['pairs_count']} refs)")

    if boardview_reports:
        report_dir = os.path.join(SETTINGS.data_dir, "ingest_reports")
        os.makedirs(report_dir, exist_ok=True)
        for board_id, report in boardview_reports.items():
            report_path = os.path.join(report_dir, f"{board_id}.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"[boardview] ingest report: {report_path}")

    if boardview_force and not boardview_done:
        print("BOARDVIEW_FORCE=1 set; no successful boardview parses. Skipping kb_text ingest.")
        return

    if not all_items and not boardview_done:
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

    boardview_failed = {bid for bid, rep in boardview_reports.items() if rep.get("parse_status") == "fail"}

    if component_counts:
        import datetime
        comp_dir = os.path.join(SETTINGS.data_dir, "components")
        os.makedirs(comp_dir, exist_ok=True)
        for board_id, counts in component_counts.items():
            if board_id in boardview_done or board_id in boardview_failed:
                continue
            filtered = {k: v for k, v in counts.items() if v >= 2}
            if not filtered:
                continue
            path = os.path.join(comp_dir, f"{board_id}.json")
            data = {
                "board_id": board_id,
                "refdes": sorted(filtered.keys()),
                "counts": filtered,
                "source": "kb_text",
                "updated_at": datetime.datetime.utcnow().isoformat(),
            }
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))
        print("Component index updated.")

    if net_ref_texts and not boardview_force:
        for board_id, texts in net_ref_texts.items():
            if board_id in boardview_done or board_id in boardview_failed:
                continue
            existing_refs, existing_meta = load_net_refs(board_id)
            if existing_refs and str(existing_meta.get("source", "")).startswith("boardview_"):
                continue
            known_nets, _ = load_netlist(board_id=board_id)
            if not known_nets:
                known_nets, _ = extract_known_nets_from_texts(texts)
            ref_counts = component_counts.get(board_id, {})
            known_refdes = {k for k, v in ref_counts.items() if v >= 2}
            if not known_nets or not known_refdes:
                continue
            net_to_refdes, meta = build_net_refs_from_texts(texts, known_nets, known_refdes)
            meta["kb_paths"] = _kb_paths_for_board(board_id)
            meta["net_count"] = len(known_nets)
            meta["refdes_count"] = len(known_refdes)
            write_net_refs_cache(board_id, net_to_refdes, meta)
        print("Net→RefDes index updated.")

if __name__ == "__main__":
    main()
