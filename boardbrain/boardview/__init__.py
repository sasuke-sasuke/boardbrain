from __future__ import annotations
import os
import json
import re
import struct
from typing import Dict, Any, List, Tuple, Optional

from ..config import SETTINGS
from ..netlist import canonicalize_net_name


_NET_RE = re.compile(r"\b(?:PP[A-Z0-9_.]+|[A-Z][A-Z0-9_.]*_[A-Z0-9_.]+)\b", re.IGNORECASE)
_REFDES_RE = re.compile(r"\b(?:TP|FB|C|R|L|D|Q|U|F|X|J|P)\d{1,5}\b", re.IGNORECASE)
_STR_ALLOWED = re.compile(r"^[A-Za-z0-9_./\-+:#]+$")
_BVRAW_HEADER = "BVRAW_FORMAT_3"


def detect_boardview_format(path: str, data: bytes) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    try:
        header = data[:128].decode("ascii", errors="ignore").splitlines()[0].strip()
    except Exception:
        header = ""
    if _BVRAW_HEADER in header:
        return "BVRAW_FORMAT_3"
    magic = data[:4]
    if magic in (b"BVR2", b"BVRE"):
        return "BVR2"
    if magic[:3] == b"BVR":
        return "BVR"
    if ext == ".pcb":
        from .xzzpcb_parser import verify_xzzpcb
        if verify_xzzpcb(data):
            return "XZZPCB"
    if ext == ".brd":
        if data.startswith(b"\x23\xe2\x63\x28") or b"str_length:" in data or b"var_data:" in data or b"BRDOUT:" in data:
            return "BRD"
    if ext == ".pcb":
        return "PCB_EMBEDDED_ZLIB"
    if ext == ".bvr":
        return None
    return None


def _extract_ascii_strings(data: bytes, min_len: int = 2, max_len: int = 80) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    i = 0
    n = len(data)
    while i < n:
        if 32 <= data[i] <= 126:
            start = i
            i += 1
            while i < n and 32 <= data[i] <= 126:
                i += 1
            length = i - start
            if min_len <= length <= max_len and i < n and data[i] == 0:
                s = data[start:i].decode("ascii", errors="ignore").strip()
                if s and _STR_ALLOWED.match(s):
                    out.append((start, s))
        i += 1
    return out


def _find_offset_runs(data: bytes, offsets: set[int], min_len: int = 20) -> List[Tuple[int, List[int]]]:
    runs: List[Tuple[int, List[int]]] = []
    i = 0
    n = len(data)
    while i + 4 <= n:
        val = struct.unpack_from("<I", data, i)[0]
        if val in offsets:
            start = i
            values: List[int] = []
            while i + 4 <= n:
                val = struct.unpack_from("<I", data, i)[0]
                if val not in offsets:
                    break
                values.append(val)
                i += 4
            if len(values) >= min_len:
                runs.append((start, values))
        else:
            i += 4
    return runs


def _choose_best_run(runs: List[Tuple[int, List[int]]], strings: Dict[int, str]) -> Tuple[int, List[str]]:
    best_start = -1
    best_items: List[str] = []
    best_score = 0
    for start, offsets in runs:
        items = [strings.get(o, "") for o in offsets]
        items = [i for i in items if i]
        uniq = len(set(items))
        score = len(items) + uniq
        if score > best_score:
            best_score = score
            best_start = start
            best_items = items
    return best_start, best_items


def _find_pin_table(
    data: bytes,
    comp_count: int,
    net_count: int,
    search_end: int,
) -> Tuple[int, int, str]:
    best = (-1, 0, "")
    stride_candidates = [8, 12, 16, 20, 24]
    orders = ["comp_net", "net_comp"]
    min_records = 50
    for stride in stride_candidates:
        for order in orders:
            i = 0
            while i + stride * min_records <= search_end:
                count = 0
                while i + stride * (count + 1) <= search_end:
                    base = i + stride * count
                    first = struct.unpack_from("<I", data, base)[0]
                    second = struct.unpack_from("<I", data, base + 4)[0]
                    if order == "comp_net":
                        comp_idx, net_idx = first, second
                    else:
                        net_idx, comp_idx = first, second
                    if comp_idx >= comp_count or net_idx >= net_count:
                        break
                    count += 1
                if count >= min_records and count > best[1]:
                    best = (i, count, order + f":{stride}")
                    i += stride * count
                else:
                    i += 4
    return best


def _build_net_refs_from_pin_table(
    data: bytes,
    start: int,
    count: int,
    order: str,
    stride: int,
    comps: List[str],
    nets: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    net_to_refs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for idx in range(count):
        base = start + stride * idx
        first = struct.unpack_from("<I", data, base)[0]
        second = struct.unpack_from("<I", data, base + 4)[0]
        if order == "comp_net":
            comp_idx, net_idx = first, second
        else:
            net_idx, comp_idx = first, second
        if comp_idx >= len(comps) or net_idx >= len(nets):
            continue
        refdes = comps[comp_idx].upper()
        net = canonicalize_net_name(nets[net_idx])
        if not net:
            continue
        kind = "TP" if refdes.startswith("TP") else ("P" if refdes.startswith("P") else refdes[:1])
        net_to_refs.setdefault(net, {})
        net_to_refs[net].setdefault(refdes, {"refdes": refdes, "kind": kind})
    return {n: list(refs.values()) for n, refs in net_to_refs.items()}


def parse_bvraw_format_3_text(text: str) -> Tuple[set[str], Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    nets: set[str] = set()
    refdes: set[str] = set()
    net_to_refs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    current_part: str | None = None

    lines = text.splitlines()
    if not lines:
        raise ValueError("empty_bvraw3_file")
    header = lines[0].strip()
    if _BVRAW_HEADER not in header:
        raise ValueError("missing_bvraw3_header")

    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("PART_NAME"):
            current_part = line.split("PART_NAME", 1)[1].strip().upper()
            if current_part:
                refdes.add(current_part)
            continue
        if line == "PART_END":
            current_part = None
            continue
        if line.startswith("PIN_NET"):
            net_raw = line.split("PIN_NET", 1)[1].strip()
            if not net_raw:
                continue
            net = canonicalize_net_name(net_raw)
            if not net:
                continue
            nets.add(net)
            if current_part:
                kind = "TP" if current_part.startswith("TP") else ("P" if current_part.startswith("P") else current_part[:1])
                net_to_refs.setdefault(net, {})
                net_to_refs[net].setdefault(current_part, {"refdes": current_part, "kind": kind})
            continue

    if not nets or not refdes:
        raise ValueError("no_nets_or_refdes_found")

    meta = {
        "format": "BVRAW_FORMAT_3",
        "nets_count": len(nets),
        "components_count": len(refdes),
        "components": sorted(refdes),
    }
    return nets, {n: list(refs.values()) for n, refs in net_to_refs.items()}, meta


def parse_bvraw_format_3(path: str) -> Tuple[set[str], Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    return parse_bvraw_format_3_text(text)


def parse_boardview(path: str) -> Tuple[set[str], Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    with open(path, "rb") as f:
        data = f.read()
    fmt = detect_boardview_format(path, data)
    if not fmt:
        raise ValueError("unsupported_boardview_format")
    if fmt == "BVRAW_FORMAT_3":
        return parse_bvraw_format_3(path)
    if fmt == "PCB_EMBEDDED_ZLIB":
        from ..pcb_boardview import parse_pcb_zlib_container
        return parse_pcb_zlib_container(path)
    if fmt == "BRD":
        from .brd_parser import parse_brd
        return parse_brd(path)
    if fmt == "XZZPCB":
        from .xzzpcb_parser import parse_xzzpcb
        return parse_xzzpcb(path)

    strings = _extract_ascii_strings(data)
    if not strings:
        raise ValueError("no_strings_found")
    strings_map = {off: s for off, s in strings}
    net_offsets = {off for off, s in strings if _NET_RE.fullmatch(s or "")}
    ref_offsets = {off for off, s in strings if _REFDES_RE.fullmatch(s or "")}
    if not net_offsets or not ref_offsets:
        raise ValueError("missing_net_or_refdes_strings")

    net_runs = _find_offset_runs(data, net_offsets, min_len=20)
    ref_runs = _find_offset_runs(data, ref_offsets, min_len=20)
    net_start, nets_raw = _choose_best_run(net_runs, strings_map)
    comp_start, comps_raw = _choose_best_run(ref_runs, strings_map)
    if not nets_raw or not comps_raw:
        raise ValueError("missing_net_or_component_tables")

    nets = [canonicalize_net_name(n) for n in nets_raw]
    nets = [n for n in nets if n]
    comps = [c.upper() for c in comps_raw if c]
    if len(nets) < 20 or len(comps) < 20:
        raise ValueError("insufficient_net_or_component_count")

    search_end = int(len(data) * 0.75)
    pin_start, pin_count, pin_meta = _find_pin_table(data, len(comps), len(nets), search_end)
    if pin_start < 0 or pin_count < 50:
        raise ValueError("pin_table_not_found")
    order, stride = pin_meta.split(":")
    stride_val = int(stride)
    net_to_refs = _build_net_refs_from_pin_table(
        data,
        pin_start,
        pin_count,
        order,
        stride_val,
        comps,
        nets,
    )

    meta = {
        "format": fmt,
        "nets_count": len(nets),
        "components_count": len(comps),
        "pin_records": pin_count,
        "net_table_offset": net_start,
        "component_table_offset": comp_start,
        "pin_table_offset": pin_start,
        "pin_table_layout": pin_meta,
    }
    return set(nets), net_to_refs, meta


def write_boardview_cache(
    board_id: str,
    nets: set[str],
    net_to_refs: Dict[str, List[Dict[str, Any]]],
    meta: Dict[str, Any],
) -> str:
    safe = re.sub(r"[^A-Z0-9_-]", "_", board_id.upper() or "UNKNOWN")
    path = os.path.join(SETTINGS.data_dir, "boardviews", f"{safe}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = dict(meta)
    meta.setdefault("board_id", board_id)
    meta.setdefault("net_count", len(nets))
    meta.setdefault("pairs_count", sum(len(v) for v in net_to_refs.values()))
    data = {
        "nets": sorted(nets),
        "net_to_refs": net_to_refs,
        "meta": meta,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path
