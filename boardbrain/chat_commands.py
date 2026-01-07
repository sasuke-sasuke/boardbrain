from __future__ import annotations
import re
import shlex
from typing import Dict, Any, List, Optional

_COMMAND_PREFIXES = ("/measure", "/note", "/update", "/done")


def parse_command(text: str) -> Optional[Dict[str, Any]]:
    t = text.strip()
    if not t.startswith("/"):
        return None

    if t.startswith("/update"):
        return {"type": "update", "args": {}}
    if t.startswith("/done"):
        return {"type": "done", "args": {}}

    if t.startswith("/note"):
        args = _parse_kv_args(t[len("/note"):])
        if not args:
            remainder = t[len("/note"):].strip()
            if remainder:
                return {"type": "note", "args": {"text": remainder}}
        return {"type": "note", "args": args}

    if t.startswith("/measure"):
        args = _parse_kv_args(t[len("/measure"):])
        return {"type": "measure", "args": args}

    return None


def _parse_kv_args(s: str) -> Dict[str, str]:
    args: Dict[str, str] = {}
    parts = shlex.split(s.strip()) if s.strip() else []
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        args[k] = v
    return args


def extract_measurements(text: str) -> List[Dict[str, Any]]:
    measurements: List[Dict[str, Any]] = []
    seen = set()

    def _add(rail: str, value: str, unit: str, note: str, raw: str) -> None:
        key = (rail.upper(), value, unit)
        if key in seen:
            return
        seen.add(key)
        measurements.append({"rail": rail, "value": value, "unit": unit, "note": note, "raw": raw})

    lines = text.splitlines()
    for line in lines:
        usb_match = re.search(r"usb-?c\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)\\s*v\\s*([0-9]+(?:\\.[0-9]+)?)\\s*a", line, flags=re.IGNORECASE)
        if usb_match:
            v = usb_match.group(1)
            a = usb_match.group(2)
            _add("USB-C", f"{v}V {a}A", "", "usb-c", line)

        rail_matches = re.findall(r"\b(PP[A-Z0-9_]+)\b", line, flags=re.IGNORECASE)
        for rail in rail_matches:
            l = line
            note = ""

            r2g_match = re.search(
                rf"(r\\s*->\\s*gnd|r\\s*to\\s*gnd|r\\s*to\\s*g|r2g).*?{re.escape(rail)}\\b.*?([0-9]+(?:\\.[0-9]+)?)\\s*([a-zA-Z]+)?",
                l,
                flags=re.IGNORECASE,
            )
            if not r2g_match:
                r2g_match = re.search(
                    rf"{re.escape(rail)}\\b.*?(r\\s*->\\s*gnd|r\\s*to\\s*gnd|r\\s*to\\s*g|r2g).*?([0-9]+(?:\\.[0-9]+)?)\\s*([a-zA-Z]+)?",
                    l,
                    flags=re.IGNORECASE,
                )
            if r2g_match:
                value = r2g_match.group(3)
                unit_raw = (r2g_match.group(4) or "").lower()
                unit = _normalize_unit(unit_raw, default="ohms")
                _add(rail, value, unit, "r2g", line)
                continue

            diode_match = re.search(
                rf"(diode)\\b.*?{re.escape(rail)}\\b.*?([0-9]+(?:\\.[0-9]+)?)\\s*([a-zA-Z]+)?",
                l,
                flags=re.IGNORECASE,
            )
            if diode_match:
                value = diode_match.group(2)
                unit_raw = (diode_match.group(3) or "").lower()
                unit = _normalize_unit(unit_raw, default="V")
                _add(rail, value, unit, "diode", line)
                continue
            diode_match = re.search(
                rf"{re.escape(rail)}\\b.*?diode\\b.*?([0-9]+(?:\\.[0-9]+)?)\\s*([a-zA-Z]+)?",
                l,
                flags=re.IGNORECASE,
            )
            if diode_match:
                value = diode_match.group(1)
                unit_raw = (diode_match.group(2) or "").lower()
                unit = _normalize_unit(unit_raw, default="V")
                _add(rail, value, unit, "diode", line)
                continue

            volt_match = re.search(
                rf"{re.escape(rail)}\\b\\s*[:=]?\\s*([0-9]+(?:\\.[0-9]+)?)\\s*(v|mv|volt|volts|millivolt|millivolts)\\b",
                l,
                flags=re.IGNORECASE,
            )
            if volt_match:
                value = volt_match.group(1)
                unit_raw = (volt_match.group(2) or "").lower()
                unit = _normalize_unit(unit_raw, default="V")
                if "stable" in l.lower():
                    note = "stable"
                _add(rail, value, unit, note, line)
                continue

    return measurements


def _normalize_unit(unit_raw: str, default: str) -> str:
    if not unit_raw:
        return default
    u = unit_raw
    if u in ("v", "volt", "volts"):
        return "V"
    if u in ("mv", "millivolt", "millivolts"):
        return "mV"
    if u in ("ohm", "ohms"):
        return "ohms"
    if u in ("kohm", "k"):
        return "kohms"
    if u in ("mohm",):
        return "mohms"
    return default


def is_clarification(text: str) -> bool:
    return bool(re.search(r"\b(explain|clarify|why|what does this mean|what does that mean)\b", text, flags=re.IGNORECASE))
