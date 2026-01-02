from __future__ import annotations
from typing import List

def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> List[str]:
    text = text.replace("\r\n", "\n")
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks
