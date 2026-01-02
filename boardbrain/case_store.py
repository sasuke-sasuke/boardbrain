from __future__ import annotations
import os
import shutil
import sqlite3
from typing import Optional, List, Dict, Any
from .config import SETTINGS

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    device_family TEXT,
    model TEXT,
    board_id TEXT,
    symptom TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    unit TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS baselines (
    baseline_id TEXT PRIMARY KEY,
    device_family TEXT,
    model TEXT,
    board_id TEXT,
    quality TEXT,
    source TEXT,
    boot_state TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baseline_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    unit TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(baseline_id) REFERENCES baselines(baseline_id)
);

CREATE TABLE IF NOT EXISTS baseline_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    baseline_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(baseline_id) REFERENCES baselines(baseline_id)
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(ddl)

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(SETTINGS.sqlite_path), exist_ok=True)
    c = sqlite3.connect(SETTINGS.sqlite_path)
    c.execute("PRAGMA journal_mode=WAL;")
    return c

def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA_SQL)
        # Lightweight migrations for older DBs
        _ensure_column(c, "cases", "board_id", "ALTER TABLE cases ADD COLUMN board_id TEXT")

def get_case_dir(case_id: str) -> str:
    return os.path.join(SETTINGS.data_dir, "cases", case_id)

def create_case(case_id: str, title: str, device_family: str = "MacBook", model: str = "", board_id: str = "", symptom: str = "") -> None:
    import datetime
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cases(case_id,title,device_family,model,board_id,symptom,created_at) VALUES(?,?,?,?,?,?,?)",
            (case_id, title, device_family, model, board_id, symptom, datetime.datetime.utcnow().isoformat()),
        )
    os.makedirs(os.path.join(get_case_dir(case_id), "attachments"), exist_ok=True)

def list_cases() -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT case_id,title,device_family,model,board_id,symptom,created_at FROM cases ORDER BY created_at DESC").fetchall()
    return [{"case_id": r[0], "title": r[1], "device_family": r[2], "model": r[3], "board_id": r[4], "symptom": r[5], "created_at": r[6]} for r in rows]

def get_case(case_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        r = c.execute("SELECT case_id,title,device_family,model,board_id,symptom,created_at FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not r:
        return None
    return {"case_id": r[0], "title": r[1], "device_family": r[2], "model": r[3], "board_id": r[4], "symptom": r[5], "created_at": r[6]}


def delete_case(case_id: str) -> bool:
    init_db()
    with _conn() as c:
        exists = c.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not exists:
            return False
        c.execute("DELETE FROM attachments WHERE case_id=?", (case_id,))
        c.execute("DELETE FROM notes WHERE case_id=?", (case_id,))
        c.execute("DELETE FROM measurements WHERE case_id=?", (case_id,))
        c.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
    case_dir = get_case_dir(case_id)
    if os.path.isdir(case_dir):
        shutil.rmtree(case_dir, ignore_errors=True)
    return True


# ---- Baseline Library ----

def get_baseline_dir(baseline_id: str) -> str:
    return os.path.join(SETTINGS.data_dir, "baselines", baseline_id)


def create_baseline(
    baseline_id: str,
    device_family: str = "MacBook",
    model: str = "",
    board_id: str = "",
    quality: str = "SILVER",
    source: str = "known-good donor",
    boot_state: str = "activation/recovery",
    notes: str = "",
) -> None:
    import datetime
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO baselines(baseline_id,device_family,model,board_id,quality,source,boot_state,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (baseline_id, device_family, model, board_id, quality, source, boot_state, notes, datetime.datetime.utcnow().isoformat()),
        )
    os.makedirs(os.path.join(get_baseline_dir(baseline_id), "attachments"), exist_ok=True)


def list_baselines() -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT baseline_id,device_family,model,board_id,quality,source,boot_state,notes,created_at FROM baselines ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "baseline_id": r[0],
            "device_family": r[1],
            "model": r[2],
            "board_id": r[3],
            "quality": r[4],
            "source": r[5],
            "boot_state": r[6],
            "notes": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]


def get_baseline(baseline_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        r = c.execute(
            "SELECT baseline_id,device_family,model,board_id,quality,source,boot_state,notes,created_at FROM baselines WHERE baseline_id=?",
            (baseline_id,),
        ).fetchone()
    if not r:
        return None
    return {
        "baseline_id": r[0],
        "device_family": r[1],
        "model": r[2],
        "board_id": r[3],
        "quality": r[4],
        "source": r[5],
        "boot_state": r[6],
        "notes": r[7],
        "created_at": r[8],
    }


def add_baseline_measurement(baseline_id: str, name: str, value: str, unit: str = "", note: str = "") -> None:
    import datetime
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT INTO baseline_measurements(baseline_id,name,value,unit,note,created_at) VALUES(?,?,?,?,?,?)",
            (baseline_id, name, value, unit, note, datetime.datetime.utcnow().isoformat()),
        )


def list_baseline_measurements(baseline_id: str) -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT name,value,unit,note,created_at FROM baseline_measurements WHERE baseline_id=? ORDER BY created_at ASC",
            (baseline_id,),
        ).fetchall()
    return [{"name": r[0], "value": r[1], "unit": r[2], "note": r[3], "created_at": r[4]} for r in rows]


def save_baseline_attachment(baseline_id: str, filename: str, content: bytes, a_type: str) -> str:
    import datetime
    init_db()
    safe_name = filename.replace("/", "_")
    rel_path = os.path.join("baselines", baseline_id, "attachments", safe_name)
    abs_path = os.path.join(SETTINGS.data_dir, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(content)
    with _conn() as c:
        c.execute(
            "INSERT INTO baseline_attachments(baseline_id,filename,rel_path,type,created_at) VALUES(?,?,?,?,?)",
            (baseline_id, safe_name, rel_path, a_type, datetime.datetime.utcnow().isoformat()),
        )
    return abs_path


def list_baseline_attachments(baseline_id: str) -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT filename,rel_path,type,created_at FROM baseline_attachments WHERE baseline_id=? ORDER BY created_at ASC",
            (baseline_id,),
        ).fetchall()
    return [{"filename": r[0], "rel_path": r[1], "type": r[2], "created_at": r[3]} for r in rows]

def add_measurement(case_id: str, name: str, value: str, unit: str = "", note: str = "") -> None:
    import datetime
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT INTO measurements(case_id,name,value,unit,note,created_at) VALUES(?,?,?,?,?,?)",
            (case_id, name, value, unit, note, datetime.datetime.utcnow().isoformat()),
        )

def list_measurements(case_id: str) -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT name,value,unit,note,created_at FROM measurements WHERE case_id=? ORDER BY created_at ASC", (case_id,)).fetchall()
    return [{"name": r[0], "value": r[1], "unit": r[2], "note": r[3], "created_at": r[4]} for r in rows]

def add_note(case_id: str, note: str) -> None:
    import datetime
    init_db()
    with _conn() as c:
        c.execute("INSERT INTO notes(case_id,note,created_at) VALUES(?,?,?)", (case_id, note, datetime.datetime.utcnow().isoformat()))

def list_notes(case_id: str) -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT note,created_at FROM notes WHERE case_id=? ORDER BY created_at ASC", (case_id,)).fetchall()
    return [{"note": r[0], "created_at": r[1]} for r in rows]

def save_attachment(case_id: str, filename: str, content: bytes, a_type: str) -> str:
    import datetime
    init_db()
    safe_name = filename.replace("/", "_")
    rel_path = os.path.join("cases", case_id, "attachments", safe_name)
    abs_path = os.path.join(SETTINGS.data_dir, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(content)
    with _conn() as c:
        c.execute(
            "INSERT INTO attachments(case_id,filename,rel_path,type,created_at) VALUES(?,?,?,?,?)",
            (case_id, safe_name, rel_path, a_type, datetime.datetime.utcnow().isoformat()),
        )
    return abs_path

def list_attachments(case_id: str) -> List[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT filename,rel_path,type,created_at FROM attachments WHERE case_id=? ORDER BY created_at ASC", (case_id,)).fetchall()
    return [{"filename": r[0], "rel_path": r[1], "type": r[2], "created_at": r[3]} for r in rows]
