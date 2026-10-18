from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

from boardbrain_v2.assistant import Assistant, ProviderError, validate_plan
from boardbrain_v2.ingest import import_source, prepare_photo
from boardbrain_v2.measurements import parse_reading, validate_measurement, fulfills
from boardbrain_v2.parsers import parse_boardview, parse_bvr3
from boardbrain_v2.server import App, make_server
from boardbrain_v2.store import Store


BVR = """BVRAW_FORMAT_3
PART_NAME U1
PART_SIDE T
PART_ORIGIN 100.5 200.25
PIN_NUMBER 1
PIN_NAME DP
PIN_ORIGIN 2.5 3.75
PIN_NET USB_D+
PIN_END
PIN_NUMBER 2
PIN_ORIGIN 4 5
PIN_NET USB_D-
PIN_END
PIN_NUMBER 3
PIN_NET RESET#
PIN_END
PIN_NUMBER 4
PIN_NET RESET
PIN_END
PART_END
"""


def reading(**changes):
    return validate_measurement(dict(target="U1.1", quantity="voltage", value=3.3,
                                     unit="V", power_state="standby", reference="board ground",
                                     polarity="", raw="", note="") | changes)


def plan():
    return {"summary": "Check the input path using the recorded point.", "hypotheses": [], "questions": [], "web_sources": [],
            "tests": [{"title": "Recheck this point", "why": "Compare with the prior reading under the same conditions.",
                       "procedure": "Use the same checked point and reference.", "quantity": "voltage", "target": "U1.1",
                       "reference": "board ground", "power_state": "standby", "polarity": "", "pin_evidence_id": "P1",
                       "expected_evidence_id": "", "expected_quote": "", "interpretation": "A changed reading narrows the intermittent path.",
                       "stop_if": "The board gets unexpectedly hot.", "evidence_ids": ["P1"]}]}


def response(output, **extra):
    return {"id": "resp_test", "status": "completed", "output": output,
            "usage": {"input_tokens": 1000, "output_tokens": 100}, **extra}


def final_response(p):
    return response([{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(p), "annotations": []}]}])


class ParserTests(unittest.TestCase):
    def test_punctuation_and_relative_coordinates(self):
        pins = parse_bvr3(BVR)
        self.assertEqual([p["net"] for p in pins], ["USB_D+", "USB_D-", "RESET#", "RESET"])
        self.assertEqual([(p["ref"], p["pin"]) for p in pins], [("U1", str(i)) for i in range(1, 5)])
        self.assertEqual((pins[0]["x"], pins[0]["y"], pins[0]["side"]), (103, 204, "T"))
        self.assertIsNone(pins[2]["x"])

    def test_truncated_file_does_not_return_partial_connectivity(self):
        result = parse_boardview("test.bvr", BVR.replace("PART_END", "").encode())
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["pins"], [])

    def test_duplicate_pin_rejected_even_on_same_net(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_bvr3(BVR.replace("PIN_NUMBER 2", "PIN_NUMBER 1"))

    def test_unknown_pin_semantics_fail_closed(self):
        result = parse_boardview("x.bvr", BVR.replace("PIN_NAME DP", "PIN_ROTATED_UNKNOWN 90").encode())
        self.assertEqual(result["status"], "invalid")

    def test_no_pin_number_is_not_a_component_level_success(self):
        result = parse_boardview("x.bvr", BVR.replace("PIN_NUMBER 1", "PIN_ID 1").encode())
        self.assertEqual(result["status"], "invalid")

    def test_binary_strings_never_become_connections(self):
        result = parse_boardview("old.tvw", b"\xff\x00U1\x00PP3V3\x00GND\x00")
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["pins"])

    def test_documented_csv_and_nonfinite_coordinates(self):
        result = parse_boardview("mapping.csv", b"ref,pin,net,side,x,y\nU1,1,USB_D+,T,1,2\nU1,2,USB_D-,T,3,4\n")
        self.assertEqual(result["status"], "parsed")
        result = parse_boardview("mapping.csv", b"ref,pin,net,x,y\nU1,1,USB_D+,nan,2\n")
        self.assertEqual(result["status"], "invalid")


class MeasurementTests(unittest.TestCase):
    def test_si_prefixes_and_negative_voltage(self):
        for text, value in [("USB_D+ = -420 mV", -.42), ("R1.1 = 12 kΩ", 12000), ("X = 2 MΩ", 2e6), ("X = 2 mΩ", .002)]:
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_reading(text)["value"], value)
        self.assertEqual(parse_reading("USB_D+ = 1 V")["target"], "USB_D+")

    def test_question_and_ambiguous_unit_not_saved(self):
        for text in ("is PP3V3 3.3 V?", "PP3V3 3.3", "R1 = 2 mohmS", "R1 = -2 Ω"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_reading(text)

    def test_diode_and_ol(self):
        self.assertEqual(parse_reading("TP1 = diode .420 V")["quantity"], "diode")
        self.assertEqual(reading(quantity="resistance", value="OL", unit="Ω", power_state="unpowered")["value"], "OL")
        with self.assertRaises(ValueError):
            reading(value="OL")

    def test_completion_requires_quantity_conditions_reference_and_polarity(self):
        test = reading()
        self.assertTrue(fulfills(reading(), test))
        for change in ({"quantity": "resistance", "unit": "Ω", "power_state": "unpowered"}, {"power_state": "running"}, {"reference": "chassis"}, {"target": "U1.2"}):
            self.assertFalse(fulfills(reading(**change), test))
        d = reading(quantity="diode", power_state="unpowered", polarity="red ground")
        self.assertFalse(fulfills(d | {"polarity": "black ground"}, d))
        self.assertFalse(fulfills(reading(power_state="unknown"), test))

    def test_invalid_physical_reading_is_retained_with_warning(self):
        r = reading(quantity="resistance", unit="Ω", power_state="running")
        self.assertTrue(r["warnings"])
        self.assertFalse(fulfills(r, r))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.a = self.store.create_case({"title": "Synthetic repair A", "device": "Test board", "board_id": "TEST-001", "revision": "A", "symptom": "No power"})["id"]
        self.b = self.store.create_case({"title": "Synthetic repair B", "board_id": "TEST-002", "revision": "B", "symptom": "No power"})["id"]

    def tearDown(self):
        self.temp.cleanup()

    def imported(self, case_id=None, confirmed=True):
        case_id = case_id or self.a
        source = self.store.add_source(case_id, "synthetic.bvr", BVR.encode(), "boardview", confirmed)
        import_source(self.store, case_id, source["id"], threading.Event(), lambda _: None)
        return source

    def test_exact_lookup_never_crosses_cases_or_joins_net_punctuation(self):
        self.imported()
        self.assertEqual(len(self.store.lookup(self.a, "USB_D+")), 1)
        self.assertEqual(self.store.lookup(self.a, "USB_D+")[0]["data"]["pin"], "1")
        self.assertEqual(self.store.lookup(self.a, "USB_D-")[0]["data"]["pin"], "2")
        self.assertEqual(self.store.lookup(self.b, "USB_D+"), [])
        self.assertEqual(self.store.lookup(self.a, "USB_D"), [])

    def test_reimport_is_deduplicated_and_keeps_original(self):
        source = self.imported()
        again = self.store.add_source(self.a, "renamed.bvr", BVR.encode(), "boardview", True)
        self.assertEqual(source["id"], again["id"])
        self.assertEqual(len(self.store.case(self.a)["sources"]), 1)
        self.assertEqual((self.store.blobs / source["sha"]).read_bytes(), BVR.encode())

    def test_conflicting_manual_map_cannot_hide_import(self):
        self.imported()
        self.store.capture(self.a, "pin", {"ref": "U1", "pin": "1", "net": "OTHER", "basis": "Synthetic conflicting check", "confirmed": True})
        self.assertTrue(self.store.lookup(self.a, "USB_D+")[0]["conflict"])

    def test_changing_board_scope_resets_authority(self):
        source = self.imported()
        self.store.capture(self.a, "pin", {"ref": "U2", "pin": "1", "net": "GND", "basis": "Synthetic check", "confirmed": True})
        self.store.update_case(self.a, {"revision": "C"})
        self.assertFalse(self.store.source(self.a, source["id"])["scope_confirmed"])
        self.assertTrue(next(e for e in self.store.case(self.a)["events"] if e["kind"] == "pin")["retracted"])

    def test_only_technician_confirmed_verified_outcomes_are_retrieved(self):
        self.store.capture(self.a, "measurement", reading())
        good = dict(status="confirmed", technician_confirmed=True, fix="Replaced damaged connector", verification="Repeated cold boot and load test passed", cause_status="suspected", root_cause="Cracked connector")
        for missing in ("technician_confirmed", "verification", "fix"):
            bad = good.copy(); bad.pop(missing)
            with self.assertRaises(ValueError):
                self.store.close_case(self.a, bad)
        self.store.close_case(self.a, good)
        found = self.store.confirmed_cases(self.b, "No power")
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["same_board_revision"])
        self.assertEqual(found[0]["outcome"]["cause_status"], "suspected")
        with self.assertRaises(ValueError):
            self.store.capture(self.a, "note", {"text": "New evidence"})
        self.store.close_case(self.a, {"status": "open"})
        self.assertEqual(self.store.confirmed_cases(self.b, "No power"), [])

    def test_retraction_preserves_history_and_excludes_evidence(self):
        e = self.store.capture(self.a, "measurement", reading())
        self.store.retract(self.a, e["id"])
        self.assertTrue(self.store.case(self.a)["events"][0]["retracted"])
        self.assertFalse(self.store.lookup(self.a, "U1.1"))

    def test_backup_contains_consistent_db_and_originals(self):
        source = self.imported()
        backup = self.store.backup()
        with zipfile.ZipFile(backup) as z:
            self.assertEqual(z.read("files/" + source["sha"]), BVR.encode())
            restored = Path(self.temp.name) / "restored.sqlite3"
            restored.write_bytes(z.read("boardbrain-v2.sqlite3"))
        with sqlite3.connect(restored) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM cases").fetchone()[0], 2)

    def test_atomic_budget_reservations_and_unknown_network_charge(self):
        self.store.set_settings({"monthly_limit": 3, "warning_at": 2})
        def reserve(_):
            try:
                return self.store.reserve(self.a, 3)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(4)))
        ids = [x for x in results if x]
        self.assertEqual(len(ids), 1)
        self.store.settle(ids[0], None, {})
        self.assertEqual(self.store.usage_summary()["estimated_cost"], 3)
        self.assertEqual(self.store.usage_summary()["reserved"], 0)

    def test_restart_recovers_interrupted_budget_and_import(self):
        self.store.reserve(self.a, 3)
        source = self.store.add_source(self.a, "x.bvr", BVR.encode(), "boardview")
        self.store.recover_interrupted()
        self.assertEqual(self.store.source(self.a, source["id"])["status"], "interrupted")
        self.assertEqual(self.store.usage_summary()["estimated_cost"], 3)

    def test_native_pdf_and_scanned_page_are_distinguished(self):
        import fitz
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "SYNTHETIC REFERENCE: output nominal 3.3 V. Check operating conditions.")
        doc.new_page()
        source = self.store.add_source(self.a, "synthetic.pdf", doc.tobytes(), "schematic", True)
        import_source(self.store, self.a, source["id"], threading.Event(), lambda _: None)
        saved = self.store.source(self.a, source["id"])
        self.assertEqual(saved["status"], "partial")
        self.assertEqual(saved["info"]["image_only_pages"], [2])
        result = self.store.lookup(self.a, "nominal")
        self.assertEqual(result[0]["kind"], "document")
        self.assertEqual(result[0]["data"]["method"], "native_text")

    def test_photo_derivative_is_bounded_and_strips_exif(self):
        from PIL import Image
        image = Image.new("RGB", (3000, 1000))
        exif = image.getexif(); exif[270] = "private description"
        original = io.BytesIO(); image.save(original, "JPEG", exif=exif)
        with Image.open(io.BytesIO(prepare_photo(original.getvalue()))) as normalized:
            self.assertEqual(normalized.size, (2048, 683))
            self.assertFalse(normalized.getexif())


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {"P1": {"kind": "pin", "scope_confirmed": 1, "conflict": False, "data": {"ref": "U1", "pin": "1", "net": "USB_D+"}}}

    def test_valid_mapping_accepted_but_foreign_id_not(self):
        self.assertEqual(validate_plan(plan(), self.evidence, set())["tests"][0]["target"], "U1.1")
        p = plan(); p["tests"][0]["pin_evidence_id"] = "OTHER_CASE_PIN"
        with self.assertRaises(ValueError):
            validate_plan(p, self.evidence, set())

    def test_unconfirmed_conflicting_and_photo_pin_claims_rejected(self):
        for change in ({"scope_confirmed": 0}, {"conflict": True}, {"kind": "photo"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_plan(plan(), {"P1": self.evidence["P1"] | change}, set())

    def test_expected_value_requires_exact_native_quote(self):
        p = plan(); t=p["tests"][0];t.update(expected_evidence_id="D1", expected_quote="Output is 3.3 V")
        evidence=self.evidence | {"D1": {"kind":"document","scope_confirmed":1,"data":{"method":"native_text","text":"Output is 3.3 V under condition X."}}}
        validate_plan(p,evidence,set())
        for changed in ("ocr",):
            bad=copy.deepcopy(evidence);bad["D1"]["data"]["method"]=changed
            with self.assertRaises(ValueError):validate_plan(p,bad,set())
        t["expected_quote"]="Output is 5 V"
        with self.assertRaises(ValueError):validate_plan(p,evidence,set())

    def test_unsupported_operational_claims_and_fake_links_rejected(self):
        p=plan();p["tests"][0]["procedure"]="Measure U7000 before proceeding."
        with self.assertRaises(ValueError):validate_plan(p,self.evidence,set())
        p=plan();p["web_sources"]=[{"url":"https://example.com/fake","title":"Invented"}]
        with self.assertRaises(ValueError):validate_plan(p,self.evidence,set())
        p=plan();p["tests"][0].update(quantity="resistance")
        with self.assertRaises(ValueError):validate_plan(p,self.evidence,set())


class AssistantTests(unittest.TestCase):
    setUp = StoreTests.setUp
    tearDown = StoreTests.tearDown
    imported = StoreTests.imported
    def test_tool_round_trip_current_case_only(self):
        self.imported()
        recorded=[]
        def factory(_):
            class Fake:
                def respond(inner,payload):
                    recorded.append(payload)
                    if len(recorded)==1:
                        return response([{"type":"function_call","name":"lookup_evidence","call_id":"call1","arguments":json.dumps({"query":"U1.1"})}])
                    result=json.loads(payload["input"][-1]["output"])[0]
                    p=plan();p["tests"][0].update(pin_evidence_id=result["id"],evidence_ids=[result["id"]])
                    return final_response(p)
            return Fake()
        keys=type("Keys",(),{"get":lambda _:"fake-test-key"})()
        assistant=Assistant(self.store,keys,factory)
        result=assistant.run(self.a,"What next?",[],False,threading.Event(),lambda _:None)
        self.assertIn("plan_id",result)
        self.assertEqual(len(recorded),2)
        self.assertEqual(recorded[0]["model"],"gpt-6.1-sol")
        self.assertEqual(recorded[0]["reasoning"],{"effort":"medium"})
        self.assertFalse(recorded[0]["store"])
        self.assertNotIn("web_search",[t["type"] for t in recorded[0]["tools"]])
        self.assertEqual(self.store.usage_summary()["reserved"],0)
        self.assertGreater(self.store.usage_summary()["estimated_cost"],0)

    def test_deeper_analysis_routes_evidence_and_settles_astra_cost(self):
        checked=self.store.capture(self.a,"measurement",reading())
        recorded=[]
        def factory(_):
            class Fake:
                def respond(inner,payload):
                    recorded.append(payload)
                    self.assertEqual(self.store.usage_summary()["reserved"],15)
                    self.assertIn(checked["id"],payload["input"][0]["content"][0]["text"])
                    p=plan();p["tests"][0].update(pin_evidence_id=checked["id"],evidence_ids=[checked["id"]])
                    return final_response(p)
            return Fake()
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:"fake"})(),factory)
        result=assistant.run(self.a,"Reconsider this reading",[],False,threading.Event(),lambda _:None,deep_analysis=True)
        self.assertEqual(len(recorded),1)
        self.assertEqual(recorded[0]["model"],"gpt-6-astra")
        self.assertEqual(recorded[0]["reasoning"],{"effort":"high"})
        self.assertNotIn("web_search",[t["type"] for t in recorded[0]["tools"]])
        self.assertEqual(self.store.settings()["model"],"gpt-6.1-sol")
        self.assertAlmostEqual(result["estimated_cost"],0.0175)
        self.assertEqual(self.store.usage_summary()["reserved"],0)
        saved=next(e for e in self.store.case(self.a)["events"] if e["id"]==result["plan_id"])
        self.assertEqual(saved["data"]["model"],"gpt-6-astra")
        self.assertEqual(saved["data"]["analysis_mode"],"deep")
        self.assertGreaterEqual(saved["data"]["elapsed_seconds"],0)

    def test_astra_budget_blocks_before_provider_or_message(self):
        self.store.set_settings({"monthly_limit":10,"warning_at":5})
        def unexpected(_):raise AssertionError("No paid call should start")
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:"fake"})(),unexpected)
        with self.assertRaises(ValueError):
            assistant.run(self.a,"Think deeper",[],False,threading.Event(),lambda _:None,deep_analysis=True)
        self.assertFalse(self.store.case(self.a)["events"])
        self.assertEqual(self.store.usage_summary()["reserved"],0)

    def test_astra_network_uncertainty_keeps_astra_reservation_without_fallback(self):
        calls=[]
        class Fake:
            def respond(_,payload):
                calls.append(payload["model"])
                raise ProviderError("Timed out",True)
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:"fake"})(),lambda _:Fake())
        with self.assertRaises(ProviderError):
            assistant.run(self.a,"Think deeper",[],False,threading.Event(),lambda _:None,deep_analysis=True)
        self.assertEqual(calls,["gpt-6-astra"])
        self.assertEqual(self.store.usage_summary()["estimated_cost"],15)
        self.assertFalse(any(e["kind"]=="plan" for e in self.store.case(self.a)["events"]))

    def test_deeper_analysis_cannot_bypass_evidence_gate(self):
        class Fake:
            def respond(_,payload):return final_response(plan())
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:"fake"})(),lambda _:Fake())
        with self.assertRaises(ValueError):
            assistant.run(self.a,"Think deeper",[],True,threading.Event(),lambda _:None,deep_analysis=True)
        self.assertFalse(any(e["kind"]=="plan" for e in self.store.case(self.a)["events"]))
        self.assertGreater(self.store.usage_summary()["estimated_cost"],0)

    def test_incomplete_response_never_saves_partial_advice(self):
        class Fake:
            def respond(_,payload):return response([],status="incomplete")
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:"fake"})(),lambda _:Fake())
        with self.assertRaisesRegex(ValueError,"incomplete"):
            assistant.run(self.a,"What next?",[],False,threading.Event(),lambda _:None)
        self.assertFalse(any(e["kind"]=="plan" for e in self.store.case(self.a)["events"]))
        self.assertGreater(self.store.usage_summary()["estimated_cost"],0)

    def test_network_uncertainty_retains_reserved_charge(self):
        class Fake:
            def respond(_,payload):raise ProviderError("Timed out",True)
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:"fake"})(),lambda _:Fake())
        with self.assertRaises(ProviderError):assistant.run(self.a,"What next?",[],False,threading.Event(),lambda _:None)
        self.assertEqual(self.store.usage_summary()["estimated_cost"],3)

    def test_missing_key_does_not_reserve_money_or_write_message(self):
        assistant=Assistant(self.store,type("Keys",(),{"get":lambda _:None})())
        with self.assertRaisesRegex(ValueError,"API key"):
            assistant.run(self.a,"What next?",[],False,threading.Event(),lambda _:None)
        self.assertEqual(self.store.usage_summary()["reserved"],0)
        self.assertFalse(self.store.case(self.a)["events"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name))
        self.app=App(self.store,type("Keys",(),{"get":lambda _:None})())
        self.server=make_server(self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.app.jobs.shutdown();self.temp.cleanup()

    def request(self,path,headers=None,data=None):
        request=urllib.request.Request(self.base+path,headers=headers or {},data=data)
        return urllib.request.urlopen(request,timeout=3)

    def test_auth_host_and_origin_checks(self):
        for headers in ({},{"Authorization":"Bearer "+self.app.token,"Origin":"https://evil.example"},{"Authorization":"Bearer "+self.app.token,"Host":"evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/state',headers)
            self.assertEqual(error.exception.code,403)
        with self.request('/api/state',{"Authorization":"Bearer "+self.app.token}) as result:
            self.assertFalse(json.load(result)["has_api_key"])

    def test_static_has_csp_and_traversal_is_not_served(self):
        with self.request('/') as result:self.assertIn("frame-ancestors 'none'",result.headers['Content-Security-Policy'])
        with self.assertRaises(urllib.error.HTTPError):self.request('/../store.py')

    def test_malformed_body_and_cross_case_file(self):
        headers={"Authorization":"Bearer "+self.app.token,"Content-Type":"application/json"}
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/cases',headers,b'{')
        self.assertEqual(error.exception.code,400)
        a=self.store.create_case({"title":"A","symptom":"Test"})['id'];b=self.store.create_case({"title":"B","symptom":"Test"})['id']
        s=self.store.add_source(a,'a.txt',b'private original','reference')
        with self.assertRaises(urllib.error.HTTPError):self.request(f'/api/file?case_id={b}&id={s["id"]}',headers)


if __name__ == '__main__':
    unittest.main()
