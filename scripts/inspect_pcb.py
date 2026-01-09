#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
import zlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

PRINTABLE = set(range(32, 127)) | {9, 10, 13}

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def printable_ratio(b: bytes) -> float:
    if not b:
        return 0.0
    good = sum(1 for x in b[:200000] if x in PRINTABLE)  # sample first 200KB
    return good / min(len(b), 200000)

def preview_printable(b: bytes, limit: int = 300) -> str:
    # keep only printable chars, replace others with '.'
    out = []
    for x in b:
        if x in PRINTABLE:
            out.append(chr(x))
        else:
            out.append(".")
        if len(out) >= limit:
            break
    return "".join(out)

def find_all(data: bytes, needle: bytes) -> List[int]:
    out = []
    start = 0
    while True:
        i = data.find(needle, start)
        if i == -1:
            break
        out.append(i)
        start = i + 1
    return out

def try_decompress(data: bytes, offset: int, mode: str, max_out: int) -> Optional[bytes]:
    chunk = data[offset:]
    try:
        if mode == "zlib":
            out = zlib.decompress(chunk, wbits=15, bufsize=max_out)
        elif mode == "gzip":
            out = zlib.decompress(chunk, wbits=31, bufsize=max_out)
        elif mode == "raw":
            out = zlib.decompress(chunk, wbits=-15, bufsize=max_out)
        else:
            return None
        return out[:max_out]
    except Exception:
        return None

@dataclass
class Candidate:
    offset: int
    mode: str
    out_len: int
    pratio: float
    keyword_hits: int
    preview: str

KEYWORDS = [
    b"NET", b"net", b"PART", b"part", b"REF", b"ref", b"NAME", b"name",
    b"PIN", b"pin", b"COMP", b"comp", b"X", b"Y"
]

def keyword_score(b: bytes) -> int:
    score = 0
    for k in KEYWORDS:
        if k in b:
            score += 1
    return score

def scan(data: bytes, max_candidates: int, max_out: int, preview_len: int) -> dict:
    # zlib headers: 78 01 / 78 9C / 78 DA are common, but not exhaustive
    zlib_offsets = []
    for sig in (b"\x78\x01", b"\x78\x9c", b"\x78\xda"):
        zlib_offsets.extend(find_all(data, sig))
    zlib_offsets = sorted(set(zlib_offsets))

    # also try a sparse scan every N bytes for raw-deflate false negatives
    step = 8192
    sparse_offsets = list(range(0, len(data), step))

    # prioritize “real” zlib-looking offsets first, then sparse
    offsets = zlib_offsets + [o for o in sparse_offsets if o not in zlib_offsets]
    offsets = offsets[:max_candidates]

    candidates: List[Candidate] = []

    for off in offsets:
        for mode in ("zlib", "raw", "gzip"):
            out = try_decompress(data, off, mode, max_out=max_out)
            if not out:
                continue
            pr = printable_ratio(out)
            ks = keyword_score(out)
            # basic sanity filter: either looks texty or contains useful keywords
            if pr < 0.05 and ks == 0:
                continue
            candidates.append(Candidate(
                offset=off,
                mode=mode,
                out_len=len(out),
                pratio=pr,
                keyword_hits=ks,
                preview=preview_printable(out, limit=preview_len),
            ))

    # rank: keyword hits first, then printable ratio, then output length
    candidates.sort(key=lambda c: (c.keyword_hits, c.pratio, c.out_len), reverse=True)

    top = candidates[:10]
    summary = {
        "file_size": len(data),
        "magic_16": data[:16].hex(),
        "zlib_like_offset_count": len(zlib_offsets),
        "zlib_like_offsets_first_20": zlib_offsets[:20],
        "tested_offset_count": len(offsets),
        "top_candidates": [
            {
                "rank": i + 1,
                "offset": c.offset,
                "mode": c.mode,
                "out_len": c.out_len,
                "printable_ratio": round(c.pratio, 4),
                "keyword_hits": c.keyword_hits,
                "preview": c.preview,
            }
            for i, c in enumerate(top)
        ],
    }
    return summary

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcb_path")
    ap.add_argument("--max-candidates", type=int, default=250, help="how many offsets to try")
    ap.add_argument("--max-out", type=int, default=400000, help="max decompressed bytes kept per attempt")
    ap.add_argument("--preview-len", type=int, default=300)
    ap.add_argument("--out", default="", help="write JSON summary to this path")
    args = ap.parse_args()

    path = args.pcb_path
    if not os.path.exists(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)

    h = sha256_file(path)
    with open(path, "rb") as f:
        data = f.read()

    summary = scan(data, args.max_candidates, args.max_out, args.preview_len)
    summary["sha256"] = h
    summary["path"] = path

    print(json.dumps(summary, indent=2))

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"\nWrote: {args.out}")

if __name__ == "__main__":
    main()
