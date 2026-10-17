# BoardBrain · repair bench preview

A local Mac workspace for board repairs: capture observations, inspect evidence,
choose the next test, and retain confirmed repair outcomes.

**Status: new runtime preview.** This rebuild is ready for code review and real-file
validation. It is not yet proven faster or more accurate on shop repairs. No live
AI diagnosis or native-window acceptance test is implied by the automated tests.

## Start on a Mac

The intended everyday launch is **BoardBrain.app from Finder or the Dock**. Cases
reopen from local storage; launch does not re-ingest documents or call the AI.

Two build paths are provided:

1. The **BoardBrain bench preview** GitHub Actions workflow builds an Apple Silicon
   `.app` zip after the test job. Download its artifact when that run succeeds.
   This is an ad-hoc-signed development preview, not a notarized installer; macOS
   may require an explicit first-open approval through Privacy & Security.
2. For a local source build, install Python 3.11+ from python.org once, then
   double-click **Build-BoardBrain.command** in the downloaded project folder.
   It creates an isolated build environment and reveals `dist/BoardBrain.app`.
   Drag the app to Applications. Future launches require no Terminal commands.

Open **Settings & equipment** to enter the API key, budget and instrument models.
The default app limit is $75/month with a $50 warning threshold. Cases and files
work without an API key. ChatGPT subscriptions and API billing are separate.

Tesseract is optional for scanned-page OCR and is not bundled in this preview.
Native PDF text and the visual page viewer work without it.

## Bench workflow

1. Create a repair with the symptom and board/revision if known.
2. Save readings and observations. Quick entry supports `PP3V3 = 3.28 V`,
   `U7000.1 = 12 kΩ` and `USB_D+ = diode 0.42 V`; review conditions before saving.
3. Add photos, PDFs and boardviews. Check the visible import status and file scope.
4. Ask for the next test. Select photos explicitly; enable online research when
   needed. Exact lookups and saving readings incur no AI charge.
5. Correct an incorrect observation by retracting it and recording the correction.
6. Confirm a repair only after recording the fix and post-repair verification.
   Keep suspected causes separate from verified causes.

Plain `BVRAW_FORMAT_3` and CSV with `ref,pin,net` columns have strict connectivity
adapters. Optional CSV columns: `side` (`T`, `B`, `O`), `x`, `y`. This is an explicit
interchange format, not a claim about a FlexBV export feature. Other boardview
variants remain stored and available to FlexBV until their parsers are validated.

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
