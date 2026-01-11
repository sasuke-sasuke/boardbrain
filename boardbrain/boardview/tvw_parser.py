from __future__ import annotations

import re
from typing import Dict, Any, List, Tuple

from ..netlist import canonicalize_net_name

_NET_RE = re.compile(
    r"^(?:PP[\w.+-]+|[A-Z][A-Z0-9]+_[A-Z0-9_]+|[0-9][A-Z0-9]+_[A-Z0-9_]+|GND|GROUND|VBUS|VBAT|VDD[A-Z0-9_]+|VCC[A-Z0-9_]+)$"
)
_REFDES_RE = re.compile(
    r"^(?:TPU|TP|FB|PU|PC|PR|PL|PD|PQ|PJ|PF|PT|PM|PS|CN|RN|"
    r"U|Q|L|C|R|D|F|J|P|X|Y)\d{1,5}[A-Z0-9]*$",
    re.IGNORECASE,
)
_NET_DENY_SUBSTR = (
    "TEST_POINT",
    "POINT",
    "MIL",
    "MILS",
    "0402",
    "0603",
    "0805",
    "1206",
    "0201",
    "01005",
    "RES",
    "CAP",
    "COIL",
    "IND",
    "LED",
    "DIODE",
    "SOLDER",
    "SHORT",
    "PAD",
    "PADS",
    "SILK",
    "TOP",
    "BOTTOM",
    "BOARD",
    "QFN",
    "DFN",
    "BGA",
    "SOT",
    "QFP",
    "LGA",
    "SOIC",
    "TQFP",
    "SOP",
    "SMT",
    "BLM",
    "NTC",
    "THERM",
    "FUSE",
)
_NET_DENY_PREFIX = re.compile(r"^[CRLDUQFPJ][0-9]{3,}_", re.IGNORECASE)


def _extract_strings(data: bytes, min_len: int = 3) -> List[str]:
    out = []
    buf: List[int] = []
    for b in data:
        if 32 <= b <= 126:
            buf.append(b)
        else:
            if len(buf) >= min_len:
                out.append(bytes(buf).decode("ascii", errors="ignore"))
            buf = []
    if len(buf) >= min_len:
        out.append(bytes(buf).decode("ascii", errors="ignore"))
    return out


def _extract_null_strings(data: bytes, min_len: int = 3) -> List[str]:
    out = []
    buf: List[int] = []
    for b in data:
        if 32 <= b <= 126:
            buf.append(b)
        else:
            if b == 0 and len(buf) >= min_len:
                out.append(bytes(buf).decode("ascii", errors="ignore"))
            buf = []
    return out


def _looks_like_net(token: str) -> bool:
    if not _NET_RE.match(token or ""):
        return False
    if "\\" in token or ":" in token:
        return False
    upper = token.upper()
    if any(s in upper for s in _NET_DENY_SUBSTR):
        return False
    if _NET_DENY_PREFIX.match(token):
        return False
    if sum(ch.isdigit() for ch in token) >= 6 and not token.startswith(("PP", "VDD", "VCC", "VBUS", "VBAT")):
        return False
    return True


def _looks_like_refdes(token: str) -> bool:
    if "_" in token:
        return False
    return bool(_REFDES_RE.match(token or ""))


def parse_tvw(path: str) -> Tuple[set[str], Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    data = open(path, "rb").read()

    strings = _extract_strings(data, min_len=3)
    null_strings = _extract_null_strings(data, min_len=3)

    nets = {canonicalize_net_name(s) for s in strings if _looks_like_net(s)}
    nets = {n for n in nets if n}

    components = {s.upper() for s in strings if _looks_like_refdes(s)}

    net_to_refs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    # Attempt minimal mapping from adjacent null-terminated strings, if any.
    for a, b in zip(null_strings, null_strings[1:]):
        if _looks_like_net(a) and _looks_like_refdes(b):
            net = canonicalize_net_name(a)
            ref = b.upper()
        elif _looks_like_refdes(a) and _looks_like_net(b):
            net = canonicalize_net_name(b)
            ref = a.upper()
        else:
            continue
        if not net or not ref:
            continue
        kind = "TP" if ref.startswith("TP") else ("P" if ref.startswith("P") else ref[:1])
        net_to_refs.setdefault(net, {})
        net_to_refs[net].setdefault(ref, {"refdes": ref, "kind": kind})

    net_to_refs_dict = {n: list(refs.values()) for n, refs in net_to_refs.items()}
    pairs_count = sum(len(v) for v in net_to_refs_dict.values())

    meta = {
        "format": "TVW_STRINGS",
        "nets_count": len(nets),
        "components_count": len(components),
        "pairs_count": pairs_count,
    }
    if pairs_count < 10:
        meta["parse_status"] = "partial_success"
        meta["parse_error"] = "tvw_no_mapping"
    meta["components"] = sorted(components)
    return nets, net_to_refs_dict, meta
