"""Synthetic grounding trials; no provider credentials or private board data."""
from __future__ import annotations

import base64
import copy
import io
import itertools
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from boardbrain_v2.assistant import Assistant, validate_plan
from boardbrain_v2.ingest import import_source
from boardbrain_v2.server import App
from boardbrain_v2.store import Store


BVR = """BVRAW_FORMAT_3
PART_NAME U100
PART_SIDE T
PART_ORIGIN 10 20
PIN_NUMBER 1
PIN_NAME A1
PIN_ORIGIN 2 3
PIN_NET TEST_NET+
PIN_END
PART_END
"""
CALL_IDS = itertools.count(1)


def claim(kind="unknown", **changes):
    return dict(kind=kind, text="The supplied evidence does not establish an expected voltage.",
                quote="", evidence_ids=[], ref="", pin="", net="") | changes


def reference_plan(*claims):
    return dict(summary="A deliberately untrusted model summary.", claims=list(claims),
                tests=[], hypotheses=[], questions=[], web_sources=[])


def response(output):
    return {"id": "synthetic_response", "status": "completed", "output": output,
            "usage": {"input_tokens": 500, "output_tokens": 100}}


def final(plan):
    return response([{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": json.dumps(plan), "annotations": []}]}])


def call(name, **args):
    return response([{"type": "function_call", "name": name,
                      "call_id": f"synthetic_call_{next(CALL_IDS)}",
                      "arguments": json.dumps(args)}])


def last_tool_result(payload):
    outputs = [item for item in payload["input"] if item.get("type") == "function_call_output"]
    return outputs[-1]["output"]


def find_evidence(value, kind):
    """Locate typed evidence without depending on tool-result wrapper wording."""
    if isinstance(value, str):
        try:
            return find_evidence(json.loads(value), kind)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        if value.get("kind") == kind and value.get("id"):
            return value
        for nested in value.values():
            found = find_evidence(nested, kind)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = find_evidence(nested, kind)
            if found:
                return found
    return None


class ClaimGateTests(unittest.TestCase):
    def setUp(self):
        self.pin = {"id": "P1", "kind": "pin", "scope_confirmed": False, "conflict": False,
                    "data": {"ref": "U100", "pin": "1", "name": "A1", "net": "TEST_NET+"}}
        self.doc = {"id": "D1", "kind": "document", "scope_confirmed": False,
                    "data": {"method": "native_text", "text": "U100 nominal input is 3.3 V when enabled.",
                             "page": 1, "sheet": "17"}}
        self.evidence = {"P1": self.pin, "D1": self.doc}

    def pin_claim(self, **changes):
        return claim("pin_connection", ref="U100", pin="1", net="TEST_NET+", evidence_ids=["P1"],
                     text="This text must be replaced by the checked source mapping.") | changes

    def test_unconfirmed_source_can_describe_file_mapping_without_claiming_physical_scope(self):
        result = validate_plan(reference_plan(self.pin_claim()), self.evidence, set(), require_claims=True)
        self.assertIn("U100", result["claims"][0]["text"])
        self.assertIn("A1", result["claims"][0]["text"])
        self.assertIn("TEST_NET+", result["claims"][0]["text"])
        self.assertNotIn("must be replaced", result["claims"][0]["text"])

    def test_wrong_net_label_as_number_and_foreign_citation_fail(self):
        for change in ({"net": "TEST_NET-"}, {"pin": "A1"}, {"ref": "U101"},
                       {"evidence_ids": ["OTHER_CASE_PIN"]}, {"evidence_ids": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_plan(reference_plan(self.pin_claim(**change)), self.evidence, set(), require_claims=True)
        ambiguous = copy.deepcopy(self.evidence)
        ambiguous["P1"]["conflict"] = True
        with self.assertRaises(ValueError):
            validate_plan(reference_plan(self.pin_claim()), ambiguous, set(), require_claims=True)

    def test_document_fact_requires_short_exact_quote_from_cited_document(self):
        good = claim("document_fact", quote="nominal input is 3.3 V when enabled.", evidence_ids=["D1"])
        validate_plan(reference_plan(good), self.evidence, set(), require_claims=True)
        for changes in ({"quote": "nominal input is 5 V"}, {"quote": ""},
                        {"quote": "x" * 401}, {"evidence_ids": ["P1"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_plan(reference_plan(good | changes), self.evidence, set(), require_claims=True)

    def test_missing_claims_unknown_ids_and_unfounded_inference_fail(self):
        for plan in (reference_plan(), reference_plan(claim(evidence_ids=["INVENTED"])),
                     reference_plan(claim("inference"))):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                validate_plan(plan, self.evidence, set(), require_claims=True)
        validate_plan(reference_plan(claim()), {}, set(), require_claims=True)

    def test_general_claim_cannot_smuggle_new_board_reference(self):
        for kind in ("general", "unknown"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_plan(reference_plan(claim(kind, text="U999 is the input regulator.")),
                              self.evidence, set(), require_claims=True)

    def test_text_only_page_is_not_visual_proof(self):
        visual = claim("visual_observation", text="The drawing shows an enable arrow.", evidence_ids=["D1"])
        with self.assertRaises(ValueError):
            validate_plan(reference_plan(visual), self.evidence, set(), require_claims=True)


class AssistantGroundingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.case = self.store.create_case({"kind": "reference", "title": "Synthetic reference workspace",
                                           "board_id": "SYNTHETIC-100"})["id"]
        self.other = self.store.create_case({"kind": "reference", "title": "Separate synthetic workspace"})["id"]
        self.recorded = []

    def tearDown(self):
        self.temp.cleanup()

    def add_boardview(self):
        source = self.store.add_source(self.case, "synthetic.bvr", BVR.encode(), "boardview")
        import_source(self.store, self.case, source["id"], threading.Event(), lambda _: None)
        return source

    def add_pdf(self, case_id=None):
        import fitz
        with fitz.open() as doc:
            first = doc.new_page(width=1200, height=800)
            first.insert_text((100, 100), "U100 synthetic schematic - input is 3.3 V when enabled.")
            first.draw_line((100, 120), (500, 120))
            doc.new_page(width=800, height=1200).insert_text((80, 80), "Second synthetic schematic page.")
            source = self.store.add_source(case_id or self.case, "synthetic.pdf", doc.tobytes(), "schematic")
        import_source(self.store, case_id or self.case, source["id"], threading.Event(), lambda _: None)
        return source

    def run_script(self, script):
        owner = self
        class Fake:
            def respond(self, payload):
                owner.recorded.append(copy.deepcopy(payload))
                return script(payload, len(owner.recorded))
        assistant = Assistant(self.store, type("Keys", (), {"get": lambda _: "fake-test-key"})(), lambda _: Fake())
        return assistant.run(self.case, "What do the supplied files establish?", [], False,
                             threading.Event(), lambda _: None)

    def saved(self, result):
        return next(e["data"] for e in self.store.case(self.case)["events"] if e["id"] == result["plan_id"])

    def test_reference_lookup_forced_and_saved_summary_uses_validated_claims(self):
        self.add_boardview()
        def script(payload, turn):
            if turn == 1:
                self.assertEqual(payload.get("tool_choice"), {"type": "function", "name": "lookup_evidence"})
                return call("lookup_evidence", query="U100.A1")
            pin = find_evidence(last_tool_result(payload), "pin")
            self.assertIsNotNone(pin)
            return final(reference_plan(claim("pin_connection", evidence_ids=[pin["id"]],
                                              ref="U100", pin="1", net="TEST_NET+")))
        saved = self.saved(self.run_script(script))
        self.assertIn("TEST_NET+", saved["plan"]["summary"])
        self.assertNotIn("untrusted", saved["plan"]["summary"])
        self.assertEqual(saved["plan"]["tests"], [])
        self.assertEqual(self.store.usage_summary()["reserved"], 0)

    def test_empty_lookup_can_report_unknown_but_missing_claims_cannot_be_saved(self):
        self.add_boardview()
        def script(payload, turn):
            if turn == 1:
                return call("lookup_evidence", query="DOES_NOT_EXIST")
            self.assertEqual(json.loads(last_tool_result(payload)), [])
            return final(reference_plan(claim()))
        result = self.run_script(script)
        self.assertIn("does not establish", self.saved(result)["plan"]["summary"])
        self.recorded.clear()
        def missing(payload, turn):
            return call("lookup_evidence", query="U100") if turn == 1 else final(reference_plan())
        with self.assertRaises(ValueError):
            self.run_script(missing)
        self.assertEqual(sum(e["kind"] == "plan" for e in self.store.case(self.case)["events"]), 1)

    def test_provider_cannot_ignore_forced_lookup_and_save_unsupported_reference_answer(self):
        self.add_boardview()
        with self.assertRaises(ValueError):
            self.run_script(lambda payload, turn: final(reference_plan(claim())))
        self.assertFalse(any(e["kind"] == "plan" for e in self.store.case(self.case)["events"]))

    def test_inspection_sends_real_pixels_and_saved_history_excludes_image_data(self):
        from PIL import Image
        pdf = self.add_pdf()
        observed = {}
        def script(payload, turn):
            if turn == 1:
                return call("lookup_evidence", query="U100")
            if turn == 2:
                return call("inspect_schematic", source_id=pdf["id"], page=1, region=[0, 0, .6, .5], focus="")
            output = last_tool_result(payload)
            self.assertIsInstance(output, list)
            image = next(item for item in output if item.get("type") == "input_image")
            self.assertEqual(image.get("detail"), "high")
            self.assertTrue(image["image_url"].startswith("data:image/png;base64,"))
            decoded = base64.b64decode(image["image_url"].split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(decoded)) as rendered:
                self.assertEqual(rendered.format, "PNG")
                self.assertGreater(rendered.width, 500)
                self.assertGreater(rendered.height, 100)
            visual = find_evidence(output, "document_image")
            self.assertIsNotNone(visual)
            observed["id"] = visual["id"]
            return final(reference_plan(claim("visual_observation", text="A horizontal wire is visible in the inspected region.",
                                              evidence_ids=[visual["id"]])))
        saved = self.saved(self.run_script(script))
        self.assertIn(observed["id"], saved["evidence"])
        self.assertNotIn("base64", json.dumps(self.store.case(self.case)))
        self.assertNotIn("image_url", json.dumps(saved))
        # Prior image evidence is auditable locally but not reusable as pixels in a later turn.
        self.recorded.clear()
        def stale(payload, turn):
            self.assertNotIn("base64", json.dumps(payload))
            if turn == 1:
                return call("lookup_evidence", query="U100")
            return final(reference_plan(claim("visual_observation", evidence_ids=[observed["id"]])))
        with self.assertRaises(ValueError):
            self.run_script(stale)

    def test_failed_and_foreign_inspections_return_errors_without_registering_evidence(self):
        local = self.add_pdf()
        foreign = self.add_pdf(self.other)
        def script(payload, turn):
            if turn == 1:
                return call("lookup_evidence", query="U100")
            if turn == 2:
                return call("inspect_schematic", source_id=foreign["id"], page=1, region=[], focus="")
            if turn == 3:
                self.assertIn("error", json.dumps(last_tool_result(payload)).lower())
                return call("inspect_schematic", source_id=local["id"], page=99, region=[], focus="")
            self.assertIn("error", json.dumps(last_tool_result(payload)).lower())
            return final(reference_plan(claim()))
        saved = self.saved(self.run_script(script))
        self.assertFalse(any(e.get("kind") == "document_image" for e in saved["evidence"].values()))
        self.assertNotIn(foreign["id"], json.dumps(saved["evidence"]))

    def test_render_failure_can_be_recovered_with_a_valid_inspection(self):
        from boardbrain_v2.schematic import inspect_page
        pdf = self.add_pdf()
        def script(payload, turn):
            if turn == 1:
                return call("lookup_evidence", query="U100")
            if turn == 2:
                return call("inspect_schematic", source_id=pdf["id"], page=1, region=[], focus="")
            if turn == 3:
                self.assertIn("error", json.dumps(last_tool_result(payload)).lower())
                return call("inspect_schematic", source_id=pdf["id"], page=1, region=[], focus="")
            evidence = find_evidence(last_tool_result(payload), "document_image")
            self.assertIsNotNone(evidence)
            return final(reference_plan(claim("visual_observation", text="A horizontal wire is visible.",
                                              evidence_ids=[evidence["id"]])))
        attempts = []
        def flaky(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise ValueError("Synthetic renderer failure; request another page or region.")
            return inspect_page(*args, **kwargs)
        with patch("boardbrain_v2.schematic.inspect_page", side_effect=flaky):
            saved = self.saved(self.run_script(script))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sum(e.get("kind") == "document_image" for e in saved["evidence"].values()), 1)

    def test_inspection_limit_stops_extra_rendering_and_allows_honest_answer(self):
        from boardbrain_v2.assistant import MAX_INSPECTIONS
        from boardbrain_v2.schematic import inspect_page
        pdf = self.add_pdf()
        self.assertEqual(MAX_INSPECTIONS, 4)
        def script(payload, turn):
            if turn == 1:
                return call("lookup_evidence", query="U100")
            if turn == 2:
                # Even a provider returning multiple calls must obey the per-turn image budget.
                return response([call("inspect_schematic", source_id=pdf["id"], page=1,
                                      region=[], focus="")["output"][0] for _ in range(MAX_INSPECTIONS)])
            if turn == 3:
                self.assertIsNotNone(find_evidence(last_tool_result(payload), "document_image"))
                return call("inspect_schematic", source_id=pdf["id"], page=2, region=[], focus="")
            self.assertIn("error", json.dumps(last_tool_result(payload)).lower())
            return final(reference_plan(claim()))
        with patch("boardbrain_v2.schematic.inspect_page", wraps=inspect_page) as render:
            self.run_script(script)
        self.assertEqual(render.call_count, MAX_INSPECTIONS)
        self.assertEqual(self.store.usage_summary()["reserved"], 0)

    def test_saved_inspection_route_checks_case_and_source_integrity(self):
        from boardbrain_v2.schematic import inspect_page
        pdf = self.add_pdf()
        visual, original_png = inspect_page(self.store, self.case, pdf["id"], 1, [.1, .1, .6, .5])
        self.store.event(self.case, "plan", {"plan": reference_plan(claim()), "evidence": {visual["id"]: visual}})
        app = App(self.store, type("Keys", (), {"get": lambda _: None})())
        try:
            query = {"case_id": [self.case], "id": [visual["id"]]}
            body, mime = app.binary("/api/inspection", query)
            self.assertEqual(mime, "image/png")
            self.assertEqual(body, original_png)
            with self.assertRaises((ValueError, LookupError)):
                app.binary("/api/inspection", query | {"case_id": [self.other]})
            with self.assertRaises((ValueError, LookupError)):
                app.binary("/api/inspection", query | {"id": ["INVENTED_VISUAL"]})
            # Replacing bytes at the same blob path must never silently show a different image.
            (self.store.blobs / pdf["sha"]).write_bytes(b"corrupted synthetic source")
            with self.assertRaises((ValueError, LookupError)):
                app.binary("/api/inspection", query)
        finally:
            app.jobs.shutdown()


if __name__ == "__main__":
    unittest.main()
