from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

from .boardview import parse_boardview, detect_boardview_format
from .pcb_boardview import _collect_candidates, _safe_decompress_stream, _is_text_like
from .netlist import canonicalize_net_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a boardview file and print summary.")
    parser.add_argument("--board_id", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"File not found: {args.path}")
        return 1
    with open(args.path, "rb") as f:
        head = f.read(256)
    fmt = detect_boardview_format(args.path, head)
    print(f"Detected format: {fmt or 'unknown'}")
    try:
        nets, net_to_refs, meta = parse_boardview(args.path)
    except Exception as e:
        print(f"Parse failed: {e}")
        if fmt == "PCB_EMBEDDED_ZLIB":
            data = Path(args.path).read_bytes()
            candidates = _collect_candidates(data, max_hits=100)
            print(f"Candidate streams: {len(candidates)}")
            good = 0
            for cand in candidates[:20]:
                out, consumed = _safe_decompress_stream(
                    data,
                    cand["offset"],
                    cand["method"],
                    8 * 1024 * 1024,
                    16 * 1024 * 1024,
                )
                if out:
                    good += 1
                    print(
                        f"- {cand['method']} offset {cand['offset']} "
                        f"decompressed_len={len(out)} text_like={_is_text_like(out)}"
                    )
            print(f"Streams decompressed: {good}")
        return 1
    net_count = len(nets)
    refdes = set()
    for refs in net_to_refs.values():
        for item in refs:
            if isinstance(item, dict):
                ref = (item.get("refdes") or "").upper()
            else:
                ref = str(item).upper()
            if ref:
                refdes.add(ref)
    pairs = sum(len(v) for v in net_to_refs.values())
    print(f"Nets: {net_count}")
    print(f"Components: {len(refdes)}")
    print(f"Net→RefDes pairs: {pairs}")
    if meta:
        print("Meta:")
        print(json.dumps(meta, indent=2))
    sample_nets = sorted(list(nets))[:20]
    sample_refdes = sorted(list(refdes))[:20]
    if sample_nets:
        print("Sample nets:", ", ".join(sample_nets))
    if sample_refdes:
        print("Sample refdes:", ", ".join(sample_refdes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
