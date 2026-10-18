"""Changed-file imports run off the UI thread, never at app startup."""
from __future__ import annotations

import io
from pathlib import Path
import threading

from .parsers import parse_boardview
from .pdf_index import sheet_index
from .store import Store


def prepare_photo(content: bytes) -> bytes:
    from PIL import Image, ImageOps
    # HEIC is common on a Mac/iPhone; keep it optional for source-only installs.
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("Photo exceeds 40 megapixels. Crop or resize it first.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((2048, 2048))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=90)  # No EXIF/GPS is sent to the model.
        return output.getvalue()


def photo_preview(store: Store, source: dict) -> bytes:
    """Rebuild disposable derivatives after restoring a database/originals backup."""
    cache = store.root / "previews"
    cache.mkdir(exist_ok=True, mode=0o700)
    target = cache / (source["sha"] + ".jpg")
    if not target.exists():
        target.write_bytes(prepare_photo((store.blobs / source["sha"]).read_bytes()))
    return target.read_bytes()


def import_source(store: Store, case_id: str, source_id: str, cancel: threading.Event, progress, ocr_pages=None):
    source = store.source(case_id, source_id)
    path = store.blobs / source["sha"]
    store.source_result(case_id, source_id, "processing", {"message": "Reading file…"}, [])
    try:
        if cancel.is_set():
            raise InterruptedError()
        if source["role"] == "boardview":
            result = parse_boardview(source["name"], path.read_bytes())
            evidence = [dict(kind="pin", **pin) for pin in result.pop("pins")]
            result["pin_count"] = len(evidence)
            store.source_result(case_id, source_id, result.pop("status"), result, evidence)
        elif source["role"] == "photo":
            photo_preview(store, source)
            store.source_result(case_id, source_id, "ready", {"message": "Photo ready. Select it beside your question to send it to the assistant."}, [{"kind": "photo", "description": source["name"]}])
        elif source["name"].lower().endswith(".pdf"):
            import_pdf(store, source, path, cancel, progress, ocr_pages or [])
        else:
            store.source_result(case_id, source_id, "unsupported", {"message": "File retained. Reference text extraction currently supports PDF; use photos for other documents."}, [])
    except InterruptedError:
        store.source_result(case_id, source_id, "interrupted", {"message": "Import stopped. Original file retained; retry when ready."}, [])
        raise
    except Exception as exc:
        message = str(exc)[:500] or type(exc).__name__
        store.source_result(case_id, source_id, "invalid", {"message": "Import needs attention: " + message}, [])
        raise ValueError(message) from None


def import_pdf(store, source, path, cancel, progress, ocr_pages):
    import fitz
    cache = store.root / "ocr"
    cache.mkdir(exist_ok=True, mode=0o700)
    evidence, missing = [], []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF is password protected. Import an unlocked copy.")
        if len(doc) > 1500:
            raise ValueError("PDF exceeds 1500 pages; split it into sections.")
        if len(ocr_pages) > 10 or any(type(p) is not int or p < 1 or p > len(doc) for p in ocr_pages):
            raise ValueError("Choose up to 10 valid page numbers for OCR.")
        index_labels = sheet_index(doc)
        for index, page in enumerate(doc):
            if cancel.is_set():
                raise InterruptedError()
            progress(f"Reading page {index + 1} of {len(doc)}")
            text = page.get_text("text")
            method = "native_text"
            cached = cache / f"{source['sha']}-{index + 1}-v1.txt"
            if len(text.strip()) < 20:
                if cached.exists():
                    text, method = cached.read_text(), "ocr"
                elif index + 1 in ocr_pages:
                    progress(f"OCR page {index + 1}; this can take a little longer")
                    text = page.get_text(textpage=page.get_textpage_ocr(dpi=200, full=True))
                    cached.write_text(text)
                    method = "ocr"
                if len(text.strip()) < 20:
                    missing.append(index + 1)
            if text.strip():
                blocks = [{"bbox": list(b[:4]), "text": b[4][:2000]} for b in page.get_text("blocks") if len(b) > 4 and isinstance(b[4], str)]
                evidence.append({"kind": "document", "page": index + 1, **index_labels.get(index + 1, {}), "text": text[:50000], "text_truncated": len(text) > 50000,
                                 "method": method, "blocks": blocks[:200], "use": "Reference text only. Does not prove circuit connectivity or a particular pin mapping."})
        info = {"parser_version": "pdf-pages/2", "pages": len(doc), "image_only_pages": missing,
                "sheet_index": {str(k): v for k, v in index_labels.items()},
                "message": f"{len(evidence)} pages have searchable text." + (f" {len(missing)} pages need OCR or visual inspection." if missing else "")}
    if cancel.is_set():
        raise InterruptedError()
    store.source_result(source["case_id"], source["id"], "partial" if missing else "ready", info, evidence)


def render_page(store: Store, source: dict, page_number: int, max_edge: int = 2200) -> bytes:
    import fitz
    if not 600 <= max_edge <= 6000:
        raise ValueError("Page resolution must be between 600 and 6000 pixels")
    with fitz.open(store.blobs / source["sha"]) as doc:
        if page_number < 1 or page_number > len(doc):
            raise ValueError("Page not found")
        page = doc[page_number - 1]
        scale = max_edge / max(page.rect.width, page.rect.height)
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
