from __future__ import annotations
import os
import re
from typing import Dict, Any, List, Tuple
import json
from .config import SETTINGS
from .case_store import (
    list_measurements, list_notes, list_attachments,
    list_baselines, list_baseline_measurements,
    list_expected_ranges
)
from .guardrails import (
    is_board_specific_question, has_required_evidence, refusal_message_missing_evidence
)
from .oai import embed_text, run_reasoning_with_vision
from .rag import query as rag_query
from .prompts import SYSTEM_PROMPT
from .netlist import load_netlist, choose_primary_power_rail, extract_net_tokens, canonicalize_net_name, suggest_nets, _expected_kb_paths
from .net_refs import get_measure_points, measurement_points_for_net
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

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


def _load_kb_boardview_images(case: Dict[str, Any], board_id: str, model: str, limit: int = 24) -> List[Dict[str, Any]]:
    kb_paths = _expected_kb_paths(case, board_id, model)
    if not kb_paths:
        return []
    try:
        limit = int(os.getenv("BOARDVIEW_SCREENS_MAX_IMAGES", str(limit)))
    except Exception:
        pass
    images: List[Dict[str, Any]] = []
    bv_dirs = [p for p in kb_paths if os.path.basename(p).lower() == "boardview_screens"]
    if not bv_dirs:
        return []
    cache_root = os.path.join(SETTINGS.data_dir, "boardview_screens_cache", board_id or "unknown")
    os.makedirs(cache_root, exist_ok=True)

    def _read_image_bytes(path: str) -> Tuple[bytes, str] | None:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ext == ".webp":
            mime = "image/webp"
        elif ext == ".gif":
            mime = "image/gif"
        else:
            mime = "image/png"
        try:
            with open(path, "rb") as f:
                return f.read(), mime
        except Exception:
            return None

    def _pdf_to_images(pdf_path: str, page_limit: int = 20) -> List[str]:
        if fitz is None:
            return []
        try:
            page_limit = int(os.getenv("BOARDVIEW_SCREENS_MAX_PAGES", str(page_limit)))
        except Exception:
            pass
        def _suppress_stderr():
            import contextlib
            import os
            @contextlib.contextmanager
            def _ctx():
                fd = None
                try:
                    fd = os.dup(2)
                    with open(os.devnull, "w") as devnull:
                        os.dup2(devnull.fileno(), 2)
                        yield
                except Exception:
                    yield
                finally:
                    try:
                        if fd is not None:
                            os.dup2(fd, 2)
                            os.close(fd)
                    except Exception:
                        pass
            return _ctx()
        try:
            with open(pdf_path, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                return []
        except Exception:
            return []
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        out_paths = []
        try:
            try:
                fitz.TOOLS.set_verbosity(0)
            except Exception:
                pass
            with _suppress_stderr():
                doc = fitz.open(pdf_path)
        except Exception:
            return []
        for i in range(min(page_limit, len(doc))):
            out_path = os.path.join(cache_root, f"{base}_p{i+1}.png")
            if not os.path.exists(out_path):
                try:
                    with _suppress_stderr():
                        page = doc[i]
                        pix = page.get_pixmap(dpi=200)
                    pix.save(out_path)
                except Exception:
                    continue
            out_paths.append(out_path)
        try:
            doc.close()
        except Exception:
            pass
        return out_paths

    candidates: List[str] = []
    for d in bv_dirs:
        try:
            for name in os.listdir(d):
                path = os.path.join(d, name)
                if os.path.isfile(path):
                    candidates.append(path)
        except Exception:
            continue
    candidates.sort()
    for path in candidates:
        if len(images) >= limit:
            break
        ext = os.path.splitext(path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            payload = _read_image_bytes(path)
            if payload:
                b, mime = payload
                images.append({"bytes": b, "mime": mime, "detail": "high"})
            continue
        if ext == ".pdf":
            for img_path in _pdf_to_images(path):
                if len(images) >= limit:
                    break
                payload = _read_image_bytes(img_path)
                if payload:
                    b, mime = payload
                    images.append({"bytes": b, "mime": mime, "detail": "high"})
    return images

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
    case_id = (case.get("case_id") or "").strip()
    m = re.search(r"\b\d{3}-\d{5}(?:_\d{3}-\d{5})?\b", case_id)
    return m.group(0) if m else ""


def _infer_model(case: Dict[str, Any]) -> str:
    model = (case.get("model") or "").strip()
    case_id = (case.get("case_id") or "").strip()
    m = re.search(r"\bA\d{4}\b", f"{model} {case_id}")
    return m.group(0) if m else model


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

def _build_expected_ranges_context(board_id: str) -> str:
    ranges = list_expected_ranges(board_id) if board_id else []
    if not ranges:
        ranges = []
    lines = ["\nEXPECTED RANGES (board-specific; label source):"]
    for r in ranges[:200]:
        unit = f" {r['unit']}" if r.get("unit") else ""
        if r.get("expected_min") == r.get("expected_max"):
            expected = f"{r.get('expected_min','')}{unit}".strip()
        else:
            expected = f"{r.get('expected_min','')}–{r.get('expected_max','')}{unit}".strip()
        src = r.get("source") or "unknown"
        note = f" (note: {r['note']})" if r.get("note") else ""
        lines.append(f"- {r['net']} | {r['measurement_type']} | expected: {expected} | source: {src}{note}")

    if board_id:
        for b in list_baselines():
            if b.get("board_id") != board_id:
                continue
            meas = list_baseline_measurements(b["baseline_id"])
            for m in meas[:200]:
                tokens = extract_net_tokens(m.get("name") or "")
                if not tokens:
                    continue
                net = canonicalize_net_name(tokens[0])
                if not net:
                    continue
                name_l = f"{m.get('name','')} {m.get('note','')}".lower()
                mtype = "voltage"
                if "diode" in name_l:
                    mtype = "diode"
                elif "ohm" in name_l or "resistance" in name_l or "r2g" in name_l:
                    mtype = "resistance"
                elif "amp" in name_l or "current" in name_l:
                    mtype = "current"
                elif "hz" in name_l or "freq" in name_l:
                    mtype = "frequency"
                unit = f" {m.get('unit')}" if m.get("unit") else ""
                expected = f"{m.get('value','')}{unit}".strip()
                note = f" (note: {m.get('note')})" if m.get("note") else ""
                lines.append(f"- {net} | {mtype} | expected: {expected} | source: baseline{note}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)

def _build_no_power_guidance(case: Dict[str, Any], board_id: str, model: str) -> str:
    symptom = (case.get("symptom") or "").strip().lower()
    if symptom != "no power":
        return ""
    device_family = (case.get("device_family") or "").strip().lower()
    if not device_family and "iphone" in model.lower():
        device_family = "iphone"
    if device_family != "iphone":
        return ""
    nets, _ = load_netlist(board_id=board_id, case=case)
    if not nets:
        return ""
    ranges = list_expected_ranges(board_id) if board_id else []
    ranges_by_net: Dict[str, List[Dict[str, Any]]] = {}
    for r in ranges:
        net = r.get("net") or ""
        ranges_by_net.setdefault(net, []).append(r)

    def _format_expected(net: str) -> str:
        items = ranges_by_net.get(net, [])
        if not items:
            return "expected: (none)"
        parts = []
        for r in items[:4]:
            unit = f" {r['unit']}" if r.get("unit") else ""
            expected = f"{r.get('expected_min','')}–{r.get('expected_max','')}{unit}".strip()
            parts.append(f"{r['measurement_type']} {expected} (source={r.get('source','unknown')})")
        return "expected: " + "; ".join(parts)

    def _points(net: str) -> str:
        pts = measurement_points_for_net(board_id, net, case=case, k=6)
        if pts:
            return ", ".join(pts)
        return "(no boardview points listed)"

    def _pick_by_patterns(patterns: List[str], limit: int) -> List[str]:
        out: List[str] = []
        seen = set()
        for pat in patterns:
            for n in nets:
                if n in seen:
                    continue
                if pat in n:
                    seen.add(n)
                    out.append(n)
                    if len(out) >= limit:
                        return out
        return out

    usb_input = _pick_by_patterns(
        ["PPVBUS", "PP_VBUS", "USB_VBUS", "VBUS", "PPDCIN", "PP5V", "PPADAPTER"],
        limit=3,
    )
    batt_main = _pick_by_patterns(
        ["PP_BATT", "PPBATT", "PP_VDD_MAIN", "PPVDD_MAIN", "PP_BATT_VCC"],
        limit=3,
    )
    always_on = _pick_by_patterns(
        ["_AON", "_ALWAYS", "PP1V8", "PP0V9", "PP0V8", "PP1V2", "PP2V8", "PP3V0"],
        limit=4,
    )
    ordered = []
    for group in (usb_input, batt_main, always_on):
        for n in group:
            if n not in ordered:
                ordered.append(n)

    if not ordered:
        return ""

    lines = [
        "",
        f"NO POWER GUIDANCE (IPHONE, board_id={board_id})",
        "Use only the nets listed here if they exist in the netlist.",
    ]
    for n in ordered:
        lines.append(f"- {n} | points: {_points(n)} | {_format_expected(n)}")
    return "\n".join(lines)
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

    ctx = (
        build_case_context(case)
        + _build_baseline_context(model=model, board_id=board_id)
        + _build_expected_ranges_context(board_id=board_id)
        + _build_no_power_guidance(case=case, board_id=board_id, model=model)
    )

    evidence_lines = ["RETRIEVED CONTEXT (cite Source file + page):"]
    for h in hits:
        m = h["metadata"] or {}
        src = m.get("source_file")
        page = m.get("page")
        label = f"{src}" + (f" p.{page}" if page else "")
        source_tag = m.get("evidence_source") or "unknown"
        evidence_lines.append(f"\n--- {label} | source={source_tag} ---\n{h['document'][:1500]}")

    image_inputs: List[Dict[str, Any]] = []
    if include_images:
        for a in attachments:
            if a.get("type") in ("schematic", "boardview_screenshot"):
                try:
                    b, mime = _load_attachment_bytes(a["rel_path"])
                    image_inputs.append({"bytes": b, "mime": mime, "detail": "high"})
                except Exception:
                    continue
        if board_id:
            image_inputs.extend(_load_kb_boardview_images(case, board_id=board_id, model=model))

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
                    "key": "CHECK_<NETNAME>",
                    "net": "<NETNAME>",
                    "type": "voltage",
                    "prompt": "Measure <NETNAME> to GND",
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
            "   - every step MUST include:",
            "     * net name(s) from netlist summary",
            "     * where-to-probe refdes (if available from boardview)",
            "     * CONFIDENCE: <raw 0-1 score>",
            "     * EVIDENCE: <boardview|schematic|case_history|community>",
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
            "- Confidence scores must be raw numeric values between 0 and 1.",
            "- You MAY use community content, but it must be labeled EVIDENCE: community and should carry a lower confidence score.",
            "- If a NO POWER GUIDANCE block is present, follow its order and use only the nets listed there.",
            "- The JSON example uses <NETNAME> as a placeholder; replace it with a real net from the netlist summary.",
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
