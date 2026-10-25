# Assessment traceability and implementation status

This matrix maps the supplied Engineering Assessment 2 brief to the repository as reviewed on 2026-10-25 and updated after real-source and generated render fixtures passed. A live backend smoke flow and container health/readiness were observed, but they do not substitute for the full browser and assessment-session evidence. "Implemented" means code exists; it does not imply all required runtime evidence has been captured. Full pass status requires reproducible scenarios and output artifacts.

## Must-haves

| # | Requirement | Current implementation and evidence gap | Status |
|---|---|---|---|
| 1 | Preserve original media; make edits reversible; render separately | A Compose real-media smoke flow exercised upload, edit, render, and download. Timeline API tests verify undo/redo state; the UI now reads server history and redo depths. Need explicit source-hash immutability and restore/render regression artifacts. | Partial |
| 2 | Anchor edits to content through cut, speed, reorder, and restore | Captions/effects target transcript word IDs; renderer maps active IDs to output time and splits caption spans across removed words. Cuts now prune dead caption/effect anchors, preserve surviving anchors, report removals, and undo restores prior state; unit/API tests cover caption and effect pruning. Rendered proof across all required edits and restore remains outstanding. | Partial; render proof missing |
| 3 | Return Applied, Needs clarification, or Rejected in required formats | Typed response models and session prompt route exist. Clarification options can be returned; runner supports a limited ordinal answer mapping. Full response shape and multi-turn continuation are not verified for every intent. | Partial |
| 4 | Keep timeline predictable; use the LLM for language-to-operation mapping | Timeline mutations and canonical hashes run in Python; mock adapter supports deterministic cases. Need repeated full-session determinism evidence, including Groq behavior boundaries. | Partial; measurement missing |
| 5 | Render to timeline with A/V sync and duration within one frame | Source-edge and stream-tail retention are implemented. Generated CFR, VFR, no-audio, boundary-cut, and speed-plus-bleep fixtures pass. The hash-matched assessment source/session render passes: 323.988027 s timeline vs 324.000 s output (0.2993 frames at 25 fps), A/V start offset 0, and A/V endpoint offset 0.014195 s (0.3549 frames). Other mixed edits and multi-speaker media remain untested. | Partial; real and generated fixtures pass |
| 6 | Support the listed edit operations and history semantics | Timeline supports cut/keep/reorder/speed/mute/bleep/manual and automatic captions/caption style/BGM/common-filler removal/transcript-gap trimming/undo/redo/restore. Long gaps use word timing as a silence proxy; waveform-based silence detection and required history scenarios remain incomplete. | Not met |
| 7 | Use and report one consistent time policy | Schemas and interpreter describe current timeline timing, but the policy and edge cases are not comprehensively surfaced and verified across responses. | Partial |
| 8 | Meet Integration Standard, provide session runner, and demonstrate live workflow | YAML runner supports local media or runner-side URL download, prompt steps, limited ordinal clarification answers, job polling, and result packs. Sample media is a placeholder; live run and demo evidence are absent. | Partial; live evidence missing |

## Integration Standard

| # | Requirement | Current implementation and evidence gap | Status |
|---|---|---|---|
| 1 | One-command Compose startup and keyless unit/integration/E2E tests | Compose startup and a real-media API upload/transcription/edit/render/download smoke flow have been run; pytest is keyless and now covers API history and worker cancellation behavior. No browser E2E or multi-speaker workflow has been verified. | Partial |
| 2 | OpenAPI 3.1 parity and versioned routes | FastAPI generates OpenAPI 3.1 at `/openapi.yaml`; `scripts/export_openapi.py` writes the checked-in schema. Tests check version and route prefix, but do not prove complete schema equality against a running service. | Partial |
| 3 | Long tasks use 202 jobs, status/progress/result, optional signed callback | Upload and render return 202 job IDs; Redis Streams back jobs, job status includes progress, and render results have an API download route. Signed callbacks are absent; queue recovery lacks integration/crash evidence. | Partial |
| 4 | Batch runner and safe retries with same key returning same job | Runner accepts a file or folder and creates a result pack per session. Upload jobs use media fingerprint plus Redis key mapping; eligible JSON POST routes use middleware response replay and fingerprint conflict detection. These paths lack full concurrency/recovery integration evidence. | Partial |
| 5 | Structured JSON errors and documented error codes | FastAPI handlers provide a structured envelope; worker failures are stored with a message. A complete error-code catalog and consistency checks across all failure paths are missing. | Partial |
| 6 | Environment-driven configuration for paths, models, and limits | Core provider, Redis, storage, Whisper, upload, job, and context settings use Pydantic Settings. Renderer profiles, callbacks, and several deployment behaviors are not independently configurable. | Partial |
| 7 | Upload or URL input; S3-compatible output and downloadable links | API file upload and S3-compatible source/render storage were exercised in one Compose smoke flow; render results download through the API. The runner can fetch HTTP(S) URLs, but the API lacks direct URL intake and storage retry/failure behavior lacks verification. | Partial |
| 8 | Structured logs with timestamp, level, job ID, correlation ID; health/readiness | Request middleware emits structured timestamped request logs and health/readiness routes exist. Worker lifecycle logs still use standard formatted logging and lack correlation propagation. | Partial |
| 9 | Result manifest with models/settings/seed/timing/cost/warnings/rights | Render job stores a partial manifest with renderer, source/timeline hashes, warnings, rights statement, and AI-media flag. Provider/model versions, complete settings, seed, timings, and cost estimate are missing. | Partial |
| 10 | Batch runner | YAML runner supports single-file and folder execution, job polling, and per-session result files. It lacks full verified clarification handling, artifact validation, and a passing run against assessment videos. | Partial; runtime evidence missing |

## Level and submission status

- **Core:** not achieved until all must-haves pass, the brief's eight-step worked example is reproduced, and single- and multi-speaker videos have verified outputs. The real-media smoke flow is evidence of the basic API path, not completion of these criteria.
- **Advanced:** not verified. Ambiguity handling, semantic matching, caption reattachment/removal, conflict rules, multi-speaker intent, and speed-caption constraints need implementation and scenarios.
- **Outstanding:** not verified. Silence snapping, VFR/no-audio handling, confidence and overlap warnings, long-video latency, OpenTimelineIO export, and the required 40+ scenarios need implementation or measurements.
- **Submission artifacts:** the 15-session pack, complete test evidence index, rendered output pack, measured metrics, demo recording/live URL, and completed submission form are not present.
- **Frontend:** requested by the candidate, but not required by the supplied brief. UI features and static assets exist; a real browser workflow has not been validated.

## Recommended implementation order

1. Verify the browser workflow and run upload -> transcription -> edit -> render -> download with controlled single-speaker and multi-speaker fixtures; retain the artifacts.
2. Fix correctness gaps revealed by those runs; complete required operations, clarification turn behavior, time-policy reporting, and history scenarios.
3. The real-source render and generated CFR/VFR/no-audio/boundary/speed-plus-bleep fixture measurements are recorded. Add rendered reorder/restore and remaining hard-case evidence; the local output and metrics files are git-ignored and must be copied into the submission pack.
4. Complete idempotent retry semantics, OpenAPI structural parity, structured worker logs, and the full output manifest; decide whether direct API URL intake and signed callbacks are in scope.
5. Run the eight-step example and build the required session/test evidence packs and demo.
6. Polish and validate the requested browser Studio over the stable API.
