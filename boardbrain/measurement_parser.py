from __future__ import annotations
import re
from typing import Dict, Any, List
from .netlist import canonicalize_net_name

_NET_TOKEN = re.compile(
    r"\b((?:PP[A-Z0-9_.]+)|(?:[A-Z][A-Z0-9_.]*_[A-Z0-9_.]+))\b",
    re.IGNORECASE,
)
_KEYED_NET = re.compile(r"\b(?:CHECK|VERIFY|MEASURE|TEST)_([A-Z0-9_.]+(?:_[A-Z0-9_.]+)*)\b", re.IGNORECASE)
_FUSE = re.compile(r"\bF\d{2,5}\b", re.IGNORECASE)
_USBC = re.compile(r"\busb-?c\b", re.IGNORECASE)

_VOLT_UNIT = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*(mv|v|volt|volts)\b", re.IGNORECASE)
_CURR_UNIT = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*(ma|a|amp|amps)\b", re.IGNORECASE)
_RES_UNIT = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*(ohm|ohms|kohm|kohms|mohm|mohms|Ω|kΩ)\b", re.IGNORECASE)
_RES_KEYWORD = re.compile(r"\b(ohm|ohms|resistance)\b.*?([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_HZ_UNIT = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*(hz|khz|mhz)\b", re.IGNORECASE)
_DIODE = re.compile(r"\b(diode|dmode)\b\s*([0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)
_R2G = re.compile(r"\b(r2g|r\s*to\s*gnd|r\s*->\s*gnd)\b", re.IGNORECASE)

_QUESTION = re.compile(r"\?|^\s*(what|why|how|when|where|is|are|do|does|can|should)\b", re.IGNORECASE)

_SPECIAL_NETS = {"PORT:USBC"}


def classify_and_parse(text: str, known_nets: set) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    net_validation: List[Dict[str, Any]] = []

    segments = _split_segments(text)
    for seg in segments:
        seg_entries, seg_invalid, seg_rejected, seg_net_validation = _parse_segment(seg, known_nets)
        entries.extend(seg_entries)
        invalid.extend(seg_invalid)
        rejected.extend(seg_rejected)
        net_validation.extend(seg_net_validation)

    has_measurements = bool(entries)
    has_question = bool(_QUESTION.search(text))

    if has_question and not has_measurements:
        classification = "QUESTION"
    elif has_measurements and has_question:
        classification = "MIXED"
    elif has_measurements:
        classification = "MEASUREMENT"
    else:
        classification = "UNKNOWN"

    return {
        "classification": classification,
        "entries": entries,
        "invalid": invalid,
        "rejected": rejected,
        "net_validation": net_validation,
    }


def _split_segments(text: str) -> List[str]:
    parts = []
    for line in text.splitlines():
        for seg in line.split(","):
            s = seg.strip()
            if s:
                parts.append(s)
    return parts


def _parse_segment(seg: str, known_nets: set) -> tuple[list, list, list, list]:
    entries: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    net_validation: List[Dict[str, Any]] = []

    raw_net = None
    key_hint = None

    m_key = _KEYED_NET.search(seg)
    if m_key:
        key_hint = f"CHECK_{m_key.group(1).upper()}"
        raw_net = m_key.group(1)
    else:
        m_net = _NET_TOKEN.search(seg)
        if m_net:
            raw_net = m_net.group(1)

    net = canonicalize_net_name(raw_net) if raw_net else ""
    if raw_net and net:
        net_validation.append({"net": net, "valid": (net in known_nets)})

    fuse_match = _FUSE.search(seg)
    if fuse_match and not raw_net:
        status = ""
        if re.search(r"\bgood\b", seg, re.IGNORECASE):
            status = "good"
        elif re.search(r"\bopen\b", seg, re.IGNORECASE):
            status = "open"
        elif re.search(r"\bcontinuity\s*pass\b", seg, re.IGNORECASE):
            status = "pass"
        elif re.search(r"\bno\s*beep\b", seg, re.IGNORECASE):
            status = "no_beep"
        if status:
            entries.append(
                {
                    "net": fuse_match.group(0).upper(),
                    "net_raw": fuse_match.group(0),
                    "type": "continuity",
                    "value": status,
                    "unit": "",
                    "raw": seg,
                    "key_hint": key_hint,
                    "matched_rule": "continuity",
                }
            )
            return entries, invalid, rejected, net_validation

    if _USBC.search(seg):
        m_v = _VOLT_UNIT.search(seg)
        m_a = _CURR_UNIT.search(seg)
        if m_v and m_a:
            entries.append(
                {
                    "net": "PORT:USBC",
                    "net_raw": "USB-C",
                    "type": "port_vi",
                    "value": f"{m_v.group(1)}V {m_a.group(1)}A",
                    "unit": "",
                    "raw": seg,
                    "key_hint": key_hint,
                    "matched_rule": "port_vi",
                }
            )
        else:
            rejected.append({"segment": seg, "reason": "port_mention_only"})
        return _finalize(entries, invalid, rejected, net_validation, known_nets)

    has_question = bool(_QUESTION.search(seg))
    has_explicit_kv = bool(raw_net and re.search(rf"\b{re.escape(raw_net)}\b\s*[:=]\s*", seg, re.IGNORECASE))
    has_inline_val = False
    if raw_net:
        has_inline_val = bool(
            re.search(
                rf"\b{re.escape(raw_net)}\b\s+(?:r2g|diode|dmode|ohms|resistance)?\s*[0-9]+(?:\.[0-9]+)?\s*(mv|v|volt|volts|ma|a|amp|amps|ohm|ohms|kohm|kohms|mohm|mohms|Ω|kΩ|hz|khz|mhz)\b",
                seg,
                re.IGNORECASE,
            )
        )
    has_measure_syntax = has_explicit_kv or has_inline_val
    if has_question and not has_measure_syntax:
        if raw_net:
            rejected.append({"segment": seg, "reason": "question_no_store"})
        return entries, invalid, rejected, net_validation

    m_diode = _DIODE.search(seg)
    if m_diode and raw_net and has_measure_syntax:
        entries.append(
            {
                "net": net or raw_net,
                "net_raw": raw_net or "",
                "type": "diode",
                "value": m_diode.group(2),
                "unit": "V",
                "raw": seg,
                "key_hint": key_hint,
                "matched_rule": "diode",
            }
        )
        return _finalize(entries, invalid, rejected, net_validation, known_nets)

    m_res = _RES_UNIT.search(seg)
    if m_res and raw_net and has_measure_syntax:
        entries.append(
            {
                "net": net or raw_net,
                "net_raw": raw_net or "",
                "type": "resistance",
                "value": m_res.group(1),
                "unit": m_res.group(2),
                "raw": seg,
                "key_hint": key_hint,
                "matched_rule": "resistance",
            }
        )
        return _finalize(entries, invalid, rejected, net_validation, known_nets)

    m_res_kw = _RES_KEYWORD.search(seg)
    if m_res_kw and raw_net and has_measure_syntax:
        entries.append(
            {
                "net": net or raw_net,
                "net_raw": raw_net or "",
                "type": "resistance",
                "value": m_res_kw.group(2),
                "unit": "ohms",
                "raw": seg,
                "key_hint": key_hint,
                "matched_rule": "resistance_keyword",
            }
        )
        return _finalize(entries, invalid, rejected, net_validation, known_nets)
    if raw_net and _R2G.search(seg) and not m_res:
        rejected.append({"segment": seg, "reason": "missing_unit"})
        return entries, invalid, rejected, net_validation

    m_v = _VOLT_UNIT.search(seg)
    if m_v and raw_net and has_measure_syntax:
        entries.append(
            {
                "net": net or raw_net,
                "net_raw": raw_net or "",
                "type": "voltage",
                "value": m_v.group(1),
                "unit": m_v.group(2),
                "raw": seg,
                "key_hint": key_hint,
                "matched_rule": "voltage",
            }
        )
        return _finalize(entries, invalid, rejected, net_validation, known_nets)

    m_a = _CURR_UNIT.search(seg)
    if m_a and raw_net and has_measure_syntax:
        entries.append(
            {
                "net": net or raw_net,
                "net_raw": raw_net or "",
                "type": "current",
                "value": m_a.group(1),
                "unit": m_a.group(2),
                "raw": seg,
                "key_hint": key_hint,
                "matched_rule": "current",
            }
        )
        return _finalize(entries, invalid, rejected, net_validation, known_nets)

    m_hz = _HZ_UNIT.search(seg)
    if m_hz and raw_net and has_measure_syntax:
        entries.append(
            {
                "net": net or raw_net,
                "net_raw": raw_net or "",
                "type": "frequency",
                "value": m_hz.group(1),
                "unit": m_hz.group(2),
                "raw": seg,
                "key_hint": key_hint,
                "matched_rule": "frequency",
            }
        )
        return _finalize(entries, invalid, rejected, net_validation, known_nets)

    if raw_net:
        if has_question:
            rejected.append({"segment": seg, "reason": "question_no_store"})
        else:
            rejected.append({"segment": seg, "reason": "missing_unit"})

    return entries, invalid, rejected, net_validation


def _finalize(entries: list, invalid: list, rejected: list, net_validation: list, known_nets: set) -> tuple[list, list, list, list]:
    filtered: List[Dict[str, Any]] = []
    for e in entries:
        net = e.get("net", "")
        if net and (net in known_nets or net in _SPECIAL_NETS):
            filtered.append(e)
        elif net:
            invalid.append(e)
        else:
            rejected.append({"segment": e.get("raw", ""), "reason": "missing_net"})
    return filtered, invalid, rejected, net_validation
