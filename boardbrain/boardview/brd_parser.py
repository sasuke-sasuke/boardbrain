from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

from ..netlist import canonicalize_net_name


BRD_SIGNATURE = bytes([0x23, 0xE2, 0x63, 0x28])


@dataclass
class BRDPart:
    name: str
    mounting_side: str
    part_type: str
    end_of_pins: int
    p1: Tuple[int, int] = (0, 0)
    p2: Tuple[int, int] = (0, 0)


@dataclass
class BRDPin:
    x: int
    y: int
    probe: int
    part: int
    net: str
    side: str = "both"


@dataclass
class BRDNail:
    probe: int
    x: int
    y: int
    side: str
    net: str


def _decode_brd(data: bytes) -> Tuple[bytes, bool]:
    if not data.startswith(BRD_SIGNATURE):
        return data, False
    out = bytearray(data)
    for i, b in enumerate(out):
        if b in (0x0D, 0x0A, 0x00):
            continue
        c = b
        x = ~(((c >> 6) & 3) | ((c << 2) & 0xFF))
        out[i] = x & 0xFF
    return bytes(out), True


def _split_lines(data: bytes) -> List[str]:
    text = data.decode("latin-1", errors="ignore")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def _read_tokens(line: str) -> List[str]:
    return [t for t in line.strip().split() if t]


def _parse_brd_file(data: bytes) -> Tuple[List[Tuple[int, int]], List[BRDPart], List[BRDPin], List[BRDNail]]:
    decoded, _ = _decode_brd(data)
    lines = _split_lines(decoded)

    current_block = 0
    num_format = 0
    num_parts = 0
    num_pins = 0
    num_nails = 0

    format_pts: List[Tuple[int, int]] = []
    parts: List[BRDPart] = []
    pins: List[BRDPin] = []
    nails: List[BRDNail] = []

    for raw in lines:
        line = raw.lstrip()
        if not line:
            continue
        if line == "str_length:":
            current_block = 1
            continue
        if line == "var_data:":
            current_block = 2
            continue
        if line in ("Format:", "format:"):
            current_block = 3
            continue
        if line in ("Parts:", "Pins1:"):
            current_block = 4
            continue
        if line in ("Pins:", "Pins2:"):
            current_block = 5
            continue
        if line == "Nails:":
            current_block = 6
            continue

        tokens = _read_tokens(line)
        if not tokens:
            continue
        if current_block == 2:
            if len(tokens) < 4:
                continue
            num_format = int(tokens[0])
            num_parts = int(tokens[1])
            num_pins = int(tokens[2])
            num_nails = int(tokens[3])
        elif current_block == 3:
            if len(tokens) < 2:
                continue
            format_pts.append((int(tokens[0]), int(tokens[1])))
        elif current_block == 4:
            if len(tokens) < 3:
                continue
            name = tokens[0]
            tmp = int(tokens[1])
            end_of_pins = int(tokens[2])
            part_type = "SMD" if (tmp & 0xC) else "TH"
            side = "both"
            if tmp == 1 or (4 <= tmp < 8):
                side = "top"
            elif tmp == 2 or tmp >= 8:
                side = "bottom"
            parts.append(BRDPart(name=name, mounting_side=side, part_type=part_type, end_of_pins=end_of_pins))
        elif current_block == 5:
            if len(tokens) < 5:
                continue
            x, y, probe, part = int(tokens[0]), int(tokens[1]), int(tokens[2]), int(tokens[3])
            net = tokens[4]
            pins.append(BRDPin(x=x, y=y, probe=probe, part=part, net=net))
        elif current_block == 6:
            if len(tokens) < 5:
                continue
            probe, x, y, side = int(tokens[0]), int(tokens[1]), int(tokens[2]), int(tokens[3])
            net = tokens[4]
            nail_side = "top" if side == 1 else "bottom"
            nails.append(BRDNail(probe=probe, x=x, y=y, side=nail_side, net=net))

    nails_to_nets = {n.probe: n.net for n in nails}
    for pin in pins:
        if not pin.net:
            pin.net = nails_to_nets.get(pin.probe, "")
        idx = pin.part - 1
        if 0 <= idx < len(parts):
            pin.side = parts[idx].mounting_side

    if not format_pts and num_format:
        format_pts = format_pts[:num_format]
    return format_pts, parts, pins, nails


def _parse_brd2_file(data: bytes) -> Tuple[List[Tuple[int, int]], List[BRDPart], List[BRDPin], List[BRDNail]]:
    lines = _split_lines(data)
    current_block = 0
    num_format = 0
    num_nets = 0
    num_parts = 0
    num_pins = 0
    num_nails = 0
    max_x = 0
    max_y = 0
    nets: Dict[int, str] = {}
    format_pts: List[Tuple[int, int]] = []
    parts: List[BRDPart] = []
    pins: List[BRDPin] = []
    nails: List[BRDNail] = []

    for raw in lines:
        line = raw.lstrip()
        if not line:
            continue
        if line.startswith("BRDOUT:"):
            current_block = 1
            tokens = _read_tokens(line[len("BRDOUT:"):])
            if len(tokens) >= 3:
                num_format = int(tokens[0])
                max_x = int(tokens[1])
                max_y = int(tokens[2])
            continue
        if line.startswith("NETS:"):
            current_block = 2
            tokens = _read_tokens(line[len("NETS:"):])
            if tokens:
                num_nets = int(tokens[0])
            continue
        if line.startswith("PARTS:"):
            current_block = 3
            tokens = _read_tokens(line[len("PARTS:"):])
            if tokens:
                num_parts = int(tokens[0])
            continue
        if line.startswith("PINS:"):
            current_block = 4
            tokens = _read_tokens(line[len("PINS:"):])
            if tokens:
                num_pins = int(tokens[0])
            continue
        if line.startswith("NAILS:"):
            current_block = 5
            tokens = _read_tokens(line[len("NAILS:"):])
            if tokens:
                num_nails = int(tokens[0])
            continue

        tokens = _read_tokens(line)
        if not tokens:
            continue
        if current_block == 1:
            if len(tokens) < 2:
                continue
            format_pts.append((int(tokens[0]), int(tokens[1])))
        elif current_block == 2:
            if len(tokens) < 2:
                continue
            nets[int(tokens[0])] = tokens[1]
        elif current_block == 3:
            if len(tokens) < 7:
                continue
            name = tokens[0]
            p1x, p1y, p2x, p2y = int(tokens[1]), int(tokens[2]), int(tokens[3]), int(tokens[4])
            end_of_pins = int(tokens[5])
            side_val = int(tokens[6])
            side = "both"
            if side_val == 1:
                side = "top"
            elif side_val == 2:
                side = "bottom"
            parts.append(
                BRDPart(
                    name=name,
                    mounting_side=side,
                    part_type="SMD",
                    end_of_pins=end_of_pins,
                    p1=(p1x, p1y),
                    p2=(p2x, p2y),
                )
            )
        elif current_block == 4:
            if len(tokens) < 4:
                continue
            x, y, netid, side_val = int(tokens[0]), int(tokens[1]), int(tokens[2]), int(tokens[3])
            side = "both"
            if side_val == 1:
                side = "top"
            elif side_val == 2:
                side = "bottom"
            net = nets.get(netid, "")
            pins.append(BRDPin(x=x, y=y, probe=1, part=0, net=net, side=side))
        elif current_block == 5:
            if len(tokens) < 5:
                continue
            probe, x, y, netid, is_top = int(tokens[0]), int(tokens[1]), int(tokens[2]), int(tokens[3]), int(tokens[4])
            net = nets.get(netid, "UNCONNECTED")
            if is_top == 1:
                side = "top"
            else:
                side = "bottom"
                y = max_y - y
            nails.append(BRDNail(probe=probe, x=x, y=y, side=side, net=net))

    # assign pins to parts
    cpi = 0
    for i in range(len(parts)):
        if i == len(parts) - 1:
            pei = len(pins)
        else:
            pei = parts[i + 1].end_of_pins
        is_dip = True
        if parts[i].mounting_side == "bottom":
            p1x, p1y = parts[i].p1
            p2x, p2y = parts[i].p2
            parts[i].p1 = (p1x, max_y - p1y)
            parts[i].p2 = (p2x, max_y - p2y)
        while cpi < pei and cpi < len(pins):
            pins[cpi].part = i + 1
            if pins[cpi].side != "top":
                pins[cpi].y = max_y - pins[cpi].y
            if (pins[cpi].side == "top" and parts[i].mounting_side == "top") or (
                pins[cpi].side == "bottom" and parts[i].mounting_side == "bottom"
            ):
                is_dip = False
            cpi += 1
        if is_dip:
            parts[i].part_type = "TH"
            parts[i].mounting_side = "both"
    # dummy parts for probe points
    parts.append(BRDPart(name="...", mounting_side="bottom", part_type="SMD", end_of_pins=0))
    parts.append(BRDPart(name="...", mounting_side="top", part_type="SMD", end_of_pins=0))

    return format_pts, parts, pins, nails


def parse_brd(path: str) -> Tuple[set, Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    data = open(path, "rb").read()
    if b"BRDOUT:" in data and b"NETS:" in data:
        fmt = "BRD2"
        format_pts, parts, pins, nails = _parse_brd2_file(data)
    else:
        fmt = "BRD"
        format_pts, parts, pins, nails = _parse_brd_file(data)

    nets: set = set()
    net_to_refs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    part_points: Dict[int, List[Tuple[int, int]]] = {}

    for pin in pins:
        net = canonicalize_net_name(pin.net or "")
        if not net or net.startswith("UNCONNECTED"):
            continue
        nets.add(net)
        idx = pin.part - 1
        if 0 <= idx < len(parts):
            refdes = parts[idx].name.strip()
            if refdes and refdes != "...":
                kind = "TP" if refdes.startswith("TP") else ("P" if refdes.startswith("P") else refdes[:1])
                net_to_refs.setdefault(net, {})
                net_to_refs[net].setdefault(
                    refdes,
                    {
                        "refdes": refdes,
                        "kind": kind,
                        "side": parts[idx].mounting_side,
                    },
                )
            part_points.setdefault(idx, []).append((pin.x, pin.y))

    # include nail nets
    testpoints: List[Dict[str, Any]] = []
    for nail in nails:
        net = canonicalize_net_name(nail.net or "")
        if net and not net.startswith("UNCONNECTED"):
            nets.add(net)
        testpoints.append(
            {
                "probe": nail.probe,
                "net": net,
                "x": nail.x,
                "y": nail.y,
                "side": nail.side,
            }
        )

    # components list
    components: List[Dict[str, Any]] = []
    for idx, part in enumerate(parts):
        refdes = part.name.strip()
        if not refdes or refdes == "...":
            continue
        if part.p1 != (0, 0) or part.p2 != (0, 0):
            x = (part.p1[0] + part.p2[0]) / 2
            y = (part.p1[1] + part.p2[1]) / 2
        else:
            pts = part_points.get(idx, [])
            if pts:
                x = sum(p[0] for p in pts) / len(pts)
                y = sum(p[1] for p in pts) / len(pts)
            else:
                x, y = 0, 0
        components.append(
            {
                "refdes": refdes,
                "side": part.mounting_side,
                "type": part.part_type,
                "x": x,
                "y": y,
            }
        )

    # finalize net_to_refs
    net_to_refs_dict: Dict[str, List[Dict[str, Any]]] = {
        n: list(refs.values()) for n, refs in net_to_refs.items()
    }
    meta = {
        "format": f"BRD_{fmt}",
        "nets_count": len(nets),
        "components_count": len(components),
        "pairs_count": sum(len(v) for v in net_to_refs_dict.values()),
        "outline_points": [{"x": x, "y": y} for x, y in format_pts],
        "testpoints_count": len(testpoints),
        "testpoints": testpoints,
        "units": "mil",
    }
    meta["components"] = [c["refdes"] for c in components]
    meta["bounds"] = _compute_bounds(format_pts)
    return nets, net_to_refs_dict, meta


def _compute_bounds(points: List[Tuple[int, int]]) -> Dict[str, int]:
    if not points:
        return {"min_x": 0, "min_y": 0, "max_x": 0, "max_y": 0}
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }
