from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from ..pcb_boardview import _collect_candidates, _collect_pcb_chunks


def _write_payload(path: Path, chunk: Dict[str, Any]) -> None:
    data = chunk.get("data") or b""
    if chunk.get("likely_text"):
        text = data.decode(chunk.get("encoding") or "utf-8", errors="ignore")
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(data)


def probe(path: str, out_dir: str | None, top: int, max_candidates: int, dense: bool) -> int:
    src = Path(path)
    if not src.exists():
        print(f"[probe] file not found: {path}")
        return 2
    data = src.read_bytes()
    candidates = _collect_candidates(data, max_candidates, dense=dense)
    chunks = _collect_pcb_chunks(
        data,
        max_candidates=max_candidates,
        max_total_out=64 * 1024 * 1024,
        max_stream_out=8 * 1024 * 1024,
        max_stream_in=16 * 1024 * 1024,
        dense=dense,
    )
    chunks_sorted = sorted(chunks, key=lambda c: c.get("score", 0), reverse=True)
    print(f"[probe] candidates: {len(candidates)}")
    print(f"[probe] decompressed chunks: {len(chunks)}")
    if not chunks:
        from ..pcb_boardview import _extract_ascii_strings, _NET_RE, _REFDES_RE
        strings = _extract_ascii_strings(data)
        nets = [s for _, s in strings if _NET_RE.fullmatch(s or "")]
        refs = [s for _, s in strings if _REFDES_RE.fullmatch(s or "")]
        print(f"[probe] null-terminated strings: {len(strings)}")
        print(f"[probe] net strings: {len(nets)} refdes strings: {len(refs)}")
        if nets:
            print("[probe] sample nets:", ", ".join(sorted(set(nets))[:10]))
        if refs:
            print("[probe] sample refdes:", ", ".join(sorted(set(refs))[:10]))
    print("")
    print("Rank | Method | Offset | OutLen | Printable | Score | Markers")
    print("-----+--------+--------+--------+-----------+-------+--------")
    for idx, ch in enumerate(chunks_sorted[:top], start=1):
        markers = ",".join(sorted(ch.get("marker_hits", {}).keys()))
        print(
            f"{idx:>4} | {ch['method']:<6} | {ch['offset']:<6} | {ch['decompressed_len']:<6} "
            f"| {ch.get('printable_ratio', 0):<9} | {ch.get('score', 0):<5} | {markers}"
        )
    print("")
    if chunks_sorted:
        best = chunks_sorted[0]
        print("[probe] best preview:")
        print(best.get("preview", ""))

    if out_dir:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        for idx, ch in enumerate(chunks_sorted[:top], start=1):
            suffix = "txt" if ch.get("likely_text") else "bin"
            name = f"chunk_{idx:02d}_{ch['method']}_0x{ch['offset']:x}.{suffix}"
            _write_payload(out_path / name, ch)
        summary = {
            "path": str(src),
            "file_size": len(data),
            "candidate_count": len(candidates),
            "chunks_count": len(chunks),
            "top_chunks": [
                {
                    "rank": idx + 1,
                    "method": ch["method"],
                    "offset": ch["offset"],
                    "decompressed_len": ch["decompressed_len"],
                    "printable_ratio": ch.get("printable_ratio"),
                    "score": ch.get("score"),
                    "marker_hits": ch.get("marker_hits"),
                    "preview": ch.get("preview"),
                }
                for idx, ch in enumerate(chunks_sorted[:top])
            ],
        }
        summary_path = out_path / "probe_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[probe] wrote: {summary_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe a FlexBV .pcb boardview container.")
    ap.add_argument("path", help="Path to .pcb boardview file")
    ap.add_argument("--out", default="", help="Write top decoded payloads + JSON summary here")
    ap.add_argument("--top", type=int, default=5, help="How many top chunks to report")
    ap.add_argument("--max-candidates", type=int, default=400, help="Max candidate offsets to scan")
    ap.add_argument("--dense", action="store_true", help="Use dense raw-deflate scanning (slow)")
    args = ap.parse_args()
    out_dir = args.out if args.out else None
    return probe(args.path, out_dir, args.top, args.max_candidates, args.dense)


if __name__ == "__main__":
    raise SystemExit(main())
