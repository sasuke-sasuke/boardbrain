from __future__ import annotations
import json
import os
import re
import difflib
from typing import Dict, Any, List, Tuple, Optional
from .config import SETTINGS

REFDES_RE = re.compile(
    r"\b(?:TP\d{1,5}|FB\d{1,5}|[URCQLDFJPX]\d{1,5})\b",
    re.IGNORECASE,
)
COMP_MEAS_RE = re.compile(
    r"(?i)\bCOMP\s+(?P<ref>(U|R|C|Q|L|D|F|FB|J|P|X)\d{1,5})\.(?P<loc>[A-Z0-9_]+)\s*[:=]\s*(?P<val>[0-9]*\.?[0-9]+)\s*(?P<unit>V|A|mA|ohms|Ω|kΩ|MΩ|Hz|kHz|MHz)\b"
)

_COMPONENT_CACHE: Dict[str, Tuple[set, Dict[str, Any]]] = {}


def extract_refdes_tokens(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for m in REFDES_RE.finditer(text or ""):
        token = m.group(0).upper()
        counts[token] = counts.get(token, 0) + 1
    return counts


def _cache_path(board_id: str, model: str) -> str:
    key = board_id or model or "unknown"
    safe = re.sub(r"[^A-Z0-9_-]", "_", key.upper())
    return os.path.join(SETTINGS.data_dir, "components", f"{safe}.json")


def load_component_index(board_id: str = "", model: str = "", case: Optional[Dict[str, Any]] = None) -> Tuple[set, Dict[str, Any]]:
    if not board_id and case:
        board_id = (case.get("board_id") or "").strip()
    if not model and case:
        model = (case.get("model") or "").strip()
    key = board_id or model or "unknown"
    if key in _COMPONENT_CACHE:
        return _COMPONENT_CACHE[key]

    cache_path = _cache_path(board_id, model)
    refdes: set = set()
    meta: Dict[str, Any] = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            refdes = set(data.get("refdes") or data.get("components", []) or [])
            meta = data
        except Exception:
            refdes = set()
            meta = {}

    meta.setdefault("cache_path", cache_path)
    meta.setdefault("board_id", board_id)
    meta.setdefault("model", model)
    meta.setdefault("component_count", len(refdes))
    if not refdes:
        meta.setdefault("source", "missing")
        meta.setdefault("reason", "component cache not found or empty")

    _COMPONENT_CACHE[key] = (refdes, meta)
    return refdes, meta


def enforce_component_guardrail(text: str, known_components: set) -> Tuple[str, Dict[str, Any]]:
    if not text or not known_components:
        return text, {"invalid_refdes": [], "replaced_count": 0}
    invalid: List[str] = []
    def _sub(m: re.Match) -> str:
        token = m.group(0).upper()
        if token in known_components:
            return token
        invalid.append(token)
        return "[UNKNOWN_REFDES]"
    updated = REFDES_RE.sub(_sub, text)
    return updated, {"invalid_refdes": sorted(set(invalid)), "replaced_count": len(invalid)}


def suggest_components(board_id: str, query: str, k: int = 5, case: Optional[Dict[str, Any]] = None) -> List[str]:
    refdes, _ = load_component_index(board_id=board_id, case=case)
    target = query.upper()
    return difflib.get_close_matches(target, sorted(refdes), n=k, cutoff=0.6)


def extract_component_tokens(text: str) -> List[str]:
    return [m.group(0).upper() for m in REFDES_RE.finditer(text or "")]


def parse_component_measurements(text: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for m in COMP_MEAS_RE.finditer(text or ""):
        ref = m.group("ref").upper()
        loc = m.group("loc").upper()
        val = m.group("val")
        unit = _normalize_unit(m.group("unit"))
        entries.append(
            {
                "refdes": ref,
                "loc": loc,
                "value": val,
                "unit": unit,
                "raw": m.group(0),
            }
        )
    return entries


def _normalize_unit(unit: str) -> str:
    u = unit.strip()
    if u in ("Ω", "ohms", "ohm"):
        return "ohms"
    if u in ("kΩ", "kohm", "kohms"):
        return "kohms"
    if u in ("MΩ",):
        return "mohms"
    if u.lower() in ("ma", "a", "v", "hz", "khz", "mhz"):
        return u.lower()
    return u
