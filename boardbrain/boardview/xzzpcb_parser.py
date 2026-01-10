from __future__ import annotations

import os
import re
from typing import Dict, Any, List, Tuple

from ..netlist import canonicalize_net_name
from .des import des_decrypt_block

try:
    from Crypto.Cipher import DES as _DES
except Exception:
    _DES = None

XZZ_GLOBAL_SCALE = 10000
XZZ_MAGIC = b"XZZPCB"
XZZ_MARKER = b"v6v6555v6v6"
XZZ_MASTER_KEY = 0xDCFC12AC00000000
_REFDES_RE = re.compile(r"^(?:TP[0-9A-Z]+|FB\\d{1,5}|[A-Z]{1,3}\\d{1,5})(?:_[0-9]+)?$", re.IGNORECASE)


def _read_u32(buf: bytes, pos: int) -> int:
    if pos + 4 > len(buf):
        return 0
    return int.from_bytes(buf[pos : pos + 4], "little", signed=False)


def _key_parity_ok(key: int) -> bool:
    parity = [1, 1, 1, 1, 1, 1, 1, 0]
    for i in range(8):
        tmp = (key >> (i * 8)) & 0xFF
        tmp ^= tmp >> 4
        tmp ^= tmp >> 2
        tmp ^= tmp >> 1
        tmp = (~tmp) & 1
        if tmp != parity[i]:
            return False
    return True


def _load_xzzpcb_key() -> Tuple[int, str]:
    env = os.environ.get("BOARDVIEW_XZZPCB_KEY") or os.environ.get("XZZPCB_KEY")
    if env:
        try:
            return int(env, 0), "env"
        except Exception:
            return 0, "env_invalid"
    candidates = [
        os.path.expanduser("~/.config/OpenBoardView/obv.conf"),
        os.path.expanduser("~/.config/openboardview/obv.conf"),
        os.path.expanduser("~/Library/Application Support/OpenBoardView/obv.conf"),
        os.path.expanduser("~/Library/Application Support/openboardview/obv.conf"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "XZZPCBKey" in line:
                        m = re.search(r"XZZPCBKey\\s*=\\s*([^\\s#]+)", line)
                        if m:
                            return int(m.group(1), 0), "obv_conf"
        except Exception:
            continue
    return 0, "missing"


def verify_xzzpcb(buf: bytes) -> bool:
    if len(buf) < 6:
        return False
    if buf[:6] == XZZ_MAGIC:
        return True
    if len(buf) > 0x10 and buf[0x10] != 0x00:
        xor_key = buf[0x10]
        head = bytes([b ^ xor_key for b in buf[:6]])
        return head == XZZ_MAGIC
    return False


def _des_decrypt_bytes(data: bytes, key: int) -> bytes:
    if _DES is not None:
        key_bytes = key.to_bytes(8, "big", signed=False)
        cipher = _DES.new(key_bytes, _DES.MODE_ECB)
        out = bytearray(len(data))
        for i in range(0, len(data), 8):
            block = data[i : i + 8]
            if len(block) < 8:
                block = block.ljust(8, b"\x00")
            dec = cipher.decrypt(block)
            out[i : i + 8] = dec
        return bytes(out)
    out = bytearray(len(data))
    for i in range(0, len(data), 8):
        block = data[i : i + 8]
        if len(block) < 8:
            block = block.ljust(8, b"\x00")
        val = int.from_bytes(block, "big", signed=False)
        dec = des_decrypt_block(val, key)
        out[i : i + 8] = dec.to_bytes(8, "big", signed=False)
    return bytes(out)


def _translate_points(points: List[Tuple[int, int]], dx: int, dy: int) -> List[Tuple[int, int]]:
    return [(x - dx, y - dy) for x, y in points]


def _find_translation(outline: List[Tuple[Tuple[int, int], Tuple[int, int]]]) -> Tuple[int, int]:
    if not outline:
        return 0, 0
    min_x = outline[0][0][0]
    min_y = outline[0][0][1]
    for a, b in outline:
        min_x = min(min_x, a[0], b[0])
        min_y = min(min_y, a[1], b[1])
    return min_x, min_y


def _parse_net_block(buf: bytes) -> Dict[int, str]:
    net_dict: Dict[int, str] = {}
    ptr = 0
    while ptr + 8 <= len(buf):
        net_size = _read_u32(buf, ptr)
        ptr += 4
        net_idx = _read_u32(buf, ptr)
        ptr += 4
        if net_size < 8 or ptr + (net_size - 8) > len(buf):
            break
        name = buf[ptr : ptr + net_size - 8].decode("latin-1", errors="ignore")
        ptr += net_size - 8
        net_dict[net_idx] = name
    return net_dict


def parse_xzzpcb(path: str) -> Tuple[set, Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    buf = open(path, "rb").read()
    if not verify_xzzpcb(buf):
        raise ValueError("unsupported_format")

    key, key_source = _load_xzzpcb_key()
    if not _key_parity_ok(key):
        key = XZZ_MASTER_KEY
        key_source = "master_key"
    if not _key_parity_ok(key):
        raise ValueError("xzzpcb_missing_or_invalid_key")

    marker_pos = buf.find(XZZ_MARKER)
    if marker_pos == -1:
        marker_pos = len(buf)
    if len(buf) > 0x10 and buf[0x10] != 0x00:
        xor_key = buf[0x10]
        header = bytearray(buf)
        for i in range(0, marker_pos):
            header[i] ^= xor_key
        buf = bytes(header)

    main_data_offset = _read_u32(buf, 0x20)
    net_data_offset = _read_u32(buf, 0x28)
    main_data_start = main_data_offset + 0x20
    net_data_start = net_data_offset + 0x20
    main_block_size = _read_u32(buf, main_data_start)
    net_block_size = _read_u32(buf, net_data_start)
    if main_block_size == 0 or net_block_size == 0:
        raise ValueError("xzzpcb_invalid_offsets")

    net_block = buf[net_data_start + 4 : net_data_start + net_block_size + 4]
    net_dict = _parse_net_block(net_block)

    outline_segments: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
    parts: List[Dict[str, Any]] = []
    pins: List[Dict[str, Any]] = []
    testpads: List[Dict[str, Any]] = []

    ptr = main_data_start + 4
    end = main_data_start + 4 + main_block_size
    while ptr < end:
        block_type = buf[ptr]
        ptr += 1
        block_size = _read_u32(buf, ptr)
        ptr += 4
        if ptr + block_size > len(buf):
            break
        block = buf[ptr : ptr + block_size]
        ptr += block_size

        if block_type == 0x01:
            layer = _read_u32(block, 0)
            if layer != 28:
                continue
            x = _read_u32(block, 4) // XZZ_GLOBAL_SCALE
            y = _read_u32(block, 8) // XZZ_GLOBAL_SCALE
            r = _read_u32(block, 12) // XZZ_GLOBAL_SCALE
            start = _read_u32(block, 16) // XZZ_GLOBAL_SCALE
            end_ang = _read_u32(block, 20) // XZZ_GLOBAL_SCALE
            # approximate with small segments
            # Simplified: only store center points as outline if arc
            outline_segments.append(((x - r, y), (x + r, y)))
        elif block_type == 0x05:
            layer = _read_u32(block, 0)
            if layer != 28:
                continue
            x1 = _read_u32(block, 4) // XZZ_GLOBAL_SCALE
            y1 = _read_u32(block, 8) // XZZ_GLOBAL_SCALE
            x2 = _read_u32(block, 12) // XZZ_GLOBAL_SCALE
            y2 = _read_u32(block, 16) // XZZ_GLOBAL_SCALE
            outline_segments.append(((x1, y1), (x2, y2)))
        elif block_type == 0x07:
            dec = _des_decrypt_bytes(block, key)
            # parse part block
            cur = 0
            part_size = _read_u32(dec, cur)
            cur += 4
            cur += 18
            group_name_size = _read_u32(dec, cur)
            cur += 4 + group_name_size
            if cur >= len(dec) or dec[cur] != 0x06:
                continue
            cur += 31
            part_name_size = _read_u32(dec, cur)
            cur += 4
            part_name = dec[cur : cur + part_name_size].decode("latin-1", errors="ignore")
            cur += part_name_size
            part_index = len(parts) + 1
            parts.append(
                {
                    "name": part_name,
                    "side": "top",
                    "type": "SMD",
                    "end_of_pins": 0,
                }
            )
            while cur < part_size + 4 and cur < len(dec):
                subtype = dec[cur]
                cur += 1
                if subtype in (0x01, 0x05, 0x06):
                    skip = _read_u32(dec, cur)
                    cur += 4 + skip
                elif subtype == 0x09:
                    pin_block_size = _read_u32(dec, cur)
                    block_end = cur + pin_block_size + 4
                    cur += 4
                    cur += 4
                    x_origin = _read_u32(dec, cur)
                    cur += 4
                    y_origin = _read_u32(dec, cur)
                    cur += 4
                    cur += 8
                    pin_name_size = _read_u32(dec, cur)
                    cur += 4
                    pin_name = dec[cur : cur + pin_name_size].decode("latin-1", errors="ignore")
                    cur += pin_name_size
                    cur += 32
                    net_index = _read_u32(dec, cur)
                    cur = block_end
                    net_name = net_dict.get(net_index, "")
                    if net_name == "NC":
                        net_name = "UNCONNECTED"
                    pins.append(
                        {
                            "x": x_origin // XZZ_GLOBAL_SCALE,
                            "y": y_origin // XZZ_GLOBAL_SCALE,
                            "name": pin_name,
                            "part": part_index,
                            "net": net_name,
                            "side": "top",
                        }
                    )
                else:
                    # unknown sub block
                    continue
            parts[-1]["end_of_pins"] = len(pins)
        elif block_type == 0x09:
            cur = 0
            cur += 4
            x_origin = _read_u32(block, cur)
            cur += 4
            y_origin = _read_u32(block, cur)
            cur += 4
            cur += 8
            name_length = _read_u32(block, cur)
            cur += 4
            name = block[cur : cur + name_length].decode("latin-1", errors="ignore")
            net_index = _read_u32(block, len(block) - 4)
            net_name = net_dict.get(net_index, "")
            if net_name in ("UNCONNECTED", "NC"):
                net_name = ""
            tp_name = name
            if tp_name and not tp_name[0].isalpha():
                tp_name = f"TP{tp_name}"
            testpads.append(
                {
                    "name": tp_name,
                    "x": x_origin // XZZ_GLOBAL_SCALE,
                    "y": y_origin // XZZ_GLOBAL_SCALE,
                    "net": net_name,
                    "side": "top",
                }
            )
            parts.append(
                {
                    "name": f"...{tp_name}",
                    "side": "top",
                    "type": "TP",
                    "end_of_pins": 0,
                }
            )
            pins.append(
                {
                    "x": x_origin // XZZ_GLOBAL_SCALE,
                    "y": y_origin // XZZ_GLOBAL_SCALE,
                    "name": tp_name,
                    "part": len(parts),
                    "net": net_name,
                    "side": "top",
                }
            )
            parts[-1]["end_of_pins"] = len(pins)

    dx, dy = _find_translation(outline_segments)
    if dx or dy:
        outline_segments = [((a[0] - dx, a[1] - dy), (b[0] - dx, b[1] - dy)) for a, b in outline_segments]
        for p in pins:
            p["x"] -= dx
            p["y"] -= dy
        for t in testpads:
            t["x"] -= dx
            t["y"] -= dy

    nets: set = set(canonicalize_net_name(n) for n in net_dict.values() if canonicalize_net_name(n))
    net_to_refs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    part_points: Dict[int, List[Tuple[int, int]]] = {}
    for pin in pins:
        net = canonicalize_net_name(pin.get("net") or "")
        if not net or net.startswith("UNCONNECTED"):
            continue
        nets.add(net)
        part_idx = pin.get("part", 0) - 1
        refdes = ""
        if 0 <= part_idx < len(parts):
            refdes = parts[part_idx]["name"]
            part_points.setdefault(part_idx, []).append((pin["x"], pin["y"]))
        if refdes.startswith("..."):
            refdes = pin.get("name") or refdes.lstrip(".")
        if not refdes:
            continue
        kind = "TP" if refdes.startswith("TP") else ("P" if refdes.startswith("P") else refdes[:1])
        net_to_refs.setdefault(net, {})
        net_to_refs[net].setdefault(refdes, {"refdes": refdes, "kind": kind, "side": "top"})

    net_to_refs_dict = {n: list(refs.values()) for n, refs in net_to_refs.items()}
    components: List[str] = []
    for p in parts:
        name = p.get("name") or ""
        if not name or name == "...":
            continue
        if name.startswith("..."):
            name = name.lstrip(".")
        if _REFDES_RE.match(name):
            components.append(name)
    component_details: List[Dict[str, Any]] = []
    for idx, part in enumerate(parts):
        name = part.get("name") or ""
        if not name or name == "...":
            continue
        if name.startswith("..."):
            name = name.lstrip(".")
        if not _REFDES_RE.match(name):
            continue
        pts = part_points.get(idx, [])
        if pts:
            x = sum(p[0] for p in pts) / len(pts)
            y = sum(p[1] for p in pts) / len(pts)
        else:
            x, y = 0, 0
        component_details.append(
            {
                "refdes": name,
                "x": x,
                "y": y,
                "side": part.get("side") or "top",
                "type": part.get("type") or "SMD",
            }
        )
    meta = {
        "format": "XZZPCB",
        "nets_count": len(nets),
        "components_count": len(components),
        "pairs_count": sum(len(v) for v in net_to_refs_dict.values()),
        "outline_segments": [
            {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in outline_segments
        ],
        "testpoints": testpads,
        "units": "mil",
        "component_details": component_details,
        "key_source": key_source,
    }
    if not pins or not components:
        meta["parse_status"] = "partial_success"
        meta["parse_error"] = "xzzpcb_missing_parts_or_pins"
    meta["components"] = components
    return nets, net_to_refs_dict, meta
