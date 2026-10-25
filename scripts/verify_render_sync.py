"""Generate a deterministic FFmpeg fixture and check render duration/sync."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.engine.renderer import VideoRenderer


@contextmanager
def _workspace_tempdir():
    """Create a temp directory with inherited workspace ACLs for FFmpeg."""
    root = Path.cwd() / f".incubrix-sync-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _probe(path: Path, ffprobe_executable: str) -> dict:
    result = subprocess.run(
        [
            ffprobe_executable, "-v", "error", "-count_frames",
            "-show_entries", "format=duration:stream=codec_type,start_time,duration,nb_read_frames,avg_frame_rate",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = json.loads(result.stdout)
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    result = {"format_duration_s": float(probe["format"]["duration"])}
    for kind in ("video", "audio"):
        if kind in streams:
            stream = streams[kind]
            result[f"{kind}_start_s"] = float(stream.get("start_time", 0.0))
            result[f"{kind}_duration_s"] = float(stream.get("duration", probe["format"]["duration"]))
            if kind == "video":
                result["video_avg_frame_rate"] = stream["avg_frame_rate"]
                result["video_frame_count"] = int(stream["nb_read_frames"])
                frames = subprocess.run(
                    [
                        ffprobe_executable, "-v", "error", "-select_streams", "v:0",
                        "-show_frames", "-show_entries", "frame=best_effort_timestamp_time,pkt_duration_time",
                        "-of", "json", str(path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                frame_records = json.loads(frames.stdout)["frames"]
                frame_pts = [float(frame["best_effort_timestamp_time"]) for frame in frame_records]
                first_frame = frame_records[0]
                last_frame = frame_records[-1]
                first_pts = float(first_frame["best_effort_timestamp_time"])
                last_pts = float(last_frame["best_effort_timestamp_time"])
                last_duration = float(last_frame.get("pkt_duration_time", 0.0))
                if last_duration <= 0.0 and len(frame_records) > 1:
                    previous_pts = float(frame_records[-2]["best_effort_timestamp_time"])
                    last_duration = max(0.0, last_pts - previous_pts)
                frame_intervals = [right - left for left, right in zip(frame_pts, frame_pts[1:]) if right > left]
                result["video_first_frame_pts_s"] = first_pts
                result["video_last_frame_pts_s"] = last_pts
                result["video_last_frame_duration_s"] = last_duration
                result["video_last_frame_duration_source"] = (
                    "ffprobe_packet_duration" if float(last_frame.get("pkt_duration_time", 0.0)) > 0.0
                    else "difference_from_previous_frame_pts"
                )
                result["video_max_frame_interval_s"] = max(frame_intervals, default=last_duration)
                result["video_presentation_end_s"] = last_pts + last_duration
                result["video_presentation_duration_s"] = result["video_presentation_end_s"] - first_pts
    return result


def _render(
    source: Path,
    output_dir: Path,
    active_ids: list[str],
    transcript: list[dict],
    duration: float,
    ffprobe_executable: str,
    *,
    effects: list[dict] | None = None,
    speed_map: dict[str, float] | None = None,
    has_audio: bool | None = None,
) -> dict:
    renderer = VideoRenderer(output_dir=str(output_dir))
    try:
        result = Path(renderer.render_timeline(
            str(source),
            active_ids,
            transcript,
            [],
            f"{len(active_ids)}-active-words.mp4",
            effects=effects,
            speed_map=speed_map,
            has_audio=has_audio,
            source_duration=duration,
        ))
    except Exception as exc:
        stderr = getattr(exc, "stderr", b"")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg failed to render the verification fixture: {stderr or exc}") from exc
    return _probe(result, ffprobe_executable)


def main() -> None:
    executable = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    ffprobe_executable = os.environ.get("FFPROBE_BINARY") or shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if executable is None or ffprobe_executable is None:
        raise SystemExit(
            "FFmpeg and ffprobe are required. Install FFmpeg and add its bin folder to PATH, "
            "or set FFMPEG_BINARY and FFPROBE_BINARY to the executable paths."
        )
    # ffmpeg-python invokes the executables by command name, even when this
    # verifier was given explicit paths through FFMPEG_BINARY/FFPROBE_BINARY.
    executable_dirs = [
        str(Path(binary).resolve().parent)
        for binary in (executable, ffprobe_executable)
        if Path(binary).is_absolute()
    ]
    if executable_dirs:
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys(executable_dirs + [os.environ.get("PATH", "")]))

    fps = 30.0
    source_duration = 12.0
    tolerance = 1.0 / fps
    transcript = [
        {"word_id": "w1", "word": "opening", "start": 2.0, "end": 3.0},
        {"word_id": "w2", "word": "ending", "start": 8.0, "end": 9.0},
    ]

    # Keep fixture writes inside the workspace with inherited ACLs so FFmpeg
    # can create files in restricted Windows environments.
    with _workspace_tempdir() as root:
        def create_source(path: Path, *, vfr: bool = False, with_audio: bool = True) -> None:
            command = [
                executable, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={source_duration}",
            ]
            if with_audio:
                command.extend([
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={source_duration}",
                ])
            if vfr:
                command.extend(["-vf", "select='not(eq(mod(n,5),1))'"])
            command.extend([
                "-t", str(source_duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            ])
            if with_audio:
                command.extend(["-c:a", "aac"])
            if vfr:
                command.extend(["-fps_mode", "vfr"])
            command.append(str(path))
            subprocess.run(command, check=True, capture_output=True)

        source = root / "source-cfr.mp4"
        vfr_source = root / "source-vfr.mp4"
        no_audio_source = root / "source-no-audio.mp4"
        create_source(source)
        create_source(vfr_source, vfr=True)
        create_source(no_audio_source, with_audio=False)

        source_probe = _probe(source, ffprobe_executable)
        cfr_tolerance = 1.0 / fps
        vfr_probe = _probe(vfr_source, ffprobe_executable)
        vfr_tolerance = max(vfr_probe["video_max_frame_interval_s"], 1.0 / fps)
        full = _render(source, root / "full", ["w1", "w2"], transcript, source_duration, ffprobe_executable)
        boundary_cut = _render(source, root / "cut", ["w2"], transcript, source_duration, ffprobe_executable)
        vfr_full = _render(vfr_source, root / "vfr", ["w1", "w2"], transcript, source_duration, ffprobe_executable)
        no_audio_full = _render(
            no_audio_source, root / "no-audio", ["w1", "w2"], transcript,
            source_duration, ffprobe_executable, has_audio=False,
        )
        mixed = _render(
            source, root / "mixed", ["w1", "w2"], transcript, source_duration,
            ffprobe_executable,
            effects=[{"effect_id": "fx1", "type": "bleep", "target_word_ids": ["w2"]}],
            speed_map={"w2": 2.0},
        )

        def sync_metrics(probe: dict) -> dict:
            video_end = probe["video_presentation_end_s"]
            audio_end = probe["audio_start_s"] + probe["audio_duration_s"]
            return {
                "av_start_offset_s": abs(probe["video_start_s"] - probe["audio_start_s"]),
                "av_end_offset_s": abs(video_end - audio_end),
            }

        full_metrics = {
            **sync_metrics(full),
            "duration_error_s": abs(full["format_duration_s"] - source_probe["format_duration_s"]),
        }
        trimmed_expected_duration = source_duration - transcript[1]["start"]
        trimmed_metrics = {
            **sync_metrics(boundary_cut),
            "expected_duration_error_s": abs(boundary_cut["format_duration_s"] - trimmed_expected_duration),
        }
        vfr_metrics = {
            **sync_metrics(vfr_full),
            "duration_error_s": abs(vfr_full["format_duration_s"] - source_duration),
        }
        no_audio_metrics = {
            "duration_error_s": abs(no_audio_full["format_duration_s"] - source_duration),
            "audio_stream_present": "audio_duration_s" in no_audio_full,
        }
        mixed_expected_duration = sum(
            (run["end"] - run["start"]) / run["speed"]
            for run in VideoRenderer._segment_runs(
                ["w1", "w2"], {word["word_id"]: word for word in transcript},
                {"w2": 2.0}, source_duration=source_duration,
            )
        )
        mixed_metrics = {
            **sync_metrics(mixed),
            "duration_error_s": abs(mixed["format_duration_s"] - mixed_expected_duration),
            "expected_duration_s": mixed_expected_duration,
        }
        report = {
            "source": source_probe,
            "vfr_source": vfr_probe,
            "cfr_one_frame_tolerance_s": cfr_tolerance,
            "vfr_tolerance_s": vfr_tolerance,
            "full_timeline": {"probe": full, "metrics": full_metrics},
            "first_word_cut": {"probe": boundary_cut, "metrics": trimmed_metrics},
            "vfr_full_timeline": {"probe": vfr_full, "metrics": vfr_metrics},
            "no_audio_full_timeline": {"probe": no_audio_full, "metrics": no_audio_metrics},
            "speed_and_bleep": {"probe": mixed, "metrics": mixed_metrics},
        }
        print(json.dumps(report, indent=2))

        if full_metrics["duration_error_s"] > cfr_tolerance:
            raise SystemExit("Full-timeline output duration exceeds one-frame tolerance.")
        if full_metrics["av_start_offset_s"] > cfr_tolerance or full_metrics["av_end_offset_s"] > cfr_tolerance:
            raise SystemExit("Full-timeline audio/video offset exceeds one-frame tolerance.")
        if trimmed_metrics["expected_duration_error_s"] > cfr_tolerance:
            raise SystemExit("Boundary-cut duration does not match the retained source span.")
        if trimmed_metrics["av_start_offset_s"] > cfr_tolerance or trimmed_metrics["av_end_offset_s"] > cfr_tolerance:
            raise SystemExit("Boundary-cut audio/video offset exceeds one-frame tolerance.")
        if vfr_metrics["duration_error_s"] > vfr_tolerance:
            raise SystemExit("VFR full-timeline duration exceeds the largest source-frame interval.")
        if vfr_metrics["av_start_offset_s"] > vfr_tolerance or vfr_metrics["av_end_offset_s"] > vfr_tolerance:
            raise SystemExit("VFR full-timeline audio/video offset exceeds the largest source-frame interval.")
        if no_audio_metrics["audio_stream_present"] or no_audio_metrics["duration_error_s"] > cfr_tolerance:
            raise SystemExit("No-audio render unexpectedly has audio or exceeds one-frame duration tolerance.")
        if mixed_metrics["duration_error_s"] > cfr_tolerance:
            raise SystemExit("Combined speed-and-bleep render duration exceeds one-frame tolerance.")
        if mixed_metrics["av_start_offset_s"] > cfr_tolerance or mixed_metrics["av_end_offset_s"] > cfr_tolerance:
            raise SystemExit("Combined speed-and-bleep audio/video offset exceeds one-frame tolerance.")


if __name__ == "__main__":
    main()
