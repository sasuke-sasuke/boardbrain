from __future__ import annotations

import argparse
import json
from pathlib import Path

from .xzzpcb_parser import parse_xzzpcb


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe an XZZPCB .pcb boardview file.")
    ap.add_argument("path", help="Path to .pcb file")
    args = ap.parse_args()
    path = Path(args.path)
    if not path.exists():
        print(f"[probe] file not found: {path}")
        return 2
    nets, net_to_refs, meta = parse_xzzpcb(str(path))
    print(f"[probe] format: {meta.get('format')}")
    print(f"[probe] nets: {len(nets)}")
    print(f"[probe] components: {meta.get('components_count')}")
    print(f"[probe] pairs: {meta.get('pairs_count')}")
    print(f"[probe] testpoints: {len(meta.get('testpoints') or [])}")
    if meta.get("key_source"):
        print(f"[probe] key_source: {meta.get('key_source')}")
    sample_nets = sorted(list(nets))[:20]
    if sample_nets:
        print("[probe] sample nets:", ", ".join(sample_nets))
    if net_to_refs:
        first = next(iter(net_to_refs.keys()))
        print("[probe] sample net refs:", first, "->", [r.get("refdes") for r in net_to_refs[first][:10]])
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
