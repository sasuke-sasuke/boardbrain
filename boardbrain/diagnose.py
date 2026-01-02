from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Tuple
from .config import SETTINGS
from .case_store import (
    list_measurements, list_notes, list_attachments,
    list_baselines, list_baseline_measurements
)
from .guardrails import (
    is_board_specific_question, has_required_evidence, refusal_message_missing_evidence
)
from .oai import embed_text, run_reasoning_with_vision
from .rag import query as rag_query
from .prompts import SYSTEM_PROMPT

def _load_attachment_bytes(rel_path: str) -> Tuple[bytes, str]:
    abs_path = os.path.join(SETTINGS.data_dir, rel_path)
    ext = os.path.splitext(abs_path)[1].lower()
    mime = "image/png"
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext == ".webp":
        mime = "image/webp"
    elif ext == ".gif":
        mime = "image/gif"
    with open(abs_path, "rb") as f:
        return f.read(), mime

def build_case_context(case: Dict[str, Any]) -> str:
    meas = list_measurements(case["case_id"])
    notes = list_notes(case["case_id"])
    lines = [
        f"CASE_ID: {case['case_id']}",
        f"TITLE: {case['title']}",
        f"MODEL: {case.get('model','')}",
        f"BOARD_ID: {case.get('board_id','')}",
        f"SYMPTOM: {case.get('symptom','')}",
    ]
    if meas:
        lines.append("\nMEASUREMENTS:")
        for m in meas[-40:]:
            unit = f" {m['unit']}" if m.get("unit") else ""
            note = f" (note: {m['note']})" if m.get("note") else ""
            lines.append(f"- {m['name']}: {m['value']}{unit}{note}")
    if notes:
        lines.append("\nNOTES:")
        for n in notes[-20:]:
            lines.append(f"- {n['note']}")
    return "\n".join(lines)


def _infer_board_id(case: Dict[str, Any]) -> str:
    """Try to infer Apple board number like 820-02020."""
    b = (case.get("board_id") or "").strip()
    if b:
        return b
    m = re.search(r"\b\d{3}-\d{5}\b", case.get("case_id", ""))
    return m.group(0) if m else ""


def _infer_model(case: Dict[str, Any]) -> str:
    m = re.search(r"\bA\d{4}\b", case.get("model", "") + " " + case.get("case_id", ""))
    return m.group(0) if m else (case.get("model") or "")


def _build_baseline_context(model: str, board_id: str) -> str:
    """Append known-good reference notes/measurements if available."""
    candidates = []
    for b in list_baselines():
        if board_id and (b.get("board_id") == board_id):
            candidates.append(b)
        elif model and model in (b.get("model") or ""):
            candidates.append(b)

    if not candidates:
        return ""

    # Prefer exact board_id matches, then newest
    candidates.sort(key=lambda x: (0 if (board_id and x.get("board_id") == board_id) else 1, x.get("created_at", "")))
    top = candidates[:2]

    lines = ["\nKNOWN-GOOD BASELINES (reference only):"]
    for b in top:
        lines.append(
            f"- BASELINE {b['baseline_id']} | {b.get('model','')} {b.get('board_id','')} | quality={b.get('quality','')} | boot={b.get('boot_state','')} | source={b.get('source','')}"
        )
        if b.get("notes"):
            lines.append(f"  Notes: {b['notes']}")
        meas = list_baseline_measurements(b["baseline_id"])
        if meas:
            lines.append("  Measurements:")
            for m in meas[:25]:
                unit = f" {m['unit']}" if m.get("unit") else ""
                note = f" (note: {m['note']})" if m.get("note") else ""
                lines.append(f"  - {m['name']}: {m['value']}{unit}{note}")
    return "\n".join(lines)

def diagnose(case: Dict[str, Any], question: str, include_images: bool = True) -> str:
    attachments = list_attachments(case["case_id"])

    model = _infer_model(case)
    board_id = _infer_board_id(case)

    # RAG over local KB text (prefer matching model/board_id)
    q_embed = embed_text([question])[0]
    hits: List[Dict[str, Any]] = []
    where = {}
    if board_id:
        where = {"board_id": board_id}
    elif model and model.startswith("A"):
        where = {"model": model}

    if where:
        hits = rag_query(q_embed, n_results=8, where=where)
    if len(hits) < 3:
        hits = (hits or []) + rag_query(q_embed, n_results=8)
        # de-dupe by id
        seen = set()
        uniq = []
        for h in hits:
            if h["id"] in seen:
                continue
            seen.add(h["id"])
            uniq.append(h)
        hits = uniq[:10]

    # Accuracy gate: board-specific questions require truth evidence from either
    # (a) case attachments (schematic/boardview screenshot) OR
    # (b) KB results from schematic/datasheet/manual.
    if is_board_specific_question(question):
        has_case_truth = has_required_evidence(attachments)
        has_kb_truth = any((h.get("metadata") or {}).get("doc_type") in ("schematic", "datasheet", "manual") for h in hits)
        if not (has_case_truth or has_kb_truth):
            return refusal_message_missing_evidence()

    ctx = build_case_context(case) + _build_baseline_context(model=model, board_id=board_id)

    evidence_lines = ["RETRIEVED CONTEXT (cite Source file + page):"]
    for h in hits:
        m = h["metadata"] or {}
        src = m.get("source_file")
        page = m.get("page")
        label = f"{src}" + (f" p.{page}" if page else "")
        evidence_lines.append(f"\n--- {label} ---\n{h['document'][:1500]}")

    user_text = f"""USER QUESTION:
{question}

{ctx}

{chr(10).join(evidence_lines)}

INSTRUCTIONS:
- Steps first, then explanations.
- Any board-specific claim MUST be supported by either:
  (a) the attached schematic/boardview images, or
  (b) retrieved context above (and cite Source file + page).
- If you cannot find evidence, you MUST NOT guess. Label as GENERAL THEORY or ask for the missing schematic/boardview snippet.
- When citing, use this format: [SourceFile p.###].
"""

    image_inputs: List[Dict[str, Any]] = []
    if include_images:
        for a in attachments:
            if a.get("type") in ("schematic", "boardview_screenshot"):
                try:
                    b, mime = _load_attachment_bytes(a["rel_path"])
                    image_inputs.append({"bytes": b, "mime": mime, "detail": "high"})
                except Exception:
                    continue

    return run_reasoning_with_vision(SYSTEM_PROMPT, user_text, image_inputs=image_inputs or None)
