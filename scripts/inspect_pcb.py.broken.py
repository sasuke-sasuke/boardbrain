from __future__ import annotations
import sys
from pathlib import Path
from pathlib import Path as _Path
import sys as _sys

ROOT = _Path(__file__).resolve().parents[1]
if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))

from boardbrain.pcb_boardview import _scan_zlib_offsets, _scan_deflate_offsets, _safe_decompress_stream, _is_text_like


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/inspect_pcb.py <path>")
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"missing file: {path}")
        return 1
    data = path.read_bytes()
    zlib_offsets = _scan_zlib_offsets(data)
    deflate_offsets = _scan_deflate_offsets(data)
    offsets = zlib_offsets + [o for o in deflate_offsets if o not in zlib_offsets]
    print(f"candidate zlib headers: {len(zlib_offsets)}")
    print(f"candidate deflate offsets: {len(deflate_offsets)}")
    scored = []
    for off in offsets[:50]:
        out, _ = _safe_decompress_stream(data, off, 8 * 1024 * 1024, 16 * 1024 * 1024)
        if not out:
            continue
        text_like = _is_text_like(out)
        preview = out[:200].decode("latin-1", errors="ignore")
        score = preview.count("BVRAW_FORMAT") + preview.count("PART") + preview.count("NET")
        scored.append((score, off, len(out), text_like, preview))
    scored.sort(reverse=True)
    for score, off, length, text_like, preview in scored[:5]:
        print(f"offset={off} len={length} text_like={text_like} score={score}")
        print(preview)
        print("---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
