"""Synthetic-only checks for bounded and attributable schematic images."""
from __future__ import annotations

import copy
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

from boardbrain_v2.schematic import inspect_page, render_inspection
from boardbrain_v2.store import Store


def synthetic_pdf(*, rotation=0, repeat=False, image_only=False):
    with fitz.open() as doc:
        page = doc.new_page(width=800, height=600)
        for box, color in (((0, 0, 400, 300), (1, 0, 0)), ((400, 0, 800, 300), (0, 1, 0)),
                           ((0, 300, 400, 600), (0, 0, 1)), ((400, 300, 800, 600), (1, 1, 0))):
            page.draw_rect(fitz.Rect(box), color=None, fill=color)
        if image_only:
            image = Image.new("RGB", (32, 32), "white")
            output = io.BytesIO()
            image.save(output, format="PNG")
            page.insert_image(fitz.Rect(340, 260, 460, 340), stream=output.getvalue())
        else:
            for xy, text in (((70, 90), "U1"), ((420, 90), "U10"), ((70, 220), "RESET#"),
                             ((420, 220), "RESET"), ((70, 400), "USB_D+"), ((420, 400), "USB_D-"),
                             ((70, 500), "U1.A1")):
                page.insert_text(xy, text, fontsize=14)
            if repeat:
                page.insert_text((420, 500), "U1", fontsize=14)
        page.set_rotation(rotation)
        return doc.tobytes()


class SchematicImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.case = self.store.create_case({"kind": "reference", "title": "Synthetic documents"})["id"]
        self.other = self.store.create_case({"kind": "reference", "title": "Other documents"})["id"]

    def add(self, content=None, *, role="schematic", name="fixture.pdf", status="ready", case=None):
        case = case or self.case
        source = self.store.add_source(case, name, content or synthetic_pdf(), role)
        self.store.source_result(case, source["id"], status,
                                 {"pages": 1, "sheet_index": {"1": {"sheet": "7", "page_title": "SYNTHETIC CIRCUIT"}}}, [])
        return self.store.source(case, source["id"])

    def inspect(self, source, **kwargs):
        return inspect_page(self.store, self.case, source["id"], kwargs.pop("page", 1), **kwargs)

    def test_full_page_metadata_and_stable_reproduction(self):
        source = self.add()
        evidence, png = self.inspect(source)
        repeat, same_png = self.inspect(source, region=[0, 0, 1, 1])
        empty, _ = self.inspect(source, region=[])
        self.assertEqual(evidence, repeat)
        self.assertEqual(evidence, empty)
        self.assertEqual(png, same_png)
        self.assertEqual(render_inspection(self.store, self.case, evidence), png)
        self.assertEqual(evidence["kind"], "document_image")
        self.assertEqual((evidence["name"], evidence["status"], evidence["scope_confirmed"]), ("fixture.pdf", "ready", 0))
        data = evidence["data"]
        self.assertEqual((data["page"], data["sheet"], data["page_title"]), (1, "7", "SYNTHETIC CIRCUIT"))
        self.assertEqual(data["source_sha"], source["sha"])
        self.assertEqual(data["image_sha256"], hashlib.sha256(png).hexdigest())
        self.assertLessEqual(max(data["pixel_width"], data["pixel_height"]), 2000)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (data["pixel_width"], data["pixel_height"]))
        self.assertNotIn("base64", str(evidence))
        self.assertIn("does not verify", data["use"])

    def test_focus_uses_exact_labels_and_preserves_electrical_punctuation(self):
        source = self.add()
        results = {}
        for label in ("U1", "U10", "RESET#", "RESET", "USB_D+", "USB_D-", "U1.A1"):
            with self.subTest(label=label):
                evidence, png = self.inspect(source, focus=label)
                data = evidence["data"]
                self.assertEqual(data["focus"], label)
                self.assertAlmostEqual(data["region"][2] - data["region"][0], .4)
                self.assertAlmostEqual(data["region"][3] - data["region"][1], .4)
                self.assertEqual(render_inspection(self.store, self.case, evidence), png)
                results[label] = evidence["id"]
        self.assertEqual(len(set(results.values())), len(results))
        for label in ("u1", "USB_D", "U1.*", "U1.A", "MISSING"):
            with self.subTest(missing=label), self.assertRaisesRegex(ValueError, "No exact native-text label"):
                self.inspect(source, focus=label)

    def test_focus_ambiguity_and_missing_label_are_actionable(self):
        source = self.add(synthetic_pdf(repeat=True))
        with self.assertRaisesRegex(ValueError, "2 locations.*normalized region.*Candidate label boxes"):
            self.inspect(source, focus="U1")
        for focus in ("a" * 81, None, "two labels", "tab\tlabel", "bad\x00label"):
            with self.subTest(focus=focus), self.assertRaises(ValueError):
                self.inspect(source, focus=focus)
        with self.assertRaisesRegex(ValueError, "not both"):
            self.inspect(source, focus="U1", region=[0, 0, 1, 1])

    def test_bounds_types_and_nonfinite_input_rejected(self):
        source = self.add()
        regions = ([0, 0, 1], "0,0,1,1", {}, [False, 0, 1, 1], ["0", 0, 1, 1],
                   [0, 0, float("nan"), 1], [0, 0, float("inf"), 1], [0, 0, 10**1000, 1],
                   [-.1, 0, 1, 1], [0, 0, 1.1, 1], [.5, 0, .4, 1], [0, .5, 1, .4],
                   [0, 0, .019, 1], [0, 0, 1, .019])
        for region in regions:
            with self.subTest(region=region), self.assertRaises(ValueError):
                self.inspect(source, region=region)
        for page in (0, -1, 2, True, 1.0, "1", None):
            with self.subTest(page=page), self.assertRaises(ValueError):
                self.inspect(source, page=page)
        evidence, _ = self.inspect(source, region=[.98, .98, 1, 1])
        self.assertEqual(evidence["data"]["region"], [.98, .98, 1.0, 1.0])

    def test_manual_regions_use_displayed_coordinates_on_rotated_pages(self):
        for rotation, color in ((90, (0, 0, 255)), (180, (255, 255, 0)), (270, (0, 255, 0))):
            with self.subTest(rotation=rotation):
                source = self.add(synthetic_pdf(rotation=rotation), name=f"rotated-{rotation}.pdf")
                full, png = self.inspect(source)
                image = Image.open(io.BytesIO(png))
                self.assertEqual(image.getpixel((image.width // 4, image.height // 4)), color)
                crop, png = self.inspect(source, region=[0, 0, .5, .5])
                image = Image.open(io.BytesIO(png))
                self.assertEqual(image.getpixel((image.width // 2, image.height // 2)), color)
                self.assertEqual(render_inspection(self.store, self.case, crop), png)
                with self.assertRaisesRegex(ValueError, "rotated.*normalized region"):
                    self.inspect(source, focus="U1")

    def test_image_only_pages_need_no_searchable_document_evidence(self):
        source = self.add(synthetic_pdf(image_only=True), status="partial")
        self.assertEqual(self.store.lookup(self.case, "page 1"), [])
        evidence, png = self.inspect(source)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(evidence["status"], "partial")
        with self.assertRaisesRegex(ValueError, "scanned PDFs"):
            self.inspect(source, focus="U1")

    def test_source_role_status_ownership_and_content_are_checked(self):
        source = self.add()
        with self.assertRaisesRegex(ValueError, "not found in this case"):
            inspect_page(self.store, self.other, source["id"], 1)
        for role, name in (("photo", "photo.pdf"), ("boardview", "board.pdf"), ("reference", "not-pdf.txt")):
            wrong = self.add(role=role, name=name)
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, "requires a schematic or reference PDF"):
                self.inspect(wrong)
        for status in ("queued", "processing", "invalid", "unsupported", "interrupted"):
            self.store.source_result(self.case, source["id"], status, {}, [])
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "import"):
                self.inspect(source)
        self.store.source_result(self.case, source["id"], "ready", {}, [])
        (self.store.blobs / source["sha"]).write_bytes(b"modified PDF")
        with self.assertRaisesRegex(ValueError, "source hash"):
            self.inspect(source)

    def test_evidence_tampering_and_renderer_mismatch_fail_closed(self):
        source = self.add()
        evidence, _ = self.inspect(source, region=[.1, .1, .6, .7])
        for key, value in (("source_sha", "0" * 64), ("image_sha256", "0" * 64),
                           ("pixel_width", 42), ("render_version", "other-renderer"), ("page", 2),
                           ("region", [0, 0, 1, 1]), ("region", [])):
            altered = copy.deepcopy(evidence)
            altered["data"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                render_inspection(self.store, self.case, altered)
        for key, value in (("id", "wrong"), ("kind", "document"), ("case_id", self.other)):
            altered = copy.deepcopy(evidence)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                render_inspection(self.store, self.case, altered)
        with self.assertRaisesRegex(ValueError, "workspace"):
            render_inspection(self.store, self.other, evidence)

    def test_different_case_identity_and_region_have_distinct_ids(self):
        source = self.add()
        first, _ = self.inspect(source)
        crop, _ = self.inspect(source, region=[.123456789, .2, .723456789, .8])
        second_source = self.add(case=self.other)
        second, _ = inspect_page(self.store, self.other, second_source["id"], 1)
        self.assertEqual(len({first["id"], crop["id"], second["id"]}), 3)
        self.assertEqual(crop["data"]["region"], [.12345679, .2, .72345679, .8])

    def test_output_size_limit_is_enforced(self):
        source = self.add()
        with patch("boardbrain_v2.schematic.MAX_IMAGE_BYTES", 100), self.assertRaisesRegex(ValueError, "exceeds 8 MB"):
            self.inspect(source)


if __name__ == "__main__":
    unittest.main()
