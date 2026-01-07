# BoardBrain v1.1 (Local Motherboard Diagnostic Copilot)

Local Streamlit web app for MacBook USB‑C no‑power workflows with **schematic-first** guardrails and a **Known-Good Baseline** library.

## Setup (macOS)
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt

cp .env.example .env
# edit .env and paste your OpenAI API key

python -m boardbrain.ingest
streamlit run app/app.py
```

You can also run via the helper script:
```bash
./run.sh
```
If you run manually, ensure the project root is on `PYTHONPATH`:
```bash
export PYTHONPATH="$(pwd)"
export KB_RAW_DIR="$(pwd)/kb_raw"
python -m boardbrain.ingest
streamlit run app/app.py
```

## Chat-First Workflow
Each case has a persistent chat thread stored in SQLite. One chat is attached to one case, and history survives app restarts.

### Commands
```
/measure rail=PP3V3_AON value=3.28 unit=V note="stable"
/note text="USB-C 5V/0.20A steady; no fan spin"
/update
/done
```

### Measurement formatting tips (strict)
Measurements only parse when units or explicit keywords are present.
Net mentions without a unit (e.g., “PP3V3_S2 is stable”) are treated as questions.
Use `NET value unit` or `NET: value unit` format; avoid prose between the net and the value.
```
PP3V3_AON: 3.29V
PPBUS_AON 12.3 V
PPBUS_AON: r2g 12.5 ohms
PP1V8_AON: 1.8v stable
PPBUS_AON: diode 0.420
CHECK_PPBUS_AON: 0.0V
F7000 good
USB-C: 5V 0.20A
```

### Plan updates (pinned panel)
- The plan auto-updates as soon as you enter valid measurements.
- You can still type `/update`, `/done`, or click **Update Plan**/**Done** to force a refresh.
- Normal chat questions never update the plan (chat replies include “Plan unchanged.”).
- `/done` marks satisfied requested measurements before recomputing the plan.
Plan updates only occur when the message matches explicit measurement grammar (units or keywords required).

### Requested Measurements
Plans include a **Requested Measurements** list. As you enter measurements, items are marked done automatically when the rail/key matches.
Requested measurements are parsed from the machine JSON block when present; the JSON is stripped from the displayed plan.
If JSON is missing/invalid, the app falls back to the human KEY/PROMPT list and preserves the previous list if parsing fails.

Note: avoid embedding literal JSON braces inside Python f-strings in prompt templates; keep JSON examples in plain strings.
The measurement-format cheat sheet is injected as static UI text after plan generation and is not sent to the model.

### Rail-name guardrail (no hallucinated nets)
BoardBrain validates any net/rail name against a schematic-derived netlist that includes both power rails (PP*) and signal nets (e.g., `SMBUS_SCL`, `KBDBKLT_SW2`). Guardrails are **balanced strict**: auto-fixes only occur for exact normalization, explicit mappings, or extremely high-confidence unique matches. Otherwise, it will refuse to treat the net as fact, suggest close matches, and ask for the exact net name or a schematic/boardview snippet. You may still request **GENERIC (NOT SCHEMATIC-CONFIRMED)** guidance.
Guardrail warnings are shown in the Debug panel and included in the Copy debug report.
If you enter a measurement with an unknown net, the app shows a **Net Confirmation** panel with suggested matches. Measurements are not saved and the plan does not update until you confirm valid nets.
USB-C meter readings are treated as a port node and stored as a single combined entry when both V and A are present (e.g., “USB-C: 5V 0.20A”).
Requested measurement keys use **CHECK_** only. Any `VERIFY_`, `MEASURE_`, or `TEST_` keys are normalized to `CHECK_<NETNAME>` where NETNAME is an exact net from the netlist (signal nets included).
After changing net extraction rules, re-run:
```bash
python -m boardbrain.ingest
```
If a net still does not validate, delete the cached netlist under `data/netlists/` and re-run ingest.

### Nets vs Components (separate truth lanes)
- Nets/rails use the netlist and can update plans when entered with explicit net measurement syntax.
- Components use the component index and never update plans in v1.
- Component measurements must use explicit COMP syntax (below).

Component measurement format (stored, no plan update):
```
COMP U7000.pin3: 1.8V
COMP Q3200.gate: 0.62V
COMP J3210.A5: 5V
```

After changing component extraction regex, re-run:
```bash
python -m boardbrain.ingest
```

### Chat UX
- Message input is at the top of the chat panel.
- Newest messages show first; use **Load older messages** to page back.

### Format Cheat Box (copy/paste)
✅ Measurements (auto-save + auto-update plan)
- “PPBUS_AON 12.3 V”
- “CHECK_PP3V3_S2 3.31 V”
- “PP3V3_S2 r2g 120 ohms”
- “L7740 diode 0.412 V”
- “F7000 continuity pass”

✅ Mixed (saves measurements AND answers question)
- “PPBUS_AON 12.3 V — is that normal for no-power?”

✅ Prefix normalization
- If a plan ever emits `VERIFY_PPBUS_AON`, it is normalized to `CHECK_PPBUS_AON`.
- Entering “PPBUS_AON 12.3 V” completes `CHECK_PPBUS_AON`.

❌ Questions (no plan update)
- “What does PP3V3_S2 feed?”
- “Where do I measure P3V3S2_PWR_EN?”
- “Do you mean stable as in no droop?”

### Deleting cases (permanent)
Deleting a case permanently removes all data tied to that case_id:
chat history, plan versions, requested measurements, measurements, notes, attachment rows, and the case folder under `data/cases/<case_id>/`.
The delete confirmation shows counts for each item before you confirm.

### Duplicate case titles
If you create a case with a title that already exists, it is auto-suffixed as `Title (2)`, `Title (3)`, etc.
Case identity is still the unique case_id; the suffix is display-only for disambiguation.

### Debug panel (netlist + plan state)
Open the sidebar expander **Debug / Netlist / Plan State** to verify netlist loading and plan state.
- Test a net name to see VALID / NOT FOUND and the normalized form.
- See netlist source, count, PP vs signal counts, cache path, and the top 50 nets preview.
- Review the top non-PP (signal) nets list (and suffix-matched list) to sanity-check signal net extraction.
- The debug panel also shows **Net→RefDes** index status and sample probe points for the test net.

### Net → RefDes measurement points
BoardBrain can suggest validated probe points (refdes) for a net using a boardview-derived index (preferred) or a KB-text fallback.
- Ask: “Where can I probe PPBUS_AON?”
- Command: `/points PPBUS_AON`
If no points are available, it will ask for a schematic/boardview snippet and will not guess.
Ranking preference (probe-friendly): TP/P > C > L > J > others, with connectors deprioritized.
The index is stored at `data/net_refs/<board_id>.json`. When a boardview is available, it uses boardview truth; otherwise it falls back to co‑occurrence of nets + refdes in ingested KB text.
Probe points shown in the UI are filtered to refdes that exist in the component index and ranked for probe‑friendliness.
If no points exist for a net, the assistant will say so and label any advice as a generic fallback.
Enable **Show machine JSON (debug)** in the Debug panel to view the raw JSON block when needed.
Rebuild the index after new KB content:
```bash
python -m boardbrain.ingest
```
- Review current plan version and requested measurements.
- Use **Copy debug report** to generate a plaintext report (clipboard copy or manual copy fallback).
- The debug panel includes message classification, parsed measurements, invalid nets, net confirmation status, and auto-update reason.
- It also shows rejected measurement reasons and net validation results.

## Evidence / citations policy (schematic-first)
For board-specific facts (rails/pins/expected voltages), BoardBrain will only respond when it has evidence:
- case attachments: schematic page screenshots or boardview screenshots
- KB retrieval hits from schematic/manual/datasheet text

If evidence is missing, the assistant will refuse and ask for the exact schematic/boardview snippet.

## Notes
- If your schematics are selectable-text PDFs, ingesting them provides strong searchable evidence.
- Many schematics are image-only; ingest will skip empty-text PDF pages. Use screenshots as case evidence.

## Baselines (Known Good)
Use the **Baselines** mode to store quick "known-good" reference measurements per board number (e.g. A2338 820-02020).
These baselines are appended to the diagnostic context automatically.



## Recommended KB layout (scales as you add devices)
You said you prefer:

`kb_raw/MacBook/A2338/820-02020/`

That works great. Example:

```
kb_raw/
  MacBook/
    A2338/
      820-02020/
        schematic/
          schematic.pdf
        reference/
          notes.md
        boardview/
          820-02020.bvr
        boardview_screens/
          PPBUS_AON.png
          USB_C_PORT_A.png
        notes/
          triage.md
```

**Important:**
- The ingester scans subfolders recursively.
- Board-specific filtering works best when the **board number** (e.g. `820-02020`) appears in the folder path or filename.
- For clean names, you can name the file simply `schematic.pdf` as long as the folder path includes the board number.

Create folders + move your PDF:

```bash
mkdir -p kb_raw/MacBook/A2338/820-02020
mv "kb_raw/820-02020 schematic.pdf" "kb_raw/MacBook/A2338/820-02020/schematic.pdf"
python -m boardbrain.ingest
```

## Boardview ingestion (.BVR)
FlexBV `BVRAW_FORMAT_3` `.BVR` files are supported.
Place the file here:
```
kb_raw/MacBook/<model>/<board_id>/boardview/<board_id>.bvr
```
Then run:
```bash
python -m boardbrain.ingest
```
This creates (when parsing succeeds):
- `data/boardviews/<board_id>.json` (boardview parse metadata)
- `data/netlists/<board_id>.json` (boardview netlist, preferred source)
- `data/components/<board_id>.json` (boardview component index)
- `data/net_refs/<board_id>.json` (net → refdes measurement points)

### Troubleshooting BVR parsing
- Confirm the file is a real FlexBV `.BVR` (magic `BVR`/`BVR2`), not a renamed export.
- Ensure the board ID (e.g., `820-02020`) appears in the folder path or filename so it can be discovered.
- If parsing fails, delete `data/boardviews/<board_id>.json`, `data/netlists/<board_id>.json`, and `data/net_refs/<board_id>.json`, then re-run ingest.
- Check the ingest console output for `[boardview]` errors.
 - The Debug panel shows the boardview parser name and parse status. Source should read `boardview_bvraw3` on success.

### Truth source selection
- When a boardview parse succeeds, BoardBrain treats it as the primary truth source for netlists, components, and net→refdes mappings.
- If parsing fails, caches are not overwritten and the Debug panel will show the exact parse error and fallback reason.

## Requested Measurements JSON block (required)
Plans must include a machine‑readable JSON block at the end:
```
---REQUESTED_MEASUREMENTS_JSON---
{ "requested_measurements": [
  {
    "key": "CHECK_PPBUS_AON",
    "net": "PPBUS_AON",
    "type": "voltage",
    "prompt": "Measure PPBUS_AON to GND",
    "hint": "Use TP or large cap pad"
  }
] }
---END_REQUESTED_MEASUREMENTS_JSON---
```
If JSON parsing fails, BoardBrain will fall back to the legacy `KEY:`/`PROMPT:` parsing. If both methods fail, the previous requested‑measurements list is preserved (not cleared).
