# BoardBrain bench rebuild

This is a working preview of a new runtime, isolated from the legacy Streamlit app.
It is not yet a validated diagnostic product. Real board files, model access and
the native Mac window must be tested before relying on it in production repairs.

## Decisions

- One Mac at the bench; typed observations and selected photos first. Voice later.
- FlexBV remains the board viewer; the initial configuration records version 5.1244.
- Validate one matching boardview/schematic pair and exact revision before adding
  other boards or parser variants. Reference files do not require a repair outcome.
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
- Strict plain `BVRAW_FORMAT_3` records (including the observed `50.50.` prefix)
  and documented CSV interchange. Component, pin number, separate label, net,
  side and coordinates are preserved; relative origins are handled. A malformed
  component is quarantined as a whole at explicit component boundaries, with its
  source lines and reason visible. Every occurrence of a duplicate component is
  quarantined. Unknown global structure rejects the entire import. A partial import
  is explicitly incomplete. No unknown record becomes guessed connectivity.
- Per-file scope checks, visible conflicts, technician-checked individual mappings,
  hashed originals, duplicate suppression and background imports. No ingest-on-launch.
- PDF native page text and source-page viewing; explicit, cached OCR for selected
  scanned pages when Tesseract is present. Neither OCR nor text proximity creates
  pin connectivity. PDF drawings are not automatically electrically reconstructed.
- Coordinate-based reading of explicit native PAGE/CSA/CONTENTS tables keeps PDF
  page position separate from schematic sheet labels. Blank table titles remain
  blank; ambiguous rows are discarded. Other table layouts receive no guessed labels.
- Mixed lookup reserves space for schematic evidence when a rail has many pins,
  exposes result truncation, respects identifier punctuation in document matching,
  supports exact pin-label lookup, and rejects ambiguous number/label collisions.
  Looking up a pin can also return its component's schematic page; that is a
  document reference, not an inferred wire or pin connection.
- JPEG/PNG/HEIC photo normalization; selected photos sent at up to 2048 px, with
  orientation corrected and EXIF removed. The original remains local.
- A bounded Responses API tool loop with local evidence lookup, confirmed repair
  references, optional web search, structured plans, source-id checks, and exact
  source quotes for numeric expectations. No invented confidence percentages.
- No automatic retries of paid calls. Cancellation stops further work; a request
  already received by the provider may still incur a charge. Missing billing
  information or a dropped connection keeps a conservative reserved charge.
- $3 reserved per Sol turn, $15 per Astra turn; actual estimated usage replaces the reservation
  when available. This is an app-local, conservative budget guard, not a provider
  account-wide hard cap. Other apps and invoice adjustments are outside it.
- API keys in the system keychain, no plaintext key in exports, a single-instance
  launcher, an Apple Silicon build recipe, a CI package/smoke test, and backups.

## Models and jobs

The legacy app defaulted to `gpt-4o`. This preview uses the Responses API with
`gpt-6.1-sol` as its default. Model access and live performance are unverified until
an API account and the first board's evidence are available.

| Job | Initial implementation | Reason |
| --- | --- | --- |
| Boardview parsing, exact pins/nets, units, lookup | Deterministic local code | Preserve identities and validate structural data without model guesses. |
| Normal diagnostic questions, typed observations, selected photos, optional source research | GPT-6.1 Sol, medium reasoning | Initial quality/cost balance to test at the bench. |
| Difficult cases or reconsidering an unproductive diagnosis | GPT-6 Astra, high reasoning through Deeper analysis | A more capable reasoning option, evaluated on the same evidence. |
| Case tags, clerical cleanup, concise indexing | Existing local storage for now; GPT-6 Luna is a later candidate | Add a small model only after a concrete task demonstrates useful savings and acceptable errors. |

Deeper analysis is a per-question choice. It uses Astra for that turn's bounded
tool loop, retains the same evidence gates and does not change the normal model
setting. The model, reasoning effort, elapsed time and cost estimate are recorded.
Online research remains an independent choice. There is no automatic second-model
vote, automatic paid retry, silent fallback, or validated difficulty classifier.
Agreement between models is not independent verification of a pin or diagnosis.
Previous preview settings for GPT-5.4 remain readable; new installations use Sol.

Checked official standard short-context rates on 2026-10-18: Sol $2 input / $10
output, Astra $10 input / $50 output per million tokens. Reasoning output also
consumes the output budget. The app conservatively estimates all input at the
cache-write rate ($2.50 / $12.50 respectively), without assuming cache savings.
It reserves $3 for Sol or $15 for Astra before starting and releases the unused
amount when usage is returned. Those reservations are not predicted turn prices;
timeouts without usable billing details keep the reservation as uncertain spend.
The monthly app limit starts at $75 with a $50 warning. Access, latency and actual
repair costs must be measured; no number of monthly repairs is promised.

Sol and Astra are the initial candidates, not a claim that either is universally
best for electronics diagnosis. The one-board trial determines the production
default and useful escalation conditions. Any later provider/model must pass the
same board-specific evaluation before routing diagnostic work to it.

## Accuracy boundaries

Schema validation does not prove the diagnosis or that a quoted specification
applies to a circuit. Identity gates and scoped evidence reduce specific classes
of mistakes; free-text suggestions can still be wrong. The native Mac UI, live AI
calls and real proprietary boardviews need actual trials.

Only plain BVR3 with the two recognized headers and the interchange CSV are implemented as connectivity
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

1. Supply **one boardview and its matching schematic PDF** as reference data. No
   repair or outcome is required for this stage. Record file hashes and known board
   identifiers. Leave an unknown revision unknown; physical-board applicability
   and FlexBV comparisons remain a separate check before probing.
2. Validate that file's adapter against FlexBV 5.1244: total parts/pins/nets,
   exact pin numbers and net spelling, side, coordinate transforms, disconnected
   pins and any duplicate/subboard behavior. Compare all records where a trusted
   reference is available, plus technician-checked points on both sides and at
   orientation-sensitive locations. Sampling alone does not validate every pin.
   Check schematic page references and quoted values in the original PDF.
3. Build a small, checked question set from that board: exact lookups, relevant
   schematic passages, missing evidence, contradictory readings, and next-test
   decisions. Preserve the correct answers privately. Exact lookup/reference
   questions must have zero unresolved mismatches before diagnostic evaluation.
4. Compare Sol and Astra on the checked reference questions using identical evidence.
   When a real or previously resolved repair becomes available, replay it with the
   outcome hidden. Give Sol and Astra identical
   evidence snapshots, without either seeing the other's answer. Record factual
   errors, unnecessary/repeated tests, useful next steps, response time, cost and
   technician corrections. Log the model/settings and repeat ambiguous results.
   Track bench minutes against the technician's baseline. A single board/case
   establishes a narrow pilot, not general diagnostic accuracy or guaranteed speed.
   Do not manufacture an outcome for a reference-only document pair.
5. Verify launch/relaunch, Keychain, photos, PDF viewing, cancel/retry, backup/restore
   and offline case access on the actual Mac. Complete one supervised repair
   workflow from intake through measured post-repair verification.
6. After that pilot passes, add the next board or format one at a time with its
   own evidence checks. Then evaluate no-schematic and scanned-document workflows.
   Do not expand coverage or replace the shop workflow based on synthetic tests.

Private boardviews, schematics and repair histories stay outside this public
repository. Only legally shareable synthetic/minimal regression fixtures belong
in the codebase. Current gate status: the first reference-only pair has been
inspected and imported locally. Full FlexBV/physical-board checks, a real repair,
and live model acceptance remain outstanding.

## Reference-pair validation

Keep board-specific counts, mappings, excerpts and timing results in private
validation notes. For each reference pair, compare accepted pin fields with the
original, review excluded records, verify PDF page/sheet mappings, and compare
selected connections with the matching board viewer. Extraction fidelity does
not establish source correctness or applicability to a physical board revision.

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

- Version 0.2.2 / setup revision 4 adds a large schematic viewer with page
  navigation, zoom, sharper rendering at higher magnification, scrolling and
  original-PDF opening. Pin results show the component, label, connected net,
  source and applicability; raw source fields are available in a collapsed panel.
  Schematic text matches are identified as page references, not proof of a pin's
  connection. No database or evidence-authority rules changed.
  On Linux Python 3.14.8, 58 tests were collected: 57 passed and the native Mac
  signing check was skipped. PDF tests cover resolution bounds, page bounds and
  case authorization. Both browser workflows passed in Chromium 140 using
  synthetic files: the existing bench workflow and the new document workflow.
  The latter covers readable pin results, viewer size, sharp zoom, panning, page
  navigation, invalid input, visible errors/retry, narrow-window layout,
  cancellation and photo preview. Native Mac verification of this viewer remains
  a separate acceptance check.
- Setup revision 3 handles signing-blocking Finder/resource-fork metadata.
  Setup now writes build/spec/dist output in a private temporary
  directory and cleans only the two blocking attributes on generated app copies.
  The bundle is explicitly ad-hoc signed and strictly verified both after build
  and after the installation copy. No source files, existing app, case data,
  quarantine attributes or global security settings are changed by cleanup.
  Signing errors still stop installation. Four portable regression checks cover
  cleanup scope, symlinks, idempotency and failure propagation. A fifth test uses
  native macOS xattr/codesign with a synthetic bundle to reproduce and repair the
  reported failure; it is skipped on Linux and runs during Mac setup and Mac CI.
  Final local check: 57 collected, 56 passed, one native-Mac test skipped on
  Linux Python 3.14.8; setup/build shell syntax checks passed. Native signing of
  this revision remains unverified until it runs on macOS.
- Setup revision 2 accepts an existing native Python 3.14 and checks python.org,
  Homebrew and PATH locations, including versioned executables. The old 3.13-only
  prerequisite message was misleading for an installed 3.14. Dependency floors
  were raised for image handling, the native window and app packaging.
  On Linux Python 3.14.8, installation of the build requirements, all 52 tests,
  the synthetic HTTP workflow passed. Python 3.12
  also passed all 52 tests. CI now includes 3.14 and uses it for the Mac build;
  these checks do not establish native macOS window or Keychain compatibility.
- 52 local Python tests passed on 2026-10-18, including Astra routing with the same
  case evidence, per-model cost settlement, budget rejection before any request,
  network uncertainty without fallback, and evidence rejection for both models.
  Python compilation and JavaScript syntax checks passed after the routing change;
  shell syntax checks passed in the original rebuild. New tests cover prefixed
  BVR3 recognition, component quarantine, global errors, duplicate identities,
  pin labels/ambiguity, schematic result availability and PDF page/sheet indexing.
- The local HTTP workflow passed against a new process: startup/authentication,
  reference creation, boardview/PDF/photo import, exact lookup, PDF/photo rendering,
  and reuse in a new repair with fresh source IDs and cleared scope checks.
  The same check is run against the built Mac executable by the setup script.
- Database schema 2 migrates existing schema-1 cases to kind `repair`. New reference
  workspaces cannot accept repair outcomes or physical measurement/pin records.
  A regression test verifies existing data survives the migration.
- Browser testing was initially blocked by a Chromium download failure. Both
  workflows now pass with the available Chromium 140 build, as recorded above.
  Native macOS window interaction still requires its own acceptance check.
- GitHub workflow runs, including a retry of the latest run, failed before any runner steps started; the Mac
  build was skipped. Job logs were unavailable through the connector. No tested
  `.app` artifact has been produced by that run.
- Live model calls, Keychain behavior and FlexBV comparison are unverified. Private reference validation is tracked separately.

## Primary references used

- [Apple QA1940: signing-blocking Finder/resource-fork metadata](https://developer.apple.com/library/archive/qa/qa1940/_index.html)
- [PyInstaller macOS code signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)
- [OpenBoardView BVR3 field semantics](https://github.com/OpenBoardView/OpenBoardView/blob/master/src/openboardview/FileFormats/BVR3File.cpp)
- [FlexBV manual](https://pldaniels.com/flexbv5/manual/flexbv-manual.html)
- [PyMuPDF OCR](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html)
- [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [GPT-6.1 Sol model](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [GPT-6 Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [Model selection](https://developers.openai.com/api/docs/guides/model-selection)
- [API pricing](https://developers.openai.com/api/docs/pricing)
- [pywebview API](https://pywebview.flowrl.com/api/)
- [PyInstaller usage](https://pyinstaller.org/en/stable/usage.html)
- [macOS keyring backend](https://keyring.readthedocs.io/en/latest/)

Rates/model compatibility were checked on 2026-10-18; update the bounded provider
adapter and rates together when changing the model selection.
