"""Conservative adapters. No string proximity or guessed binary connectivity."""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path

PARSER_VERSION = "bvr3-csv/1"


def _number(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite coordinate")
    return result


def _identifier(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError("Missing or invalid electrical identifier")
    return value  # USB_D+, USB_D-, RESET# and RESET remain distinct.


def parse_bvr3(text: str) -> list[dict]:
    """Read explicit BVRAW_FORMAT_3 records, including relative pin origins.

    Field semantics cross-checked against OpenBoardView BVR3File.cpp.
    Reject a malformed file as a whole; an earlier good record is not a success.
    """
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "BVRAW_FORMAT_3":
        raise ValueError("Not a plain BVRAW_FORMAT_3 file")
    parts, pins, part, pin = set(), [], {}, {}
    part_pins = []
    known_part = {"PART_NAME", "PART_SIDE", "PART_ORIGIN", "PART_MOUNT", "PART_OUTLINE_RELATIVE", "PART_VALUE", "PART_COMMENT", "PART_ID"}
    known_pin = {"PIN_ID", "PIN_NUMBER", "PIN_NAME", "PIN_SIDE", "PIN_ORIGIN", "PIN_RADIUS", "PIN_NET", "PIN_TYPE", "PIN_COMMENT", "PIN_OUTLINE_RELATIVE"}
    for lineno, raw in enumerate(lines[1:], 2):
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        field, _, value = line.partition(" ")
        if "\t" in field:
            field, value = line.split(None, 1)
        value = value.strip()
        if field == "PART_END":
            if not part or pin or not part_pins:
                raise ValueError(f"Incomplete part at line {lineno}")
            ref = _identifier(part.get("PART_NAME", ""))
            if ref in parts:
                raise ValueError(f"Duplicate part {ref}; subboard scope needs a dedicated adapter")
            parts.add(ref)
            origin = part.get("PART_ORIGIN", "0 0").split()
            if len(origin) != 2:
                raise ValueError(f"Invalid part origin for {ref}")
            ox, oy = map(_number, origin)
            for item in part_pins:
                xy = item.pop("origin")
                item.update(ref=ref, side=item["side"] or part.get("PART_SIDE", ""))
                if item["side"] not in ("T", "B", "O", ""):
                    raise ValueError(f"Invalid side for {ref}")
                item.update(x=xy[0] + ox if xy else None, y=xy[1] + oy if xy else None)
                pins.append(item)
            part, part_pins = {}, []
        elif field == "PIN_END":
            if not part or not pin:
                raise ValueError(f"Pin outside part at line {lineno}")
            number = _identifier(pin.get("PIN_NUMBER", ""))
            net = _identifier(pin.get("PIN_NET", ""))
            xy = pin.get("PIN_ORIGIN", "").split()
            if xy and len(xy) != 2:
                raise ValueError(f"Invalid pin origin at line {lineno}")
            part_pins.append(dict(pin=number, net=net, name=pin.get("PIN_NAME", ""),
                                  side=pin.get("PIN_SIDE", ""), origin=list(map(_number, xy)), line=lineno))
            pin = {}
        elif field in known_part:
            if field in part or pin:
                raise ValueError(f"Repeated or misplaced part field at line {lineno}")
            part[field] = value
        elif field in known_pin:
            if not part or field in pin:
                raise ValueError(f"Repeated or misplaced pin field at line {lineno}")
            pin[field] = value
        elif field.startswith(("PART_", "PIN_")):
            raise ValueError(f"Unsupported structural field {field} at line {lineno}")
        elif field not in {"OUTLINE_POINTS", "OUTLINE_SEGMENTED", "BOARD_NAME", "BOARD_DESCRIPTION", "BOARD_UNITS"}:
            raise ValueError(f"Unknown record {field} at line {lineno}; sample validation needed")
    if part or pin or part_pins:
        raise ValueError("Truncated file: missing PIN_END or PART_END")
    return _validate_pins(pins)


def _validate_pins(pins: list[dict]) -> list[dict]:
    if not pins:
        raise ValueError("No explicit component/pin/net records")
    seen = set()
    for item in pins:
        key = item["ref"], item["pin"]
        if key in seen:
            raise ValueError(f"Duplicate or conflicting pin: {key[0]}.{key[1]}")
        seen.add(key)
    return pins


def parse_csv(text: str) -> list[dict]:
    """Documented interchange, not an assumed FlexBV export format."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not {"ref", "pin", "net"}.issubset(reader.fieldnames or []):
        raise ValueError("CSV requires ref,pin,net columns; optional side,x,y")
    pins = []
    for line, row in enumerate(reader, 2):
        item = {k: _identifier(row.get(k) or "") for k in ("ref", "pin", "net")}
        side = (row.get("side") or "").strip()
        if side not in ("", "T", "B", "O"):
            raise ValueError(f"Invalid side at CSV line {line}")
        x, y = row.get("x") or "", row.get("y") or ""
        if bool(x) != bool(y):
            raise ValueError(f"Both x and y are needed at CSV line {line}")
        item.update(side=side, x=_number(x) if x else None, y=_number(y) if y else None, line=line)
        pins.append(item)
    return _validate_pins(pins)


def parse_boardview(filename: str, content: bytes) -> dict:
    result = {"parser_version": PARSER_VERSION, "status": "unsupported", "pins": [],
              "message": "File retained. This format needs a validated adapter. Use FlexBV and record checked pin mappings for now."}
    if len(content) > 25 * 1024 * 1024:
        raise ValueError("File exceeds 25 MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return result
    adapter = parse_bvr3 if text.startswith("BVRAW_FORMAT_3") else (parse_csv if Path(filename).suffix.lower() == ".csv" else None)
    if adapter is None:
        return result
    try:
        pins = adapter(text)
        result.update(status="parsed", pins=pins, message=f"{len(pins)} explicit pins parsed. Confirm the board/revision and compare with FlexBV before probing.")
    except (ValueError, TypeError) as exc:
        result.update(status="invalid", message=str(exc))
    return result
