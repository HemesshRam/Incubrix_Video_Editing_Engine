"""Render a saved API session locally and verify its source hash, A/V sync, and duration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import httpx

from app.engine.renderer import VideoRenderer
from app.engine.timeline import TimelineEngine
from scripts.verify_render_sync import _probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True, help="Saved session ID from the API")
    parser.add_argument("--source", required=True, type=Path, help="Original source video file")
    parser.add_argument("--output", required=True, type=Path, help="New MP4 output path; existing files are refused")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/v1", help="Versioned API base URL")
    parser.add_argument("--ffprobe", default=os.environ.get("FFPROBE_BINARY") or shutil.which("ffprobe") or shutil.which("ffprobe.exe"))
    args = parser.parse_args()

    if not args.source.is_file():
        raise SystemExit(f"Source video does not exist: {args.source}")
    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {output}")
    if args.ffprobe is None or not Path(args.ffprobe).is_file():
        raise SystemExit("ffprobe is required; set FFPROBE_BINARY to its full executable path.")

    response = httpx.get(f"{args.api_url.rstrip('/')}/sessions/{args.session_id}", timeout=30)
    response.raise_for_status()
    session = response.json()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    expected_hash = session["video_metadata"].get("source_sha256")
    if expected_hash and source_hash != expected_hash:
        raise SystemExit(f"Source hash does not match the saved session: {source_hash} != {expected_hash}")

    engine = TimelineEngine(session["transcript"], session["video_metadata"])
    engine.active_word_ids = session["active_word_ids"]
    engine.captions = session["captions"]
    engine.effects = session["effects"]
    engine.speed_map = session["speed_map"]
    engine.background_music = session.get("background_music")
    engine.long_pause_threshold_s = session.get("long_pause_threshold_s")
    timeline_duration = engine.get_duration()
    fps = float(session["video_metadata"].get("fps") or 30.0)
    tolerance = 1.0 / fps

    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_path = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg_path is None:
        raise SystemExit("ffmpeg is required; set FFMPEG_BINARY to its full executable path.")
    bin_paths = [str(Path(binary).resolve().parent) for binary in (ffmpeg_path, args.ffprobe) if Path(binary).is_absolute()]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(bin_paths + [os.environ.get("PATH", "")]))

    renderer = VideoRenderer(output_dir=str(output.parent))
    rendered = Path(renderer.render_timeline(
        str(source),
        engine.active_word_ids,
        engine.raw_transcript,
        engine.captions,
        output.name,
        effects=engine.effects,
        speed_map=engine.speed_map,
        background_music=engine.background_music,
        has_audio=engine.metadata.get("has_audio"),
        long_pause_threshold_s=engine.long_pause_threshold_s,
        source_duration=engine.metadata.get("duration"),
    ))

    probe = _probe(rendered, args.ffprobe)
    video_end = probe["video_presentation_end_s"]
    audio_end = probe.get("audio_start_s", 0.0) + probe.get("audio_duration_s", 0.0)
    metrics = {
        "session_id": args.session_id,
        "source_sha256": source_hash,
        "source_path": str(source),
        "render_path": str(rendered),
        "render_bytes": rendered.stat().st_size,
        "timeline_duration_s": timeline_duration,
        "reported_session_duration_s": session.get("duration_s"),
        "api_session_duration_error_s": (
            abs(float(session["duration_s"]) - timeline_duration)
            if session.get("duration_s") is not None else None
        ),
        "render_probe": probe,
        "duration_error_s": abs(probe["format_duration_s"] - timeline_duration),
        "av_start_offset_s": abs(probe["video_start_s"] - probe.get("audio_start_s", 0.0)),
        "av_end_offset_s": abs(video_end - audio_end) if "audio_duration_s" in probe else 0.0,
        "frame_tolerance_s": tolerance,
        "renderer_warnings": renderer.warnings,
    }
    metrics["api_session_duration_matches_recomputed_timeline"] = (
        metrics["api_session_duration_error_s"] is None
        or metrics["api_session_duration_error_s"] <= tolerance
    )
    metrics_path = rendered.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    if metrics["duration_error_s"] > tolerance:
        raise SystemExit(f"Render duration differs from timeline by more than one frame; see {metrics_path}")
    if metrics["av_start_offset_s"] > tolerance or metrics["av_end_offset_s"] > tolerance:
        raise SystemExit(f"Rendered audio/video offset exceeds one frame; see {metrics_path}")
    if not metrics["api_session_duration_matches_recomputed_timeline"]:
        raise SystemExit(
            "The running API reports a stale timeline duration; restart/rebuild the API before treating this as an end-to-end pass. "
            f"See {metrics_path}"
        )


if __name__ == "__main__":
    main()
