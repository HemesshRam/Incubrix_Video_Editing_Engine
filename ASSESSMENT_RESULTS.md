# IncuBrix Assessment 2: Evidence-Based Results

**Review date:** 2026-10-25 (updated after real-source render and container verification)
**Project:** IncuBrix Prompt-Based Video Editing Engine
**Submission snapshot:** The candidate is checking the browser workflow locally. That manual check is in progress; its result is not yet included as verified evidence below.
**Assessment references:** `F:\IncuBrix_Assessment_2_Prompt_Based_Video_Editing_Engine_Submission_Form.xlsx`, `F:\IncuBrix_Assessment_2_Prompt_Based_Video_Editing_Engine_Brief.pdf`, repository source, [ASSESSMENT_TRACEABILITY.md](ASSESSMENT_TRACEABILITY.md), [REPORT.md](REPORT.md), [README.md](README.md).

## Summary

The project has a substantial backend implementation and a reported successful real-media API smoke flow. The current project virtual environment run shows **64 passed, 0 failed, 27 FastAPI/Starlette deprecation warnings**. That verifies the current automated suite; it does not establish that every assessment requirement passes in the browser or across all required source types and scripted scenarios.

| Measure | Evidence-based status |
|---|---:|
| Automated tests, current project `.venv` run | 64 / 64 passed |
| Must-haves fully evidenced as passing | 0 / 8 |
| Integration Standard points fully evidenced as passing | 0 / 10; all are partial in the current traceability review |
| Core level | Not achieved / not demonstrated |
| Advanced level | Not demonstrated |
| Outstanding level | Not demonstrated |
| Assessment metric categories with an initial measured result | 1 / 6 |

These counts distinguish **implemented or partly exercised** from **fully evidenced as passing**. The workbook says all must-haves must pass for the submission gate. Based on evidence currently available, do not mark a must-have Yes or claim Core. The ten Integration rows are assessed as Partial in the current traceability matrix; if those Partial ratings and evidence are accepted when entered into the workbook, that corresponds to a provisional 10/20 Integration points, not a verified score. The workbook's hidden tests and assessor review can change the final result.

## Must-haves

| # | Assessment criterion | Current result | Evidence and gap |
|---:|---|---|---|
| 1 | Preserve the original; make every edit reversible; render separately | **Partial** | Source media is stored separately from edit state; timeline history, undo/redo, source hashing, and render jobs exist. Tests cover history behavior. A recorded source-hash immutability and restore/render regression session is still needed. |
| 2 | Keep captions, mutes, bleeps, and effects attached through edits | **Partial** | Anchors use stable transcript word IDs and renderer timing follows active order. Unit/API coverage exists, but rendered proof across cuts, speed changes, reorder, and restore is missing. |
| 3 | Return Applied, Needs clarification, or Rejected in the required formats | **Partial** | Typed response schemas, clarification paths, and rejection handling exist. Full-format and multi-turn behavior for every assessment session has not been run and retained as evidence. |
| 4 | Deterministic timeline; AI only maps language to structured operations | **Partial** | Timeline updates and hashes are implemented in ordinary code; the LLM adapter provides structured operations. Three-run equality for the same complete scripted sessions has not been measured. |
| 5 | Audio/video sync and output length within one frame | **Partial; verified fixtures pass** | CFR, VFR, no-audio, boundary-cut, and speed-plus-bleep generated fixtures passed. The hash-matched real assessment source/session was re-rendered after fixes: 323.988027 s timeline, 324.000 s output (0.299 frames duration error at 25 fps), 0 s A/V start offset, and 0.014195 s A/V endpoint offset (0.355 frames). Additional mixed edits and multi-speaker inputs remain unverified. |
| 6 | All required operations and history semantics work | **Not demonstrated as met** | Code supports many operations, including cut/keep/reorder/speed, mute/bleep, captions, filler/pause handling, and undo/redo/restore. The required complete scripted scenarios and exact one-instruction/one-undo behavior are not fully evidenced. |
| 7 | One clear, consistently applied time policy, stated in responses | **Partial** | Current-timeline anchors and source/timeline timing exist in code. Required post-cut time interpretation and response disclosure have not been verified across scenarios. |
| 8 | Integration Standard, runner, and live demo work for the review period | **Partial** | API, Compose configuration, YAML runner, and one backend media smoke flow are documented. The candidate is currently checking the frontend at `http://localhost:8000/`; no completed browser workflow results, full assessment sessions, live demo, or retained frontend artifacts are available in this snapshot. |

## Integration Standard

The form awards 2 marks for Yes, 1 for Partial, and 0 for No. Current self-review rates each item Partial, pending stronger evidence and some implementation work.

| # | Integration point | Current result | Main gap |
|---:|---|---|---|
| 1 | One-command start, tests, and complete `.env.example` | **Partial** | Compose and keyless tests exist; browser E2E, full one-command unit/integration/E2E verification, and an audited environment-variable inventory are still needed. |
| 2 | Exact OpenAPI 3.1 match for the running API | **Partial** | OpenAPI 3.1 and versioned routes exist; full structural parity against the running service is not tested. |
| 3 | Background jobs and optional signed callback | **Partial** | Upload/render jobs return IDs and have status/result routes. Signed HMAC callback support is absent; crash recovery lacks runtime evidence. |
| 4 | Safe idempotent retries for every POST | **Partial** | Idempotency middleware and upload content-hash handling exist; concurrency/retry integration evidence for all POST routes is missing. |
| 5 | Structured error envelope and documented codes | **Partial** | Structured API errors and a catch-all handler exist; a complete code catalog and consistency verification across worker/API failures are missing. |
| 6 | Environment-driven settings, paths, models, and limits | **Partial** | Most major settings are environment-driven. Some renderer and deployment behavior is not independently configurable or fully audited. |
| 7 | Upload or URL input, S3-compatible output, download link | **Partial** | Upload, S3-compatible storage, result download, and runner-side URL retrieval exist. Direct API URL intake and storage failure/retry tests are absent. |
| 8 | Structured logs with correlation and health/readiness | **Partial** | Request logs and health/readiness routes exist; worker logs do not yet carry the complete structured correlation context. |
| 9 | Complete output manifest | **Partial** | A partial manifest includes renderer and source/timeline details. Provider versions, complete settings, seed, timings, and cost estimate are absent. |
| 10 | Batch runner writes repeatable results | **Partial** | YAML file/folder runner and result outputs exist. Full assessment runs, clarification cases, artifact validation, and retained evidence pack are missing. |

**Provisional points:** if the workbook is filled with the ten Partial ratings above, Integration would total **10/20**. This is a self-assessment estimate, not an assessor result. The assessment gate still requires all eight must-haves.

## Levels

- **Core: not achieved.** The must-have gate is not evidenced as passed. The eight-step example and verified single-speaker plus multi-speaker outputs are also outstanding.
- **Advanced: not demonstrated.** Caption movement/removal rules, semantic matches, ambiguity/reference handling, conflict behavior, multi-speaker intent, and minimum readable display duration need scenario evidence.
- **Outstanding: not demonstrated.** Silence snapping, confidence/overlap/speaker-swap warnings, VFR and no-audio behavior, 60-minute latency, OpenTimelineIO export, and 40+ scripted scenarios running in CI need implementation and/or proof.

The form makes levels sequential. Therefore the current evidence supports **Below Core** and **0/40 level marks**; it does not support claiming isolated higher-level marks as a reached level.

## Runtime evidence and metrics

### Evidence already recorded

- A backend smoke flow is documented for a 33.7 MB, 323.99-second source: 149 timed words, Groq GPT-OSS captions, and a rendered/downloaded MP4. A Tamil translation/render smoke check was reported, but caption translation accuracy is not measured. The saved transcript contains low-confidence recognition, so that run cannot prove accurate translation.
- That earlier render was 266.10 seconds. It exposed missing unspoken source-edge footage. A code fix now preserves leading/trailing source media when corresponding boundary words survive, and tests cover duration and timing behavior.
- Current verification in the project `.venv`: 64 passed, 0 failed, 27 FastAPI/Starlette deprecation warnings. The initial attempt with global Python failed at collection because Redis is not installed there; using the project virtual environment resolved that environment mismatch.
- Generated CFR, VFR, no-audio, boundary-cut, and speed-plus-bleep render fixtures passed against the provided Windows FFmpeg 9 binary. The latest real-source evidence is summarized in [ASSESSMENT_RENDER_METRICS.md](ASSESSMENT_RENDER_METRICS.md); the MP4 and JSON sidecar are local artifacts at `storage/assessment-postfix-render-v3.mp4` and `storage/assessment-postfix-render-v3.metrics.json` (storage files are git-ignored and must be included separately in the submission); timeline duration matched the live API session at 323.988027 s. Output duration error was 0.011973 s (0.2993 frames at 25 fps), A/V start offset 0 s, and A/V endpoint offset 0.014195 s (0.3549 frames). FFprobe final-frame duration is inferred from adjacent presentation timestamps.

### Still not measured

| Workbook metric | Result |
|---|---|
| Instruction latency (5-minute and 60-minute videos) | Not measured |
| Largest A/V offset and output length error in frames | Among currently measured fixtures: real assessment render A/V endpoint offset 0.3549 frames and container-duration error 0.2993 frames; generated fixtures also pass their configured tolerances. This is not a measured maximum across all project edits. |
| Identical timeline hashes across three runs | Not measured |
| Ambiguous instructions clarified vs guessed | Not measured |
| Word clipping across 20 listening-checked cuts | Not measured |
| Speech-to-text and LLM cost per edited minute | Not measured |

Do not enter guessed values. The re-render, API/worker rebuild, health/readiness checks, and API-versus-timeline duration comparison are complete. Reorder, multi-speaker, browser, and remaining hard-case workflows still need retained evidence.

## Hard cases and submission materials

The workbook lists seven groups of hard cases, including caption/restore behavior, reorder plus effects, repeated/time-based requests, history/music requests, repeated phrase ambiguity, impossible and multi-part operations, and difficult two-speaker/VFR media. The repository contains tests for selected behaviors, but no complete hard-case run log or result pack was found. The live backend smoke flow and passing health/readiness checks are partial integration evidence, not a verified browser session or full assessment run. Mark these **not yet evidenced**, not Yes.

Before submission, the owner still needs to supply personal/contact details, repository and demo links, compute and cost details, hours spent, and signed declaration answers. Those are personal attestations and are intentionally not inferred here. The workbook also asks for a demo, evidence links, measured metrics, and a completed declaration; this Markdown review does not complete or replace that form.

## Submission checks still in progress

1. Finish the browser upload -> transcription -> edit -> render -> download check and retain screenshots/logs and output artifacts. Do not mark it passed until each step succeeds.
2. Execute the brief's eight-step example and single-speaker and multi-speaker scenarios; retain timelines, hashes, and outputs.
3. Run repeated determinism sessions and the remaining workbook hard cases; test Redis crash recovery and concurrent idempotency.
4. Measure the unmeasured latency, ambiguity, clipping, and cost metrics using the stated video fixtures.
5. Add evidence links to the submission workbook and complete personal fields/declarations yourself.

## Review boundary

This document treats instructions in the attached workbook and brief as **assessment criteria**, not as user authorization to fill personal fields, sign declarations, or claim a requirement passed. Results are based on the repository documents/code/tests plus the test output and command output supplied in this conversation. No unsupported score or runtime success is asserted.
