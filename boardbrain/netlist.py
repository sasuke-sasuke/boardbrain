from __future__ import annotations
import json
import os
import re
import difflib
from typing import Dict, Any, List, Set, Tuple, Optional

from .config import SETTINGS
from .rag import get_collection


_NET_RE = re.compile(
    r"\b(?:PP[A-Z0-9_.]+|[A-Z][A-Z0-9_.]*_[A-Z0-9_.]+|[0-9][A-Z0-9_.]*_[A-Z0-9_.]+)\b",
    re.IGNORECASE,
)
_MEAS_KEY_RE = re.compile(r"\b((?:CHECK_|VERIFY_|MEASURE_|TEST_|READ_))([A-Z0-9_.]+?)(_(?:R2G|DIODE))?\b", re.IGNORECASE)
_NET_STOPLIST = {
    "PLAN_UNCHANGED",
    "REQUESTED_MEASUREMENTS",
    "EVIDENCE_USED",
}
_SIGNAL_SUFFIXES = {
    "EN",
    "PWR",
    "CLK",
    "RST",
    "RESET",
    "SCL",
    "SDA",
    "INT",
    "SW",
    "PG",
    "PGOOD",
    "WAKE",
    "SLEEP",
    "BOOT",
    "ISENSE",
    "VSENSE",
}
_SIGNAL_EXCLUDE = {"ALLOW", "IGNORE", "PREFIX"}
_NETLIST_CACHE: Dict[str, Tuple[Set[str], Dict[str, Any]]] = {}


def normalize_net_name(name: str) -> str:
    n = name.strip().upper()
    n = n.replace(".", "_")
    n = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", n)
    n = re.sub(r"[\s\-/]+", "_", n)
    n = re.sub(r"_+", "_", n)
    return n


def canonicalize_net_name(name: str) -> str:
    return normalize_net_name(name)


def _infer_board_id(case: Dict[str, Any]) -> str:
    b = (case.get("board_id") or "").strip()
    if b:
        return b
    case_id = (case.get("case_id") or "").strip()
    m = re.search(r"\b\d{3}-\d{5}(?:_\d{3}-\d{5})?\b", case_id)
    return m.group(0) if m else ""


def _infer_model(case: Dict[str, Any]) -> str:
    model = (case.get("model") or "").strip()
    case_id = (case.get("case_id") or "").strip()
    m = re.search(r"\bA\d{4}\b", f"{model} {case_id}", re.IGNORECASE)
    return m.group(0).upper() if m else model


def _extract_nets_from_text(text: str) -> Set[str]:
    counts = _extract_net_counts_from_text(text)
    nets, _ = _filter_net_counts(counts)
    return nets


def _extract_net_counts_from_text(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for m in _NET_RE.finditer(text or ""):
        token = normalize_net_name(m.group(0))
        if len(token) < 5:
            continue
        if "__" in token:
            continue
        if token in _NET_STOPLIST:
            continue
        counts[token] = counts.get(token, 0) + 1
    return counts


def _has_signal_suffix(token: str) -> bool:
    return any(token.endswith(suf) or token.endswith(f"_{suf}") for suf in _SIGNAL_SUFFIXES)


def _filter_net_counts(counts: Dict[str, int]) -> Tuple[Set[str], Dict[str, int]]:
    nets: Set[str] = set()
    filtered: Dict[str, int] = {}
    for token, count in counts.items():
        if token.startswith("PP"):
            if count < 2:
                continue
            nets.add(token)
            filtered[token] = count
            continue
        if "_" not in token:
            continue
        if len(token) < 5 or len(token) > 40:
            continue
        if "__" in token:
            continue
        if token in _NET_STOPLIST:
            continue
        has_digit = any(ch.isdigit() for ch in token)
        has_suffix = _has_signal_suffix(token)
        if not (has_digit or has_suffix):
            continue
        if any(word in token for word in _SIGNAL_EXCLUDE) and not (has_digit or has_suffix):
            continue
        min_count = 2 if has_digit else 3
        if count < min_count:
            continue
        nets.add(token)
        filtered[token] = count
    return nets, filtered


def extract_known_nets_from_texts(texts: List[str]) -> Tuple[Set[str], Dict[str, int]]:
    counts: Dict[str, int] = {}
    for text in texts:
        chunk_counts = _extract_net_counts_from_text(text)
        for k, v in chunk_counts.items():
            counts[k] = counts.get(k, 0) + v
    return _filter_net_counts(counts)


def _cache_path(board_id: str, model: str) -> str:
    key = board_id or model or "unknown"
    safe = re.sub(r"[^A-Z0-9_-]", "_", key.upper())
    return os.path.join(SETTINGS.data_dir, "netlists", f"{safe}.json")


def _load_cached_netlist(path: str) -> Tuple[Set[str], Dict[str, Any]]:
    if not os.path.exists(path):
        return set(), {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        nets = set(data.get("nets", []))
        meta = data.get("meta", {})
        return nets, meta
    except Exception:
        return set(), {}


def _ingest_report_path(board_id: str) -> str:
    safe = re.sub(r"[^A-Z0-9_-]", "_", board_id.upper() or "UNKNOWN")
    return os.path.join(SETTINGS.data_dir, "ingest_reports", f"{safe}.json")


def _load_ingest_report(board_id: str) -> Dict[str, Any]:
    if not board_id:
        return {}
    path = _ingest_report_path(board_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cached_netlist(path: str, nets: Set[str], meta: Dict[str, Any]) -> None:
    import datetime
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if "updated_at" not in meta:
        meta["updated_at"] = datetime.datetime.utcnow().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        payload = {"nets": sorted(nets), "meta": meta}
        for key in ("source", "updated_at", "net_count", "pp_net_count", "signal_net_count"):
            if key in meta:
                payload[key] = meta[key]
        json.dump(payload, f, indent=2)


def write_netlist_cache(
    board_id: str,
    nets: Set[str],
    meta: Dict[str, Any],
) -> str:
    path = _cache_path(board_id, "")
    _save_cached_netlist(path, nets, meta)
    return path


def _get_kb_paths(board_id: str, model: str) -> List[str]:
    kb_root = SETTINGS.kb_raw_dir
    matches = []
    if not os.path.isdir(kb_root):
        return matches
    board_id = (board_id or "").strip()
    model = (model or "").strip()
    for root, dirs, _ in os.walk(kb_root):
        path = root.replace("\\", "/")
        if board_id and board_id in path:
            matches.append(root)
        elif model and model in path:
            matches.append(root)
    return sorted(set(matches))


def _expected_kb_paths(case: Dict[str, Any], board_id: str, model: str) -> List[str]:
    family = (case.get("device_family") or "MacBook").strip()
    board_id = (board_id or "").strip()
    model = (model or "").strip()
    paths: List[str] = []
    fam_l = family.lower()
    if fam_l.startswith("iphone"):
        base = os.path.join(SETTINGS.kb_raw_dir, "iPhone")
        if board_id:
            matches = []
            for root, dirs, _ in os.walk(base):
                path = root.replace("\\", "/")
                if board_id in path:
                    matches.append(root)
            if matches:
                for m in sorted(set(matches), key=len):
                    paths.append(m)
                    for entry in os.listdir(m):
                        full = os.path.join(m, entry)
                        if os.path.isdir(full):
                            paths.append(full)
                return paths
        if model:
            parts = [SETTINGS.kb_raw_dir, "iPhone", model]
            if board_id:
                parts.append(board_id)
            base = os.path.join(*parts)
            if os.path.isdir(base):
                paths.append(base)
                for entry in os.listdir(base):
                    full = os.path.join(base, entry)
                    if os.path.isdir(full):
                        paths.append(full)
            return paths
    parts = [SETTINGS.kb_raw_dir, family]
    if model:
        parts.append(model)
    if board_id:
        parts.append(board_id)
    base = os.path.join(*parts)
    if os.path.isdir(base):
        paths.append(base)
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if os.path.isdir(full):
                paths.append(full)
    return paths


def _extract_from_kb_text(paths: List[str]) -> Tuple[Set[str], Dict[str, int]]:
    counts: Dict[str, int] = {}
    def _suppress_stderr():
        import contextlib
        import os
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
    for root in paths:
        for dirpath, _, files in os.walk(root):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                full = os.path.join(dirpath, fn)
                if ext in (".txt", ".md", ".csv", ".tsv"):
                    try:
                        with open(full, "r", encoding="utf-8", errors="ignore") as f:
                            chunk_counts = _extract_net_counts_from_text(f.read())
                            for k, v in chunk_counts.items():
                                counts[k] = counts.get(k, 0) + v
                    except Exception:
                        continue
                elif ext == ".pdf":
                    try:
                        with open(full, "rb") as f:
                            header = f.read(5)
                        if header != b"%PDF-":
                            print(f"[pdf] skipped {full} reason=header_mismatch")
                            continue
                        import fitz
                        try:
                            fitz.TOOLS.set_verbosity(0)
                        except Exception:
                            pass
                        from contextlib import redirect_stderr
                        import io
                        err_buf = io.StringIO()
                        with _suppress_stderr():
                            with redirect_stderr(err_buf):
                                doc = fitz.open(full)
                        for i in range(len(doc)):
                            try:
                                with _suppress_stderr():
                                    text = (doc[i].get_text("text") or "").strip()
                            except Exception:
                                continue
                            if text:
                                chunk_counts = _extract_net_counts_from_text(text)
                                for k, v in chunk_counts.items():
                                    counts[k] = counts.get(k, 0) + v
                        doc.close()
                    except Exception as e:
                        print(f"[pdf] skipped {full} reason={e}")
                        continue
    nets, filtered = _filter_net_counts(counts)
    return nets, filtered


def _extract_from_chroma(board_id: str, model: str) -> Tuple[Set[str], Dict[str, int]]:
    counts: Dict[str, int] = {}
    try:
        col = get_collection()
        where: Dict[str, Any] = {}
        if board_id:
            where = {"board_id": board_id, "doc_type": "schematic"}
        elif model:
            where = {"model": model, "doc_type": "schematic"}
        if not where:
            return set()
        res = col.get(where=where, include=["documents", "metadatas"])
        for doc in res.get("documents", []) or []:
            chunk_counts = _extract_net_counts_from_text(doc)
            for k, v in chunk_counts.items():
                counts[k] = counts.get(k, 0) + v
    except Exception:
        return set(), {}
    nets, filtered = _filter_net_counts(counts)
    return nets, filtered


def get_known_nets(case: Dict[str, Any]) -> Tuple[Set[str], Dict[str, Any]]:
    board_id = _infer_board_id(case)
    model = _infer_model(case)
    kb_paths = _get_kb_paths(board_id, model)
    cache_path = _cache_path(board_id, model)
    report = _load_ingest_report(board_id)
    report_status = (report.get("parse_status") or "").lower()

    nets, counts = _extract_from_chroma(board_id, model)
    source = "ingest_artifact" if nets else None
    meta: Dict[str, Any] = {}
    if nets:
        meta = {
            "source": source,
            "board_id": board_id,
            "model": model,
            "counts": counts,
        }

    if not nets:
        nets, meta = _load_cached_netlist(cache_path)
        if nets:
            source = meta.get("source") or "cache"

    if not nets:
        nets, counts = _extract_from_kb_text(kb_paths)
        source = "kb_text"
        meta = {
            "source": source,
            "board_id": board_id,
            "model": model,
            "counts": counts,
        }
        if report_status != "fail":
            _save_cached_netlist(cache_path, nets, meta)

    meta.update(
        {
            "cache_path": cache_path,
            "kb_paths": kb_paths,
            "net_count": len(nets),
        }
    )
    return nets, meta


def load_netlist(board_id: str = "", model: str = "", case: Optional[Dict[str, Any]] = None) -> Tuple[Set[str], Dict[str, Any]]:
    if not board_id and case:
        board_id = _infer_board_id(case)
    if not model and case:
        model = _infer_model(case)
    key = board_id or model or "unknown"
    if key in _NETLIST_CACHE:
        return _NETLIST_CACHE[key]
    cache_path = _cache_path(board_id, model)
    report = _load_ingest_report(board_id)
    report_path = _ingest_report_path(board_id) if board_id else ""
    report_status = (report.get("parse_status") or "").lower()
    report_selected = report.get("selected_boardview_file")
    report_selected_files = report.get("selected_boardview_files") or []
    report_parser = report.get("parser_used")
    report_error = report.get("parse_error")
    report_files = report.get("detected_boardview_files") or []
    report_files_preview = [f.get("path", "") for f in report_files[:3] if f.get("path")]
    report_files_count = len(report_files)

    nets: Set[str] = set()
    meta: Dict[str, Any] = {}
    nets_cached, meta_cached = _load_cached_netlist(cache_path)
    cache_source = (meta_cached.get("source") or "").lower()
    if report_status == "unsupported_format":
        nets, meta = set(), {}
        meta["source"] = "boardview_unsupported"
        meta["source_reason"] = "boardview_unsupported"
    elif report_status in ("success", "partial_success"):
        nets, meta = nets_cached, meta_cached
        if nets:
            meta["source_reason"] = "boardview_partial" if report_status == "partial_success" else "boardview_success"
        else:
            meta = {}
    elif report_status == "fail":
        nets, meta = set(), {}
    elif cache_source.startswith("boardview_"):
        nets, meta = nets_cached, meta_cached
        meta["source_reason"] = "boardview_cache_no_report"
    skip_fallback = False
    if not nets:
        if report_status == "fail":
            if report_error == "xzzpcb_missing_or_invalid_key":
                source_reason = "boardview_key_missing"
                meta = {
                    "source": "boardview_key_missing",
                    "source_reason": source_reason,
                    "board_id": board_id,
                    "model": model,
                }
                nets = set()
                skip_fallback = True
            else:
                source_reason = "boardview_parse_failed_fallback"
        elif report_status == "unsupported_format":
            source_reason = "boardview_unsupported"
            meta = {
                "source": "boardview_unsupported",
                "source_reason": source_reason,
                "board_id": board_id,
                "model": model,
            }
            nets = set()
            skip_fallback = True
        elif report_status in ("success", "partial_success"):
            source_reason = "boardview_success_missing_netlist_fallback"
        elif report:
            source_reason = "boardview_missing_fallback"
        else:
            source_reason = "boardview_missing"
        if not skip_fallback:
            if case:
                kb_paths = _expected_kb_paths(case, board_id, model)
            else:
                kb_paths = _get_kb_paths(board_id, model)
            nets, counts = _extract_from_kb_text(kb_paths)
            meta = {
                "source": "kb_text",
                "source_reason": source_reason,
                "board_id": board_id,
                "model": model,
                "counts": counts,
            }
            if nets and source_reason != "boardview_parse_failed_fallback":
                _save_cached_netlist(cache_path, nets, meta)
    meta.setdefault("cache_path", cache_path)
    meta.setdefault("board_id", board_id)
    meta.setdefault("model", model)
    meta.setdefault("net_count", len(nets))
    if report_path:
        meta["ingest_report_path"] = report_path if os.path.exists(report_path) else ""
    if report:
        meta["boardview_file_used"] = report_selected
        meta["boardview_files_used"] = report_selected_files
        meta["boardview_parse_status"] = report.get("parse_status")
        meta["boardview_parse_error"] = report_error
        meta["boardview_parser_used"] = report_parser
        meta["boardview_files_count"] = report_files_count
        meta["boardview_files_preview"] = report_files_preview
    kb_paths = meta.get("kb_paths") or []
    if case:
        kb_paths = _expected_kb_paths(case, board_id, model)
    if not kb_paths:
        reason = "expected path not found"
        if not board_id or not model:
            reason = "board_id/model missing"
        meta["kb_paths_reason"] = reason
    meta["kb_paths"] = kb_paths
    _NETLIST_CACHE[key] = (nets, meta)
    return nets, meta


def is_valid_net(board_id: str, net_name: str, case: Optional[Dict[str, Any]] = None) -> bool:
    nets, _ = load_netlist(board_id=board_id, case=case)
    return canonicalize_net_name(net_name) in nets


def suggest_nets(board_id: str, net_name: str, k: int = 5, case: Optional[Dict[str, Any]] = None) -> List[str]:
    nets, _ = load_netlist(board_id=board_id, case=case)
    target = canonicalize_net_name(net_name)
    if not nets:
        return []
    prefix = target.split("_", 1)[0]
    same_prefix = [n for n in nets if n.startswith(prefix)]
    ranked = difflib.get_close_matches(target, sorted(same_prefix), n=k, cutoff=0.6)
    if len(ranked) < k:
        ranked += difflib.get_close_matches(target, sorted(nets), n=k - len(ranked), cutoff=0.6)
    return ranked[:k]


def choose_primary_power_rail(board_id: str, case: Optional[Dict[str, Any]] = None) -> Optional[str]:
    nets, _ = load_netlist(board_id=board_id, case=case)
    device_family = (case.get("device_family") if case else "") or ""
    model = (case.get("model") if case else "") or ""
    if not device_family and model.lower().startswith("iphone"):
        device_family = "iPhone"
    if device_family.lower() == "iphone":
        for cand in ("PP_VDD_MAIN", "PPVDD_MAIN", "PP_BATT", "PPBATT", "PPVBAT", "PP_VBAT", "PPVBUS", "VBUS"):
            matches = [n for n in nets if n.startswith(cand)]
            if matches:
                return sorted(matches)[0]
    if "PPBUS_AON" in nets:
        return "PPBUS_AON"
    g3h = [n for n in nets if n.startswith("PPBUS_G3H")]
    if g3h:
        return sorted(g3h)[0]
    for cand in ("PPVBAT", "PPDCIN", "PPBUS", "PPBUS_S5"):
        matches = [n for n in nets if n.startswith(cand)]
        if matches:
            return sorted(matches)[0]
    for cand in ("CHARGER_IN", "VBUS", "ADP_", "DCIN", "VIN", "VCC", "VDD", "ALW", "BATT"):
        matches = [n for n in nets if n.startswith(cand)]
        if matches:
            return sorted(matches)[0]
    return None


def assert_known_net_or_refuse(net_name: str, case: Dict[str, Any], known_nets: Set[str]) -> Tuple[bool, str]:
    normalized = normalize_net_name(net_name)
    if normalized and normalized in known_nets:
        return True, ""
    msg = (
        f"I can't confirm net '{net_name}' exists in the loaded {case.get('board_id','')} netlist.\n"
        "Please provide the exact net name or a schematic page/snippet. I can give generic guidance if needed."
    )
    return False, msg


def extract_nets_from_text(text: str) -> List[str]:
    return [normalize_net_name(n) for n in _NET_RE.findall(text or "")]


def extract_net_tokens(text: str) -> List[str]:
    out: List[str] = []
    for m in _NET_RE.finditer(text or ""):
        token = m.group(0)
        canon = canonicalize_net_name(token)
        if not canon:
            continue
        if canon.startswith(("CHECK_", "VERIFY_", "MEASURE_", "READ_")):
            continue
        if canon.startswith("PP"):
            out.append(token)
            continue
        if "_" in canon or any(ch.isdigit() for ch in canon):
            out.append(token)
    return out


def split_measurement_key(token: str) -> Tuple[str, str, str]:
    m = _MEAS_KEY_RE.search(token or "")
    if not m:
        return "", "", ""
    prefix = (m.group(1) or "").upper()
    net = m.group(2) or ""
    suffix = m.group(3) or ""
    return prefix, net, suffix


def enforce_net_guardrail(
    board_id: str,
    text: str,
    plan_items: Optional[List[Dict[str, Any]]] = None,
    case: Optional[Dict[str, Any]] = None,
    fuzzy_threshold: float = 0.97,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    nets, _ = load_netlist(board_id=board_id, case=case)
    invalid: List[str] = []
    auto_fixes: List[Dict[str, str]] = []
    suggestions: Dict[str, List[str]] = {}

    def _best_match(candidate: str) -> Optional[str]:
        if not nets:
            return None
        best = None
        best_score = 0.0
        best_count = 0
        for n in nets:
            score = difflib.SequenceMatcher(a=candidate, b=n).ratio()
            if score > best_score:
                best_score = score
                best = n
                best_count = 1
            elif score == best_score:
                best_count += 1
        if best and best_score >= fuzzy_threshold and best_count == 1:
            return best
        return None

    def _looks_like_net(token: str) -> bool:
        if token.startswith("PP"):
            return True
        if any(ch.isdigit() for ch in token):
            return True
        return _has_signal_suffix(token)

    replacements: Dict[str, str] = {}
    for raw in extract_net_tokens(text):
        canon = canonicalize_net_name(raw)
        if canon in nets:
            continue
        fixed = None
        if canon == "PPBUS_G3H" and "PPBUS_AON" in nets:
            fixed = "PPBUS_AON"
            auto_fixes.append({"from": raw, "to": fixed, "reason": "apple_silicon_mapping"})
        else:
            match = _best_match(canon)
            if match:
                fixed = match
                auto_fixes.append({"from": raw, "to": fixed, "reason": "fuzzy_match"})
        if fixed:
            replacements[raw] = fixed
        else:
            if not _looks_like_net(canon):
                continue
            invalid.append(raw)
            suggestions[raw] = suggest_nets(board_id, raw, k=5, case=case)

    key_replacements: Dict[str, str] = {}
    for m in _MEAS_KEY_RE.finditer(text or ""):
        prefix, net_part, suffix = m.group(1), m.group(2), m.group(3) or ""
        canon = canonicalize_net_name(net_part)
        if canon in nets:
            continue
        fixed = None
        if canon == "PPBUS_G3H" and "PPBUS_AON" in nets:
            fixed = "PPBUS_AON"
            auto_fixes.append({"from": net_part, "to": fixed, "reason": "apple_silicon_mapping"})
        else:
            match = _best_match(canon)
            if match:
                fixed = match
                auto_fixes.append({"from": net_part, "to": fixed, "reason": "fuzzy_match"})
        if fixed:
            key_replacements[m.group(0)] = f"CHECK_{fixed}{suffix}"
        else:
            invalid.append(net_part)
            suggestions[net_part] = suggest_nets(board_id, net_part, k=5, case=case)

    def _replace_text(src: str) -> str:
        if not replacements and not invalid:
            return src
        def _sub_key(m: re.Match) -> str:
            token = m.group(0)
            if token in key_replacements:
                return key_replacements[token]
            prefix, net_part, suffix = split_measurement_key(token)
            if net_part in invalid:
                return f"{prefix}[UNKNOWN_NET]{suffix}"
            canon = canonicalize_net_name(net_part)
            for raw in invalid:
                if canonicalize_net_name(raw) == canon:
                    return f"{prefix}[UNKNOWN_NET]{suffix}"
            return token
        def _sub(m: re.Match) -> str:
            token = m.group(0)
            if token in replacements:
                return replacements[token]
            if token in invalid:
                return "[UNKNOWN_NET]"
            canon = canonicalize_net_name(token)
            for k, v in replacements.items():
                if canonicalize_net_name(k) == canon:
                    return v
            if canon in [canonicalize_net_name(x) for x in invalid]:
                return "[UNKNOWN_NET]"
            return token
        updated = _MEAS_KEY_RE.sub(_sub_key, src)
        return _NET_RE.sub(_sub, updated)

    sanitized_text = _replace_text(text)
    if invalid:
        note = (
            f"Net(s) not found in loaded {board_id} netlist: {', '.join(sorted(set(invalid)))}. "
            "Please confirm the exact net name or provide a schematic/boardview snippet."
        )
        sanitized_text = sanitized_text.rstrip() + "\n\n" + note

    cleaned_items: List[Dict[str, Any]] = []
    invalid_plan_items: List[str] = []
    for item in plan_items or []:
        key = item.get("key", "")
        key_u = key.upper()
        meta = dict(item.get("meta") or {})
        target = item.get("net") or meta.get("net")
        if isinstance(target, str) and target.upper().startswith(("CHECK_", "VERIFY_", "MEASURE_", "READ_")):
            for p in ("CHECK_", "VERIFY_", "MEASURE_", "READ_"):
                if target.upper().startswith(p):
                    target = target[len(p):]
                    break
        prefix, net_part, suffix = split_measurement_key(key_u)
        base = key_u
        if suffix and base.endswith(suffix):
            base = base[: -len(suffix)]
        for p in ("CHECK_", "VERIFY_", "MEASURE_", "TEST_", "READ_"):
            if base.startswith(p):
                base = base[len(p):]
                break
        if net_part and net_part.endswith("_R2G"):
            net_part = net_part[: -len("_R2G")]
        if net_part and net_part.endswith("_DIODE"):
            net_part = net_part[: -len("_DIODE")]
        if net_part:
            if target and canonicalize_net_name(target) in nets:
                target = canonicalize_net_name(target)
            else:
                target = net_part
        if not target:
            target = base or ""
        node = item.get("node") or meta.get("node") or ""
        if node.startswith("PORT:"):
            meta["net_valid"] = True
            meta["needs_confirmation"] = False
            new_item = dict(item)
            new_item["meta"] = meta
            cleaned_items.append(new_item)
            continue

        if not target:
            meta["net_valid"] = False
            meta["needs_confirmation"] = True
            new_item = dict(item)
            new_item["meta"] = meta
            cleaned_items.append(new_item)
            invalid_plan_items.append(key)
            continue

        canon = canonicalize_net_name(target)
        fixed = None
        if canon in nets:
            meta["net_valid"] = True
            meta["needs_confirmation"] = False
        else:
            if canon == "PPBUS_G3H" and "PPBUS_AON" in nets:
                fixed = "PPBUS_AON"
                auto_fixes.append({"from": target, "to": fixed, "reason": "apple_silicon_mapping"})
            else:
                match = _best_match(canon)
                if match:
                    fixed = match
                    auto_fixes.append({"from": target, "to": fixed, "reason": "fuzzy_match"})
            if fixed:
                meta["net_valid"] = True
                meta["needs_confirmation"] = False
                target = fixed
            else:
                meta["net_valid"] = False
                meta["needs_confirmation"] = True
                meta["net_original"] = target
                meta["suggestions"] = suggest_nets(board_id, target, k=5, case=case)
                invalid_plan_items.append(key)

        new_item = dict(item)
        if not meta.get("net_valid"):
            new_item["net"] = "[UNKNOWN_NET]"
            new_item["key"] = f"CHECK_[UNKNOWN_NET]{suffix}"
        else:
            new_item["net"] = target
        new_item["meta"] = meta
        if net_part and meta.get("net_valid"):
            normalized = f"CHECK_{canonicalize_net_name(target)}{suffix}"
            if prefix and prefix.upper() != "CHECK_":
                meta["key_normalized_from"] = key
            new_item["key"] = normalized
        elif not net_part and meta.get("net_valid") and target:
            new_item["key"] = f"CHECK_{canonicalize_net_name(target)}"
        cleaned_items.append(new_item)

    report = {
        "board_id": board_id,
        "invalid_nets_detected": sorted(set(invalid)),
        "invalid_plan_items": sorted(set(invalid_plan_items)),
        "auto_fixes_applied": auto_fixes,
        "suggestions": suggestions,
    }
    return sanitized_text, cleaned_items, report
