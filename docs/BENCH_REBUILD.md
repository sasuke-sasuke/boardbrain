# BoardBrain bench rebuild

This is a working preview of a new runtime, isolated from the legacy Streamlit app.
It is not yet a validated diagnostic product. Real board files, model access and
the native Mac window must be tested before relying on it in production repairs.

## Decisions

- One Mac at the bench; typed observations and selected photos first. Voice later.
- FlexBV remains the board viewer; the initial configuration records version 5.1244.
- SQLite and original files stay local. Only explicitly requested AI work is paid.
- Start with a $75/month app limit and a $50 warning threshold; both are editable.
- Scope/logic-analyzer procedures require exact instrument/probe details and ground
  domain checks. The assistant is told the technician is a beginner with those tools.
- Confirmed cases require a technician-confirmed fix and post-repair checks. A
  verified fix does not automatically mean the root cause is proved. Stored cases
  are retrieved as references; they do not automatically train a model.

The proposed Tauri wrapper was replaced for this first Mac-only build by a smaller
Python/Cocoa wrapper using pywebview. The local service and UI stay separate. This
keeps one Python runtime and avoids introducing Rust and a JavaScript build chain
before real-board validation. Native UI testing is still required.

## Implemented

- An authenticated loopback service and a bench UI with cases, next tests, evidence,
  readings, photos, file statuses, confirmed-case retrieval and a repair timeline.
- Explicit measurement capture, SI prefix conversion, negative readings, OL,
  polarity/reference/power state, correction history, and condition-aware completion.
- Exact original net identities: `USB_D+`, `USB_D-`, `RESET#`, `RESET` stay distinct.
- Strict plain `BVRAW_FORMAT_3` records and documented CSV interchange. Component,
  pin, net, side and coordinates are preserved; relative origins are handled. A
  malformed record invalidates the whole connectivity result. Unknown structural
  fields or binary formats do not fall back to string adjacency.
- Per-file scope checks, visible conflicts, technician-checked individual mappings,
  hashed originals, duplicate suppression and background imports. No ingest-on-launch.
- PDF native page text and source-page viewing; explicit, cached OCR for selected
  scanned pages when Tesseract is present. Neither OCR nor text proximity creates
  pin connectivity. PDF drawings are not automatically electrically reconstructed.
- JPEG/PNG/HEIC photo normalization; selected photos sent at up to 2048 px, with
  orientation corrected and EXIF removed. The original remains local.
- A bounded Responses API tool loop with local evidence lookup, confirmed repair
  references, optional web search, structured plans, source-id checks, and exact
  source quotes for numeric expectations. No invented confidence percentages.
- No automatic retries of paid calls. Cancellation stops further work; a request
  already received by the provider may still incur a charge. Missing billing
  information or a dropped connection keeps a conservative reserved charge.
- $3 reserved per assistant turn; actual estimated usage replaces the reservation
  when available. This is an app-local, conservative budget guard, not a provider
  account-wide hard cap. Other apps and invoice adjustments are outside it.
- API keys in the system keychain, no plaintext key in exports, a single-instance
  launcher, an Apple Silicon build recipe, a CI package/smoke test, and backups.

## Accuracy boundaries

Schema validation does not prove the diagnosis or that a quoted specification
applies to a circuit. Identity gates and scoped evidence reduce specific classes
of mistakes; free-text suggestions can still be wrong. The native Mac UI, live AI
calls and real proprietary boardviews need actual trials.

Only literal plain BVR3 and the interchange CSV are implemented as connectivity
adapters in this preview. `.brd`, `.tvw`, encrypted/binary `.bvr`, `.pcb`/XZZ and
other formats are retained for FlexBV, with an explicit unsupported status.
There is no assumed FlexBV export command or undocumented IPC/API integration.
Board scope confirmation checks identity; it is not certification of parser accuracy.

Without documents, the useful path is observations, clear photos, checked component
markings and technician-recorded temporary points. The assistant can reuse a point
the technician has measured; it cannot invent a pin mapping from a photo. Datasheet
and online search references remain research leads until applicability is checked.

Powered current injection, hot-air/rework, live mains probing, instrument control,
and automatic component replacement are outside the preview's action workflow.
These are product limits, not claims that generic text validation detects every
possible unsafe or incorrect instruction.

## Real-board acceptance gate

1. Supply 2–4 actual boardviews across the formats used in the shop, matching
   schematics where available, and FlexBV screenshots of several exact pins.
2. For each supported variant, compare pin numbers, net spelling, side, coordinate
   transforms, total counts, disconnected pins and duplicate/subboard behavior.
   Add legally shareable synthetic/minimal regression fixtures; keep customer and
   proprietary data out of this public repository.
3. Replay repairs with known outcomes while hiding the fix from the assistant.
   Include no-schematic, contradictory-reading, unknown-board and scanned-PDF cases.
4. Compare bench minutes, repeated measurements, corrections, unsupported exact
   claims, provider cost and response latency against the technician's baseline.
   Initial goals from the research are targets, not measured performance promises.
5. Verify native Mac launch/relaunch, Keychain prompts, photo uploads, PDF viewing,
   downloads, cancel/retry, backup/restore and offline case access on the actual Mac.
6. Only after these gates should the preview replace the existing shop workflow.

## Data and recovery

Default Mac data directory: `~/Library/Application Support/BoardBrain/`.
The new database is `boardbrain-v2.sqlite3`; it does not read, overwrite or delete
the legacy `data/boardbrain.sqlite3`, `.env`, Chroma index or original file folders.
Automatic legacy migration is pending a representative backup: older measurements
lack power state/probe polarity, and old AI plans cannot be promoted to verified
facts. Preserve the old installation until migration is validated.

**Back up** creates a consistent SQLite snapshot plus all original hashed files in
a zip and offers it for download. Preview/OCR derivatives are disposable and are
not included. To restore manually, quit BoardBrain, preserve the current data
directory, and extract a trusted backup's `boardbrain-v2.sqlite3` and `files/`
into an empty BoardBrain data directory. Photo previews regenerate from originals
when opened. OCR can be rerun if its disposable cache is needed. Never mix the
backup database with unrelated old WAL/SHM files.

## Verification recorded for this draft

- 34 local Python tests passed; Python, JavaScript and shell syntax checks passed.
- The local service was started in a fresh temporary data directory and its
  authenticated state endpoint responded successfully without an API key.
- The browser workflow test is provided but could not execute here: Chromium's
  download failed. Native window layout/interaction has not been visually verified.
- The first GitHub workflow run failed before any runner steps started; the Mac
  build was skipped. Job logs were unavailable through the connector. No tested
  `.app` artifact has been produced by that run.
- Live model calls, Keychain behavior and actual shop boardviews are unverified.

## Primary references used

- [OpenBoardView BVR3 field semantics](https://github.com/OpenBoardView/OpenBoardView/blob/master/src/openboardview/FileFormats/BVR3File.cpp)
- [FlexBV manual](https://pldaniels.com/flexbv5/manual/flexbv-manual.html)
- [PyMuPDF OCR](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html)
- [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [GPT-6.1 Sol model](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [API pricing](https://developers.openai.com/api/docs/pricing)
- [pywebview API](https://pywebview.flowrl.com/api/)
- [PyInstaller usage](https://pyinstaller.org/en/stable/usage.html)
- [macOS keyring backend](https://keyring.readthedocs.io/en/latest/)

Rates/model compatibility were checked on 2026-10-17; update the bounded provider
adapter and rates together when changing the model selection.
