from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from boardbrain_v2.assistant import Assistant
from boardbrain_v2.ingest import import_source
from boardbrain_v2.reference import import_reference_bundle
from boardbrain_v2.store import SCHEMA, Store


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self):
        data = b"ref,pin,net\nU1,1,USB_D+\nU1,2,USB_D-\n"
        (self.root / "synthetic.csv").write_bytes(data)
        manifest = {"format": "boardbrain-reference-1", "title": "Test reference", "device": "Synthetic", "board_id": "TEST-001", "revision": "A",
                    "files": [{"name": "synthetic.csv", "role": "boardview", "sha256": hashlib.sha256(data).hexdigest()}]}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return path

    def test_setup_import_is_idempotent_and_reference_has_no_outcome(self):
        path = self.bundle()
        first = import_reference_bundle(self.store, path)
        again = import_reference_bundle(self.store, path)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["sources"], again["sources"])
        self.assertEqual(len(self.store.list_cases()), 1)
        self.assertEqual(first["kind"], "reference")
        self.assertEqual(first["events"], [])
        self.assertEqual(first["sources"][0]["status"], "parsed")
        with self.assertRaisesRegex(ValueError, "no repair outcome"):
            self.store.close_case(first["id"], {"status": "confirmed"})
        with self.assertRaisesRegex(ValueError, "Start a repair"):
            self.store.capture(first["id"], "measurement", {})
        self.store.capture(first["id"], "note", {"text": "A document observation, not a repair."})

    def test_bundle_failure_creates_no_workspace_or_files(self):
        path = self.bundle()
        (self.root / "synthetic.csv").write_bytes(b"truncated")
        with self.assertRaisesRegex(ValueError, "changed or incomplete"):
            import_reference_bundle(self.store, path)
        self.assertEqual(self.store.list_cases(), [])
        self.assertEqual(list(self.store.blobs.iterdir()), [])

    def test_bundle_rejects_path_escape(self):
        path = self.bundle()
        manifest = json.loads(path.read_text())
        manifest["files"][0]["name"] = "../synthetic.csv"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Invalid reference filename"):
            import_reference_bundle(self.store, path)

    def test_repair_reuses_files_but_not_checks_notes_or_history(self):
        ref = import_reference_bundle(self.store, self.bundle())
        self.store.confirm_source(ref["id"], ref["sources"][0]["id"], True)
        self.store.capture(ref["id"], "note", {"text": "Reference-only note"})
        repair = self.store.create_case({"title": "Actual repair", "symptom": "No power", "board_id": "TEST-001", "revision": "A", "reference_id": ref["id"]})
        self.assertEqual(repair["kind"], "repair")
        self.assertEqual(repair["events"], [])
        self.assertFalse(repair["sources"][0]["scope_confirmed"])
        self.assertNotEqual(repair["sources"][0]["id"], ref["sources"][0]["id"])
        found = self.store.lookup(repair["id"], "USB_D+")
        original = self.store.lookup(ref["id"], "USB_D+")
        self.assertEqual(found[0]["data"], original[0]["data"])
        self.assertNotEqual(found[0]["id"], original[0]["id"])
        self.assertFalse(found[0]["scope_confirmed"])
        self.assertTrue(original[0]["scope_confirmed"])
        self.assertEqual(len(list(self.store.blobs.iterdir())), 1)

    def test_schema_one_upgrade_retains_existing_repair(self):
        folder = self.root / "old"
        folder.mkdir()
        db = sqlite3.connect(folder / "boardbrain-v2.sqlite3")
        db.executescript(SCHEMA)
        db.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?)", ("COLD", "Existing", "Mac", "TEST", "", "No power", "open", "before", "before"))
        db.execute("PRAGMA user_version=1")
        db.commit(); db.close()
        upgraded = Store(folder)
        self.assertEqual(upgraded.case("COLD")["kind"], "repair")
        self.assertEqual(Store(folder).case("COLD")["symptom"], "No power")
        with upgraded.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_reference_answer_is_not_saved_as_a_repair_plan(self):
        ref = import_reference_bundle(self.store, self.bundle())
        recorded = []
        answer = {"summary": "This is reference material.", "tests": [], "hypotheses": [], "questions": [], "web_sources": []}
        class Provider:
            def respond(_, payload):
                recorded.append(payload)
                return {"id": "fake", "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 10},
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}
        assistant = Assistant(self.store, type("Keys", (), {"get": lambda _: "fake"})(), lambda _: Provider())
        assistant.run(ref["id"], "What is in these reference files?", [], False, threading.Event(), lambda *_: None)
        self.assertIn('"kind": "reference"', recorded[0]["input"][0]["content"][0]["text"])
        answer["hypotheses"] = [{"idea": "An invented fault", "support": [], "contradictions": []}]
        with self.assertRaisesRegex(ValueError, "active repair"):
            assistant.run(ref["id"], "Explain the files", [], False, threading.Event(), lambda *_: None)
        self.assertEqual(len([e for e in self.store.case(ref["id"])["events"] if e["kind"] == "plan"]), 1)


if __name__ == "__main__":
    unittest.main()
