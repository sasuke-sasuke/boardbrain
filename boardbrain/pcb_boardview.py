from __future__ import annotations
import hashlib
import os
import re
import zlib
import lzma
import json
from typing import Dict, Any, List, Tuple, Iterable, Optional

from .netlist import canonicalize_net_name
from .config import SETTINGS
from .boardview import parse_bvraw_format_3_text


_NET_RE = re.compile(
    r"\b(?:PP[A-Z0-9_.]+|[A-Z][A-Z0-9_.]*_[A-Z0-9_.]+)\b",
    re.IGNORECASE,
)
_REFDES_RE = re.compile(
    r"\b(?:TPU|TPE|TPJ|TPP|TP|T|FB|C|R|L|D|Q|U|F|X|J|P|S|Y|Z)\d{1,5}[A-Z0-9]*\b",
    re.IGNORECASE,
)
_ZLIB_HEADERS = (b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda")
_MAGIC_GZIP = b"\x1f\x8b"
_MAGIC_XZ = b"\xfd7zXZ\x00"
_MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"
_MAGIC_LZ4 = b"\x04\x22\x4d\x18"

try:
    import zstandard as _zstd
except Exception:
    _zstd = None

try:
    import lz4.frame as _lz4_frame
except Exception:
    _lz4_frame = None


def _is_text_like(data: bytes) -> bool:
    if not data:
        return False
    printable = 0
    for b in data:
        if b in (9, 10, 13) or 32 <= b <= 126:
            printable += 1
    return printable / max(1, len(data)) >= 0.85


def _guess_encoding(data: bytes) -> str:
    try:
        data.decode("utf-8")
        return "utf-8"
    except Exception:
        return "latin-1"

def _scan_magic_offsets(data: bytes, magic: bytes, max_hits: int) -> List[int]:
    hits: List[int] = []
    start = 0
    while True:
        i = data.find(magic, start)
        if i < 0:
            break
        hits.append(i)
        if len(hits) >= max_hits:
            break
        start = i + 1
    return hits


def _scan_zlib_offsets(data: bytes, max_hits: int = 500) -> List[int]:
    hits: List[int] = []
    for sig in _ZLIB_HEADERS:
        hits.extend(_scan_magic_offsets(data, sig, max_hits))
        if len(hits) >= max_hits:
            break
    return sorted(set(hits))[:max_hits]


def _scan_deflate_offsets(data: bytes, max_hits: int = 200) -> List[int]:
    hits: List[int] = []
    step = 4096
    for i in range(0, len(data), step):
        hits.append(i)
        if len(hits) >= max_hits:
            break
    return hits


def _expand_offsets(offsets: List[int], backtrack: Tuple[int, ...] = (0, 1, 2, 4, 8, 16, 32, 64)) -> List[int]:
    out: List[int] = []
    seen = set()
    for off in offsets:
        for b in backtrack:
            val = off - b
            if val < 0:
                continue
            if val in seen:
                continue
            seen.add(val)
            out.append(val)
    return out


def _decompress_zlib_stream(
    data: bytes,
    offset: int,
    wbits: int,
    max_out: int,
    max_in: int,
) -> Tuple[bytes, int]:
    d = zlib.decompressobj(wbits)
    out = bytearray()
    pos = offset
    while pos < len(data) and len(out) < max_out:
        chunk = data[pos:pos + 65536]
        pos += len(chunk)
        try:
            out.extend(d.decompress(chunk, max_out - len(out)))
        except Exception:
            break
        if d.eof:
            consumed = pos - len(d.unused_data)
            return bytes(out), max(0, consumed - offset)
        if pos - offset >= max_in:
            break
    return b"", 0


def _decompress_lzma_stream(
    data: bytes,
    offset: int,
    max_out: int,
    max_in: int,
) -> Tuple[bytes, int]:
    d = lzma.LZMADecompressor()
    out = bytearray()
    pos = offset
    while pos < len(data) and len(out) < max_out:
        chunk = data[pos:pos + 65536]
        pos += len(chunk)
        try:
            out.extend(d.decompress(chunk, max_out - len(out)))
        except Exception:
            break
        if d.eof:
            consumed = pos - len(d.unused_data)
            return bytes(out), max(0, consumed - offset)
        if pos - offset >= max_in:
            break
    return b"", 0


def _safe_decompress_stream(
    data: bytes,
    offset: int,
    method: str,
    max_out: int,
    max_in: int,
) -> Tuple[bytes, int]:
    if method == "zlib":
        return _decompress_zlib_stream(data, offset, zlib.MAX_WBITS, max_out, max_in)
    if method == "deflate":
        return _decompress_zlib_stream(data, offset, -zlib.MAX_WBITS, max_out, max_in)
    if method == "gzip":
        return _decompress_zlib_stream(data, offset, 16 + zlib.MAX_WBITS, max_out, max_in)
    if method in ("xz", "lzma"):
        return _decompress_lzma_stream(data, offset, max_out, max_in)
    if method == "zstd" and _zstd is not None:
        try:
            out = _zstd.ZstdDecompressor().decompress(
                data[offset:offset + max_in],
                max_output_size=max_out,
            )
        except Exception:
            return b"", 0
        return out, min(len(data) - offset, max_in)
    if method == "lz4" and _lz4_frame is not None:
        try:
            out = _lz4_frame.decompress(data[offset:offset + max_in])
        except Exception:
            return b"", 0
        return out[:max_out], min(len(data) - offset, max_in)
    return b"", 0


def _marker_hits(text: str) -> Dict[str, int]:
    markers = ("BVRAW_FORMAT_3", "PART_NAME", "PIN_NET", "NET", "REF", "REFDES", "PAD", "PIN", "NAME")
    return {m: text.count(m) for m in markers if m in text}


def _score_payload(text: str, printable_ratio: float) -> int:
    score = int(printable_ratio * 100)
    if "BVRAW_FORMAT_3" in text:
        score += 200
    marker_score = sum(1 for k in _marker_hits(text).keys())
    score += marker_score * 10
    net_hits = len(_NET_RE.findall(text))
    ref_hits = len(_REFDES_RE.findall(text))
    score += min(100, net_hits // 5)
    score += min(100, ref_hits // 5)
    return score


def _preview_printable(data: bytes, limit: int = 200) -> str:
    out = []
    for b in data:
        if b in (9, 10, 13) or 32 <= b <= 126:
            out.append(chr(b))
        else:
            out.append(".")
        if len(out) >= limit:
            break
    return "".join(out)


def _extract_bvraw_text(text: str) -> str:
    if "BVRAW_FORMAT_3" not in text:
        return text
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if "BVRAW_FORMAT_3" in line:
            return "\n".join(lines[idx:])
    return text


def _extract_strings(data: bytes, min_len: int = 3) -> List[str]:
    out = []
    buf = []
    for b in data:
        if 32 <= b <= 126:
            buf.append(chr(b))
        else:
            if len(buf) >= min_len:
                out.append("".join(buf))
            buf = []
    if len(buf) >= min_len:
        out.append("".join(buf))
    return out


def _extract_ascii_strings(
    data: bytes,
    min_len: int = 2,
    max_len: int = 80,
) -> List[Tuple[int, str]]:
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
                if s:
                    out.append((start, s))
        i += 1
    return out


def _find_offset_runs(data: bytes, offsets: set[int], min_len: int = 20) -> List[Tuple[int, List[int]]]:
    runs: List[Tuple[int, List[int]]] = []
    i = 0
    n = len(data)
    while i + 4 <= n:
        val = int.from_bytes(data[i:i + 4], "little", signed=False)
        if val in offsets:
            start = i
            values: List[int] = []
            while i + 4 <= n:
                val = int.from_bytes(data[i:i + 4], "little", signed=False)
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
                    first = int.from_bytes(data[base:base + 4], "little", signed=False)
                    second = int.from_bytes(data[base + 4:base + 8], "little", signed=False)
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
        first = int.from_bytes(data[base:base + 4], "little", signed=False)
        second = int.from_bytes(data[base + 4:base + 8], "little", signed=False)
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
        net_to_refs[net].setdefault(refdes, {"refdes": refdes, "kind": kind, "sub_board": "unknown"})
    return {n: list(refs.values()) for n, refs in net_to_refs.items()}


def _attempt_binary_tables(data: bytes) -> Tuple[set, set, Dict[str, List[Dict[str, Any]]], Dict[str, Any]] | None:
    strings = _extract_ascii_strings(data)
    if not strings:
        return None
    strings_map = {off: s for off, s in strings}
    net_offsets = {off for off, s in strings if _NET_RE.fullmatch(s or "")}
    ref_offsets = {off for off, s in strings if _REFDES_RE.fullmatch(s or "")}
    if not net_offsets or not ref_offsets:
        return None
    net_runs = _find_offset_runs(data, net_offsets, min_len=20)
    ref_runs = _find_offset_runs(data, ref_offsets, min_len=20)
    net_start, nets_raw = _choose_best_run(net_runs, strings_map)
    comp_start, comps_raw = _choose_best_run(ref_runs, strings_map)
    if not nets_raw or not comps_raw:
        return None
    nets = [canonicalize_net_name(n) for n in nets_raw]
    nets = [n for n in nets if n]
    comps = [c.upper() for c in comps_raw if c]
    if len(nets) < 50 or len(comps) < 50:
        return None
    search_end = int(len(data) * 0.8)
    pin_start, pin_count, pin_meta = _find_pin_table(data, len(comps), len(nets), search_end)
    net_to_refs: Dict[str, List[Dict[str, Any]]] = {}
    if pin_start >= 0 and pin_count >= 50:
        order, stride = pin_meta.split(":")
        net_to_refs = _build_net_refs_from_pin_table(
            data,
            pin_start,
            pin_count,
            order,
            int(stride),
            comps,
            nets,
        )
    meta = {
        "binary_table": True,
        "net_table_offset": net_start,
        "component_table_offset": comp_start,
        "pin_table_offset": pin_start,
        "pin_table_records": pin_count,
        "pin_table_layout": pin_meta,
    }
    return set(nets), set(comps), net_to_refs, meta


def _find_json_start_offsets(data: bytes) -> List[int]:
    starts: List[int] = []
    marker = data.rfind(b"===PCB")
    if marker != -1:
        tail = data[marker:]
        brace = tail.find(b"{")
        if brace != -1:
            starts.append(marker + brace)
    for needle in (b'{"net"', b'{"nets"', b'{"NET"', b'{"Net"', b'"net":[', b'"NET":['):
        start = 0
        while True:
            idx = data.find(needle, start)
            if idx == -1:
                break
            brace = data.rfind(b"{", 0, idx + 1)
            if brace != -1:
                starts.append(brace)
            start = idx + 1
    return sorted(set(starts))


def _extract_json_block(data: bytes, start: int) -> Optional[bytes]:
    if start < 0 or start >= len(data):
        return None
    if data[start] not in (ord("{"), ord("[")):
        return None
    stack = [data[start]]
    in_str = False
    escape = False
    for i in range(start + 1, len(data)):
        b = data[i]
        if in_str:
            if escape:
                escape = False
            elif b == ord("\\"):
                escape = True
            elif b == ord('"'):
                in_str = False
            continue
        if b == ord('"'):
            in_str = True
            continue
        if b in (ord("{"), ord("[")):
            stack.append(b)
            continue
        if b in (ord("}"), ord("]")):
            if not stack:
                return None
            stack.pop()
            if not stack:
                return data[start:i + 1]
    return None


def _parse_json_candidates(data: bytes) -> List[Any]:
    out: List[Any] = []
    for start in _find_json_start_offsets(data):
        blob = _extract_json_block(data, start)
        if not blob:
            continue
        try:
            text = blob.decode("utf-8")
        except Exception:
            text = blob.decode("utf-8", errors="ignore")
        try:
            out.append(json.loads(text))
        except Exception:
            continue
    return out


def _walk_json(
    obj: Any,
    nets: set,
    refdes: set,
    pairs: Dict[str, set],
    component_info: Dict[str, Dict[str, Any]],
    parent_key: str = "",
    active_nets: Optional[List[str]] = None,
) -> None:
    if isinstance(obj, dict):
        lower_keys = {k.lower(): k for k in obj.keys()}
        net_val = None
        ref_val = None
        if "net" in lower_keys:
            net_val = obj.get(lower_keys["net"])
        elif "net_name" in lower_keys:
            net_val = obj.get(lower_keys["net_name"])
        elif "name" in lower_keys and parent_key in ("net", "nets"):
            net_val = obj.get(lower_keys["name"])
        if "alias" in lower_keys:
            alias_val = obj.get(lower_keys["alias"])
            if isinstance(alias_val, str) and _NET_RE.fullmatch(alias_val):
                nets.add(canonicalize_net_name(alias_val))
        if "ref" in lower_keys:
            ref_val = obj.get(lower_keys["ref"])
        elif "refdes" in lower_keys:
            ref_val = obj.get(lower_keys["refdes"])
        elif "component" in lower_keys:
            ref_val = obj.get(lower_keys["component"])
        elif "part" in lower_keys:
            ref_val = obj.get(lower_keys["part"])
        if "name" in lower_keys and parent_key in ("component", "components", "parts"):
            ref_val = obj.get(lower_keys["name"])

        net_list: List[str] = []
        if isinstance(net_val, str) and _NET_RE.fullmatch(net_val):
            net_list.append(canonicalize_net_name(net_val))
        elif isinstance(net_val, list):
            for nv in net_val:
                if isinstance(nv, str) and _NET_RE.fullmatch(nv):
                    net_list.append(canonicalize_net_name(nv))
        for nv in net_list:
            nets.add(nv)

        ref_list: List[str] = []
        if isinstance(ref_val, str) and _REFDES_RE.fullmatch(ref_val):
            ref_list.append(ref_val.upper())
        elif isinstance(ref_val, list):
            for rv in ref_val:
                if isinstance(rv, str) and _REFDES_RE.fullmatch(rv):
                    ref_list.append(rv.upper())
        for rv in ref_list:
            refdes.add(rv)

        active = list(active_nets or [])
        if net_list:
            active = list(net_list)
        if ref_list and active:
            for nv in active:
                pairs.setdefault(nv, set()).update(ref_list)

        if ref_list:
            info = {}
            if "x" in lower_keys:
                info["x"] = obj.get(lower_keys["x"])
            if "y" in lower_keys:
                info["y"] = obj.get(lower_keys["y"])
            if "layer" in lower_keys:
                info["layer"] = obj.get(lower_keys["layer"])
            if "side" in lower_keys and not info.get("layer"):
                info["layer"] = obj.get(lower_keys["side"])
            if "layer" in info and isinstance(info["layer"], str):
                layer_val = info["layer"].lower()
                if "top" in layer_val:
                    info["sub_board"] = "top"
                elif "bottom" in layer_val or "bot" in layer_val:
                    info["sub_board"] = "bottom"
            if info:
                for rv in ref_list:
                    component_info.setdefault(rv, {}).update(info)

        # If a component has pins with nets, pair them.
        pin_key = None
        for k in ("pins", "pin", "pads"):
            if k in lower_keys:
                pin_key = lower_keys[k]
                break
        if pin_key and ref_list:
            pin_obj = obj.get(pin_key)
            if isinstance(pin_obj, list):
                for p in pin_obj:
                    if not isinstance(p, dict):
                        continue
                    p_keys = {pk.lower(): pk for pk in p.keys()}
                    p_net = None
                    if "net" in p_keys:
                        p_net = p.get(p_keys["net"])
                    elif "net_name" in p_keys:
                        p_net = p.get(p_keys["net_name"])
                    if isinstance(p_net, str) and _NET_RE.fullmatch(p_net):
                        canon = canonicalize_net_name(p_net)
                        nets.add(canon)
                        for rv in ref_list:
                            pairs.setdefault(canon, set()).add(rv)

        for k, v in obj.items():
            _walk_json(v, nets, refdes, pairs, component_info, parent_key=str(k).lower(), active_nets=active)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json(item, nets, refdes, pairs, component_info, parent_key=parent_key, active_nets=active_nets)


def _extract_pairs_from_text(
    text: str,
    known_nets: set,
    known_refdes: set,
) -> Dict[str, set]:
    pairs: Dict[str, set] = {}
    for line in text.splitlines():
        m_net = re.search(r"\bNET\s*[:=]\s*([A-Za-z0-9_.-]+)", line, re.IGNORECASE)
        m_ref = re.search(r"\bREF(?:DES)?\s*[:=]\s*([A-Za-z0-9_.-]+)", line, re.IGNORECASE)
        if m_net and m_ref:
            net = canonicalize_net_name(m_net.group(1))
            ref = m_ref.group(1).upper()
            if net in known_nets and ref in known_refdes:
                pairs.setdefault(net, set()).add(ref)
            continue
        if "\t" in line or "," in line:
            delim = "\t" if "\t" in line else ","
            cols = [c.strip() for c in line.split(delim)]
            if not any(c.upper() == "NET" for c in cols):
                continue
            if not any(c.upper() in ("REF", "REFDES") for c in cols):
                continue
            net_idx = next(i for i, c in enumerate(cols) if c.upper() == "NET")
            ref_idx = next(i for i, c in enumerate(cols) if c.upper() in ("REF", "REFDES"))
            continue
    return pairs


def _extract_pairs_from_table(
    text: str,
    known_nets: set,
    known_refdes: set,
) -> Dict[str, set]:
    pairs: Dict[str, set] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        delim = "\t" if "\t" in line else ("," if "," in line else None)
        if not delim:
            continue
        headers = [h.strip().upper() for h in line.split(delim)]
        if "NET" not in headers:
            continue
        if not any(h in ("REF", "REFDES") for h in headers):
            continue
        net_idx = headers.index("NET")
        ref_idx = headers.index("REF") if "REF" in headers else headers.index("REFDES")
        for row in lines[i+1:i+200]:
            cols = [c.strip() for c in row.split(delim)]
            if len(cols) <= max(net_idx, ref_idx):
                continue
            net = canonicalize_net_name(cols[net_idx])
            ref = cols[ref_idx].upper()
            if net in known_nets and ref in known_refdes:
                pairs.setdefault(net, set()).add(ref)
        break
    return pairs


def _extract_pairs_from_fixed_width(
    text: str,
    known_nets: set,
    known_refdes: set,
) -> Dict[str, set]:
    pairs: Dict[str, set] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "NET" not in line.upper() or "REF" not in line.upper():
            continue
        if "NET" not in line or "REF" not in line:
            continue
        net_idx = line.upper().find("NET")
        ref_idx = line.upper().find("REF")
        if net_idx < 0 or ref_idx < 0:
            continue
        start = min(net_idx, ref_idx)
        for row in lines[i + 1 : i + 200]:
            if len(row) < max(net_idx, ref_idx) + 3:
                continue
            net_raw = row[net_idx: net_idx + 40].strip()
            ref_raw = row[ref_idx: ref_idx + 16].strip()
            if not net_raw or not ref_raw:
                continue
            net = canonicalize_net_name(net_raw)
            ref = ref_raw.upper()
            if net in known_nets and ref in known_refdes:
                pairs.setdefault(net, set()).add(ref)
        if pairs:
            break
    return pairs


def _extract_pairs_from_line_tokens(
    text: str,
    known_nets: set,
    known_refdes: set,
) -> Dict[str, set]:
    pairs: Dict[str, set] = {}
    for line in text.splitlines():
        tokens = re.findall(r"[A-Za-z0-9_.-]+", line)
        if not tokens or len(tokens) > 12:
            continue
        nets = [canonicalize_net_name(t) for t in tokens if _NET_RE.fullmatch(t or "")]
        refs = [t.upper() for t in tokens if _REFDES_RE.fullmatch(t or "")]
        nets = [n for n in nets if n in known_nets]
        refs = [r for r in refs if r in known_refdes]
        if len(nets) == 1 and len(refs) == 1 and ("," in line or "\t" in line or ":" in line):
            pairs.setdefault(nets[0], set()).add(refs[0])
    return pairs


def _collect_candidates(data: bytes, max_hits: int, dense: bool = False) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    zlib_offsets = _expand_offsets(_scan_zlib_offsets(data, max_hits=max_hits))
    for off in zlib_offsets:
        candidates.append({"offset": off, "method": "zlib"})
    gzip_offsets = _expand_offsets(_scan_magic_offsets(data, _MAGIC_GZIP, max_hits))
    for off in gzip_offsets:
        candidates.append({"offset": off, "method": "gzip"})
    xz_offsets = _expand_offsets(_scan_magic_offsets(data, _MAGIC_XZ, max_hits))
    for off in xz_offsets:
        candidates.append({"offset": off, "method": "xz"})
    if _zstd is not None:
        zstd_offsets = _expand_offsets(_scan_magic_offsets(data, _MAGIC_ZSTD, max_hits))
        for off in zstd_offsets:
            candidates.append({"offset": off, "method": "zstd"})
    if _lz4_frame is not None:
        lz4_offsets = _expand_offsets(_scan_magic_offsets(data, _MAGIC_LZ4, max_hits))
        for off in lz4_offsets:
            candidates.append({"offset": off, "method": "lz4"})

    if len(candidates) < max_hits:
        if dense:
            step = 256
            remaining = max_hits - len(candidates)
            offsets = list(range(0, len(data), step))[:remaining]
            for off in offsets:
                candidates.append({"offset": off, "method": "deflate"})
        else:
            deflate_offsets = _scan_deflate_offsets(data, max_hits=max_hits - len(candidates))
            for off in deflate_offsets:
                candidates.append({"offset": off, "method": "deflate"})

    seen = set()
    ordered: List[Dict[str, Any]] = []
    for c in candidates:
        key = (c["offset"], c["method"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(c)
        if len(ordered) >= max_hits:
            break
    return ordered


def _collect_pcb_chunks(
    data: bytes,
    max_candidates: int,
    max_total_out: int,
    max_stream_out: int,
    max_stream_in: int,
    dense: bool = False,
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    total_out = 0
    candidates = _collect_candidates(data, max_candidates, dense=dense)
    for cand in candidates:
        if total_out >= max_total_out:
            break
        out, consumed = _safe_decompress_stream(
            data,
            cand["offset"],
            cand["method"],
            max_stream_out,
            max_stream_in,
        )
        if not out or consumed <= 0:
            continue
        total_out += len(out)
        preview = _preview_printable(out, limit=200)
        printable = 0
        for b in out[:200000]:
            if b in (9, 10, 13) or 32 <= b <= 126:
                printable += 1
        printable_ratio = printable / max(1, min(len(out), 200000))
        likely_text = printable_ratio >= 0.85
        encoding = _guess_encoding(out) if likely_text else "binary"
        text = out.decode(encoding, errors="ignore") if likely_text else ""
        score = _score_payload(text, printable_ratio) if text else 0
        chunks.append(
            {
                "offset": cand["offset"],
                "method": cand["method"],
                "compressed_len": consumed,
                "decompressed_len": len(out),
                "sha1": hashlib.sha1(out).hexdigest(),
                "preview": preview,
                "likely_text": likely_text,
                "encoding": encoding,
                "printable_ratio": round(printable_ratio, 4),
                "score": score,
                "marker_hits": _marker_hits(text) if text else {},
                "data": out,
            }
        )
    return chunks


def parse_pcb_zlib_container(
    path: str,
    max_streams: int = 200,
    max_total_out: int = 64 * 1024 * 1024,
    max_stream_out: int = 8 * 1024 * 1024,
    max_stream_in: int = 16 * 1024 * 1024,
) -> Tuple[set, Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    with open(path, "rb") as f:
        data = f.read()
    debug_enabled = os.environ.get("BOARDVIEW_PCB_DEBUG", "").strip() in ("1", "true", "yes", "on")
    debug_dir = os.environ.get("BOARDVIEW_PCB_DEBUG_DIR", "").strip()
    if debug_enabled and not debug_dir:
        debug_dir = os.path.join(SETTINGS.data_dir, "pcb_debug")
    json_objs = _parse_json_candidates(data)
    json_nets: set = set()
    json_refdes: set = set()
    json_pairs: Dict[str, set] = {}
    component_info: Dict[str, Dict[str, Any]] = {}
    for obj in json_objs:
        _walk_json(obj, json_nets, json_refdes, json_pairs, component_info)

    chunks = _collect_pcb_chunks(
        data,
        max_candidates=max_streams,
        max_total_out=max_total_out,
        max_stream_out=max_stream_out,
        max_stream_in=max_stream_in,
    )
    if not chunks:
        dense_enabled = os.environ.get("BOARDVIEW_PCB_DENSE_SCAN", "").strip() in ("1", "true", "yes", "on")
        if dense_enabled:
            chunks = _collect_pcb_chunks(
                data,
                max_candidates=max_streams * 10,
                max_total_out=max_total_out,
                max_stream_out=max_stream_out,
                max_stream_in=max_stream_in,
                dense=True,
            )
    if not chunks and not json_objs:
        raise ValueError("no_valid_streams")

    best_text = ""
    best_score = -1
    for ch in sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)[:10]:
        if not ch["likely_text"]:
            continue
        txt = ch["data"].decode(ch["encoding"], errors="ignore")
        if "BVRAW_FORMAT_3" in txt:
            try:
                return parse_bvraw_format_3_text(_extract_bvraw_text(txt))
            except Exception:
                pass
        if ch.get("score", 0) > best_score:
            best_text = txt
            best_score = ch.get("score", 0)
    if debug_enabled:
        os.makedirs(debug_dir, exist_ok=True)
        top = sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)[:3]
        if not chunks:
            print("[pcb] no chunks decoded; consider BOARDVIEW_PCB_DENSE_SCAN=1")
        for c in top:
            print(
                "[pcb] candidate",
                f"offset=0x{c['offset']:x}",
                f"method={c['method']}",
                f"out_len={c['decompressed_len']}",
                f"printable={c.get('printable_ratio')}",
                f"score={c.get('score')}",
            )
        summary = {
            "path": path,
            "file_size": len(data),
            "json_objects": len(json_objs),
            "chunks": [
                {
                    "offset": c["offset"],
                    "method": c["method"],
                    "decompressed_len": c["decompressed_len"],
                    "printable_ratio": c.get("printable_ratio"),
                    "score": c.get("score"),
                    "marker_hits": c.get("marker_hits"),
                    "preview": c.get("preview"),
                }
                for c in top
            ],
        }
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(os.path.join(debug_dir, f"{stem}_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        for idx, c in enumerate(top, start=1):
            suffix = "txt" if c.get("likely_text") else "bin"
            out_path = os.path.join(
                debug_dir,
                f"{stem}_chunk{idx:02d}_{c['method']}_0x{c['offset']:x}.{suffix}",
            )
            if c.get("likely_text"):
                text = c["data"].decode(c["encoding"], errors="ignore")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(text)
            else:
                with open(out_path, "wb") as f:
                    f.write(c["data"])

    nets: set = set(json_nets)
    refdes: set = set(json_refdes)
    for ch in chunks:
        if ch["likely_text"]:
            text = ch["data"].decode(ch["encoding"], errors="ignore")
            for m in _NET_RE.finditer(text):
                nets.add(canonicalize_net_name(m.group(0)))
            for m in _REFDES_RE.finditer(text):
                refdes.add(m.group(0).upper())
        else:
            strings = _extract_strings(ch["data"])
            for s in strings:
                if _NET_RE.fullmatch(s):
                    nets.add(canonicalize_net_name(s))
                if _REFDES_RE.fullmatch(s):
                    refdes.add(s.upper())
    if not nets or not refdes:
        strings = _extract_strings(data)
        for s in strings:
            if _NET_RE.fullmatch(s):
                nets.add(canonicalize_net_name(s))
            if _REFDES_RE.fullmatch(s):
                refdes.add(s.upper())

    nets = {n for n in nets if n}
    refdes = {r for r in refdes if r}
    if not nets or not refdes:
        raise ValueError("no_nets_or_refdes_found")

    net_to_refs: Dict[str, set] = {k: set(v) for k, v in json_pairs.items()}
    for ch in chunks:
        if not ch["likely_text"]:
            continue
        text = ch["data"].decode(ch["encoding"], errors="ignore")
        pairs = _extract_pairs_from_text(text, nets, refdes)
        for net, refs in pairs.items():
            net_to_refs.setdefault(net, set()).update(refs)
        pairs = _extract_pairs_from_table(text, nets, refdes)
        for net, refs in pairs.items():
            net_to_refs.setdefault(net, set()).update(refs)
        pairs = _extract_pairs_from_fixed_width(text, nets, refdes)
        for net, refs in pairs.items():
            net_to_refs.setdefault(net, set()).update(refs)
        pairs = _extract_pairs_from_line_tokens(text, nets, refdes)
        for net, refs in pairs.items():
            net_to_refs.setdefault(net, set()).update(refs)

    net_to_refs_dict: Dict[str, List[Dict[str, Any]]] = {}
    for net, refs in net_to_refs.items():
        items = []
        for r in sorted(refs):
            kind = "TP" if r.startswith("TP") else ("P" if r.startswith("P") else r[:1])
            info = component_info.get(r, {})
            item = {"refdes": r, "kind": kind, "sub_board": info.get("sub_board", "unknown")}
            if "x" in info and "y" in info:
                item["x"] = info.get("x")
                item["y"] = info.get("y")
            if "layer" in info:
                item["layer"] = info.get("layer")
            items.append(item)
        net_to_refs_dict[net] = items
    parse_status = "success"
    if not net_to_refs_dict:
        parse_status = "partial_success"
        fallback = _attempt_binary_tables(data)
        if fallback:
            bin_nets, bin_comps, bin_refs, bin_meta = fallback
            if bin_nets and len(bin_nets) > len(nets):
                nets = bin_nets
            if bin_comps and len(bin_comps) > len(refdes):
                refdes = bin_comps
            if bin_refs:
                net_to_refs_dict = bin_refs
                parse_status = "success"
            meta_extra = bin_meta
        else:
            meta_extra = {}
    else:
        meta_extra = {}

    meta = {
        "format": "PCB_EMBEDDED_ZLIB",
        "candidate_streams": len(_collect_candidates(data, max_streams)),
        "streams_decompressed": len(chunks),
        "text_chunks": sum(1 for c in chunks if c["likely_text"]),
        "nets_count": len(nets),
        "components_count": len(refdes),
        "pairs_count": sum(len(v) for v in net_to_refs_dict.values()),
        "json_objects": len(json_objs),
        "parse_status": parse_status,
        "sample_nets": sorted(list(nets))[:20],
        "sample_refdes": sorted(list(refdes))[:20],
        "top_chunks": sorted(
            [
                {
                    "offset": c["offset"],
                    "method": c["method"],
                    "decompressed_len": c["decompressed_len"],
                    "score": c.get("score", 0),
                    "preview": c["preview"],
                }
                for c in chunks
            ],
            key=lambda x: x["score"],
            reverse=True,
        )[:5],
    }
    meta.update(meta_extra)
    meta["components"] = sorted(refdes)
    return nets, net_to_refs_dict, meta
