from __future__ import annotations
import re
from typing import Dict, Any, List, Tuple, Optional, Set
from .netlist import canonicalize_net_name

KEY_PREFIXES = ["CHECK_", "VERIFY_", "MEASURE_", "TEST_"]
_DENYLIST_KEYS = {"PROMPT", "TYPE", "NET", "INFERENCE", "WHERE", "LOCATION", "HINT"}
_REQ_KEY_RE = re.compile(
    r"^(?:(?:CHECK_|VERIFY_|MEASURE_|TEST_))?([A-Z][A-Z0-9_.]*_[A-Z0-9_.]+|PP[A-Z0-9_.]+)(?:_(R2G|DIODE))?$",
    re.IGNORECASE,
)
_ALLOWED_TYPES = {"voltage", "resistance", "diode", "current", "frequency", "continuity"}


def split_req_key(key: str) -> Tuple[str, str, str]:
    key_u = key.upper()
    prefix = ""
    for p in KEY_PREFIXES:
        if key_u.startswith(p):
            prefix = p
            key_u = key_u[len(p):]
            break
    suffix = ""
    if key_u.endswith("_R2G"):
        suffix = "_R2G"
        key_u = key_u[: -len("_R2G")]
    elif key_u.endswith("_DIODE"):
        suffix = "_DIODE"
        key_u = key_u[: -len("_DIODE")]
    return prefix, key_u, suffix


def normalize_req_key(key: str) -> Tuple[str, Dict[str, Any]]:
    prefix, base, suffix = split_req_key(key)
    meta: Dict[str, Any] = {}
    if prefix in ("VERIFY_", "MEASURE_", "TEST_"):
        meta["key_normalized_from"] = key
        return f"CHECK_{base}{suffix}", meta
    if prefix == "CHECK_":
        return f"CHECK_{base}{suffix}", meta
    if base:
        meta["key_normalized_from"] = key
        return f"CHECK_{base}{suffix}", meta
    return key.upper(), meta


def _is_measurement_key(key: str) -> bool:
    key_u = key.strip().upper()
    if not key_u or key_u in _DENYLIST_KEYS:
        return False
    return bool(_REQ_KEY_RE.match(key_u))


def _extract_base_net(key: str) -> str:
    prefix, base, suffix = split_req_key(key)
    if not base:
        return ""
    return canonicalize_net_name(base)


def parse_requested_measurements(
    plan_markdown: str,
    known_nets: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    invalid_keys: List[str] = []

    lines = plan_markdown.splitlines()
    for line in lines:
        key_match = re.search(r"\bKEY\s*:\s*([A-Za-z0-9_\.\-]+)", line)
        prompt_match = re.search(r"\bPROMPT\s*:\s*(.+)", line)
        hint_match = re.search(r"\b(?:OPTIONAL\s+HINT|HINT|WHERE|LOCATION)\s*:\s*(.+)", line)
        net_match = re.search(r"\bNET\s*:\s*([A-Za-z0-9_\.\-]+)", line)
        type_match = re.search(r"\bTYPE\s*:\s*([A-Za-z0-9_\-]+)", line)

        if key_match:
            if current.get("key") and current.get("prompt"):
                items.append(current)
                current = {}
            raw_key = key_match.group(1).strip()
            if _is_measurement_key(raw_key):
                current["key"] = raw_key
            else:
                invalid_keys.append(raw_key)

        if prompt_match:
            current["prompt"] = prompt_match.group(1).strip()

        if hint_match:
            meta = current.get("meta") or {}
            meta["hint"] = hint_match.group(1).strip()
            current["meta"] = meta
        if net_match:
            meta = current.get("meta") or {}
            meta["net"] = net_match.group(1).strip()
            current["meta"] = meta
        if type_match:
            meta = current.get("meta") or {}
            meta["type"] = type_match.group(1).strip().lower()
            current["meta"] = meta

        inline = re.search(r"KEY\s*:\s*([A-Za-z0-9_\.\-]+).+PROMPT\s*:\s*([^|]+)", line)
        if inline and not current.get("key"):
            raw_key = inline.group(1).strip()
            if _is_measurement_key(raw_key):
                items.append({"key": raw_key, "prompt": inline.group(2).strip()})
            else:
                invalid_keys.append(raw_key)

    if current.get("key") and current.get("prompt"):
        items.append(current)

    for item in items:
        key = item.get("key", "")
        normalized, meta = normalize_req_key(key)
        if meta:
            imeta = item.get("meta") or {}
            imeta.update(meta)
            item["meta"] = imeta
        item["key"] = normalized

    parse_failed = False
    if known_nets is not None:
        valid_items: List[Dict[str, Any]] = []
        for item in items:
            base_net = _extract_base_net(item.get("key", ""))
            if base_net and base_net in known_nets:
                valid_items.append(item)
            else:
                invalid_keys.append(item.get("key", ""))
        if invalid_keys:
            parse_failed = True
        items = valid_items

    meta = {
        "parse_failed": parse_failed,
        "invalid_keys": sorted({k for k in invalid_keys if k}),
        "parse_error": "human_parse_failed" if parse_failed else "",
    }
    return items, meta


def normalize_requested_items(
    items: List[Dict[str, Any]],
    known_nets: Optional[Set[str]] = None,
    known_refdes: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    cleaned: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return [], "json_item_not_object"
        allowed = {"key", "net", "type", "prompt", "hint"}
        if any(k not in allowed for k in item.keys()):
            return [], "json_item_extra_keys"
        key = (item.get("key") or "").strip()
        net = canonicalize_net_name(item.get("net") or "")
        mtype = (item.get("type") or "").strip().lower()
        prompt = (item.get("prompt") or "").strip()
        hint = (item.get("hint") or "").strip()
        if not key or not net or not prompt:
            return [], "json_item_missing_fields"
        if mtype and mtype not in _ALLOWED_TYPES:
            return [], "json_item_invalid_type"
        if known_nets is not None and net not in known_nets:
            return [], "json_item_unknown_net"
        if known_refdes is not None:
            from .components import extract_component_tokens
            tokens = extract_component_tokens(f"{prompt} {hint}".strip())
            for t in tokens:
                if t not in known_refdes:
                    return [], "json_item_unknown_refdes"
        key_norm, meta = normalize_req_key(key)
        expected = f"CHECK_{net}"
        if key_norm.upper() != expected:
            meta["key_normalized_from"] = key_norm
            key_norm = expected
        out: Dict[str, Any] = {"key": key_norm, "prompt": prompt}
        out_meta = {"net": net}
        if mtype:
            out_meta["type"] = mtype
        if hint:
            out_meta["hint"] = hint
        if meta:
            out_meta.update(meta)
        out["meta"] = out_meta
        cleaned.append(out)
    return cleaned, ""


def build_aliases_for_key(key: str) -> List[str]:
    aliases = {key.upper()}
    key_u = key.upper()
    prefix, base, suffix = split_req_key(key_u)
    if prefix:
        aliases.add(base + suffix)
        aliases.add(canonicalize_net_name(base))
        aliases.add(f"CHECK_{base}{suffix}")
        aliases.add(f"VERIFY_{base}{suffix}")
        aliases.add(f"MEASURE_{base}{suffix}")
        aliases.add(f"TEST_{base}{suffix}")
    if not key_u.endswith("_R2G") and not key_u.endswith("_DIODE"):
        rail_match = re.match(r"^(PP[A-Z0-9_]+)", key_u)
        if rail_match:
            aliases.add(rail_match.group(1))
    return sorted(aliases)
