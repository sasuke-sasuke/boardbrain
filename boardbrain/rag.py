from __future__ import annotations
import os
from typing import List, Dict, Any, Optional
import chromadb
from .config import SETTINGS

COLLECTION_NAME = "boardbrain_kb"

def get_collection():
    os.makedirs(SETTINGS.chroma_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=SETTINGS.chroma_dir)
    return client.get_or_create_collection(COLLECTION_NAME)

def upsert_text_chunks(ids: List[str], embeddings: List[List[float]], documents: List[str], metadatas: List[Dict[str, Any]]) -> None:
    col = get_collection()
    col.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

def query(query_embedding: List[float], n_results: int = 8, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    col = get_collection()
    res = col.query(query_embeddings=[query_embedding], n_results=n_results, where=where or {})
    out = []
    for i in range(len(res["ids"][0])):
        out.append({
            "id": res["ids"][0][i],
            "document": res["documents"][0][i],
            "metadata": res["metadatas"][0][i],
            "distance": res["distances"][0][i],
        })
    return out
