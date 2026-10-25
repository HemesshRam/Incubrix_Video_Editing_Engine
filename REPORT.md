# Engineering assessment report

## Current implementation

The repository has a browser editing shell, typed prompt endpoint, Groq/mock adapters, word-level transcription, Redis-backed session storage and Streams jobs, local or S3-compatible media storage, upload and render job routes, generated OpenAPI, a YAML assessment runner, and a timeline-to-FFmpeg compiler. Compose configures SeaweedFS as its local S3-compatible store. On 2026-10-25, a backend API flow processed a 33.7 MB, 323.99-second 1280×720 source: transcription returned 149 timed words, GPT-OSS generated six-word automatic captions, and an earlier 44.90 MB MP4 had visible captions. That earlier output was 266.10 seconds and exposed omitted source footage after the final transcribed word. After implementing edge retention and stream-tail alignment, the hash-matched source/session was rendered locally with its 149-word transcript and 32 saved captions. The 53.34 MB output duration was 324.000 seconds against a 323.988027-second timeline (0.299 frames at 25 fps); A/V start offset was 0, and the A/V endpoint offset was 0.014195 seconds (0.355 frames). FFmpeg reported cloning the final source video frame for 0.068027 seconds because source audio outlasted source video. The latest render measurements are summarized in `ASSESSMENT_RENDER_METRICS.md`; the MP4 and JSON sidecar are local artifacts at `storage/assessment-postfix-render-v3.mp4` and `storage/assessment-postfix-render-v3.metrics.json`. The `storage/*` files are git-ignored and must be attached/copied separately for a submission. The saved API session duration was confirmed equal to the recomputed timeline duration (323.988027 seconds), and rebuilt API/worker containers are healthy. API health and readiness returned 200 after restart. Deterministic CFR, VFR, no-audio, boundary-cut, and speed-plus-bleep fixtures pass with the installed Windows FFmpeg 9 build. A prior Tamil translation/render smoke check was reported, but translation accuracy has not been measured. The saved source transcript includes low-confidence words, so that sample cannot establish language-level correctness. Automated tests cover API session cycles and frontend asset serving, but no browser automation has verified the complete UI workflow. Upload-job acceptance uses a streamed SHA-256 and an atomic Redis idempotency mapping; eligible JSON POST routes use middleware response replay with request fingerprint conflict detection. Neither idempotency path has concurrency testing. Worker recovery still needs crash-recovery evidence.

## Production readiness changes (2026-10-25 update)

### Frontend workflow

The frontend JavaScript was rewritten to fix the browser-based workflow:
- **Upload**: File validation (type + 2 GB size limit), drag-and-drop support on the preview frame, 10-minute upload timeout for large files, and error recovery that resets the preview state on failure.
- **Progress**: Job polling with 5-retry tolerance for network drops, reconnection messages, and proper status transitions from queued through running to complete.
- **Prompt editing**: Chat-style conversation with labeled user/assistant messages, an overall operation summary with operation types for multi-operation edits, clarification cards with clickable options, and conversation clearing on new project. The clarification flow has not been validated in a browser or across all operation types.
- **Undo/redo**: The API now returns actual `history_depth` and `redo_depth`; the UI uses these values rather than the monotonically increasing session revision. Keyboard shortcuts (Ctrl+Z / Ctrl+Y) work when the prompt input is not focused. Automated API tests cover undo/redo state transitions; browser interaction remains unverified.
- **Preview**: Source/rendered video switching after render completes. A toggle button appears in the conversation that flips between original source and rendered output.
- **Export**: Download link appears in conversation after render succeeds, with `download` attribute for direct save.
- **New project**: Full state reset including conversation history, caption lane, session chip, and job status.

### Broader prompt coverage

The mock interpreter now supports:
- **Reorder**: `Move 'phrase A' after 'phrase B'` resolves both phrases in the active transcript, with clarification when either phrase appears more than once.
- **Improved rejection**: Unsupported requests return a detailed message listing all available operations (cut, keep, reorder, speed, mute/bleep, caption, auto-caption, filler removal, pause removal, undo, redo, restore) so users can reformulate their request.
- All existing operations continue to work: time-range cuts/keeps, exact-phrase cuts/keeps, speed changes, mute/bleep effects, caption addition/update, auto-captioning, filler word removal, long pause removal, undo/redo/restore.

### Security and deployment

- **Security headers**: All responses include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and `Permissions-Policy` for camera/microphone/geolocation. Non-development environments additionally set a strict `Content-Security-Policy`.
- **CORS**: Allowed HTTP(S) origins are configured through `CORS_ALLOWED_ORIGINS` as a JSON array; wildcard and path-based entries are rejected. Only `GET` and `POST` methods are allowed. Custom headers are limited to `Content-Type`, `Idempotency-Key`, and `X-Correlation-ID`.
- **Catch-all error handler**: An injected unhandled exception returns a structured JSON error with `code: INTERNAL_ERROR`; the response has security headers and does not expose the exception detail. Allowed-origin CORS headers are also preserved on this response.
- **Worker reliability**: The job worker handles Redis connection loss with exponential backoff (up to 30 seconds). SIGTERM/SIGINT stops new claims and waits for active jobs; a cancelled, nonterminal job is left unacknowledged so Redis can reclaim it. A unit test covers cancellation/ack behavior, but a Redis crash-recovery integration test is still needed. Worker logs use standard formatted logging and do not yet carry the full structured correlation context required by the brief.

## Timeline representation

Current runtime state consists of an immutable source transcript keyed by stable `word_id`, an ordered list of active word IDs, and content-anchored captions, effects, and speed settings. Source timing is retained in the transcript; timeline order is represented separately. Captions and effects reference word IDs, not output clock positions. Cuts remove IDs from the active sequence; reorder changes sequence order. This means an anchor continues to refer to the same source words across those operations.

Every state hash is SHA-256 over canonical JSON containing the source hash, transcript, active order, and edit state. Transcriber word IDs are stable sequential IDs within the source transcript rather than random UUIDs. A batch of ordinary operations is applied atomically and records a single history step. Invalid IDs or invalid operations roll back the batch. Undo and redo move between snapshots; restore creates a new step from a selected prior snapshot. Session state survives process restarts when Redis is configured; local in-memory mode does not persist.

### Runtime timeline JSON Schema (current state shape)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "IncuBrix Timeline State",
  "type": "object",
  "required": ["active_words", "captions", "effects", "speed_map", "background_music"],
  "properties": {
    "active_words": {"type": "array", "items": {"type": "string"}, "uniqueItems": true},
    "captions": {
      "type": "array", "items": {
        "type": "object", "required": ["caption_id", "text", "target_word_ids", "style"],
        "properties": {
          "caption_id": {"type": "string"}, "text": {"type": "string"},
          "target_word_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
          "style": {"type": "object"}
        }
      }
    },
    "effects": {
      "type": "array", "items": {
        "type": "object", "required": ["effect_id", "type", "target_word_ids"],
        "properties": {
          "effect_id": {"type": "string"}, "type": {"enum": ["mute", "bleep"]},
          "target_word_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}
        }
      }
    },
    "speed_map": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0.25, "maximum": 4}},
    "background_music": {
      "oneOf": [
        {"type": "null"},
        {"type": "object", "required": ["audio_url", "auto_ducking"],
         "properties": {"audio_url": {"type": "string"}, "auto_ducking": {"type": "boolean"}}}
      ]
    }
  }
}
```

This schema describes the state portion of the persisted session document. The versioned document also includes source hash/media metadata and undo/redo snapshots. The render job currently stores a partial output manifest; it does not yet include model versions, complete settings, seed, timings, or cost estimate.

## Design decisions and limits

- Groq is accessed through a small provider boundary; `GROQ_MODEL` defaults to `openai/gpt-oss-20b`. GPT-OSS uses native JSON Schema structured output; other configured Groq models use tool mode. Session prompt requests receive transcript, detected source-language, and timeline context. Accepted operations are validated and applied by the timeline. The standalone `/edit` endpoint returns an interpretation and does not mutate a session.
- Upload can select a Whisper source-language code or leave it blank for automatic detection. Caption translation is a Groq-backed operation that preserves caption IDs and word anchors; translated text is stored as an undoable timeline edit. The supported operation schema defines the action boundary; arbitrary requests outside it are clarified or rejected with an enumerated list of available operations. Model support for every language and prompt cannot be guaranteed.
- Mock mode handles a limited set of transcript-aware requests deterministically, including automatic caption generation, conservative removal of common filler tokens, trimming long transcript gaps, and reordering word segments. Gap trimming uses word timestamps as a proxy and does not confirm silence from audio samples.
- The renderer compiles ordered word spans with speed changes and applies word-anchored caption/mute/bleep intervals and sidechain-ducked background music. When the original first/last transcript words remain at the corresponding timeline boundaries, the rendered runs now retain the media before/after those words; timeline duration and prompt time anchors account for the retained head and tail. Unit tests and generated CFR, VFR, no-audio, boundary-cut, and speed-plus-bleep FFprobe fixtures cover these cases. The hash-matched assessment source/session was also rendered after the fixes and meets the one-frame A/V and output-duration tolerance; more edit combinations and multi-speaker inputs remain unverified. Deployment depends on FFmpeg availability.
- The worker uses Redis Streams consumer groups and reclaims stale work, with a lease refresh during active processing. Redis disconnections are handled with exponential backoff reconnection. Graceful shutdown drains active jobs. This behavior still needs a Redis integration test and crash-recovery exercise.
- The YAML runner accepts a file or folder, downloads runner-provided HTTP(S) video URLs before uploading, polls jobs, and stores result artifacts. Clarification answer mapping is limited to ordinal selections and supported edit types. Direct API URL intake, signed callbacks, complete manifests, and frame-level render tests remain incomplete.
- S3-compatible source and render object storage are implemented behind the `minio`/`s3` storage settings; Compose uses SeaweedFS. The result API downloads stored output. A Compose smoke flow exercised upload, storage, rendering, and result download once; failure recovery, repeated runs, and concurrent storage behavior remain untested.

## Test evidence

64 automated tests passed on 2026-10-25 (latest local run 1.16s; 27 FastAPI/Starlette deprecation warnings):
- **test_api.py** (11 tests): Health/readiness endpoints, OpenAPI schema completeness, frontend serving, mock edit endpoint, caption translation intent detection across 5 languages.
- **test_production_ready.py** (20 tests): Mock reorder operations, rejection enumeration, time cuts, restore, security headers, allowed/disallowed CORS origins and configuration, injected catch-all 500 response, frontend assets, reorder validation/history, session create/edit/undo/redo state depths, auto-caption undo, and interrupted worker acknowledgment.
- **test_sanity.py** (12 tests): Timeline engine cut/undo/redo determinism, caption style update with history and atomicity, auto-caption splitting, caption translation with anchor preservation, filler removal reversibility, long pause removal with duration update, legacy document round-trip, source-edge duration and prompt timing, duration/time anchors with overlapping words, and speed with overlapping boundaries.
- **test_mock_interpreter.py** (7 tests): Phrase-anchored captions, time-range speed changes, caption style targeting, auto-caption intent, translation rejection in mock mode, filler word filtering, pause threshold parsing.
- **test_renderer.py** (8 tests): Segment ordering with speed, reordered word timing, source-edge retention, overlapping timestamps and speed boundaries, silent output command construction, long-pause threshold consistency, caption color injection rejection.
- **test_llm.py** (2 tests): Caption translation ID/order preservation, reordered ID rejection.
- **test_session_runner.py** (2 tests): Clarification answer option selection, sample session YAML shape validation.
- **test_transcriber.py** (2 tests): Word duration rounding, Whisper language code validation.

## Measurements

The generated CFR full (12 s), CFR boundary-cut (4 s), VFR full (12 s), no-audio (12 s), and speed-plus-bleep (7.5 s) fixtures pass. Maximum A/V endpoint offset was approximately 0.000001 seconds; container-duration error was 0 seconds. The real hash-matched assessment source/session render also passes one-frame bounds: output 324.000 s versus timeline 323.988027 s (0.299 frames); A/V end offset 0.014195 s (0.355 frames), A/V start offset 0. This render was produced directly with current local code; the saved API duration was verified at 323.988027 s, matching the recomputed timeline, after rebuilding the API and worker. Both containers are running; the API health/readiness routes returned 200. FFprobe's final-frame duration was inferred from adjacent presentation timestamps because it omits `pkt_duration_time` for the final frame. Also not measured: transcription/translation accuracy across languages, latency, three-run determinism across complete sessions, ambiguity handling rate, clipped-word count, or cost per minute. No performance or production-readiness claim is made until these values are measured across reproducible assessment scenarios.

## Remaining work

- **Concurrency testing**: The two idempotency paths (SHA-256 upload dedup and middleware response replay) have not been tested under concurrent load.
- **Crash recovery**: Worker restart behavior needs a Redis integration test that verifies stale job reclaim after simulated worker death.
- **Language quality**: Translation preserves caption IDs and anchors, but semantic translation accuracy is not tested. The earlier Tamil visual smoke check is not evidence that each caption matches the speech; the assessment transcript contains low-confidence recognition. CJK, Arabic, Devanagari, and other scripts also need visual verification.
- **Frame-accurate sync**: Generated CFR/VFR/no-audio/speed-plus-bleep fixtures and one real source/session render meet the checked tolerances. Run browser and multi-speaker scenarios and retain their artifacts; the API and worker rebuild, readiness check, live session duration comparison, and repeatable assessment render are now complete. Multi-speaker and other mixed-edit combinations still need checks.
- **Storage integration**: The Compose smoke flow exercised upload, object storage, render storage, and result download once. Failure recovery, repeated runs, and concurrent storage behavior remain untested.
- **Rate limiting**: No request rate limiting is in place; the CORS and security headers protect against common attacks but not abuse.
- **Complete render manifests**: The output manifest lacks model versions, processing timings, seed, and cost estimate fields.
