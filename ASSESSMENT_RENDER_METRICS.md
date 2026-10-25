# Real-source render verification

**Run date:** 2026-10-25
**Session:** `cd0e99a4-9f34-4673-b0be-c0cbae4df7f5`
**Source:** `storage/test_video.mp4` (SHA-256 `31263c1b8493b1b44a1bf6879f4b4d0d62318c6e4f30c5c197947e405ddce400`)
**Output:** `storage/assessment-postfix-render-v3.mp4` (53,344,739 bytes)
**Raw metrics:** `storage/assessment-postfix-render-v3.metrics.json`

## Measurements

| Check | Result | Limit |
|---|---:|---:|
| Timeline duration | 323.988027 s | — |
| Live API session duration | 323.988027 s | Must match recomputed timeline |
| Render/container duration | 324.000000 s | — |
| Duration error | 0.011973 s = 0.2993 frames at 25 fps | ≤ 1 frame |
| A/V start offset | 0 s | ≤ 1 frame |
| A/V endpoint offset | 0.014195 s = 0.3549 frames at 25 fps | ≤ 1 frame |
| Video frames | 8,100 at 25 fps | — |

The renderer cloned the final video frame for 0.068027 s because the source audio presentation endpoint is later than the source video endpoint. FFprobe does not report duration for the final video frame in this file; the verifier derives it from adjacent frame presentation timestamps.

## Reproduction and runtime

Run `python -m scripts.verify_assessment_render --session-id cd0e99a4-9f34-4673-b0be-c0cbae4df7f5 --source storage/test_video.mp4 --output storage/assessment-postfix-render-v3.mp4` with `FFMPEG_BINARY` and `FFPROBE_BINARY` set to the installed executable paths. The script checks the source hash and compares the saved API session duration with the locally recomputed timeline before rendering.

After rebuilding the Compose API and worker, `/api/v1/health` and `/api/v1/ready` returned HTTP 200. The API session endpoint returned duration `323.988027` seconds. The generated CFR, VFR, no-audio, boundary-cut, and speed-plus-bleep fixtures also passed `python -m scripts.verify_render_sync`.

These results cover one real source/session and the listed generated fixtures. They do not establish every mixed edit, multi-speaker media, browser behavior, transcription accuracy, translation accuracy, or the remaining assessment hard cases. The `storage/` outputs are git-ignored and must be copied into any external evidence/submission package.
