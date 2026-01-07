from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boardbrain.boardview import parse_bvraw_format_3


def main() -> int:
    path = Path("kb_raw/MacBook/A2338/820-02020/boardview/820-02020.BVR")
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    if not path.exists():
        print(f"Missing boardview file: {path}")
        return 1
    nets, net_to_refs, meta = parse_bvraw_format_3(str(path))
    print(f"Header: {meta.get('format')}")
    print(f"Net count: {len(nets)}")
    print(f"Component count: {meta.get('components_count')}")
    print(f"Net→RefDes pairs: {sum(len(v) for v in net_to_refs.values())}")
    if meta.get("format") != "BVRAW_FORMAT_3":
        print("FAIL: header not detected")
        return 1
    if len(nets) <= 0 or meta.get("components_count", 0) <= 0 or sum(len(v) for v in net_to_refs.values()) <= 0:
        print("FAIL: missing nets/components/refs")
        return 1
    print("boardview_bvraw3_test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
