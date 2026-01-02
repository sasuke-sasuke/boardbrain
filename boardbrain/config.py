from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _get(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None or v.strip() == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v

@dataclass(frozen=True)
class Settings:
    reason_model: str = os.getenv("REASON_MODEL", "gpt-4o")
    embed_model: str = os.getenv("EMBED_MODEL", "text-embedding-3-large")
    data_dir: str = os.getenv("DATA_DIR", "./data")
    kb_raw_dir: str = os.getenv("KB_RAW_DIR", "./kb_raw")
    chroma_dir: str = os.getenv("CHROMA_DIR", "./data/chroma")
    sqlite_path: str = os.getenv("SQLITE_PATH", "./data/boardbrain.sqlite3")
    openai_api_key: str = _get("OPENAI_API_KEY", "")

SETTINGS = Settings()
