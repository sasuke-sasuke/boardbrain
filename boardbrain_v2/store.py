from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import uuid
import zipfile

from .measurements import validate_measurement


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def uid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:20]


def require_text(value, label: str, limit=4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} is required (maximum {limit} characters).")
    return value.strip()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, device TEXT NOT NULL,
 board_id TEXT NOT NULL, revision TEXT NOT NULL, symptom TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), kind TEXT NOT NULL,
 data TEXT NOT NULL, created TEXT NOT NULL, retracted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS events_case ON events(case_id,created);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), name TEXT NOT NULL,
 sha TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL, info TEXT NOT NULL,
 scope_confirmed INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL,
 UNIQUE(case_id,sha,role));
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
 source_id TEXT NOT NULL REFERENCES sources(id), kind TEXT NOT NULL, data TEXT NOT NULL,
 net TEXT, ref TEXT, pin TEXT, body TEXT);
CREATE INDEX IF NOT EXISTS evidence_exact ON evidence(case_id,net,ref,pin);
CREATE TABLE IF NOT EXISTS usage (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), month TEXT NOT NULL,
 reserved REAL NOT NULL, cost REAL NOT NULL DEFAULT 0, status TEXT NOT NULL,
 details TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DEFAULT_SETTINGS = {"model": "gpt-6.1-sol", "monthly_limit": 75.0, "warning_at": 50.0,
                    "flexbv_version": "5.1244", "equipment": "Multimeter, bench power supply, USB-C meter, thermal camera. Oscilloscope and logic analyzer available; beginner guidance required. Exact models not yet recorded."}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.blobs = self.root / "files"
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "boardbrain-v2.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("This data was created by a newer BoardBrain. Install that version before opening it.")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self) -> dict:
        with self.connect() as db:
            return DEFAULT_SETTINGS | {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM settings")}

    def set_settings(self, data: dict) -> dict:
        if set(data) - set(DEFAULT_SETTINGS):
            raise ValueError("Unknown setting")
        proposed = self.settings() | data
        if proposed["model"] not in ("gpt-6.1-sol", "gpt-6-astra", "gpt-5.4"):
            raise ValueError("Select a supported model with known pricing.")
        if not 1 <= float(proposed["monthly_limit"]) <= 1000:
            raise ValueError("Monthly limit must be $1–$1000.")
        if not 0 <= float(proposed["warning_at"]) <= float(proposed["monthly_limit"]):
            raise ValueError("Warning threshold must be within the monthly limit.")
        for key in ("equipment", "flexbv_version"):
            proposed[key] = require_text(proposed[key], key, 4000)
        with self.connect() as db:
            for key, value in proposed.items():
                db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, dumps(value)))
        return proposed

    def create_case(self, data: dict) -> dict:
        values = {k: str(data.get(k, "")).strip() for k in ("title", "device", "board_id", "revision", "symptom")}
        for field, cap in (("title", 120), ("symptom", 4000)):
            values[field] = require_text(values[field], field, cap)
        if any(len(values[k]) > 200 for k in ("device", "board_id", "revision")):
            raise ValueError("Device/board identifiers are too long.")
        case_id, timestamp = uid("C"), now()
        with self.connect() as db:
            db.execute("INSERT INTO cases(id,title,device,board_id,revision,symptom,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                       (case_id, *values.values(), timestamp, timestamp))
        return self.case(case_id)

    def list_cases(self, query="") -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM cases ORDER BY updated DESC").fetchall()
        return [dict(r) for r in rows if not query or query.casefold() in " ".join(str(v) for v in r).casefold()]

    def update_case(self, case_id: str, data: dict):
        current = self.case(case_id)
        if current["status"] != "open":
            raise ValueError("Reopen this case to edit its details.")
        values = {k: str(data.get(k, current[k])).strip() for k in ("title", "device", "board_id", "revision", "symptom")}
        require_text(values["title"], "Title", 120)
        require_text(values["symptom"], "Symptom", 4000)
        if any(len(values[k]) > 200 for k in ("device", "board_id", "revision")):
            raise ValueError("Board identifiers are too long.")
        identity_changed = any(values[k] != current[k] for k in ("board_id", "revision"))
        timestamp = now()
        with self.connect() as db:
            db.execute("UPDATE cases SET title=?,device=?,board_id=?,revision=?,symptom=?,updated=? WHERE id=?", (*values.values(), timestamp, case_id))
            if identity_changed:
                db.execute("UPDATE sources SET scope_confirmed=0 WHERE case_id=?", (case_id,))
                # A different board identity must not inherit exact mapping authority.
                db.execute("UPDATE events SET retracted=1 WHERE case_id=? AND kind='pin'", (case_id,))
            db.execute("INSERT INTO events(id,case_id,kind,data,created) VALUES(?,?,?,?,?)", (uid("E"), case_id, "case_update", dumps({"previous": {k: current[k] for k in values}, "current": values, "mapping_checks_reset": identity_changed}), timestamp))
        return self.case(case_id)

    def confirm_source(self, case_id: str, source_id: str, confirmed: bool):
        case = self.case(case_id)
        self.source(case_id, source_id)
        if case["status"] != "open":
            raise ValueError("Reopen the case to change file scope.")
        if confirmed and (not case["board_id"] or not case["revision"]):
            raise ValueError("Record the board number and revision in Edit details first.")
        with self.connect() as db:
            db.execute("UPDATE sources SET scope_confirmed=? WHERE case_id=? AND id=?", (int(confirmed), case_id, source_id))
            db.execute("UPDATE cases SET updated=? WHERE id=?", (now(), case_id))
        return {"ok": True}

    def case(self, case_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                raise ValueError("Case not found")
            events = [dict(r) for r in db.execute("SELECT * FROM events WHERE case_id=? ORDER BY created,id", (case_id,))]
            sources = [dict(r) for r in db.execute("SELECT * FROM sources WHERE case_id=? ORDER BY created", (case_id,))]
        for event in events:
            event["data"] = json.loads(event["data"])
        for source in sources:
            source["info"] = json.loads(source["info"])
        return dict(row) | {"events": events, "sources": sources}

    def event(self, case_id: str, kind: str, data: dict) -> dict:
        self.case(case_id)
        event_id, timestamp = uid("E"), now()
        with self.connect() as db:
            db.execute("INSERT INTO events(id,case_id,kind,data,created) VALUES(?,?,?,?,?)", (event_id, case_id, kind, dumps(data), timestamp))
            db.execute("UPDATE cases SET updated=? WHERE id=?", (timestamp, case_id))
        return dict(id=event_id, case_id=case_id, kind=kind, data=data, created=timestamp, retracted=0)

    def capture(self, case_id: str, kind: str, data: dict) -> dict:
        if self.case(case_id)["status"] != "open":
            raise ValueError("Reopen this case before changing its evidence.")
        if kind == "measurement":
            data = validate_measurement(data)
        elif kind == "note":
            data = {"text": require_text(data.get("text"), "Note")}
        elif kind == "pin":
            if data.get("confirmed") is not True:
                raise ValueError("Confirm the component, pin and net against FlexBV or a continuity measurement.")
            data = {k: require_text(data.get(k), k, 200 if k != "basis" else 2000) for k in ("ref", "pin", "net", "basis")}
            data["trust"] = "technician_checked"
        else:
            raise ValueError("Unknown observation type")
        return self.event(case_id, kind, data)

    def retract(self, case_id: str, event_id: str):
        if self.case(case_id)["status"] != "open":
            raise ValueError("Reopen the case before correcting readings.")
        with self.connect() as db:
            row = db.execute("SELECT kind FROM events WHERE id=? AND case_id=?", (event_id, case_id)).fetchone()
            if not row or row[0] not in ("measurement", "note", "pin"):
                raise ValueError("Only observations can be retracted.")
            db.execute("UPDATE events SET retracted=1 WHERE id=?", (event_id,))
            db.execute("UPDATE cases SET updated=? WHERE id=?", (now(), case_id))

    def close_case(self, case_id: str, data: dict) -> dict:
        case = self.case(case_id)
        status = data.get("status")
        if status not in ("confirmed", "unresolved", "open"):
            raise ValueError("Choose confirmed, unresolved or reopen.")
        outcome = {"status": status}
        if status == "confirmed":
            if data.get("technician_confirmed") is not True:
                raise ValueError("Only you can confirm a repair outcome.")
            for key in ("fix", "verification", "cause_status"):
                outcome[key] = require_text(data.get(key), key)
            if outcome["cause_status"] not in ("verified", "suspected", "unknown"):
                raise ValueError("Root cause status must be verified, suspected or unknown.")
            outcome["root_cause"] = str(data.get("root_cause", "")).strip()[:4000]
            if outcome["cause_status"] == "verified" and not outcome["root_cause"]:
                raise ValueError("Describe the evidence establishing the root cause.")
            if not any(e["kind"] in ("measurement", "note") and not e["retracted"] for e in case["events"]):
                raise ValueError("Record at least one observation before confirming this repair.")
        elif status == "unresolved":
            outcome["reason"] = require_text(data.get("reason"), "Reason")
        # Atomic event + status: no half-closed or falsely confirmed case on a crash.
        with self.connect() as db:
            db.execute("INSERT INTO events(id,case_id,kind,data,created) VALUES(?,?,?,?,?)", (uid("E"), case_id, "outcome", dumps(outcome), now()))
            db.execute("UPDATE cases SET status=?,updated=? WHERE id=?", (status, now(), case_id))
        return self.case(case_id)

    def add_source(self, case_id: str, filename: str, content: bytes, role: str, scope_confirmed=False) -> dict:
        case = self.case(case_id)
        if case["status"] != "open":
            raise ValueError("Reopen the case before adding evidence.")
        if role not in ("boardview", "schematic", "photo", "reference"):
            raise ValueError("Choose a file type.")
        if not content or len(content) > 25 * 1024 * 1024:
            raise ValueError("Upload a non-empty file up to 25 MB.")
        filename = require_text(Path(filename).name, "Filename", 250)
        sha = hashlib.sha256(content).hexdigest()
        with self.connect() as db:
            old = db.execute("SELECT id FROM sources WHERE case_id=? AND sha=? AND role=?", (case_id, sha, role)).fetchone()
            if old:
                return self.source(case_id, old[0])
        path = self.blobs / sha
        if not path.exists():
            temp = self.blobs / (sha + "." + uuid.uuid4().hex + ".tmp")
            temp.write_bytes(content)
            temp.chmod(0o600)
            os.replace(temp, path)
        source_id = uid("S")
        with self.connect() as db:
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)", (source_id, case_id, filename, sha, role, "queued", "{}", int(bool(scope_confirmed and case["board_id"] and case["revision"])), now()))
        return self.source(case_id, source_id)

    def source(self, case_id: str, source_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=? AND case_id=?", (source_id, case_id)).fetchone()
        if not row:
            raise ValueError("File not found in this case")
        source = dict(row)
        source["info"] = json.loads(source["info"])
        return source

    def source_result(self, case_id: str, source_id: str, status: str, info: dict, evidence: list[dict]):
        self.source(case_id, source_id)
        with self.connect() as db:
            db.execute("DELETE FROM evidence WHERE source_id=?", (source_id,))
            for item in evidence:
                stable_id = "D" + hashlib.sha256((source_id + dumps(item)).encode()).hexdigest()[:20]
                db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?)", (stable_id, case_id, source_id, item["kind"], dumps(item), item.get("net"), item.get("ref"), item.get("pin"), item.get("text", "")))
            db.execute("UPDATE sources SET status=?,info=? WHERE id=?", (status, dumps(info), source_id))
            db.execute("UPDATE cases SET updated=? WHERE id=?", (now(), case_id))

    def lookup(self, case_id: str, query: str, limit=25) -> list[dict]:
        query = require_text(query, "Search", 200)
        limit = max(1, min(int(limit), 100))
        case = self.case(case_id)
        select = "SELECT e.*,s.name,s.scope_confirmed,s.status FROM evidence e JOIN sources s ON e.source_id=s.id WHERE e.case_id=?"
        pin_clause = " AND e.kind='pin' AND (e.net=? OR e.ref=? OR e.ref||'.'||e.pin=? OR e.ref||'.'||json_extract(e.data,'$.name')=?)"
        params = (case_id, query, query, query, query)
        with self.connect() as db:
            pin_total = db.execute("SELECT COUNT(*) FROM evidence e WHERE e.case_id=?" + pin_clause, params).fetchone()[0]
            pin_rows = db.execute(select + pin_clause + " ORDER BY e.ref,e.pin,e.id LIMIT 100", params).fetchall()
            # A schematic normally labels the component separately from the pad.
            # Return its page as a reference, never claim text proximity proves wiring.
            doc_query = query
            refs = {r["ref"] for r in pin_rows}
            if len(refs) == 1 and query.startswith(next(iter(refs)) + "."):
                doc_query = next(iter(refs))
            escaped = doc_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            direct = re.fullmatch(r"(?i)(?:schematic\s+)?(sheet|(?:pdf\s+)?page)\s+(\d+)", query)
            if direct:
                key = "sheet" if direct[1].lower() == "sheet" else "page"
                doc_rows = db.execute(select + f" AND e.kind='document' AND CAST(json_extract(e.data,'$.{key}') AS TEXT)=?", (case_id, str(int(direct[2])))).fetchall()
            else:
                doc_rows = db.execute(select + " AND e.kind='document' AND (e.body LIKE ? ESCAPE '\\' OR json_extract(e.data,'$.page_title') LIKE ? ESCAPE '\\')",
                                      (case_id, "%" + escaped + "%", "%" + escaped + "%")).fetchall()
        # Electrical punctuation belongs to the identifier, not to a word boundary.
        pattern = re.compile(r"(?<![\w+#./-])" + re.escape(doc_query) + r"(?![\w+#./-])", re.IGNORECASE)
        def unpack(row):
            item = dict(row)
            item["data"] = json.loads(item.pop("data"))
            item.pop("body", None)
            item["data"].pop("blocks", None)  # Page coordinates stay in the stored document; keep lookup bounded.
            return item
        pins = [unpack(r) for r in pin_rows]
        docs = []
        for row in doc_rows:
            item = unpack(row)
            full = item["data"].get("text", "")
            match = pattern.search(full)
            if not direct and not match and not pattern.search(item["data"].get("page_title", "")):
                continue
            if "text" in item["data"]:
                start = max(0, (match.start() if match else 0) - 250)
                item["data"]["text"] = full[start:start + 1800]
                item["data"]["excerpted"] = start > 0 or len(full) > 1800
            item["matched_reference"] = doc_query
            docs.append(item)
        docs.sort(key=lambda e: (e["data"].get("page", 0), e["id"]))
        observations, problems = [], []
        for e in case["events"]:
            if not e["retracted"] and e["kind"] == "pin" and query in (e["data"]["net"], e["data"]["ref"], e["data"]["ref"] + "." + e["data"]["pin"]):
                observations.append(e | {"scope_confirmed": 1, "name": "Technician checked mapping"})
            elif not e["retracted"] and e["kind"] in ("measurement", "note") and query.casefold() in dumps(e["data"]).casefold():
                observations.append(e)
        for source in case["sources"]:
            for problem in source["info"].get("issues", []):
                if query == problem["ref"] or query.startswith(problem["ref"] + "."):
                    problems.append({"id": source["id"] + ":excluded:" + problem["ref"], "kind": "import_issue", "source_id": source["id"],
                                     "name": "Excluded component " + problem["ref"], "data": problem | {"text": "No imported mapping accepted for this component. " + problem["reason"]}})
        # Reserve space for schematic pages, even when a rail has hundreds of pins.
        # Fill unused space from the remaining results; disclose any truncation.
        doc_slots = min(len(docs), max(1, limit // 3))
        obs_slots = min(len(observations), max(1, limit // 4))
        chosen = problems + docs[:doc_slots] + observations[:obs_slots]
        chosen += pins[:max(0, limit - len(chosen))]
        chosen += docs[doc_slots:] + observations[obs_slots:]
        found = chosen[:limit]
        alias_keys = {(p["data"]["ref"], p["data"]["pin"]) for p in pins
                      if query in (p["data"]["ref"] + "." + p["data"]["pin"], p["data"]["ref"] + "." + p["data"].get("name", ""))}
        coverage = {"imported_pin_matches": pin_total, "document_matches": len(docs),
                    "imported_pins_returned": sum(e in pins for e in found), "documents_returned": sum(e in docs for e in found)}
        coverage["truncated"] = pin_total > coverage["imported_pins_returned"] or len(docs) > coverage["documents_returned"] or len(observations) > sum(e in observations for e in found)
        # Conflicts remain visible to humans and make all competing mappings ineligible.
        for item in found:
            item["lookup_coverage"] = coverage
            if item["kind"] == "pin":
                d = item["data"]
                item["alias_ambiguous"] = len(alias_keys) > 1
                item["conflict"] = item["alias_ambiguous"] or len(self.pin_nets(case_id, d["ref"], d["pin"])) > 1
        return found

    def pin_nets(self, case_id: str, ref: str, pin: str) -> set[str]:
        with self.connect() as db:
            nets = {r[0] for r in db.execute("SELECT net FROM evidence WHERE case_id=? AND ref=? AND pin=?", (case_id, ref, pin))}
        for e in self.case(case_id)["events"]:
            if e["kind"] == "pin" and not e["retracted"] and e["data"]["ref"] == ref and e["data"]["pin"] == pin:
                nets.add(e["data"]["net"])
        return nets

    def confirmed_cases(self, case_id: str, query: str) -> list[dict]:
        current = self.case(case_id)
        results = []
        words = set(query.casefold().split())
        for candidate in self.list_cases():
            if candidate["id"] == case_id or candidate["status"] != "confirmed":
                continue
            case = self.case(candidate["id"])
            outcome = next(e for e in reversed(case["events"]) if e["kind"] == "outcome")["data"]
            exact = bool(current["board_id"] and current["revision"] and current["board_id"] == case["board_id"] and current["revision"] == case["revision"])
            score = len(words & set((case["symptom"] + " " + dumps(outcome)).casefold().split())) + (10 if exact else 0)
            if score:
                results.append({"id": case["id"], "title": case["title"], "board_id": case["board_id"], "revision": case["revision"], "symptom": case["symptom"], "outcome": outcome, "same_board_revision": exact, "score": score,
                                "use": "Comparison only. Does not establish the present board's fault or pin mapping."})
        return sorted(results, key=lambda x: -x["score"])[:5]

    def usage_summary(self) -> dict:
        month = now()[:7]
        with self.connect() as db:
            cost, held = db.execute("SELECT COALESCE(SUM(cost),0),COALESCE(SUM(reserved),0) FROM usage WHERE month=?", (month,)).fetchone()
        return {"month": month, "estimated_cost": cost, "reserved": held,
                "limit": float(self.settings()["monthly_limit"]), "warning_at": float(self.settings()["warning_at"])}

    def reserve(self, case_id: str, amount: float) -> str:
        request_id = uid("R")
        limit = float(self.settings()["monthly_limit"])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COALESCE(SUM(cost+reserved),0) FROM usage WHERE month=?", (now()[:7],)).fetchone()[0]
            if used + amount > limit:
                raise ValueError("Monthly app spending limit reached. Local cases and searches still work. Review usage or adjust the limit in Settings.")
            db.execute("INSERT INTO usage VALUES(?,?,?,?,?,?,?,?)", (request_id, case_id, now()[:7], amount, 0, "pending", "{}", now()))
        return request_id

    def settle(self, request_id: str, cost: float | None, details: dict):
        with self.connect() as db:
            row = db.execute("SELECT reserved,status FROM usage WHERE id=?", (request_id,)).fetchone()
            if row and row[1] == "pending":
                # Unknown network outcome may still be billed: retain a conservative charge.
                db.execute("UPDATE usage SET cost=?,reserved=0,status=?,details=? WHERE id=?", (row[0] if cost is None else cost, "uncertain" if cost is None else "complete", dumps(details), request_id))

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute("UPDATE usage SET cost=reserved,reserved=0,status='uncertain' WHERE status='pending'")
            db.execute("UPDATE sources SET status='interrupted',info=? WHERE status IN ('queued','processing')", (dumps({"message": "Import interrupted. Retry this file."}),))

    def backup(self) -> Path:
        folder = self.root / "backups"
        folder.mkdir(exist_ok=True, mode=0o700)
        name = "boardbrain-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        snapshot = folder / (name + ".sqlite3")
        with self.connect() as db, sqlite3.connect(snapshot) as dest:
            db.backup(dest)
        target = folder / (name + ".zip")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, "boardbrain-v2.sqlite3")
            for path in self.blobs.iterdir():
                if path.is_file() and len(path.name) == 64:
                    z.write(path, "files/" + path.name)
        snapshot.unlink()
        target.chmod(0o600)
        return target
