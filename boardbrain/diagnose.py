from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Tuple
import json
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
from .netlist import load_netlist, choose_primary_power_rail, extract_net_tokens, canonicalize_net_name, suggest_nets
from .net_refs import get_measure_points, measurement_points_for_net

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

def _build_netlist_summary(case: Dict[str, Any], board_id: str, model: str) -> str:
    nets, meta = load_netlist(board_id=board_id, model=model, case=case)
    if not nets:
        return "NETLIST: none loaded."
    primary = choose_primary_power_rail(board_id, case=case) or "unknown"
    prefixes = ("PPBUS", "PP3V", "PP5V", "PP1V", "PP0V", "PPV", "PPDCIN", "USBC", "VBUS")
    key_nets = sorted([n for n in nets if n.startswith(prefixes)])
    if len(key_nets) > 200:
        key_nets = key_nets[:200]
    signal_nets = sorted([n for n in nets if not n.startswith("PP") and "_" in n])
    if len(signal_nets) > 200:
        signal_nets = signal_nets[:200]
    return (
        f"NETLIST SUMMARY: {meta.get('net_count', len(nets))} nets. Primary rail: {primary}.\n"
        "Only use nets from this list:\n"
        + ", ".join(key_nets)
        + ("\nSignal nets (subset):\n" + ", ".join(signal_nets) if signal_nets else "")
    )
def _retrieve_context(case: Dict[str, Any], question: str, include_images: bool) -> Dict[str, Any]:
    attachments = list_attachments(case["case_id"])

    model = _infer_model(case)
    board_id = _infer_board_id(case)

    q_embed = embed_text([question])[0]
    hits: List[Dict[str, Any]] = []
    where: Dict[str, Any] = {}
    if board_id:
        where = {"board_id": board_id}
    elif model and model.startswith("A"):
        where = {"model": model}

    if where:
        hits = rag_query(q_embed, n_results=8, where=where)
    if len(hits) < 3:
        hits = (hits or []) + rag_query(q_embed, n_results=8)
        seen = set()
        uniq = []
        for h in hits:
            if h["id"] in seen:
                continue
            seen.add(h["id"])
            uniq.append(h)
        hits = uniq[:10]

    ctx = build_case_context(case) + _build_baseline_context(model=model, board_id=board_id)

    evidence_lines = ["RETRIEVED CONTEXT (cite Source file + page):"]
    for h in hits:
        m = h["metadata"] or {}
        src = m.get("source_file")
        page = m.get("page")
        label = f"{src}" + (f" p.{page}" if page else "")
        evidence_lines.append(f"\n--- {label} ---\n{h['document'][:1500]}")

    image_inputs: List[Dict[str, Any]] = []
    if include_images:
        for a in attachments:
            if a.get("type") in ("schematic", "boardview_screenshot"):
                try:
                    b, mime = _load_attachment_bytes(a["rel_path"])
                    image_inputs.append({"bytes": b, "mime": mime, "detail": "high"})
                except Exception:
                    continue

    return {
        "attachments": attachments,
        "hits": hits,
        "model": model,
        "board_id": board_id,
        "ctx": ctx,
        "evidence_lines": evidence_lines,
        "image_inputs": image_inputs,
    }


def answer_question(case: Dict[str, Any], question: str, include_images: bool = True) -> str:
    q = (question or "").strip()
    if q.lower().startswith("/points"):
        q = q[len("/points"):].strip()
        if not q:
            return "Please provide a net name after /points (example: /points PPBUS_AON)."
        nets, _ = load_netlist(board_id=case.get("board_id", ""), case=case)
        tokens = extract_net_tokens(q)
        if not tokens:
            return "No valid net token found. Please provide the exact net name."
        responses = []
        for raw in tokens:
            canon = canonicalize_net_name(raw)
            if canon not in nets:
                sugg = suggest_nets(case.get("board_id", ""), raw, k=8, case=case)
                msg = f"I can't confirm net '{raw}' exists in the loaded {case.get('board_id','')} netlist."
                if sugg:
                    msg += f" Closest matches: {', '.join(sugg)}"
                responses.append(msg)
                continue
            points = measurement_points_for_net(case.get("board_id", ""), canon, case=case, k=10)
            if points:
                responses.append(
                    f"Validated measurement points for {canon} (from boardview): {', '.join(points)}.\n"
                    "Confirm physically in boardview/schematic for accessibility."
                )
            else:
                responses.append(
                    f"I can't find validated refdes measurement points for {canon} in the boardview index.\n"
                    "Fallback (generic): use any large capacitor on the same net if available. "
                    "Confirm in boardview/schematic."
                )
        return "\n\n".join(responses)

    if re.search(
        r"\b(where|what)\b.*\b(measure|probe)\b|\bmeasure points\b|"
        r"\bgive\b.*\b(components|caps|test points)\b.*\bmeasure\b",
        q,
        re.IGNORECASE,
    ):
        nets, _ = load_netlist(board_id=case.get("board_id", ""), case=case)
        tokens = extract_net_tokens(q)
        if not tokens:
            return "Please provide the exact net name so I can look up validated measurement points."
        responses = []
        for raw in tokens:
            canon = canonicalize_net_name(raw)
            if canon not in nets:
                sugg = suggest_nets(case.get("board_id", ""), raw, k=8, case=case)
                msg = f"I can't confirm net '{raw}' exists in the loaded {case.get('board_id','')} netlist."
                if sugg:
                    msg += f" Closest matches: {', '.join(sugg)}"
                responses.append(msg)
                continue
            points = measurement_points_for_net(case.get("board_id", ""), canon, case=case, k=10)
            if points:
                responses.append(
                    f"Validated measurement points for {canon} (from boardview): {', '.join(points)}.\n"
                    "Confirm physically in boardview/schematic for accessibility."
                )
            else:
                responses.append(
                    f"I can't find validated refdes measurement points for {canon} in the boardview index.\n"
                    "Fallback (generic): use any large capacitor on the same net if available. "
                    "Confirm in boardview/schematic."
                )
        return "\n\n".join(responses)

    info = _retrieve_context(case, question, include_images=include_images)

    if is_board_specific_question(question):
        has_case_truth = has_required_evidence(info["attachments"])
        has_kb_truth = any((h.get("metadata") or {}).get("doc_type") in ("schematic", "datasheet", "manual") for h in info["hits"])
        if not (has_case_truth or has_kb_truth):
            return refusal_message_missing_evidence()

    netlist_summary = _build_netlist_summary(case, board_id=info["board_id"], model=info["model"])
    user_text = f"""USER QUESTION:
{question}

{info['ctx']}

{netlist_summary}

{chr(10).join(info['evidence_lines'])}

INSTRUCTIONS:
- You MUST only reference nets present in the netlist summary above. If unsure, ask for the exact net or a schematic snippet.
- Be concise and actionable.
- Any board-specific claim MUST be supported by either:
  (a) the attached schematic/boardview images, or
  (b) retrieved context above (and cite Source file + page).
- If you cannot find evidence, you MUST NOT guess. Label as GENERAL THEORY or ask for the missing schematic/boardview snippet.
- When citing, use this format: [SourceFile p.###].
"""

    return run_reasoning_with_vision(SYSTEM_PROMPT, user_text, image_inputs=info["image_inputs"] or None)


def generate_plan(case: Dict[str, Any], question: str, include_images: bool = True, done_mode: bool = False) -> str:
    info = _retrieve_context(case, question, include_images=include_images)

    if is_board_specific_question(question):
        has_case_truth = has_required_evidence(info["attachments"])
        has_kb_truth = any((h.get("metadata") or {}).get("doc_type") in ("schematic", "datasheet", "manual") for h in info["hits"])
        if not (has_case_truth or has_kb_truth):
            return refusal_message_missing_evidence()

    done_note = ""
    if done_mode:
        done_note = "The user indicated all requested measurements have been provided; advance to the next diagnostic branch."

    netlist_summary = _build_netlist_summary(case, board_id=info["board_id"], model=info["model"])
    json_example = json.dumps(
        {
            "requested_measurements": [
                {
                    "key": "CHECK_PPBUS_AON",
                    "net": "PPBUS_AON",
                    "type": "voltage",
                    "prompt": "Measure PPBUS_AON to GND",
                    "hint": "Use TP or large cap pad",
                }
            ]
        },
        indent=2,
    )
    user_text = "\n".join(
        [
            "USER QUESTION:",
            question,
            "",
            done_note,
            "",
            info["ctx"],
            "",
            netlist_summary,
            "",
            "\n".join(info["evidence_lines"]),
            "",
            "OUTPUT CONTRACT (STRICT):",
            "1) STEPS (DO THIS NOW)",
            "   - numbered steps, short and actionable",
            "2) REQUESTED MEASUREMENTS (WHAT I NEED FROM YOU)",
            "   - each item MUST be a single line like:",
            "     KEY: CHECK_<NETNAME> | PROMPT: Measure <NETNAME> to GND | TYPE: voltage | NET: <NETNAME> | OPTIONAL HINT: <where-to-measure>",
            "3) REQUESTED MEASUREMENTS JSON (MACHINE-READABLE)",
            "   - append at END of response using exact markers:",
            "     ---REQUESTED_MEASUREMENTS_JSON---",
            json_example,
            "     ---END_REQUESTED_MEASUREMENTS_JSON---",
            "   - JSON block must be the final content in the response",
            "   - each item must be:",
            '     {"key":"CHECK_<NETNAME>","net":"<NETNAME>","type":"voltage|resistance|diode|current|frequency|continuity","prompt":"Measure ...","hint":"Where to probe ..."}',
            "   - do not include any extra fields",
            "   - DO NOT output any cheat sheet or example block",
            "4) EVIDENCE USED (CITATIONS REQUIRED)",
            "5) INFERENCE (ONLY IF NECESSARY)",
            "   - label INFERENCE and include verification steps",
            "",
            "INSTRUCTIONS:",
            "- You MUST only reference nets present in the netlist summary above. If unsure, ask for the exact net or a schematic snippet.",
            "- Requested measurement KEY and NET must match the exact net name from the netlist summary (no invented PP* variants).",
            "- Any board-specific claim MUST be supported by either:",
            "  (a) the attached schematic/boardview images, or",
            "  (b) retrieved context above (and cite Source file + page).",
            "- If you cannot find evidence, you MUST NOT guess; ask for the missing schematic/boardview snippet.",
            "- When citing, use this format: [SourceFile p.###].",
        ]
    )

    plan_text = run_reasoning_with_vision(SYSTEM_PROMPT, user_text, image_inputs=info["image_inputs"] or None)
    return plan_text


def extract_requested_measurements_json(text: str) -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
    start = "---REQUESTED_MEASUREMENTS_JSON---"
    end = "---END_REQUESTED_MEASUREMENTS_JSON---"
    s_idx = text.find(start)
    e_idx = text.find(end)
    if s_idx == -1 or e_idx == -1 or e_idx <= s_idx:
        return [], text, "missing_json_block"
    inner = text[s_idx + len(start):e_idx].strip()
    if inner.startswith("```"):
        inner = inner[3:].strip()
        if inner.lower().startswith("json"):
            inner = inner[4:].strip()
    if inner.endswith("```"):
        inner = inner[:-3].strip()
    try:
        payload = json.loads(inner)
    except Exception as e:
        cleaned = _strip_json_block(text, s_idx, e_idx, start, end)
        return [], cleaned, f"json_parse_error:{e}"
    items = payload.get("requested_measurements")
    if not isinstance(items, list):
        cleaned = _strip_json_block(text, s_idx, e_idx, start, end)
        return [], cleaned, "json_missing_requested_measurements"
    cleaned = _strip_json_block(text, s_idx, e_idx, start, end)
    return items, cleaned, None


def _strip_json_block(text: str, s_idx: int, e_idx: int, start: str, end: str) -> str:
    lines = text.splitlines()
    start_line = text[:s_idx].count("\n")
    end_line = text[:e_idx].count("\n")
    remove_start = start_line
    remove_end = end_line
    if remove_start > 0:
        prev = lines[remove_start - 1].strip().lower()
        if "requested measurements json" in prev or "machine-readable" in prev:
            remove_start -= 1
    kept = [
        line for i, line in enumerate(lines)
        if i < remove_start or i > remove_end
    ]
    return "\n".join(kept).strip()
