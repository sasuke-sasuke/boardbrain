"""Bounded, reproducible PDF images for explicit visual schematic inspection.

Regions use the displayed (rotated) page, with a top-left origin and normalized
coordinates. Text focus is deliberately limited to exact native PDF word labels;
OCR, substring matches and proximity never establish an electrical connection.
"""
from __future__ import annotations

import hashlib
import json
import math

from .store import Store


MAX_EDGE = 2000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 25 * 1024 * 1024
IMAGE_USE = (
    "Visual evidence from the imported document. Inspect symbols, labels and "
    "drawn connections; unreadable or ambiguous details remain unknown. This "
    "image does not verify physical-board applicability or a pin mapping."
)


def _region(value) -> list[float]:
    if value is None or value == []:
        return [0.0, 0.0, 1.0, 1.0]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Region must be [left, top, right, bottom] in normalized 0–1 page coordinates.")
    if any(type(n) not in (int, float) or not 0 <= n <= 1 or not math.isfinite(n) for n in value):
        raise ValueError("Region coordinates must be finite numbers between 0 and 1.")
    x0, y0, x1, y1 = (float(n) for n in value)
    if x1 - x0 < .02 - 1e-12 or y1 - y0 < .02 - 1e-12:
        raise ValueError("Region must have ordered corners and be at least 2% of the page in both dimensions.")
    # A canonical precision makes retries and equivalent JSON numeric spellings
    # refer to the same pixels. Rerenders use these recorded coordinates exactly.
    return [round(n, 8) for n in (x0, y0, x1, y1)]


def _source(store, case_id, source_id):
    source = store.source(case_id, source_id)
    if source["role"] not in ("schematic", "reference") or not source["name"].lower().endswith(".pdf"):
        raise ValueError("Visual schematic inspection requires a schematic or reference PDF from this workspace.")
    if source["status"] not in ("ready", "partial"):
        raise ValueError("Wait for this PDF import to finish, or retry an unsuccessful import before inspecting it.")
    path = store.blobs / source["sha"]
    try:
        if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
            raise ValueError("The stored PDF has an invalid size. Import a fresh copy up to 25 MB.")
        content = path.read_bytes()
    except OSError:
        raise ValueError("The stored PDF is missing or unreadable. Restore or import the original file.") from None
    if hashlib.sha256(content).hexdigest() != source["sha"]:
        raise ValueError("The stored PDF differs from its recorded source hash. Restore or import the original file.")
    return source, content


def _focus_region(page, focus) -> list[float]:
    if page.rotation:
        raise ValueError("Text focus on rotated PDF pages is not supported. Inspect the full page or provide a normalized region instead.")
    bounds = page.rect
    matches = {}
    for word in page.get_text("words"):
        # Literal equality preserves punctuation such as +, -, #, / and dots.
        if word[4] == focus:
            box = tuple(round(float(n), 4) for n in word[:4])
            matches[box] = box
    if not matches:
        raise ValueError("No exact native-text label was found. Labels are case-sensitive; inspect the full page and choose a normalized region, especially for scanned PDFs.")
    if len(matches) > 1:
        boxes = [[round(max(0.0, min(1.0, (box[i] - (bounds.x0 if i % 2 == 0 else bounds.y0)) /
                              (bounds.width if i % 2 == 0 else bounds.height))), 5)
                  for i in range(4)] for box in sorted(matches)[:5]]
        raise ValueError(f"The exact label occurs at {len(matches)} locations. Inspect the full page or choose a normalized region around one location. Candidate label boxes (up to 5): {json.dumps(boxes)}")
    x0, y0, x1, y1 = next(iter(matches))
    # Keep generous surrounding circuit context, never pretend that a label box
    # alone proves the wiring. Edge clamping retains the requested context size.
    width = min(bounds.width, max(bounds.width * .4, (x1 - x0) * 3))
    height = min(bounds.height, max(bounds.height * .4, (y1 - y0) * 3))
    left = max(bounds.x0, min((x0 + x1 - width) / 2, bounds.x1 - width))
    top = max(bounds.y0, min((y0 + y1 - height) / 2, bounds.y1 - height))
    return _region([(left - bounds.x0) / bounds.width, (top - bounds.y0) / bounds.height,
                    (left + width - bounds.x0) / bounds.width, (top + height - bounds.y0) / bounds.height])


def _identity(case_id, source, page_number, region, version):
    payload = [case_id, source["id"], source["sha"], page_number, region, version]
    return "V" + hashlib.sha256(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:24]


def inspect_page(store: Store, case_id: str, source_id: str, page_number: int,
                 region=None, focus: str = "") -> tuple[dict, bytes]:
    """Return visual evidence metadata and its bounded PNG, never inline base64."""
    import fitz

    if type(page_number) is not int or page_number < 1:
        raise ValueError("PDF page must be a positive whole page number starting at 1.")
    if not isinstance(focus, str) or len(focus) > 80:
        raise ValueError("Focus must be an exact native-text label of at most 80 characters.")
    focus = focus.strip()
    if any(c.isspace() or ord(c) < 32 for c in focus):
        raise ValueError("Focus must be one exact native-text label. Use a normalized region for phrases or other areas.")
    if focus and region is not None and region != []:
        raise ValueError("Choose either a focus label or a normalized region, not both.")
    selected = _region(region)
    source, content = _source(store, case_id, source_id)
    version = "schematic-image/1-pymupdf-" + fitz.VersionBind
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except (RuntimeError, ValueError):
        raise ValueError("The stored PDF cannot be opened. Import a valid unlocked PDF.") from None
    with doc:
        if doc.needs_pass:
            raise ValueError("PDF is password protected. Import an unlocked copy.")
        if page_number > len(doc):
            raise ValueError(f"PDF page is out of range. Choose a page from 1 to {len(doc)}.")
        page = doc[page_number - 1]
        bounds = page.rect
        if any(not math.isfinite(n) or not 1 <= n <= 1_000_000 for n in (bounds.width, bounds.height)):
            raise ValueError("This PDF page has unsupported dimensions. Export a standard-size PDF page first.")
        if focus:
            selected = _focus_region(page, focus)
        clip = fitz.Rect(bounds.x0 + selected[0] * bounds.width, bounds.y0 + selected[1] * bounds.height,
                         bounds.x0 + selected[2] * bounds.width, bounds.y0 + selected[3] * bounds.height)
        # Display-list clipping uses displayed page coordinates, including PDF
        # rotation. Fixed RGB/alpha/annotation options are part of this version.
        scale = MAX_EDGE / max(clip.width, clip.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip,
                              colorspace=fitz.csRGB, alpha=False, annots=True)
        if max(pix.width, pix.height) > MAX_EDGE:
            # Non-integer crop origins can add a rounding pixel at each edge.
            scale *= (MAX_EDGE - 2) / max(pix.width, pix.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip,
                                  colorspace=fitz.csRGB, alpha=False, annots=True)
        if min(pix.width, pix.height) < 16 or max(pix.width, pix.height) > MAX_EDGE:
            raise ValueError("This region is too narrow to inspect usefully. Choose a less elongated region.")
        png = pix.tobytes("png")
        if len(png) > MAX_IMAGE_BYTES:
            raise ValueError("Rendered page exceeds 8 MB. Choose a smaller region or a simpler PDF.")
        labels = source.get("info", {}).get("sheet_index", {}).get(str(page_number), {})
        data = {"page": page_number, "sheet": labels.get("sheet", ""), "page_title": labels.get("page_title", ""),
                "region": selected, "focus": focus, "source_sha": source["sha"], "render_version": version,
                "pixel_width": pix.width, "pixel_height": pix.height,
                "image_sha256": hashlib.sha256(png).hexdigest(), "use": IMAGE_USE}
    evidence = {"id": _identity(case_id, source, page_number, selected, version), "case_id": case_id,
                "kind": "document_image", "source_id": source_id, "name": source["name"],
                "scope_confirmed": source["scope_confirmed"], "status": source["status"], "data": data}
    return evidence, png


def render_inspection(store: Store, case_id: str, evidence: dict) -> bytes:
    """Recreate a recorded image only if ownership, source and pixels still match."""
    if not isinstance(evidence, dict) or evidence.get("kind") != "document_image" or evidence.get("case_id") != case_id:
        raise ValueError("Visual evidence does not belong to this workspace.")
    data = evidence.get("data")
    if not isinstance(data, dict) or not isinstance(evidence.get("source_id"), str):
        raise ValueError("Visual evidence metadata is incomplete. Inspect the page again.")
    source = store.source(case_id, evidence["source_id"])
    if data.get("source_sha") != source["sha"]:
        raise ValueError("Visual evidence source hash no longer matches. Inspect the current document again.")
    if "region" not in data or not isinstance(data["region"], list) or len(data["region"]) != 4:
        raise ValueError("Visual evidence has no recorded region. Inspect the page again.")
    current, png = inspect_page(store, case_id, source["id"], data.get("page"), region=data["region"])
    checks = ("render_version", "pixel_width", "pixel_height", "image_sha256")
    if current["id"] != evidence.get("id") or any(current["data"][key] != data.get(key) for key in checks):
        raise ValueError("Visual evidence cannot be reproduced exactly. Inspect the current document again.")
    return png
