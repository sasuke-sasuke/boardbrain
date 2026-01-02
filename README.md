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

## Usage
1) Create a case (use the recommended Case ID format).
2) Upload at least one **schematic PDF/page** or **FlexBV boardview screenshot**.
   (You can store full boardview files too, but screenshots are required for evidence.)
3) Enter USB‑C readings (ex: `5V / 0.20A`), and any key ohms/diode readings.
4) Click **Generate plan**.

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
        schematic.pdf
        notes.md
        boardview_screens/
          PPBUS_AON.png
          USB_C_PORT_A.png
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

