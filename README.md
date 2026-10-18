# BoardBrain · repair bench preview

A local Mac workspace for board repairs: capture observations, inspect evidence,
choose the next test, and retain confirmed repair outcomes.

**Status: new runtime preview.** This rebuild is ready for code review and real-file
validation. It is not yet proven faster or more accurate on shop repairs. No live
AI diagnosis or native-window acceptance test is implied by the automated tests.

**First milestone: one boardview and its matching schematic.** These can be
reference files; a repair symptom or outcome is not required to build and validate
the import. Verify exact board/revision applicability and compare with FlexBV
5.1244 before using mappings on a physical repair. Diagnostic outcome evaluation
comes later, when a real repair is available.

## Start on a Mac

The intended everyday launch is **BoardBrain.app from Finder or the Dock**. Cases
reopen from local storage; launch does not re-ingest documents or call the AI.

For the prepared setup download, unzip it and double-click
**Setup-BoardBrain.command**. The one-time setup downloads dependencies, runs the
checks, builds the Apple Silicon app, checks the packaged runtime, imports an
included private reference bundle if present, and installs it in
`~/Applications/BoardBrain.app`. It then opens the app and reveals it in Finder.
Keep it in the Dock. No typed Terminal commands are needed.

Setup uses an existing native Python 3.11–3.13 installation. If one is missing,
it opens the official [Python 3.13 installer page](https://www.python.org/downloads/release/python-31316/).
Install its macOS package, then double-click setup again. Network access is needed
for the one-time dependency installation. If Apple Command Line Tools are missing,
setup opens Apple's installer; finish that installation and rerun setup. The full
Xcode application is not needed. The app runs independently of that
build environment afterward. If setup stops, its `BoardBrain-Setup.log` records
the failure; do not include an API key when sharing a log.

This is a development preview, not a notarized installer. If macOS blocks the
setup file on first open, use the per-file **Open Anyway** option in System
Settings → Privacy & Security. Do not disable macOS security globally.
An existing app at the installation destination is moved to a dated Previous
copy; saved cases live separately. Quit an existing BoardBrain before setup.

The **BoardBrain bench preview** GitHub Actions workflow also defines an Apple
Silicon app build after verification. Current runs stop before test steps and
produce no app artifact; the underlying runner failure remains unresolved.
The local setup path is the available delivery path, not a claim of a tested Mac
binary from this Linux development environment.

Open **Settings & equipment** to enter the API key, budget and instrument models.
The default app limit is $75/month with a $50 warning threshold. Cases and files
work without an API key. ChatGPT subscriptions and API billing are separate.

Tesseract is optional for scanned-page OCR and is not bundled in this preview.
Native PDF text and the visual page viewer work without it.

## Bench workflow

1. Open a **Board reference** to explore files without a symptom or outcome.
   The private setup package includes A2338 / 820-02020 in this mode. Search
   `U5200.A1` or `sheet 52`, and inspect **Files & photos** for import details.
   When a physical repair is ready, choose **Start repair from these files**.
   It reuses original files and indexed evidence but requires fresh scope checks;
   it does not copy reference notes, model answers, measurements or outcomes.
   A new repair without reference files is also available.
2. Save readings and observations. Quick entry supports `PP3V3 = 3.28 V`,
   `U7000.1 = 12 kΩ` and `USB_D+ = diode 0.42 V`; review conditions before saving.
3. Add photos, PDFs and boardviews. Check the visible import status and file scope.
4. Ask for the next test. Standard analysis defaults to **GPT-6.1 Sol**. Use
   **Deeper analysis** for **GPT-6 Astra** on difficult cases; it can take longer
   and costs more. Select photos explicitly; enable online research when needed.
   The response records its model, elapsed time and estimated cost. Exact lookups
   and saving readings incur no AI charge.
5. Correct an incorrect observation by retracting it and recording the correction.
6. Confirm a repair only after recording the fix and post-repair verification.
   Keep suspected causes separate from verified causes.

Plain `BVRAW_FORMAT_3` (including the observed `50.50.` prefix) and CSV with
`ref,pin,net` columns have strict connectivity adapters. Malformed component
records are excluded and listed in import details; a partial import is never a
complete netlist. Global structural errors still reject the whole import.
Optional CSV columns: `side` (`T`, `B`, `O`), `x`, `y`. This is an explicit
interchange format, not a claim about a FlexBV export feature. Other boardview
variants remain stored and available to FlexBV until their parsers are validated.

Exact lookup preserves boardview pin numbers and separate pin labels, including
ball labels such as `A1`. It also returns matching schematic pages when a net has
many pins. Supported native contents tables supply separate PDF page and schematic
sheet labels; the original PDF remains the source for interpreting the drawing.

The new runtime uses a separate data directory/database. It leaves the old data
and legacy app intact. See [the rebuild notes](docs/BENCH_REBUILD.md) for evidence
rules, limitations, backup/restore, research sources and the real-board acceptance
gate. The [legacy setup instructions](docs/LEGACY.md) remain available.

## Development and verification

```bash
python3 -m pip install -r requirements-bench.txt
python3 -m boardbrain_v2 --browser
python3 -m unittest discover -s tests_v2 -v
node --check boardbrain_v2/static/app.js
```

Use `--data-dir /path/to/isolated/test-data --serve` for a test server. Its private
`runtime.json` contains the ephemeral port and bearer token. Do not publish it.
The browser workflow test requires Playwright and its Chromium installation:

```bash
node tests_v2/ui_smoke.cjs /path/to/isolated/test-data/runtime.json
```

The tests use synthetic fixtures and a fake model transport. Customer boardviews,
private case data and API keys must not be committed to this public repository.
