"""Deterministic reading capture. Parsing proposes; the technician commits."""
from __future__ import annotations

import math
import re

QUANTITIES = ("voltage", "resistance", "diode", "current", "frequency", "continuity")
POWER_STATES = ("unpowered", "standby", "attempting_boot", "running", "unknown")
UNITS = {"voltage": "V", "resistance": "Ω", "diode": "V", "current": "A",
         "frequency": "Hz", "continuity": ""}
# Case matters: mΩ is milliohms; MΩ is megohms. Never lowercase SI prefixes.
_UNITS = {"V": ("voltage", 1), "mV": ("voltage", .001),
          "A": ("current", 1), "mA": ("current", .001), "uA": ("current", 1e-6),
          "µA": ("current", 1e-6), "Ω": ("resistance", 1),
          "ohm": ("resistance", 1), "ohms": ("resistance", 1),
          "kΩ": ("resistance", 1000), "kohm": ("resistance", 1000),
          "MΩ": ("resistance", 1e6), "Mohm": ("resistance", 1e6),
          "mΩ": ("resistance", .001), "mohm": ("resistance", .001),
          "Hz": ("frequency", 1), "kHz": ("frequency", 1000), "MHz": ("frequency", 1e6)}
_VALUE = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
_READING = re.compile(r"^\s*(\S+)\s*(?:=|:)\s*(?:(diode)\s+)?" + _VALUE + r"\s*([a-zA-ZΩµ]+)\s*$")


def parse_reading(text: str) -> dict:
    """Accept only explicit target=value unit syntax. Ambiguity stays visible."""
    raw = text.strip()
    match = _READING.fullmatch(raw)
    if not match:
        raise ValueError("Use target = value unit, e.g. PP3V3 = 3.28 V or USB_D+ = 0.42 diode is entered as USB_D+ = diode 0.42 V. Use the fields for OL or continuity.")
    target, diode, value, unit = match.groups()
    unit = {"v": "V", "mv": "mV", "a": "A", "ma": "mA", "hz": "Hz", "khz": "kHz"}.get(unit, unit)
    if unit not in _UNITS:
        raise ValueError("Unit is ambiguous or unsupported. Use V, mV, A, mA, Ω, kΩ, MΩ, mΩ, Hz, kHz or MHz.")
    quantity, multiplier = _UNITS[unit]
    if diode:
        if quantity != "voltage":
            raise ValueError("Diode mode is recorded in V or mV.")
        quantity = "diode"
    number = float(value) * multiplier
    return validate_measurement({"target": target, "quantity": quantity, "value": number,
                                 "unit": UNITS[quantity], "power_state": "unknown",
                                 "reference": "", "polarity": "", "raw": raw, "note": ""})


def validate_measurement(data: dict) -> dict:
    result = {k: data.get(k, "") for k in ("target", "quantity", "value", "unit", "power_state", "reference", "polarity", "raw", "note")}
    for field in ("target", "reference", "polarity", "raw", "note"):
        if not isinstance(result[field], str) or len(result[field]) > 4000:
            raise ValueError(f"Invalid {field}.")
        result[field] = result[field].strip()
    if not result["target"] or len(result["target"]) > 200:
        raise ValueError("Name the net, component pin, or a temporary physical test point.")
    q = result["quantity"]
    if q not in QUANTITIES or result["power_state"] not in POWER_STATES:
        raise ValueError("Choose a measurement type and power state.")
    if result["unit"] != UNITS[q]:
        raise ValueError(f"Store {q} in {UNITS[q] or 'pass/fail'}; use quick entry to convert prefixes.")
    value = result["value"]
    if isinstance(value, str) and value.strip().upper() in ("OL", "PASS", "FAIL"):
        value = value.strip().upper()
        if (value == "OL" and q not in ("resistance", "diode")) or (value != "OL" and q != "continuity"):
            raise ValueError("OL is only a resistance/diode reading; PASS/FAIL is only continuity.")
    else:
        try:
            if isinstance(value, bool):
                raise ValueError()
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("Enter a numeric reading, OL, or continuity PASS/FAIL.") from None
        if not math.isfinite(value) or (q in ("resistance", "frequency") and value < 0):
            raise ValueError("Reading must be finite and appropriate for this measurement type.")
        if q == "continuity":
            raise ValueError("Use PASS or FAIL for continuity; capture resistance separately.")
    result["value"] = value
    # Do not discard a real observation merely because it was taken incorrectly.
    result["warnings"] = []
    if result["power_state"] == "unknown":
        result["warnings"].append("Power state is unknown; this reading cannot complete a requested test.")
    if q in ("resistance", "diode", "continuity") and result["power_state"] not in ("unpowered", "unknown"):
        result["warnings"].append("This meter mode requires an unpowered, discharged board. Recheck before using the reading.")
    if not result["reference"]:
        result["warnings"].append("Probe reference is missing.")
    if q == "diode" and not result["polarity"]:
        result["warnings"].append("Diode probe polarity is missing.")
    return result


def fulfills(reading: dict, test: dict) -> bool:
    """Exact electrical identity AND test conditions; no net-only completion."""
    return (not reading.get("warnings") and reading.get("target") == test.get("target")
            and reading.get("quantity") == test.get("quantity")
            and reading.get("power_state") == test.get("power_state") != "unknown"
            and reading.get("reference") == test.get("reference") != ""
            and (reading["quantity"] != "diode" or reading.get("polarity") == test.get("polarity") != ""))
