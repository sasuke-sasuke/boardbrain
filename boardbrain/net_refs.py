from __future__ import annotations
import json
import os
import re
import datetime
from typing import Dict, Any, List, Tuple, Optional

from .config import SETTINGS
from .netlist import canonicalize_net_name, extract_net_tokens, load_netlist
from .components import load_component_index


_REF_RE = re.compile(r"\b(?:TP|FB|C|R|L|D|Q|U|F|X|J|P)\d{1,5}\b", re.IGNORECASE)
_PREF_ORDER = ["TP", "P", "C", "L", "J", "R", "D", "Q", "U", "F", "X"]
_PREF_RANK = {p: i for i, p in enumerate(_PREF_ORDER)}


def _cache_path(board_id: str, model: str = "") -> str:
    key = board_id or model or "unknown"
    safe = re.sub(r"[^A-Z0-9_-]", "_", key.upper())
    return os.path.join(SETTINGS.data_dir, "net_refs", f"{safe}.json")


def _extract_refdes_tokens(text: str, known_refdes: set) -> List[str]:
    out: List[str] = []
    for m in _REF_RE.finditer(text or ""):
        token = m.group(0).upper()
        if token in known_refdes:
            out.append(token)
    return out


def build_net_refs_from_texts(
    texts: List[str],
    known_nets: set,
    known_refdes: set,
) -> Tuple[Dict[str, List[str]], Dict[str, Any]]:
    scores: Dict[str, Dict[str, int]] = {}
    evidence: Dict[str, Dict[str, int]] = {}

    for text in texts:
        lines = text.splitlines() if text else []
        nets_by_line: List[List[str]] = []
        refs_by_line: List[List[str]] = []
        for line in lines:
            nets = []
            for raw in extract_net_tokens(line):
                canon = canonicalize_net_name(raw)
                if canon in known_nets:
                    nets.append(canon)
            refs = _extract_refdes_tokens(line, known_refdes)
            nets_by_line.append(sorted(set(nets)))
            refs_by_line.append(sorted(set(refs)))

        for i, nets in enumerate(nets_by_line):
            if not nets or len(nets) > 3:
                continue
            refs_same = refs_by_line[i]
            if refs_same and len(refs_same) <= 5:
                for n in nets:
                    for r in refs_same:
                        scores.setdefault(n, {})
                        evidence.setdefault(n, {})
                        scores[n][r] = scores[n].get(r, 0) + 3
                        evidence[n][r] = evidence[n].get(r, 0) + 1
            for j in range(max(0, i - 2), min(len(refs_by_line), i + 3)):
                if j == i:
                    continue
                refs_adj = refs_by_line[j]
                if not refs_adj or len(refs_adj) > 5:
                    continue
                for n in nets:
                    for r in refs_adj:
                        scores.setdefault(n, {})
                        evidence.setdefault(n, {})
                        scores[n][r] = scores[n].get(r, 0) + 1
                        evidence[n][r] = evidence[n].get(r, 0) + 1

    net_to_refdes: Dict[str, List[str]] = {}
    for net, ref_scores in scores.items():
        items = []
        for refdes, score in ref_scores.items():
            items.append(
                {
                    "refdes": refdes,
                    "score": score,
                    "evidence_count": evidence.get(net, {}).get(refdes, 0),
                }
            )
        items.sort(key=lambda x: (-x["score"], -x["evidence_count"], x["refdes"]))
        net_to_refdes[net] = [i["refdes"] for i in items[:30]]

    meta = {
        "net_count": len(net_to_refdes),
        "refdes_count": len(known_refdes),
        "pairs_count": sum(len(v) for v in net_to_refdes.values()),
    }
    return net_to_refdes, meta


def load_net_refs(
    board_id: str,
    model: str = "",
    case: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, List[str]], Dict[str, Any]]:
    if not board_id and case:
        board_id = case.get("board_id", "") or ""
    if not model and case:
        model = case.get("model", "") or ""
    path = _cache_path(board_id, model)
    meta: Dict[str, Any] = {"cache_path": path}
    if not os.path.exists(path):
        meta["source"] = "missing"
        return {}, meta
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta_loaded = data.get("meta", meta)
        meta_loaded.setdefault("cache_path", path)
        if "pairs_count" not in meta_loaded and "pairs_count" in data:
            meta_loaded["pairs_count"] = data.get("pairs_count")
        if "source" not in meta_loaded and "source" in data:
            meta_loaded["source"] = data.get("source")
        net_to_refdes = data.get("net_to_refdes")
        if not net_to_refdes:
            net_to_refdes = data.get("pairs", {})
        return net_to_refdes or {}, meta_loaded
    except Exception:
        meta["source"] = "error"
        return {}, meta


def get_measure_points(
    board_id: str,
    net: str,
    case: Optional[Dict[str, Any]] = None,
    k: int = 10,
) -> List[str]:
    nets, _ = load_netlist(board_id=board_id, case=case)
    canon = canonicalize_net_name(net)
    if not canon or canon not in nets:
        return []
    net_refs, _ = load_net_refs(board_id=board_id, case=case)
    items = net_refs.get(canon, []) or []
    def _rank(ref: str, kind: str, idx: int) -> tuple:
        prefix = "TP" if ref.startswith("TP") else kind
        pref_rank = _PREF_RANK.get(prefix, 99)
        short_penalty = 0
        if prefix in ("J", "P") and len(ref) <= 2:
            short_penalty = 50
        return (pref_rank, short_penalty, idx, ref)
    ranked: List[Tuple[str, int, str]] = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            ref = (item.get("refdes") or "").upper()
            kind = (item.get("kind") or ref[:1]).upper()
            if not ref:
                continue
            ranked.append((ref, i, kind))
        else:
            ref = str(item).upper()
            if not ref:
                continue
            kind = "TP" if ref.startswith("TP") else ("FB" if ref.startswith("FB") else ref[:1])
            ranked.append((ref, i, kind))
    ranked_sorted = sorted(ranked, key=lambda x: _rank(x[0], x[2], x[1]))
    return [r for r, _, _ in ranked_sorted[:k] if r]


def get_measurement_points(
    board_id: str,
    net: str,
    case: Optional[Dict[str, Any]] = None,
    k: int = 10,
) -> List[str]:
    return get_measure_points(board_id, net, case=case, k=k)


def measurement_points_for_net(
    board_id: str,
    net: str,
    case: Optional[Dict[str, Any]] = None,
    k: int = 6,
    known_components: Optional[set] = None,
) -> List[str]:
    nets, _ = load_netlist(board_id=board_id, case=case)
    canon = canonicalize_net_name(net)
    if not canon or canon not in nets:
        return []
    if known_components is None:
        known_components, _ = load_component_index(board_id=board_id, case=case)
    net_refs, _ = load_net_refs(board_id=board_id, case=case)
    items = net_refs.get(canon, []) or []
    refs: List[str] = []
    for item in items:
        if isinstance(item, dict):
            ref = (item.get("refdes") or "").upper()
        else:
            ref = str(item).upper()
        if ref and ref in known_components:
            refs.append(ref)
    if not refs:
        return []
    def _rank(ref: str) -> tuple:
        if ref.startswith("TPU") or ref.startswith("TP"):
            group = 0
        elif ref.startswith("C"):
            group = 1
        elif ref.startswith("L"):
            group = 2
        elif ref.startswith("R"):
            group = 3
        elif ref.startswith("U"):
            group = 4
        else:
            group = 5
        return (group, ref)
    ranked = sorted(set(refs), key=_rank)
    return ranked[:k]


def get_measurement_points_from_cache(
    net: str,
    net_to_refdes: Dict[str, List[Any]],
    known_refdes: set,
    limit: int = 8,
) -> List[str]:
    canon = canonicalize_net_name(net)
    if not canon or canon not in net_to_refdes:
        return []
    refs: List[str] = []
    for item in net_to_refdes.get(canon, []) or []:
        if isinstance(item, dict):
            ref = (item.get("refdes") or "").upper()
        else:
            ref = str(item).upper()
        if ref and ref in known_refdes:
            refs.append(ref)
    if not refs:
        return []
    def _rank(ref: str) -> tuple:
        if ref.startswith(("TPU", "TPE", "TPJ", "TPP", "TP")):
            group = 0
        elif ref.startswith("T"):
            group = 1
        elif ref.startswith("C"):
            group = 2
        elif ref.startswith("L"):
            group = 3
        elif ref.startswith("R"):
            group = 4
        elif ref.startswith("FB"):
            group = 5
        else:
            group = 6
        return (group, ref)
    ranked = sorted(set(refs), key=_rank)
    return ranked[:limit]


def write_net_refs_cache(
    board_id: str,
    net_to_refdes: Dict[str, List[str]],
    meta: Dict[str, Any],
) -> str:
    import datetime
    path = _cache_path(board_id, "")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta.setdefault("updated_at", datetime.datetime.utcnow().isoformat())
    meta.setdefault("source", "kb_text")
    meta.setdefault("board_id", board_id)
    data = {"net_to_refdes": net_to_refdes, "meta": meta}
    if "source" in meta:
        data["source"] = meta["source"]
    if "updated_at" in meta:
        data["updated_at"] = meta["updated_at"]
    if "pairs" not in meta:
        data["pairs"] = {
            k: [d["refdes"] if isinstance(d, dict) else str(d) for d in v]
            for k, v in net_to_refdes.items()
        }
        data["pairs_count"] = sum(len(v) for v in data["pairs"].values())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path
