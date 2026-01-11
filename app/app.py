from __future__ import annotations
import os
import json
import html
import re
import datetime
import streamlit as st

import sys
from pathlib import Path

# Ensure project root is on sys.path so `import boardbrain` works regardless of how Streamlit is launched.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

def _rerun():
    """Streamlit rerun compatibility across versions."""
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


from boardbrain.case_store import (
    create_case, list_cases, get_case, delete_case,
    add_measurement, add_note, list_measurements,
    save_attachment, list_attachments, init_db,
    add_chat_message, list_chat_messages,
    add_plan_version, get_latest_plan, list_plan_versions,
    set_requested_measurements, mark_requested_measurement_done, list_requested_measurements,
    get_case_delete_summary,
    add_expected_range, list_expected_ranges, update_expected_range, delete_expected_range,
)
from boardbrain.diagnose import answer_question, generate_plan, extract_requested_measurements_json
from boardbrain.chat_commands import parse_command
from boardbrain.measurement_parser import classify_and_parse
from boardbrain.plan_utils import parse_requested_measurements, build_aliases_for_key, normalize_requested_items
from boardbrain.netlist import (
    load_netlist,
    enforce_net_guardrail,
    suggest_nets,
    canonicalize_net_name,
    normalize_net_name,
    extract_net_tokens,
    split_measurement_key,
)
from boardbrain.net_refs import load_net_refs, get_measure_points, measurement_points_for_net, get_measurement_points_from_cache
from boardbrain.components import (
    load_component_index,
    extract_component_tokens,
    suggest_components,
    parse_component_measurements,
    enforce_component_guardrail,
)
from boardbrain.config import SETTINGS

st.set_page_config(page_title="BoardBrain v1.1", layout="wide")

init_db()
os.makedirs(SETTINGS.data_dir, exist_ok=True)
os.makedirs(SETTINGS.kb_raw_dir, exist_ok=True)

st.title("BoardBrain - Motherboard Troubleshooting Tool")

st.markdown(
    """
<style>
:root {
  --net-bg: #f2efe6;
  --net-fg: #2d2a24;
  --card-bg: #fbf8f1;
  --card-border: #e6dccb;
  --status-pending: #b46b00;
  --status-done: #1f7a3f;
  --status-other: #6a6a6a;
}
.net-token {
  background: var(--net-bg);
  color: var(--net-fg);
  padding: 2px 6px;
  border-radius: 6px;
  font-weight: 600;
  font-family: "IBM Plex Mono", "Menlo", "Consolas", monospace;
  font-size: 0.95em;
  border: 1px solid var(--card-border);
}
.req-card {
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 10px;
  padding: 10px 12px;
  margin: 10px 0;
}
.req-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}
.req-status {
  font-weight: 700;
  font-size: 0.85em;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--card-border);
  text-transform: uppercase;
}
.req-status.pending { color: var(--status-pending); }
.req-status.done { color: var(--status-done); }
.req-status.other { color: var(--status-other); }
.req-key {
  font-weight: 600;
  color: #2b2b2b;
}
.req-line {
  margin: 4px 0;
  color: #2b2b2b;
}
.req-label {
  font-weight: 700;
  margin-right: 6px;
}
.req-points {
  margin-top: 6px;
  font-size: 0.95em;
}
.evidence-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 999px;
  background: #efe6d6;
  color: #3a342b;
  border: 1px solid var(--card-border);
  font-size: 0.85em;
  font-family: "IBM Plex Mono", "Menlo", "Consolas", monospace;
}
</style>
""",
    unsafe_allow_html=True,
)

_SIGNAL_SUFFIXES = (
    "EN",
    "PWR",
    "CLK",
    "RST",
    "RESET",
    "SCL",
    "SDA",
    "INT",
    "SW",
    "PG",
    "PGOOD",
    "WAKE",
    "SLEEP",
    "BOOT",
    "ISENSE",
    "VSENSE",
)

def _strip_cheat_sheet(text: str) -> str:
    lines = text.splitlines()
    out = []
    skip = False
    for line in lines:
        if re.search(r"cheat sheet", line, re.IGNORECASE):
            skip = True
            continue
        if skip:
            if re.match(r"^[A-Z][A-Z0-9 /()_-]{3,}$", line.strip()):
                skip = False
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out).strip()


def _render_requested_measurements_section(
    plan_text: str,
    items: list,
    net_to_refdes: dict,
    known_refdes: set,
) -> str:
    if not items:
        return plan_text
    lines = plan_text.splitlines()
    out = []
    in_req = False
    req_header = "REQUESTED MEASUREMENTS (WHAT I NEED FROM YOU)"
    for line in lines:
        if re.search(r"REQUESTED MEASUREMENTS", line, re.IGNORECASE):
            in_req = True
            continue
        if in_req:
            if re.match(r"^[A-Z][A-Z0-9 /()_-]{3,}$", line.strip()):
                in_req = False
                out.append(line)
            else:
                continue
        if not in_req:
            out.append(line)
    block = [req_header]
    for item in items:
        meta = item.get("meta") or {}
        net = meta.get("net") or ""
        mtype = meta.get("type") or ""
        hint = meta.get("hint") or ""
        line = f"- KEY: {item.get('key')} | PROMPT: {item.get('prompt')} | TYPE: {mtype} | NET: {net}"
        if hint:
            line += f" | OPTIONAL HINT: {hint}"
        block.append(line)
        points = get_measurement_points_from_cache(net, net_to_refdes, known_refdes, limit=8)
        if points:
            block.append(f"  MEASUREMENT POINTS (BOARDVIEW): {', '.join(points)}")
        else:
            block.append("  MEASUREMENT POINTS (BOARDVIEW): (no boardview points listed) probe any large decoupling capacitor on NET")
    if out and out[-1].strip() != "":
        out.append("")
    out.extend(block)
    return "\n".join(out).strip()


_NET_TOKEN_RE = re.compile(r"\b[A-Z0-9_.+-]{3,}\b")

_EVIDENCE_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?EVIDENCE\s*:\s*(.+)$", re.IGNORECASE)
_EVIDENCE_PAGE_RE = re.compile(r"(?:p(?:age)?[.:]?\s*(\d+))", re.IGNORECASE)


def _strip_requested_measurements_block(text: str) -> str:
    lines = text.splitlines()
    out = []
    in_req = False
    for line in lines:
        if re.search(r"REQUESTED MEASUREMENTS", line, re.IGNORECASE):
            in_req = True
            continue
        if in_req:
            if re.match(r"^[A-Z][A-Z0-9 /()_-]{3,}$", line.strip()):
                in_req = False
                out.append(line)
            else:
                continue
        if not in_req:
            out.append(line)
    return "\n".join(out).strip()


def _format_evidence_label(raw: str) -> tuple[str, str]:
    src = raw.strip().strip("[]()")
    lower = src.lower()
    page = ""
    m = _EVIDENCE_PAGE_RE.search(src)
    if m:
        page = m.group(1)
        src = _EVIDENCE_PAGE_RE.sub("", src).strip()
    label = "Evidence"
    if "schematic" in lower or ".pdf" in lower:
        label = "Schematic"
    elif "boardview" in lower:
        label = "Boardview"
    else:
        filename = os.path.basename(src)
        if filename:
            label = f"Attachment: {filename}"
    if page:
        label = f"{label} p.{page}"
    return label, raw.strip()


def _format_evidence_line(line: str) -> str | None:
    m = _EVIDENCE_LINE_RE.match(line)
    if not m:
        return None
    raw = m.group(1).strip()
    label, full = _format_evidence_label(raw)
    prefix = html.escape(m.group(0).split(":", 1)[0])
    return (
        f"{prefix}: "
        f"<span class=\"evidence-tag\" title=\"{html.escape(full)}\">{html.escape(label)}</span>"
    )


def _wrap_net_tokens(escaped: str, known_nets: set) -> str:
    def _fallback_net(token: str) -> bool:
        if len(token) < 4:
            return False
        if not token.isupper():
            return False
        return "_" in token or any(ch.isdigit() for ch in token) or token.startswith("PP")
    def _sub(m: re.Match) -> str:
        token = m.group(0)
        if known_nets:
            if token in known_nets:
                return f'<span class="net-token">{token}</span>'
            return token
        if _fallback_net(token):
            return f'<span class="net-token">{token}</span>'
        return token
    return _NET_TOKEN_RE.sub(_sub, escaped)


def _render_text_html(text: str, known_nets: set, strip_requested: bool = True) -> str:
    if not text:
        return ""
    cleaned = html.unescape(text)
    cleaned = re.sub(r"</?span[^>]*>", "", cleaned)
    cleaned = cleaned.replace("<br>", "\n")
    if strip_requested:
        cleaned = _strip_requested_measurements_block(cleaned)
    out_lines: list[str] = []
    for line in cleaned.splitlines():
        evidence_html = _format_evidence_line(line)
        if evidence_html is not None:
            out_lines.append(evidence_html)
            continue
        escaped = html.escape(line)
        out_lines.append(_wrap_net_tokens(escaped, known_nets))
    return "<br>".join(out_lines)


def _load_plan_state(case_id: str) -> None:
    if st.session_state.get("active_case_id") != case_id:
        st.session_state["active_case_id"] = case_id
        st.session_state["plan_state"] = None
    if st.session_state.get("plan_state") is None:
        plan_markdown = get_latest_plan(case_id)
        requested = list_requested_measurements(case_id)
        history = list_plan_versions(case_id)
        st.session_state["plan_state"] = {
            "plan_markdown": plan_markdown,
            "requested_measurements": requested,
            "plan_history": history,
        }


def _update_plan_state(case_id: str, plan_markdown: str) -> None:
    if plan_markdown is None:
        plan_markdown = get_latest_plan(case_id)
    st.session_state["plan_state"] = {
        "plan_markdown": plan_markdown,
        "requested_measurements": list_requested_measurements(case_id),
        "plan_history": list_plan_versions(case_id),
    }


def _next_pending_requested(plan_state: dict) -> dict | None:
    for r in plan_state.get("requested_measurements", []):
        if r.get("status") == "pending":
            return r
    return None


def _build_debug_report(
    case: dict,
    net_meta: dict,
    net_refs_meta: dict,
    plan_state: dict,
    test_net: str,
    test_result: str,
    guardrail_report: dict | None,
    test_points: list[str] | None,
) -> str:
    def _dedupe_paths(paths: list[str]) -> list[str]:
        seen = set()
        out = []
        for p in paths:
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
        return out
    lines = []
    lines.append("BoardBrain Debug Report")
    lines.append("")
    lines.append("Case")
    lines.append(f"- case_id: {case.get('case_id','')}")
    lines.append(f"- model: {case.get('model','')}")
    lines.append(f"- board_id: {case.get('board_id','')}")
    lines.append("")
    lines.append("KB Paths")
    lines.append(f"- kb_raw_dir: {SETTINGS.kb_raw_dir}")
    kb_paths = _dedupe_paths(net_meta.get("kb_paths") or [])
    if kb_paths:
        for p in kb_paths:
            lines.append(f"- {p}")
    else:
        reason = "none detected"
        if not case.get("board_id") and not case.get("model"):
            reason = "board_id/model missing"
        if net_meta.get("kb_paths_reason"):
            reason = net_meta.get("kb_paths_reason")
        lines.append(f"- (none detected: {reason})")
    lines.append("")
    lines.append("Netlist Status")
    lines.append(f"- source: {net_meta.get('source','unknown')}")
    lines.append(f"- source_reason: {net_meta.get('source_reason','')}")
    lines.append(f"- net_count: {net_meta.get('net_count',0)}")
    lines.append(f"- pp_net_count: {net_meta.get('pp_net_count',0)}")
    lines.append(f"- signal_net_count: {net_meta.get('signal_net_count',0)}")
    lines.append(f"- cache_path: {net_meta.get('cache_path','')}")
    if net_meta.get("updated_at"):
        lines.append(f"- updated_at: {net_meta.get('updated_at')}")
    lines.append("Boardview Ingest Report")
    lines.append(f"- report_path: {net_meta.get('ingest_report_path','')}")
    lines.append(f"- boardview_files_count: {net_meta.get('boardview_files_count',0)}")
    if net_meta.get("boardview_files_preview"):
        for p in net_meta.get("boardview_files_preview", [])[:3]:
            lines.append(f"- boardview_file: {p}")
    lines.append(f"- boardview_selected_file: {net_meta.get('boardview_file_used','')}")
    if net_meta.get("boardview_files_used"):
        for p in net_meta.get("boardview_files_used", []):
            lines.append(f"- boardview_selected_file: {p}")
    lines.append(f"- boardview_parser_used: {net_meta.get('boardview_parser_used','')}")
    lines.append(f"- boardview_parse_status: {net_meta.get('boardview_parse_status','')}")
    if net_meta.get("boardview_parse_error"):
        lines.append(f"- boardview_parse_error: {net_meta.get('boardview_parse_error')}")
    lines.append("")
    lines.append("Net→RefDes Index Status")
    lines.append(f"- source: {net_refs_meta.get('source','unknown')}")
    lines.append(f"- pairs_count: {net_refs_meta.get('pairs_count',0)}")
    lines.append(f"- cache_path: {net_refs_meta.get('cache_path','')}")
    if net_refs_meta.get("updated_at"):
        lines.append(f"- updated_at: {net_refs_meta.get('updated_at')}")
    lines.append("")
    lines.append("Component Index Status")
    lines.append(f"- source: {comp_meta.get('source','unknown')}")
    lines.append(f"- component_count: {comp_meta.get('component_count',0)}")
    lines.append(f"- cache_path: {comp_meta.get('cache_path','')}")
    if comp_meta.get("updated_at"):
        lines.append(f"- updated_at: {comp_meta.get('updated_at')}")
    if comp_meta.get("components_preview"):
        lines.append("- top_components:")
        for c in comp_meta.get("components_preview")[:50]:
            lines.append(f"  - {c}")
        prefix_counts = {}
        for ref in comp_meta.get("components_preview_full", []) or comp_meta.get("components_preview", []):
            if ref.startswith("FB"):
                prefix = "FB"
            elif ref.startswith("TP"):
                prefix = "TP"
            else:
                prefix = ref[:1]
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        if prefix_counts:
            lines.append("- prefix_histogram:")
            lines.append("  - " + ", ".join(f"{k}: {v}" for k, v in sorted(prefix_counts.items())))
    if comp_meta.get("component_count", 0) and comp_meta.get("component_count", 0) < 200:
        lines.append("- component_index_warning: Component index seems incomplete; verify PDFs are selectable text, or add component-identification PDFs to kb_raw/.../reference and re-ingest.")
    lines.append("")
    lines.append("Net Validation Test")
    lines.append(f"- input: {test_net}")
    lines.append(f"- result: {test_result}")
    if test_result == "NOT FOUND" and test_net:
        suggestions = suggest_nets(case.get("board_id", ""), test_net, k=8, case=case)
        if suggestions:
            lines.append(f"- suggestions: {', '.join(suggestions)}")
    lines.append("")
    lines.append("Net→RefDes Test Points")
    if test_points:
        lines.append(f"- count: {len(test_points)}")
        lines.append(f"- points: {', '.join(test_points)}")
        prefix_counts = {}
        for ref in test_points:
            if ref.startswith("TP"):
                prefix = "TP"
            elif ref.startswith("FB"):
                prefix = "FB"
            else:
                prefix = ref[:1]
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        if prefix_counts:
            lines.append(f"- prefix_counts: {', '.join(f'{k}:{v}' for k, v in sorted(prefix_counts.items()))}")
    else:
        lines.append("- count: 0")
        lines.append("- points: (none)")
    lines.append("")
    lines.append("Top 50 Nets")
    top_nets = sorted(list(net_meta.get("nets_preview", [])))[:50]
    if top_nets:
        for n in top_nets:
            lines.append(f"- {n}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Top 25 Non-PP Nets")
    non_pp = net_meta.get("non_pp_preview") or []
    if non_pp:
        for n in non_pp[:25]:
            lines.append(f"- {n}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Top 25 Signal Nets (suffix match)")
    suffix_preview = net_meta.get("signal_suffix_preview") or []
    if suffix_preview:
        for n in suffix_preview[:25]:
            lines.append(f"- {n}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("PlanState")
    history = plan_state.get("plan_history") or []
    if history:
        latest = history[0]
        lines.append(f"- latest_plan_version: v{latest.get('version')} @ {latest.get('created_at')}")
    else:
        lines.append("- latest_plan_version: (none)")
    reqs = plan_state.get("requested_measurements") or []
    lines.append("- requested_measurements:")
    if reqs:
        for r in reqs:
            lines.append(f"  - {r.get('key')} [{r.get('status')}] {r.get('prompt')}")
    else:
        lines.append("  - (none)")
    next_req = _next_pending_requested(plan_state)
    if next_req:
        lines.append(f"- next_pending: {next_req.get('key')}")
    else:
        lines.append("- next_pending: (none)")
    lines.append(f"- requested_measurement_count: {len(reqs)}")
    lines.append(f"- requested_measurements_parsed_count: {st.session_state.get('requested_measurements_parsed_count', 0)}")
    lines.append(f"- requested_measurements_parse_failed: {st.session_state.get('requested_measurements_parse_failed', False)}")
    lines.append(f"- requested_measurements_parse_error: {st.session_state.get('requested_measurements_parse_error','')}")
    if st.session_state.get("component_validation_results"):
        lines.append("- component_validation_results:")
        lines.append(json.dumps(st.session_state.get("component_validation_results"), indent=2))
    lines.append(f"- last_message_classification: {st.session_state.get('last_message_classification','')}")
    lines.append(f"- net_confirmation_pending: {st.session_state.get('net_confirmation_pending', False)}")
    lines.append(f"- auto_update_triggered: {st.session_state.get('auto_update_triggered', False)}")
    lines.append(f"- plan_update_reason: {st.session_state.get('plan_update_reason','')}")
    if st.session_state.get("parsed_measurements"):
        lines.append("- parsed_measurements:")
        lines.append(json.dumps(st.session_state.get("parsed_measurements"), indent=2))
    if st.session_state.get("rejected_measurement_reasons"):
        lines.append("- rejected_measurement_reasons:")
        lines.append(json.dumps(st.session_state.get("rejected_measurement_reasons"), indent=2))
    if st.session_state.get("completed_measurement_keys"):
        lines.append(f"- completed_measurement_keys: {', '.join(st.session_state['completed_measurement_keys'])}")
    if st.session_state.get("invalid_nets_detected"):
        lines.append(f"- invalid_nets_detected: {', '.join(st.session_state['invalid_nets_detected'])}")
    if st.session_state.get("net_validation_results"):
        lines.append("- net_validation_results:")
        lines.append(json.dumps(st.session_state.get("net_validation_results"), indent=2))
    lines.append("")
    lines.append("Rail-Name Guardrail")
    if guardrail_report:
        lines.append(f"- last_run_time: {guardrail_report.get('last_run_time','')}")
        if guardrail_report.get("classification"):
            lines.append(f"- classification: {guardrail_report.get('classification')}")
        invalid = guardrail_report.get("invalid_nets_detected") or []
        lines.append(f"- invalid_nets_detected: {len(invalid)}")
        if invalid:
            lines.append(f"  - {', '.join(invalid)}")
        invalid_items = guardrail_report.get("invalid_plan_items") or []
        if invalid_items:
            lines.append(f"- invalid_plan_items: {', '.join(invalid_items)}")
        fixes = guardrail_report.get("auto_fixes_applied") or []
        if fixes:
            lines.append("- auto_fixes_applied:")
            for f in fixes:
                lines.append(f"  - {f.get('from')} -> {f.get('to')} ({f.get('reason')})")
        suggestions = guardrail_report.get("suggestions") or {}
        if suggestions:
            lines.append("- suggestions:")
            for k, v in suggestions.items():
                lines.append(f"  - {k}: {', '.join(v)}")
        invalid_refdes = guardrail_report.get("invalid_refdes_detected") or []
        if invalid_refdes:
            lines.append(f"- invalid_refdes_detected: {', '.join(invalid_refdes)}")
            lines.append(f"- refdes_replaced_count: {guardrail_report.get('refdes_replaced_count', 0)}")
    else:
        lines.append("- last_run_time: (none)")
    return "\n".join(lines)

with st.sidebar:
    st.header("BoardBrain")
    mode = st.radio("Mode", ["Cases", "Baselines"], index=0)

if mode == "Baselines":
    from boardbrain.case_store import (
        create_baseline, list_baselines, get_baseline,
        add_baseline_measurement, list_baseline_measurements,
        save_baseline_attachment, list_baseline_attachments
    )

    st.sidebar.header("Baselines")
    baselines = list_baselines()
    b_ids = ["(new baseline)"] + [b["baseline_id"] for b in baselines]
    bsel = st.sidebar.selectbox("Open baseline", b_ids)

    if bsel == "(new baseline)":
        st.subheader("Create baseline")
        st.caption("Example ID: A2338_820-02020_KG_YYYY-MM-DD")
        baseline_id = st.text_input("Baseline ID", value="A2338_820-02020_KG_YYYY-MM-DD")
        device_family = st.text_input("Device family", value="MacBook")
        model = st.text_input("Model", value="A2338")
        board_id = st.text_input("Board ID", value="820-02020")
        quality = st.selectbox("Quality", ["GOLD", "SILVER", "BRONZE"], index=1)
        source = st.text_input("Source", value="known-good donor / iCloud locked")
        boot_state = st.text_input("Boot state", value="activation/recovery")
        notes = st.text_area("Notes", value="")
        if st.button("Create / Open baseline"):
            create_baseline(
                baseline_id=baseline_id,
                device_family=device_family,
                model=model,
                board_id=board_id,
                quality=quality,
                source=source,
                boot_state=boot_state,
                notes=notes,
            )
            _rerun()

    if bsel == "(new baseline)":
        st.info("Create a baseline in the sidebar to begin.")
        st.stop()

    b = get_baseline(bsel)
    if not b:
        st.error("Baseline not found.")
        st.stop()

    st.subheader("Baseline")
    st.write(f"**{b['baseline_id']}** — {b.get('model','')} {b.get('board_id','')}")
    st.write(f"Quality: {b.get('quality','')} | Source: {b.get('source','')} | Boot: {b.get('boot_state','')}")
    if b.get("notes"):
        st.write(b["notes"])

    st.divider()
    st.subheader("Add baseline measurements")
    bm_name = st.text_input("Measurement name", value="PPBUS_AON to GND")
    bm_value = st.text_input("Value", value="")
    bm_unit = st.text_input("Unit (optional)", value="ohms")
    bm_note = st.text_input("Note (optional)", value="meter polarity: red to rail")
    if st.button("Add baseline measurement"):
        add_baseline_measurement(b["baseline_id"], bm_name, bm_value, bm_unit, bm_note)
        _rerun()

    st.subheader("Baseline attachments")
    st.caption("Store reference screenshots/photos/scope captures for this board.")
    ba_type = st.selectbox("Type", ["board_photo", "scope", "thermal", "boardview_screenshot", "schematic_pdf", "other"], key="ba_type")
    bup = st.file_uploader("Upload file", key="bup")
    if st.button("Save baseline attachment") and bup is not None:
        save_baseline_attachment(b["baseline_id"], bup.name, bup.getvalue(), ba_type)
        _rerun()

    bats = list_baseline_attachments(b["baseline_id"])
    if bats:
        st.write("Saved baseline attachments:")
        for a in bats:
            st.write(f"- **{a['type']}**: {a['filename']}")

    st.divider()
    st.subheader("Baseline measurements")
    bmeas = list_baseline_measurements(b["baseline_id"])
    if bmeas:
        for m in bmeas[-40:]:
            st.write(f"- {m['name']}: {m['value']} {m.get('unit','')} — {m.get('note','')}")
    else:
        st.write("No baseline measurements yet.")

    st.stop()


with st.sidebar:
    st.header("Cases")
    if "confirm_delete_case_id" not in st.session_state:
        st.session_state["confirm_delete_case_id"] = None
    if st.session_state.get("case_deleted_message"):
        st.success(st.session_state["case_deleted_message"])
        st.session_state["case_deleted_message"] = None
    cases = list_cases()
    case_ids = ["(new case)"] + [c["case_id"] for c in cases]
    case_map = {c["case_id"]: c for c in cases}

    def _case_label(case_id: str) -> str:
        if case_id == "(new case)":
            return case_id
        c = case_map.get(case_id, {})
        title = c.get("title") or case_id
        created_at = c.get("created_at") or ""
        short_date = created_at.split("T")[0] if created_at else ""
        return f"{title} — {short_date}" if short_date else title

    selected = st.selectbox("Open case", case_ids, format_func=_case_label)

    if selected == "(new case)":
        st.subheader("Create case")
        st.caption("Format: A2338_820-02020_NoPower_YYYY-MM-DD")
        kb_root = SETTINGS.kb_raw_dir
        families = []
        kb_tree = {}
        if os.path.isdir(kb_root):
            for fam in sorted(os.listdir(kb_root)):
                if fam.startswith("."):
                    continue
                fam_path = os.path.join(kb_root, fam)
                if not os.path.isdir(fam_path):
                    continue
                families.append(fam)
                kb_tree[fam] = {}
                for model in sorted(os.listdir(fam_path)):
                    if model.startswith("."):
                        continue
                    model_path = os.path.join(fam_path, model)
                    if not os.path.isdir(model_path):
                        continue
                    boards = [
                        b for b in sorted(os.listdir(model_path))
                        if os.path.isdir(os.path.join(model_path, b)) and not b.startswith(".")
                    ]
                    if boards:
                        kb_tree[fam][model] = boards
        device_family = st.selectbox("Device family", families, index=0 if families else None)
        models = sorted(kb_tree.get(device_family, {}).keys()) if device_family else []
        model = st.selectbox("Model", models, index=0 if models else None)
        boards = kb_tree.get(device_family, {}).get(model, []) if device_family and model else []
        board_id = st.selectbox("Board ID", boards, index=0 if boards else None)
        c_model = model or ""
        c_board = board_id or ""
        c_kind = st.text_input("Issue tag", value="NoPower")
        c_date = st.text_input("Date", value="YYYY-MM-DD")
        case_id = st.text_input("Case ID", value=f"{c_model}_{c_board}_{c_kind}_{c_date}")
        title = st.text_input("Title", value="A2337 No Power")
        symptom = st.text_area("Symptom", value="USB‑C: 5V ~0.20A, no power")
        if st.button("Create / Open"):
            create_case(case_id=case_id, title=title, device_family=device_family, model=model, board_id=c_board, symptom=symptom)
            _rerun()
    else:
        st.divider()
        st.subheader("Danger Zone")
        st.caption("Deleting a case is permanent. This removes it from the database and deletes its files in data/cases/<case_id>.")
        if st.button("Delete case"):
            st.session_state["confirm_delete_case_id"] = selected
        if st.session_state.get("confirm_delete_case_id") == selected:
            summary = get_case_delete_summary(selected)
            st.warning("This cannot be undone.")
            st.write("This will permanently delete:")
            st.write(f"- {summary['chat_messages']} chat messages")
            st.write(f"- {summary['plan_versions']} plan versions")
            st.write(f"- {summary['requested_measurements']} requested measurements")
            st.write(f"- {summary['measurements']} measurements")
            st.write(f"- {summary['notes']} notes")
            st.write(f"- {summary['attachments']} attachments (and the case folder)")
            if summary.get("case_dir_exists"):
                st.write(f"- case folder files: {summary.get('case_dir_files', 0)}")
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Yes, delete permanently", key="confirm_delete_case"):
                    deleted = delete_case(selected)
                    st.session_state["confirm_delete_case_id"] = None
                    if deleted:
                        st.session_state["case_deleted_message"] = f"Deleted case {selected}."
                        st.session_state["Open case"] = "(new case)"
                        st.session_state["active_case_id"] = None
                        st.session_state["plan_state"] = None
                        st.session_state["known_nets_case_id"] = None
                        st.session_state["known_nets"] = set()
                        st.session_state["known_nets_meta"] = {}
                        st.session_state["net_refs_case_id"] = None
                        st.session_state["net_refs"] = {}
                        st.session_state["net_refs_meta"] = {}
                        st.session_state["debug_report"] = None
                        st.session_state["guardrail_report"] = None
                        _rerun()
                    else:
                        st.info("Case not found. Nothing was deleted.")
            with c2:
                if st.button("Cancel", key="cancel_delete_case"):
                    st.session_state["confirm_delete_case_id"] = None

if selected == "(new case)":
    st.info("Create a case in the sidebar to begin.")
    st.stop()

case = get_case(selected)
if not case:
    st.error("Case not found.")
    st.stop()

if st.session_state.get("known_nets_case_id") != case["case_id"]:
    known_nets, net_meta = load_netlist(board_id=case.get("board_id", ""), model=case.get("model", ""), case=case)
    st.session_state["known_nets_case_id"] = case["case_id"]
    st.session_state["known_nets"] = known_nets
    st.session_state["known_nets_meta"] = net_meta
else:
    known_nets = st.session_state.get("known_nets", set())
    net_meta = st.session_state.get("known_nets_meta", {})
net_meta["nets_preview"] = sorted(list(known_nets))[:50]
pp_nets = sorted([n for n in known_nets if n.startswith("PP")])
signal_nets = sorted([n for n in known_nets if not n.startswith("PP")])
net_meta["pp_net_count"] = len(pp_nets)
net_meta["signal_net_count"] = len(signal_nets)
net_meta["non_pp_preview"] = signal_nets[:25]
net_meta["signal_suffix_preview"] = [
    n for n in signal_nets if any(n.endswith(suf) or n.endswith(f"_{suf}") for suf in _SIGNAL_SUFFIXES)
][:25]
_load_plan_state(case["case_id"])

if st.session_state.get("known_components_case_id") != case["case_id"]:
    known_components, comp_meta = load_component_index(
        board_id=case.get("board_id", ""), model=case.get("model", ""), case=case
    )
    st.session_state["known_components_case_id"] = case["case_id"]
    st.session_state["known_components"] = known_components
    st.session_state["components_meta"] = comp_meta
else:
    known_components = st.session_state.get("known_components", set())
    comp_meta = st.session_state.get("components_meta", {})
comp_meta["components_preview"] = sorted(list(known_components))[:50]
comp_meta["components_preview_full"] = sorted(list(known_components))

if st.session_state.get("net_refs_case_id") != case["case_id"]:
    net_refs, net_refs_meta = load_net_refs(
        board_id=case.get("board_id", ""), model=case.get("model", ""), case=case
    )
    st.session_state["net_refs_case_id"] = case["case_id"]
    st.session_state["net_refs"] = net_refs
    st.session_state["net_refs_meta"] = net_refs_meta
else:
    net_refs = st.session_state.get("net_refs", {})
    net_refs_meta = st.session_state.get("net_refs_meta", {})

with st.sidebar.expander("Debug / Netlist / Plan State", expanded=False):
    st.write(f"Case: {case.get('case_id','')}")
    st.write(f"Model: {case.get('model','')} | Board: {case.get('board_id','')}")
    kb_paths = list(dict.fromkeys(net_meta.get("kb_paths") or []))
    st.write("KB paths:")
    st.write(f"KB_RAW_DIR: {SETTINGS.kb_raw_dir}")
    if kb_paths:
        for p in kb_paths:
            st.write(f"- {p}")
    else:
        reason = "none detected"
        if not case.get("board_id") and not case.get("model"):
            reason = "board_id/model missing"
        if net_meta.get("kb_paths_reason"):
            reason = net_meta.get("kb_paths_reason")
        st.write(f"- (none detected: {reason})")
    st.write(f"Netlist source: {net_meta.get('source','unknown')}")
    st.write(f"Source reason: {net_meta.get('source_reason','')}")
    st.write(f"Net count: {net_meta.get('net_count',0)}")
    st.write(f"PP nets: {net_meta.get('pp_net_count',0)} | Signal nets: {net_meta.get('signal_net_count',0)}")
    st.write(f"Cache: {net_meta.get('cache_path','')}")
    if net_meta.get("updated_at"):
        st.write(f"Updated: {net_meta.get('updated_at')}")
    st.write("Boardview Ingest Report:")
    st.write(f"- report_path: {net_meta.get('ingest_report_path','')}")
    st.write(f"- boardview_files_count: {net_meta.get('boardview_files_count',0)}")
    if net_meta.get("boardview_files_preview"):
        st.write("- boardview_files_preview:")
        for p in net_meta.get("boardview_files_preview", [])[:3]:
            st.write(f"  - {p}")
    st.write(f"- boardview_selected_file: {net_meta.get('boardview_file_used','')}")
    st.write(f"- boardview_parser_used: {net_meta.get('boardview_parser_used','')}")
    st.write(f"- boardview_parse_status: {net_meta.get('boardview_parse_status','')}")
    if net_meta.get("boardview_parse_error"):
        st.write(f"- boardview_parse_error: {net_meta.get('boardview_parse_error')}")
    if st.button("Force reload netlist", key="force_reload_netlist"):
        st.session_state["known_nets_case_id"] = None
        st.session_state["known_nets"] = set()
        st.session_state["known_nets_meta"] = {}
        _rerun()
    st.write("Net→RefDes Index Status:")
    st.write(f"- source: {net_refs_meta.get('source','unknown')}")
    st.write(f"- pairs_count: {net_refs_meta.get('pairs_count',0)}")
    st.write(f"- cache_path: {net_refs_meta.get('cache_path','')}")
    if net_refs_meta.get("updated_at"):
        st.write(f"- updated_at: {net_refs_meta.get('updated_at')}")
    st.write("Component Index Status:")
    st.write(f"- source: {comp_meta.get('source','unknown')}")
    st.write(f"- component_count: {comp_meta.get('component_count',0)}")
    st.write(f"- cache_path: {comp_meta.get('cache_path','')}")
    if comp_meta.get("updated_at"):
        st.write(f"- updated_at: {comp_meta.get('updated_at')}")
    if comp_meta.get("components_preview"):
        st.write("Top 50 components:")
        st.code("\n".join(comp_meta.get("components_preview")[:50]))
        prefix_counts = {}
        for ref in comp_meta.get("components_preview_full", []) or comp_meta.get("components_preview", []):
            if ref.startswith("FB"):
                prefix = "FB"
            elif ref.startswith("TP"):
                prefix = "TP"
            else:
                prefix = ref[:1]
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        if prefix_counts:
            st.write("Prefix histogram:")
            st.code(", ".join(f"{k}: {v}" for k, v in sorted(prefix_counts.items())))
    if comp_meta.get("component_count", 0) and comp_meta.get("component_count", 0) < 200:
        st.warning("Component index seems incomplete; verify PDFs are selectable text, or add component-identification PDFs to kb_raw/.../reference and re-ingest.")

    test_net = st.text_input("Test net name", value="", key="debug_test_net")
    normalized_test = normalize_net_name(test_net) if test_net else ""
    test_result = "NOT FOUND"
    if test_net and normalized_test in known_nets:
        test_result = "VALID"
    if test_net:
        st.write(f"Normalized: {normalized_test} — {test_result}")
        if test_result == "NOT FOUND":
            suggestions = suggest_nets(case.get("board_id", ""), test_net, k=8, case=case)
            if suggestions:
                st.write(f"Closest matches: {', '.join(suggestions)}")
        test_points = get_measure_points(case.get("board_id", ""), test_net, case=case, k=10)
        if test_points:
            st.write(f"RefDes points ({len(test_points)}): {', '.join(test_points)}")
            prefix_counts = {}
            for ref in test_points:
                if ref.startswith("TP"):
                    prefix = "TP"
                elif ref.startswith("FB"):
                    prefix = "FB"
                else:
                    prefix = ref[:1]
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            if prefix_counts:
                st.write("RefDes prefix counts:")
                st.code(", ".join(f"{k}:{v}" for k, v in sorted(prefix_counts.items())))
        else:
            st.write("RefDes points: (none)")

    st.write("Top 50 nets (alphabetical):")
    if net_meta.get("nets_preview"):
        st.code("\n".join(net_meta["nets_preview"]))
    else:
        st.write("(none)")

    st.write("Top 25 non-PP nets:")
    if net_meta.get("non_pp_preview"):
        st.code("\n".join(net_meta["non_pp_preview"]))
    else:
        st.write("(none)")

    st.write("Top 25 signal nets (suffix match):")
    if net_meta.get("signal_suffix_preview"):
        st.code("\n".join(net_meta["signal_suffix_preview"]))
    else:
        st.write("(none)")

    plan_state = st.session_state.get("plan_state") or {}
    history = plan_state.get("plan_history") or []
    if history:
        latest = history[0]
        st.write(f"Plan version: v{latest.get('version')} @ {latest.get('created_at')}")
    else:
        st.write("Plan version: (none)")
    st.write("Requested measurements:")
    reqs = plan_state.get("requested_measurements") or []
    if reqs:
        for r in reqs:
            st.write(f"- {r.get('key')} [{r.get('status')}] {r.get('prompt')}")
    else:
        st.write("- (none)")
    next_req = _next_pending_requested(plan_state)
    st.write(f"Next pending: {next_req.get('key') if next_req else '(none)'}")
    st.write(f"requested_measurement_count: {len(reqs)}")
    st.write(f"requested_measurements_parsed_count: {st.session_state.get('requested_measurements_parsed_count', 0)}")
    st.write(f"requested_measurements_parse_failed: {st.session_state.get('requested_measurements_parse_failed', False)}")
    st.write(f"requested_measurements_parse_error: {st.session_state.get('requested_measurements_parse_error','')}")
    show_json = st.checkbox("Show machine JSON (debug)", value=False)
    if show_json:
        raw_json = st.session_state.get("last_plan_json")
        if raw_json:
            st.code(json.dumps(raw_json, indent=2))
        else:
            st.write("(no machine JSON captured)")
    if st.session_state.get("component_validation_results"):
        st.write("component_validation_results:")
        st.code(json.dumps(st.session_state.get("component_validation_results"), indent=2))

    guardrail_report = st.session_state.get("guardrail_report")
    st.write(f"last_message_classification: {st.session_state.get('last_message_classification','')}")
    st.write(f"net_confirmation_pending: {st.session_state.get('net_confirmation_pending', False)}")
    st.write(f"auto_update_triggered: {st.session_state.get('auto_update_triggered', False)}")
    st.write(f"plan_update_reason: {st.session_state.get('plan_update_reason','')}")
    if st.session_state.get("parsed_measurements"):
        st.write("parsed_measurements:")
        st.code(json.dumps(st.session_state.get("parsed_measurements"), indent=2))
    if st.session_state.get("rejected_measurement_reasons"):
        st.write("rejected_measurement_reasons:")
        st.code(json.dumps(st.session_state.get("rejected_measurement_reasons"), indent=2))
    if st.session_state.get("completed_measurement_keys"):
        st.write(f"completed_measurement_keys: {', '.join(st.session_state['completed_measurement_keys'])}")
    if st.session_state.get("invalid_nets_detected"):
        st.write(f"invalid_nets_detected: {', '.join(st.session_state['invalid_nets_detected'])}")
    if st.session_state.get("net_validation_results"):
        st.write("net_validation_results:")
        st.code(json.dumps(st.session_state.get("net_validation_results"), indent=2))

    st.write("Rail-name Guardrail:")
    if guardrail_report:
        st.write(f"- last_run_time: {guardrail_report.get('last_run_time','')}")
        if guardrail_report.get("classification"):
            st.write(f"- classification: {guardrail_report.get('classification')}")
        st.write(f"- invalid_nets_detected: {len(guardrail_report.get('invalid_nets_detected') or [])}")
        if guardrail_report.get("invalid_nets_detected"):
            st.write(f"- invalid nets: {', '.join(guardrail_report['invalid_nets_detected'])}")
        if guardrail_report.get("invalid_plan_items"):
            st.write(f"- invalid_plan_items: {', '.join(guardrail_report['invalid_plan_items'])}")
        if guardrail_report.get("auto_fixes_applied"):
            st.write("- auto_fixes_applied:")
            for f in guardrail_report["auto_fixes_applied"]:
                st.write(f"  - {f.get('from')} -> {f.get('to')} ({f.get('reason')})")
        if guardrail_report.get("suggestions"):
            st.write("- suggestions:")
            for k, v in guardrail_report["suggestions"].items():
                st.write(f"  - {k}: {', '.join(v)}")
        if guardrail_report.get("invalid_refdes_detected"):
            st.write(f"- invalid_refdes_detected: {', '.join(guardrail_report['invalid_refdes_detected'])}")
            st.write(f"- refdes_replaced_count: {guardrail_report.get('refdes_replaced_count', 0)}")
    else:
        st.write("- last_run_time: (none)")

    if st.button("Copy debug report", key="copy_debug_report"):
        test_points = get_measure_points(case.get("board_id", ""), test_net, case=case, k=10) if test_net else []
        report = _build_debug_report(
            case,
            net_meta,
            net_refs_meta,
            plan_state,
            test_net,
            test_result,
            guardrail_report,
            test_points,
        )
        st.session_state["debug_report"] = report

    report = st.session_state.get("debug_report")
    if report:
        st.code(report)
        st.components.v1.html(
            f"""
            <button onclick="navigator.clipboard.writeText({json.dumps(report)})">Copy to clipboard</button>
            """,
            height=40,
        )
        st.text_area("Debug report (manual copy)", value=report, height=200)

st.subheader("Case")
st.write(f"**{case['case_id']}** — {case['title']}")
st.write(f"Model: {case.get('model','')}")
st.write(f"Symptom: {case.get('symptom','')}")

update_trigger = False
done_trigger = False
derived_from_message_id = None
if "net_confirmation" not in st.session_state:
    st.session_state["net_confirmation"] = None
if "last_message_classification" not in st.session_state:
    st.session_state["last_message_classification"] = ""
if "parsed_measurements" not in st.session_state:
    st.session_state["parsed_measurements"] = []
if "invalid_nets_detected" not in st.session_state:
    st.session_state["invalid_nets_detected"] = []
if "net_confirmation_pending" not in st.session_state:
    st.session_state["net_confirmation_pending"] = False
if "auto_update_triggered" not in st.session_state:
    st.session_state["auto_update_triggered"] = False
if "completed_measurement_keys" not in st.session_state:
    st.session_state["completed_measurement_keys"] = []
if "plan_update_reason" not in st.session_state:
    st.session_state["plan_update_reason"] = ""
if "rejected_measurement_reasons" not in st.session_state:
    st.session_state["rejected_measurement_reasons"] = []
if "net_validation_results" not in st.session_state:
    st.session_state["net_validation_results"] = []
if "requested_measurements_parse_failed" not in st.session_state:
    st.session_state["requested_measurements_parse_failed"] = False
if "requested_measurements_parsed_count" not in st.session_state:
    st.session_state["requested_measurements_parsed_count"] = 0
if "requested_measurements_parse_error" not in st.session_state:
    st.session_state["requested_measurements_parse_error"] = ""
if "last_plan_json" not in st.session_state:
    st.session_state["last_plan_json"] = None
if "component_validation_results" not in st.session_state:
    st.session_state["component_validation_results"] = []

def _mark_done_from_existing_measurements(case_id: str, requested: list) -> None:
    meas = list_measurements(case_id)
    if not meas or not requested:
        return
    alias_map = {}
    for r in requested:
        aliases = (r.get("meta") or {}).get("aliases") or build_aliases_for_key(r["key"])
        for a in aliases:
            alias_map[normalize_net_name(a)] = r["key"]
    for m in meas:
        name = m.get("name", "").upper()
        key = alias_map.get(normalize_net_name(name))
        if key:
            mark_requested_measurement_done(case_id, key)


def _run_plan_update(
    done_mode: bool,
    derived_id: int | None,
    announce_plan: bool = True,
    rerun: bool = True,
    reason: str = "manual",
    auto_update: bool = False,
) -> None:
    prompt = "Update the diagnostic plan for this case."
    if done_mode:
        _mark_done_from_existing_measurements(case["case_id"], st.session_state["plan_state"]["requested_measurements"])
    with st.spinner("Thinking..."):
        plan_text = generate_plan(case, prompt, include_images=True, done_mode=done_mode)
    known_nets = st.session_state.get("known_nets", set())
    items_json, plan_text_display, json_err = extract_requested_measurements_json(plan_text)
    st.session_state["last_plan_json"] = items_json if items_json else None
    items = []
    parse_meta = {"parse_failed": False, "parse_error": ""}
    plan_text_display = _strip_cheat_sheet(plan_text_display)
    if items_json:
        known_refdes = st.session_state.get("known_components", set())
        items, err = normalize_requested_items(items_json, known_nets=known_nets, known_refdes=known_refdes)
        if err == "json_item_unknown_net":
            items, err = normalize_requested_items(items_json, known_nets=None, known_refdes=known_refdes)
        if err:
            items, err2 = normalize_requested_items(items_json, known_nets=None, known_refdes=None)
            if err2:
                parse_meta = {"parse_failed": True, "parse_error": err}
                items = []
            else:
                parse_meta = {"parse_failed": False, "parse_error": ""}
    else:
        items, parse_meta = parse_requested_measurements(plan_text_display, known_nets=known_nets)
        if items:
            known_refdes = st.session_state.get("known_components", set())
            invalid_refdes = []
            for item in items:
                meta = item.get("meta") or {}
                hint = meta.get("hint") or ""
                tokens = extract_component_tokens(f"{item.get('prompt','')} {hint}")
                for t in tokens:
                    if t not in known_refdes:
                        invalid_refdes.append(t)
            if invalid_refdes:
                parse_meta = {"parse_failed": True, "parse_error": "human_item_unknown_refdes"}
                items = []
    st.session_state["requested_measurements_parse_failed"] = False
    st.session_state["requested_measurements_parse_error"] = ""
    if parse_meta.get("parse_failed"):
        st.session_state["requested_measurements_parse_failed"] = True
        st.session_state["requested_measurements_parse_error"] = parse_meta.get("parse_error", "")
        items = st.session_state.get("plan_state", {}).get("requested_measurements", [])
    elif not items:
        if "REQUESTED MEASUREMENTS" in plan_text_display.upper():
            st.session_state["requested_measurements_parse_failed"] = True
            st.session_state["requested_measurements_parse_error"] = "empty_requested_measurements"
            items = st.session_state.get("plan_state", {}).get("requested_measurements", [])
    plan_text_display, items, report = enforce_net_guardrail(
        board_id=case.get("board_id", ""),
        text=plan_text_display,
        plan_items=items,
        case=case,
    )
    if items:
        invalid_items = [it for it in items if (it.get("meta") or {}).get("net_valid") is False or it.get("net") == "[UNKNOWN_NET]"]
        if invalid_items:
            st.session_state["requested_measurements_parse_failed"] = True
            st.session_state["requested_measurements_parse_error"] = "invalid_plan_item_net"
            items = st.session_state.get("plan_state", {}).get("requested_measurements", [])
    plan_text_display = _strip_cheat_sheet(plan_text_display)
    allow_tokens = set()
    board_id = (case.get("board_id") or "").upper()
    model = (case.get("model") or "").upper()
    for token in (board_id, model):
        if token:
            allow_tokens.add(token)
            for part in re.split(r"[^A-Z0-9]+", token):
                if part:
                    allow_tokens.add(part)
    comp_guarded_text, comp_report = enforce_component_guardrail(
        plan_text_display,
        st.session_state.get("known_components", set()),
        allow_tokens=allow_tokens,
    )
    plan_text_display = comp_guarded_text
    report["invalid_refdes_detected"] = comp_report.get("invalid_refdes", [])
    report["refdes_replaced_count"] = comp_report.get("replaced_count", 0)
    report["last_run_time"] = datetime.datetime.utcnow().isoformat()
    st.session_state["guardrail_report"] = report
    st.session_state["requested_measurements_parsed_count"] = len(items)
    net_to_refdes = st.session_state.get("net_refs", {})
    known_refdes = st.session_state.get("known_components", set())
    plan_text_display = _render_requested_measurements_section(
        plan_text_display,
        items,
        net_to_refdes,
        known_refdes,
    )
    if announce_plan:
        add_chat_message(case["case_id"], "assistant", plan_text_display)
    add_plan_version(
        case["case_id"],
        plan_text_display,
        citations={
            "auto_update_from_measurements": auto_update,
            "triggering_message_id": derived_id,
            "plan_update_reason": reason,
        },
        derived_from_message_id=derived_id,
    )
    for it in items:
        meta = it.get("meta") or {}
        if "aliases" not in meta:
            meta["aliases"] = build_aliases_for_key(it["key"])
        it["meta"] = meta
    if items and not st.session_state.get("requested_measurements_parse_failed"):
        known_components = st.session_state.get("known_components", set())
        for it in items:
            meta = it.get("meta") or {}
            net = canonicalize_net_name(meta.get("net") or "")
            if not net:
                _, net_part, _ = split_measurement_key(it.get("key", ""))
                net = canonicalize_net_name(net_part)
            if net:
                meta["net"] = net
            if meta.get("type"):
                meta["type"] = str(meta.get("type"))
            probe_points = measurement_points_for_net(
                case.get("board_id", ""),
                net,
                case=case,
                k=8,
                known_components=known_components,
            )
            if probe_points:
                counts = {}
                for ref in probe_points:
                    if ref.startswith("TP"):
                        prefix = "TP"
                    elif ref.startswith("C"):
                        prefix = "C"
                    elif ref.startswith("L"):
                        prefix = "L"
                    elif ref.startswith("R"):
                        prefix = "R"
                    elif ref.startswith("U"):
                        prefix = "U"
                    else:
                        prefix = "OTHER"
                    counts[prefix] = counts.get(prefix, 0) + 1
                meta.update(
                    {
                        "points": probe_points,
                        "point_counts": counts,
                    }
                )
            it["meta"] = meta
        set_requested_measurements(case["case_id"], items)
    _update_plan_state(case["case_id"], plan_text_display)
    st.session_state["plan_update_reason"] = reason
    st.session_state["auto_update_triggered"] = auto_update
    if rerun:
        _rerun()


def _persist_measurements_and_update(
    entries: list,
    question_text: str | None,
    derived_id: int | None,
) -> None:
    requested = list_requested_measurements(case["case_id"])
    alias_map = {}
    for r in requested:
        aliases = (r.get("meta") or {}).get("aliases") or build_aliases_for_key(r["key"])
        for a in aliases:
            alias_map[normalize_net_name(a)] = r["key"]

    completed = []
    for m in entries:
        net = canonicalize_net_name(m.get("net", ""))
        if not net:
            continue
        name = net
        m_type = m.get("type", "")
        note_parts = []
        if m_type:
            note_parts.append(f"type:{m_type}")
        if m.get("raw"):
            note_parts.append(f"raw:{m['raw']}")
        if m.get("key_hint"):
            note_parts.append(f"key_hint:{m['key_hint']}")
        note = " | ".join(note_parts)
        add_measurement(case["case_id"], name, m.get("value", ""), m.get("unit", ""), note)

        key_hint = (m.get("key_hint") or "").upper()
        if key_hint:
            for r in requested:
                if r["key"].upper() == key_hint:
                    mark_requested_measurement_done(case["case_id"], r["key"])
                    completed.append(r["key"])
                    break

        candidates = [normalize_net_name(net)]
        for cand in candidates:
            match_key = alias_map.get(cand)
            if match_key:
                mark_requested_measurement_done(case["case_id"], match_key)
                completed.append(match_key)
                break

        if m_type == "continuity" and net.upper().startswith("F"):
            for r in requested:
                key_u = r["key"].upper()
                if "FUSE" in key_u or net.upper() in key_u:
                    mark_requested_measurement_done(case["case_id"], r["key"])
                    completed.append(r["key"])
                    break

    st.session_state["completed_measurement_keys"] = sorted(set(completed))
    st.session_state["auto_update_triggered"] = True
    st.session_state["plan_update_reason"] = "auto_measurements"
    _run_plan_update(
        done_mode=False,
        derived_id=derived_id,
        announce_plan=True,
        rerun=False,
        reason="auto_measurements",
        auto_update=True,
    )
    if question_text:
        response = answer_question(case, question_text, include_images=True)
        response, _, report = enforce_net_guardrail(
            board_id=case.get("board_id", ""),
            text=response,
            plan_items=[],
            case=case,
        )
        report["last_run_time"] = datetime.datetime.utcnow().isoformat()
        st.session_state["guardrail_report"] = report
        add_chat_message(case["case_id"], "assistant", f"Plan updated from measurements.\\n\\n{response}")


left, right = st.columns([2, 1])

with left:
    st.subheader("Chat")
    if "chat_limit" not in st.session_state:
        st.session_state["chat_limit"] = 20
    messages = list_chat_messages(case["case_id"])
    messages_rev = list(reversed(messages))
    known_nets = st.session_state.get("known_nets", set())

    with st.form("chat_form", clear_on_submit=True):
        user_text = st.text_input("Message")
        submitted = st.form_submit_button("Send")

    display_messages = messages_rev[: st.session_state["chat_limit"]]
    for m in display_messages:
        with st.chat_message(m["role"]):
            st.markdown(_render_text_html(m["content"], known_nets), unsafe_allow_html=True)

    if len(messages_rev) > st.session_state["chat_limit"]:
        if st.button("Load older messages", key="load_older"):
            st.session_state["chat_limit"] += 20
            _rerun()

    if submitted and user_text:
        should_rerun = False
        derived_from_message_id = add_chat_message(case["case_id"], "user", user_text)
        cmd = parse_command(user_text)
        st.session_state["parsed_measurements"] = []
        st.session_state["invalid_nets_detected"] = []
        st.session_state["last_message_classification"] = ""
        st.session_state["net_confirmation_pending"] = False
        st.session_state["rejected_measurement_reasons"] = []
        st.session_state["net_validation_results"] = []
        st.session_state["component_validation_results"] = []

        if cmd and cmd["type"] in ("update", "done"):
            st.session_state["last_message_classification"] = "command"
            reason = "command"
            if cmd["type"] == "done":
                reason = "command_done"
            st.session_state["plan_update_reason"] = reason
            st.session_state["auto_update_triggered"] = False
            _run_plan_update(
                done_mode=(cmd["type"] == "done"),
                derived_id=derived_from_message_id,
                reason=reason,
                auto_update=False,
            )
            should_rerun = True
        else:
            if cmd and cmd["type"] == "note":
                note_text = cmd.get("args", {}).get("text", "").strip()
                if note_text:
                    add_note(case["case_id"], note_text)
                    add_chat_message(case["case_id"], "assistant", "Saved note.")
                    should_rerun = True

            comp_tokens = extract_component_tokens(user_text)
            comp_results = []
            comp_invalid = []
            for ref in comp_tokens:
                valid = ref in known_components
                comp_results.append({"refdes": ref, "valid": valid})
                if not valid:
                    comp_invalid.append(ref)
            st.session_state["component_validation_results"] = comp_results

            comp_meas = parse_component_measurements(user_text)
            if comp_meas:
                st.session_state["last_message_classification"] = "component_measurement"
                invalid_refs = [m for m in comp_meas if m["refdes"] not in known_components]
                if invalid_refs:
                    lines = []
                    for m in invalid_refs:
                        sugg = suggest_components(case.get("board_id", ""), m["refdes"], k=5, case=case)
                        line = f"I can't confirm component {m['refdes']} exists on this board."
                        if sugg:
                            line += f" Closest matches: {', '.join(sugg)}"
                        lines.append(line)
                    add_chat_message(case["case_id"], "assistant", "\n".join(lines) + "\n\nPlan unchanged.")
                    should_rerun = True
                else:
                    for m in comp_meas:
                        name = f"COMP:{m['refdes']}.{m['loc']}"
                        note = f"type:component | raw:{m['raw']}"
                        add_measurement(case["case_id"], name, m["value"], m["unit"], note)
                    add_chat_message(case["case_id"], "assistant", "Saved component measurements. Plan unchanged.")
                    should_rerun = True
                if should_rerun:
                    _rerun()
                    st.stop()
            if comp_invalid and st.session_state["last_message_classification"] == "question":
                lines = []
                for ref in comp_invalid:
                    sugg = suggest_components(case.get("board_id", ""), ref, k=5, case=case)
                    line = f"Component {ref} not found in index."
                    if sugg:
                        line += f" Closest matches: {', '.join(sugg)}"
                    lines.append(line)
                add_chat_message(case["case_id"], "assistant", "\n".join(lines) + "\n\nPlan unchanged.")
                should_rerun = True
                _rerun()
                st.stop()

            if cmd and cmd.get("type") == "measure":
                args = cmd.get("args", {})
                rail = args.get("rail", "").strip()
                value = args.get("value", "").strip()
                unit = args.get("unit", "").strip()
                note = args.get("note", "").strip()
                m_type = "voltage"
                if note.lower() in ("r2g", "resistance"):
                    m_type = "resistance"
                elif note.lower() == "diode":
                    m_type = "diode"
                entries = []
                if unit and value and rail:
                    entries = [
                        {
                            "net": canonicalize_net_name(rail),
                            "net_raw": rail,
                            "type": m_type,
                            "value": value,
                            "unit": unit,
                            "raw": user_text,
                            "key_hint": None,
                        }
                    ]
                invalid = []
                if entries and canonicalize_net_name(rail) not in known_nets:
                    invalid = [entries[0]]
                    entries = []
                if not unit:
                    st.session_state["rejected_measurement_reasons"] = [{"segment": user_text, "reason": "missing_unit"}]
                st.session_state["last_message_classification"] = "measurement"
                if rail:
                    st.session_state["net_validation_results"] = [
                        {"net": canonicalize_net_name(rail), "valid": canonicalize_net_name(rail) in known_nets}
                    ]
                if not entries:
                    st.session_state["last_message_classification"] = "question"
            else:
                parsed = classify_and_parse(user_text, known_nets)
                entries = parsed["entries"]
                invalid = parsed["invalid"]
                st.session_state["rejected_measurement_reasons"] = parsed["rejected"]
                st.session_state["net_validation_results"] = parsed["net_validation"]
                st.session_state["last_message_classification"] = parsed["classification"].lower()
                if parsed["classification"] in ("QUESTION", "UNKNOWN"):
                    entries = []
                    invalid = []

            st.session_state["parsed_measurements"] = entries
            st.session_state["invalid_nets_detected"] = [i.get("net_raw") for i in invalid if i.get("net_raw")]

            question_present = bool(re.search(r"\\?|\\b(why|how|what|when|where|explain|meaning|clarify|is|are|do|does|can|should)\\b", user_text, re.IGNORECASE))

            if invalid:
                st.session_state["last_message_classification"] = "measurement"
                st.session_state["auto_update_triggered"] = False
                st.session_state["plan_update_reason"] = "pending_confirmation"
                suggestions = {}
                for i in invalid:
                    raw = i.get("net_raw") or i.get("net") or ""
                    suggestions[raw] = suggest_nets(case.get("board_id", ""), raw, k=5, case=case)
                st.session_state["net_confirmation"] = {
                    "entries": entries,
                    "invalid": invalid,
                    "suggestions": suggestions,
                    "question_text": user_text if question_present else "",
                    "message_id": derived_from_message_id,
                }
                st.session_state["net_confirmation_pending"] = True
                add_chat_message(case["case_id"], "assistant", "Net confirmation required before saving measurements.")
                should_rerun = True
            elif entries:
                st.session_state["last_message_classification"] = "measurement"
                st.session_state["net_confirmation_pending"] = False
                _persist_measurements_and_update(entries, user_text if question_present else None, derived_from_message_id)
                if not question_present:
                    add_chat_message(case["case_id"], "assistant", "Saved measurements and updated plan.")
                should_rerun = True
            else:
                st.session_state["last_message_classification"] = "question"
                st.session_state["auto_update_triggered"] = False
                st.session_state["plan_update_reason"] = "question"
                invalid_user_nets = []
                suggestions = {}
                for raw in extract_net_tokens(user_text):
                    canon = canonicalize_net_name(raw)
                    if canon and canon not in known_nets:
                        invalid_user_nets.append(raw)
                        suggestions[raw] = suggest_nets(case.get("board_id", ""), raw, k=8, case=case)
                if invalid_user_nets:
                    lines = []
                    for raw in invalid_user_nets:
                        lines.append(
                            f"I can't confirm net '{raw}' exists in the loaded netlist for {case.get('board_id','')}."
                        )
                        if suggestions.get(raw):
                            lines.append(f"Closest matches: {', '.join(suggestions[raw])}")
                    lines.append("Please confirm the exact net name or provide a schematic/boardview snippet.")
                    response = "\n".join(lines)
                    add_chat_message(case["case_id"], "assistant", response + "\n\nPlan unchanged.")
                    st.session_state["guardrail_report"] = {
                        "last_run_time": datetime.datetime.utcnow().isoformat(),
                        "invalid_nets_detected": sorted(set(invalid_user_nets)),
                        "auto_fixes_applied": [],
                        "suggestions": suggestions,
                        "source": "user_input",
                    }
                    should_rerun = True
                elif re.search(r"what .*measure|measure first|measure next|most important measurement", user_text, re.IGNORECASE):
                    plan_state = st.session_state.get("plan_state") or {}
                    next_req = _next_pending_requested(plan_state)
                    if next_req:
                        add_chat_message(case["case_id"], "assistant", f"{next_req['key']}: {next_req['prompt']}")
                    else:
                        _run_plan_update(
                            done_mode=False,
                            derived_id=derived_from_message_id,
                            announce_plan=False,
                            rerun=False,
                            reason="manual",
                        )
                        plan_state = st.session_state.get("plan_state") or {}
                        next_req = _next_pending_requested(plan_state)
                        if next_req:
                            add_chat_message(case["case_id"], "assistant", f"{next_req['key']}: {next_req['prompt']}")
                    _rerun()
                else:
                    response = answer_question(case, user_text, include_images=True)
                    response, _, report = enforce_net_guardrail(
                        board_id=case.get("board_id", ""),
                        text=response,
                        plan_items=[],
                        case=case,
                    )
                    report["last_run_time"] = datetime.datetime.utcnow().isoformat()
                    report["classification"] = st.session_state.get("last_message_classification")
                    st.session_state["guardrail_report"] = report
                    add_chat_message(case["case_id"], "assistant", response + "\n\nPlan unchanged.")
                    should_rerun = True

        if should_rerun:
            _rerun()

    if st.session_state.get("net_confirmation"):
        with st.expander("Net Confirmation", expanded=True):
            pending = st.session_state["net_confirmation"]
            invalid_items = pending.get("invalid", [])
            suggestions = pending.get("suggestions", {})
            selections = {}
            for i in invalid_items:
                raw = i.get("net_raw") or i.get("net") or ""
                options = suggestions.get(raw, [])
                options = ["-- select --"] + options + ["Other...", "Cancel"]
                choice = st.selectbox(f"Replace {raw}", options, key=f"confirm_{raw}")
                if choice == "Other...":
                    manual = st.text_input(f"Enter valid net for {raw}", value="", key=f"manual_{raw}")
                    selections[raw] = manual.strip()
                else:
                    selections[raw] = choice
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Confirm nets", key="confirm_nets"):
                    if any(v in ("", "-- select --", "Cancel", "Other...") for v in selections.values()):
                        st.warning("Select a valid net for each entry or cancel.")
                    else:
                        invalid_manual = [v for v in selections.values() if canonicalize_net_name(v) not in known_nets]
                        if invalid_manual:
                            st.warning("One or more selected nets are not in the netlist.")
                        else:
                            updated_entries = []
                            for m in pending.get("entries", []) + invalid_items:
                                net = m.get("net") or ""
                                raw = m.get("net_raw") or m.get("net") or ""
                                if raw in selections:
                                    net = selections[raw]
                                updated = dict(m)
                                updated["net"] = net
                                updated_entries.append(updated)
                            st.session_state["net_confirmation"] = None
                            st.session_state["net_confirmation_pending"] = False
                            _persist_measurements_and_update(
                                updated_entries,
                                pending.get("question_text") or None,
                                pending.get("message_id"),
                            )
                            add_chat_message(case["case_id"], "assistant", "Measurements saved after net confirmation.")
                            _rerun()
            with c2:
                if st.button("Cancel", key="cancel_nets"):
                    st.session_state["net_confirmation"] = None
                    st.session_state["net_confirmation_pending"] = False
                    add_chat_message(case["case_id"], "assistant", "Net confirmation canceled.")
                    _rerun()

    with st.expander("Attachments (evidence)", expanded=False):
        st.caption(
            "Best: put full selectable-text schematics into kb_raw + ingest. For case-specific truth, upload schematic page screenshots and/or FlexBV boardview screenshots."
        )
        a_type = st.selectbox(
            "Type",
            ["schematic", "boardview_screenshot", "boardview_file", "thermal", "microscope", "scope", "other"],
            key="attach_type",
        )
        up = st.file_uploader("Upload file", key="attach_upload")
        if st.button("Save attachment") and up is not None:
            save_attachment(case["case_id"], up.name, up.getvalue(), a_type)
            _rerun()

        atts = list_attachments(case["case_id"])
        if atts:
            st.write("Saved attachments:")
            for a in atts:
                st.write(f"- **{a['type']}**: {a['filename']}")
        else:
            st.warning("No attachments yet.")

    with st.expander("Expected Ranges (manual entry)", expanded=False):
        board_id = case.get("board_id", "")
        known_nets = st.session_state.get("known_nets", set())
        allowed_types = {"voltage", "resistance", "diode", "current", "frequency", "continuity"}
        type_aliases = {
            "v": "voltage",
            "volt": "voltage",
            "volts": "voltage",
            "ohm": "resistance",
            "ohms": "resistance",
            "r2g": "resistance",
            "diodev": "diode",
            "a": "current",
            "amp": "current",
            "amps": "current",
            "hz": "frequency",
            "freq": "frequency",
            "cont": "continuity",
        }
        st.caption("Add per-board expected ranges from known-good measurements. These are used as truth for diagnostics.")
        er_net = st.text_input("Net", value="")
        er_type = st.selectbox(
            "Measurement type",
            ["voltage", "resistance", "diode", "current", "frequency", "continuity"],
            index=0,
        )
        er_value = st.text_input("Measured value", value="")
        er_unit = st.text_input("Unit", value="V")
        er_source = st.selectbox(
            "Source",
            ["known-good-board", "case_history", "schematic", "boardview", "community"],
            index=0,
        )
        er_note = st.text_input("Note (optional)", value="known-good iCloud-locked board")
        if st.button("Add expected range"):
            canon = canonicalize_net_name(er_net)
            if not canon:
                st.warning("Enter a valid net name.")
            elif known_nets and canon not in known_nets:
                st.warning("Net not found in current boardview netlist.")
            elif not board_id:
                st.warning("Board ID missing for this case.")
            else:
                add_expected_range(
                    board_id=board_id,
                    net=canon,
                    measurement_type=er_type,
                    expected_min=er_value,
                    expected_max=er_value,
                    unit=er_unit,
                    source=er_source,
                    note=er_note,
                )
                st.success("Expected range saved.")
                _rerun()
        st.divider()
        st.subheader("Bulk entry")
        st.caption("Format: NET, type, value, unit, note (one per line). Type optional.")
        bulk_text = st.text_area("Bulk lines", value="", height=120)
        if st.button("Import bulk lines"):
            if not board_id:
                st.warning("Board ID missing for this case.")
            else:
                added = 0
                lines = [l.strip() for l in bulk_text.splitlines() if l.strip()]
                for line in lines:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        net_raw = parts[0]
                        mtype = parts[1] or "voltage"
                        value = parts[2]
                        unit = parts[3] if len(parts) > 3 else ""
                        note = parts[4] if len(parts) > 4 else ""
                    else:
                        tokens = line.split()
                        if len(tokens) < 2:
                            continue
                        net_raw = tokens[0]
                        value = tokens[1]
                        unit = tokens[2] if len(tokens) > 2 else ""
                        mtype = tokens[3] if len(tokens) > 3 else "voltage"
                        note = " ".join(tokens[4:]) if len(tokens) > 4 else ""
                    mtype = (mtype or "voltage").strip().lower()
                    mtype = type_aliases.get(mtype, mtype)
                    if mtype not in allowed_types:
                        mtype = "voltage"
                    canon = canonicalize_net_name(net_raw)
                    if not canon:
                        continue
                    if known_nets and canon not in known_nets:
                        continue
                    add_expected_range(
                        board_id=board_id,
                        net=canon,
                        measurement_type=mtype,
                        expected_min=value,
                        expected_max=value,
                        unit=unit,
                        source="known-good-board",
                        note=note,
                    )
                    added += 1
                st.success(f"Imported {added} entries.")
                _rerun()
        st.divider()
        st.subheader("Import from baseline measurements")
        st.caption("Copy known-good baseline measurements into expected ranges for this board.")
        if st.button("Import baseline measurements"):
            if not board_id:
                st.warning("Board ID missing for this case.")
            else:
                existing = list_expected_ranges(board_id)
                seen = {(r["net"], r["measurement_type"], r.get("expected_min"), r.get("expected_max"), r.get("unit"), r.get("source")) for r in existing}
                added = 0
                for b in list_baselines():
                    if b.get("board_id") != board_id:
                        continue
                    for m in list_baseline_measurements(b["baseline_id"]):
                        tokens = extract_net_tokens(m.get("name") or "")
                        if not tokens:
                            continue
                        net = canonicalize_net_name(tokens[0])
                        if not net:
                            continue
                        if known_nets and net not in known_nets:
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
                        unit = m.get("unit") or ""
                        value = m.get("value") or ""
                        key = (net, mtype, value, value, unit, "baseline")
                        if key in seen:
                            continue
                        add_expected_range(
                            board_id=board_id,
                            net=net,
                            measurement_type=mtype,
                            expected_min=value,
                            expected_max=value,
                            unit=unit,
                            source="baseline",
                            note=m.get("note") or "",
                        )
                        seen.add(key)
                        added += 1
                st.success(f"Imported {added} baseline measurements.")
                _rerun()
        existing = list_expected_ranges(board_id) if board_id else []
        if existing:
            st.write("Latest expected ranges:")
            for r in existing[:30]:
                unit = f" {r.get('unit','')}".strip()
                if r.get("expected_min") == r.get("expected_max"):
                    exp = f"{r.get('expected_min','')}{unit}"
                else:
                    exp = f"{r.get('expected_min','')}–{r.get('expected_max','')}{unit}"
                st.write(f"- #{r['id']} {r['net']} | {r['measurement_type']} | {exp} | source={r.get('source')}")
            st.divider()
            st.subheader("Edit / Delete expected range")
            options = {f"#{r['id']} {r['net']} {r['measurement_type']}": r for r in existing}
            sel = st.selectbox("Select range", list(options.keys()))
            r = options[sel]
            er_net_e = st.text_input("Net (edit)", value=r["net"], key="er_edit_net")
            er_type_e = st.selectbox(
                "Measurement type (edit)",
                ["voltage", "resistance", "diode", "current", "frequency", "continuity"],
                index=["voltage", "resistance", "diode", "current", "frequency", "continuity"].index(r["measurement_type"])
                if r["measurement_type"] in ["voltage", "resistance", "diode", "current", "frequency", "continuity"] else 0,
                key="er_edit_type",
            )
            er_min_e = st.text_input("Expected min (edit)", value=r.get("expected_min", ""), key="er_edit_min")
            er_max_e = st.text_input("Expected max (edit)", value=r.get("expected_max", ""), key="er_edit_max")
            er_unit_e = st.text_input("Unit (edit)", value=r.get("unit", ""), key="er_edit_unit")
            er_source_e = st.selectbox(
                "Source (edit)",
                ["known-good-board", "baseline", "case_history", "schematic", "boardview", "community"],
                index=["known-good-board", "baseline", "case_history", "schematic", "boardview", "community"].index(r.get("source", "known-good-board"))
                if r.get("source") in ["known-good-board", "baseline", "case_history", "schematic", "boardview", "community"] else 0,
                key="er_edit_source",
            )
            er_note_e = st.text_input("Note (edit)", value=r.get("note", ""), key="er_edit_note")
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Save changes"):
                    canon = canonicalize_net_name(er_net_e)
                    if not canon:
                        st.warning("Enter a valid net name.")
                    elif known_nets and canon not in known_nets:
                        st.warning("Net not found in current boardview netlist.")
                    else:
                        update_expected_range(
                            range_id=r["id"],
                            net=canon,
                            measurement_type=er_type_e,
                            expected_min=er_min_e,
                            expected_max=er_max_e,
                            unit=er_unit_e,
                            source=er_source_e,
                            note=er_note_e,
                        )
                        st.success("Expected range updated.")
                        _rerun()
            with c2:
                if st.button("Delete range"):
                    delete_expected_range(r["id"])
                    st.success("Expected range deleted.")
                    _rerun()

with right:
    st.subheader("Current Plan")
    plan_state = st.session_state.get("plan_state") or {}
    latest_plan = plan_state.get("plan_markdown")
    watermark_parts = [f"Case: {case.get('title','')}", f"ID: {case.get('case_id','')}"]
    if case.get("board_id"):
        watermark_parts.append(f"Board: {case.get('board_id')}")
    st.caption(" | ".join([p for p in watermark_parts if p]))
    if latest_plan:
        known_nets = st.session_state.get("known_nets", set())
        plan_lines = latest_plan.splitlines()
        max_lines = 24
        if len(plan_lines) > max_lines:
            preview = "\n".join(plan_lines[:max_lines]).rstrip()
            preview = preview + "\n…"
            st.markdown(_render_text_html(preview, known_nets), unsafe_allow_html=True)
            with st.expander(f"Show full plan ({len(plan_lines)} lines)", expanded=False):
                st.markdown(_render_text_html(latest_plan, known_nets), unsafe_allow_html=True)
        else:
            st.markdown(_render_text_html(latest_plan, known_nets), unsafe_allow_html=True)
    else:
        st.info("No plan yet. Use Update Plan to generate the first plan.")

    st.subheader("Requested Measurements")
    reqs = plan_state.get("requested_measurements") or []
    if reqs:
        known_nets = st.session_state.get("known_nets", set())
        for r in reqs:
            meta = r.get("meta") or {}
            net = meta.get("net") or ""
            mtype = meta.get("type") or ""
            hint = meta.get("hint") or ""
            points = meta.get("points") or []
            status = (r.get("status") or "other").lower()
            status_label = status.upper()
            status_class = "other"
            if status == "pending":
                status_class = "pending"
            elif status == "done":
                status_class = "done"
            net_badge = f'<span class="net-token">{html.escape(net)}</span>' if net else ""
            prompt = html.escape(r.get("prompt") or "")
            key = html.escape(r.get("key") or "")
            hint_html = html.escape(hint) if hint else ""
            points_html = ", ".join(html.escape(p) for p in points) if points else "(no boardview points listed)"
            lines = [
                '<div class="req-card">',
                '  <div class="req-header">',
                f'    <span class="req-status {status_class}">{status_label}</span>',
                f'    <span class="req-key">{key}</span>',
                f'    {net_badge}',
                '  </div>',
                f'  <div class="req-line"><span class="req-label">Prompt:</span> {prompt}</div>',
            ]
            if mtype:
                lines.append(
                    f'  <div class="req-line"><span class="req-label">Type:</span> {html.escape(str(mtype))}</div>'
                )
            if hint_html:
                lines.append(
                    f'  <div class="req-line"><span class="req-label">Hint:</span> {hint_html}</div>'
                )
            lines.append(
                f'  <div class="req-points"><span class="req-label">Measurement points (boardview):</span> {points_html}</div>'
            )
            lines.append("</div>")
            st.markdown("\n".join(lines), unsafe_allow_html=True)
    else:
        st.write("None yet.")

    if st.button("Update Plan", key="btn_update_plan"):
        update_trigger = True
    if st.button("Done", key="btn_done_plan"):
        done_trigger = True

    st.subheader("Plan History")
    plans = plan_state.get("plan_history") or []
    if plans:
        labels = {f"v{p['version']} — {p['created_at']}": p for p in plans}
        selected_label = st.selectbox("Select plan version", list(labels.keys()))
        selected_plan = labels[selected_label]
        known_nets = st.session_state.get("known_nets", set())
        st.markdown(_render_text_html(selected_plan["plan_markdown"], known_nets), unsafe_allow_html=True)
    else:
        st.write("No previous plans.")

if update_trigger or done_trigger:
    reason = "manual_button"
    if done_trigger:
        reason = "done_button"
    _run_plan_update(done_mode=done_trigger, derived_id=derived_from_message_id, reason=reason, auto_update=False)
