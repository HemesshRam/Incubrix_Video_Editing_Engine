"""Compile a content-anchored timeline to an ordered FFmpeg filter graph."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import ffmpeg

from app.core.config import get_settings


class VideoRenderer:
    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or get_settings().storage_path).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.warnings: List[str] = []

    def render_timeline(
        self,
        source_video_path: str,
        active_word_ids: List[str],
        transcript: List[Dict[str, Any]],
        captions: List[Dict[str, Any]],
        output_filename: str = "output.mp4",
        *,
        effects: Optional[List[Dict[str, Any]]] = None,
        speed_map: Optional[Dict[str, float]] = None,
        background_music: Optional[Dict[str, Any]] = None,
        has_audio: Optional[bool] = None,
        long_pause_threshold_s: Optional[float] = None,
        source_duration: Optional[float] = None,
    ) -> str:
        source = Path(source_video_path).resolve()
        output_path = (self.output_dir / output_filename).resolve()
        if self.output_dir not in output_path.parents:
            raise ValueError("Render output must remain inside the configured output directory.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file():
            raise ValueError("Source video is unavailable.")

        word_map = {word["word_id"]: word for word in transcript}
        if len(word_map) != len(transcript):
            raise ValueError("Transcript word IDs must be unique.")
        if not active_word_ids or any(word_id not in word_map for word_id in active_word_ids):
            raise ValueError("Timeline has no active words or references unknown word IDs.")
        if len(set(active_word_ids)) != len(active_word_ids):
            raise ValueError("Timeline contains duplicate active word IDs.")
        speeds = speed_map or {}
        self.warnings = []

        timing = self._current_word_times(active_word_ids, word_map, speeds, long_pause_threshold_s)
        runs = self._segment_runs(
            active_word_ids,
            word_map,
            speeds,
            long_pause_threshold_s,
            source_duration,
        )
        stream = ffmpeg.input(str(source))
        video_segments = []
        audio_segments = []
        try:
            source_probe = ffmpeg.probe(str(source))
        except ffmpeg.Error as exc:
            detail = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise ValueError(f"Unable to probe source media: {detail[-1000:]}") from exc
        source_streams = source_probe.get("streams", [])
        video_stream = next((item for item in source_streams if item.get("codec_type") == "video"), None)
        if video_stream is None:
            raise ValueError("Source media has no video stream.")
        source_has_audio = (
            any(item.get("codec_type") == "audio" for item in source_streams)
            if has_audio is None else has_audio
        )
        if source_duration is None:
            raw_duration = source_probe.get("format", {}).get("duration")
            source_duration = float(raw_duration) if raw_duration is not None else None
        video_input = stream.video
        video_stream_duration = video_stream.get("duration")
        if source_duration is not None and video_stream_duration is not None:
            video_end = float(video_stream.get("start_time", 0.0)) + float(video_stream_duration)
            missing_tail = max(0.0, float(source_duration) - video_end)
            if missing_tail > 1e-6:
                # Keep the audio/container timeline length when the source
                # audio outlasts its final video frame (common in phone media).
                video_input = video_input.filter(
                    "tpad", stop_mode="clone", stop_duration=missing_tail
                )
                self.warnings.append(
                    f"Cloned the final video frame for {missing_tail:.6f}s to align the video stream with the source duration."
                )

        for run in runs:
            speed = run["speed"]
            video = video_input.filter("trim", start=run["start"], end=run["end"])
            video = video.filter("setpts", f"(PTS-STARTPTS)/{speed:.9f}")
            video_segments.append(video)
            if source_has_audio:
                audio = stream.audio.filter("atrim", start=run["start"], end=run["end"])
                audio = audio.filter("asetpts", "PTS-STARTPTS")
                audio = self._atempo(audio, speed)
                audio_segments.append(audio)

        video = self._concat(video_segments, "v")
        video = video.filter("setpts", "PTS-STARTPTS")
        audio = self._concat(audio_segments, "a") if source_has_audio else None
        if audio is not None:
            audio = audio.filter("asetpts", "PTS-STARTPTS")

        duration = sum((run["end"] - run["start"]) / run["speed"] for run in runs)
        if audio is not None:
            audio = self._apply_effects(audio, effects or [], timing)
            if background_music:
                audio = self._mix_background_music(audio, background_music, duration)
        elif effects or background_music:
            self.warnings.append("Audio effects and background music were skipped because the source has no audio stream.")

        video = self._apply_captions(video, captions, active_word_ids, timing, duration)
        output_streams = [video] + ([audio] if audio is not None else [])
        output_options = {
            "vcodec": "libx264",
            "pix_fmt": "yuv420p",
            "movflags": "+faststart",
            "fps_mode": "vfr",
            # Filter timestamps can leave a fractional frame beyond the
            # calculated timeline (especially at speed boundaries). Bound
            # encoded packets to the same duration reported by the session.
            "t": max(0.001, duration),
        }
        if audio is not None:
            output_options["acodec"] = "aac"
        output = ffmpeg.output(*output_streams, str(output_path), **output_options)
        output.run(overwrite_output=True, quiet=True, capture_stderr=True)
        return str(output_path)

    @staticmethod
    def _concat(segments, kind: str):
        if not segments:
            raise ValueError(f"No {kind} segments were produced for rendering.")
        if len(segments) == 1:
            return segments[0]
        return ffmpeg.concat(*segments, v=1 if kind == "v" else 0, a=1 if kind == "a" else 0)

    @staticmethod
    def _has_audio(source: Path) -> bool:
        try:
            return any(item.get("codec_type") == "audio" for item in ffmpeg.probe(str(source))["streams"])
        except ffmpeg.Error as exc:
            detail = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise ValueError(f"Unable to probe source media: {detail[-1000:]}") from exc

    @staticmethod
    def _atempo(audio, speed: float):
        if not 0.25 <= speed <= 4.0:
            raise ValueError("Speed factor must be between 0.25 and 4.0.")
        remaining = speed
        while remaining < 0.5:
            audio = audio.filter("atempo", 0.5)
            remaining /= 0.5
        while remaining > 2.0:
            audio = audio.filter("atempo", 2.0)
            remaining /= 2.0
        return audio.filter("atempo", remaining)

    @staticmethod
    def _segment_runs(active_ids, word_map, speed_map, long_pause_threshold_s=None, source_duration=None):
        source_order = {word_id: index for index, word_id in enumerate(word_map)}
        runs = []
        previous_id = None
        for word_id in active_ids:
            word = word_map[word_id]
            speed = float(speed_map.get(word_id, 1.0))
            if not 0.25 <= speed <= 4.0:
                raise ValueError(f"Invalid speed factor for {word_id}.")
            contiguous = previous_id is not None and source_order[word_id] == source_order[previous_id] + 1
            if contiguous and long_pause_threshold_s is not None:
                gap = max(0.0, float(word["start"]) - float(word_map[previous_id]["end"]))
                if gap >= long_pause_threshold_s:
                    contiguous = False
            previous_speed = float(speed_map.get(previous_id, 1.0)) if previous_id is not None else None
            if runs and contiguous and abs(previous_speed - speed) < 1e-9 and abs(runs[-1]["speed"] - speed) < 1e-9:
                runs[-1]["end"] = max(runs[-1]["end"], float(word["end"]))
            else:
                start = float(word["start"])
                # Timeline duration assigns an original inter-word gap to the following word.
                if contiguous:
                    start = max(float(word_map[previous_id]["end"]), runs[-1]["end"] if runs else 0.0)
                elif not runs and source_order[word_id] == 0:
                    # Keep leading source footage while the original first word remains first.
                    start = 0.0
                end = max(start, float(word["end"]))
                # A fully overlapped word has no new source interval at this
                # speed boundary; don't emit an empty FFmpeg trim.
                if end > start:
                    runs.append({"start": start, "end": end, "speed": speed})
            previous_id = word_id
        if (
            runs
            and source_duration is not None
            and source_order[active_ids[-1]] == len(source_order) - 1
        ):
            # Keep trailing source footage while the original last word remains last.
            runs[-1]["end"] = max(runs[-1]["end"], float(source_duration))
        return runs

    @staticmethod
    def _current_word_times(active_ids, word_map, speed_map, long_pause_threshold_s=None):
        source_order = {word_id: index for index, word_id in enumerate(word_map)}
        timing = {}
        current = 0.0
        previous_id = None
        run_start = 0.0
        run_end = 0.0
        run_speed = 1.0
        run_output_start = 0.0
        source_cursor = 0.0
        for index, word_id in enumerate(active_ids):
            word = word_map[word_id]
            speed = float(speed_map.get(word_id, 1.0))
            source_start = float(word["start"])
            source_end = float(word["end"])
            contiguous = previous_id is not None and source_order[word_id] == source_order[previous_id] + 1
            if contiguous and long_pause_threshold_s is not None:
                gap = max(0.0, float(word["start"]) - float(word_map[previous_id]["end"]))
                if gap >= long_pause_threshold_s:
                    contiguous = False

            if index == 0:
                # The first original word keeps the silent lead-in. Otherwise
                # this is a new clip that starts at timeline time zero.
                run_start = 0.0 if source_order[word_id] == 0 else source_start
                run_end = source_end
                run_speed = speed
                run_output_start = 0.0
                start = run_output_start + max(0.0, source_start - run_start) / speed
                end = run_output_start + max(0.0, source_end - run_start) / speed
                current = run_output_start + max(0.0, run_end - run_start) / speed
                source_cursor = max(source_cursor, run_end)
            elif contiguous and abs(run_speed - speed) < 1e-9:
                # Within one source run, map overlapping transcript words to
                # their actual source offsets instead of counting overlap twice.
                start = run_output_start + max(0.0, source_start - run_start) / speed
                end = run_output_start + max(0.0, source_end - run_start) / speed
                run_end = max(run_end, source_end)
                current = run_output_start + max(0.0, run_end - run_start) / speed
                source_cursor = max(source_cursor, run_end)
            else:
                if contiguous:
                    # The renderer assigns an inter-word gap to the next
                    # speed segment; overlapping source is assigned to the
                    # preceding segment and cannot create a negative trim.
                    run_start = max(float(word_map[previous_id]["end"]), source_cursor)
                else:
                    # Cuts, reorders, and removed long pauses start a fresh
                    # source span at the selected word with no source gap.
                    run_start = source_start
                run_end = max(run_start, source_end)
                run_speed = speed
                run_output_start = current
                start = run_output_start + max(0.0, source_start - run_start) / speed
                end = run_output_start + max(0.0, source_end - run_start) / speed
                current = run_output_start + max(0.0, run_end - run_start) / speed
                source_cursor = run_end
            timing[word_id] = {"start": start, "end": end}
            previous_id = word_id
        return timing

    def _apply_captions(self, video, captions, active_ids, timing, duration):
        active = set(active_ids)
        for caption in captions:
            targets = [word_id for word_id in caption.get("target_word_ids", []) if word_id in active and word_id in timing]
            if not targets:
                self.warnings.append(f"Caption {caption.get('caption_id', '')} was omitted because all anchored words were cut.")
                continue
            # Split at non-target words so partially retained captions remain attached only to surviving anchors.
            spans = []
            current = []
            for word_id in active_ids:
                if word_id in targets:
                    current.append(word_id)
                elif current:
                    spans.append(current)
                    current = []
            if current:
                spans.append(current)
            style = caption.get("style") or {}
            color = self._safe_color(style.get("color", "white"))
            size = max(12, min(96, int(style.get("font_size", 24))))
            text = str(caption.get("text", "")).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
            for span in spans:
                start = timing[span[0]]["start"]
                end = timing[span[-1]]["end"]
                if end - start < 0.7:
                    end = min(duration, start + 0.7)
                if end <= start:
                    continue
                enable = f"between(t,{start:.6f},{end:.6f})"
                video = video.filter(
                    "drawtext",
                    text=text,
                    font=self._caption_font(text),
                    fontsize=size,
                    fontcolor=color,
                    x="(w-text_w)/2",
                    y="h-text_h-48",
                    box=1,
                    boxcolor="black@0.45",
                    boxborderw=8,
                    text_shaping=1,
                    enable=enable,
                )
        return video

    @staticmethod
    def _caption_font(text: str) -> str:
        """Choose a Noto font with glyph coverage for the caption's script."""
        for char in text:
            codepoint = ord(char)
            if 0x0B80 <= codepoint <= 0x0BFF:
                return "Noto Sans Tamil"
            if 0x0600 <= codepoint <= 0x06FF:
                return "Noto Sans Arabic"
            if 0x0900 <= codepoint <= 0x097F:
                return "Noto Sans Devanagari"
            if 0x0980 <= codepoint <= 0x09FF:
                return "Noto Sans Bengali"
            if 0x0A00 <= codepoint <= 0x0A7F:
                return "Noto Sans Gurmukhi"
            if 0x0A80 <= codepoint <= 0x0AFF:
                return "Noto Sans Gujarati"
            if 0x0B00 <= codepoint <= 0x0B7F:
                return "Noto Sans Oriya"
            if 0x0C00 <= codepoint <= 0x0C7F:
                return "Noto Sans Telugu"
            if 0x0C80 <= codepoint <= 0x0CFF:
                return "Noto Sans Kannada"
            if 0x0D00 <= codepoint <= 0x0D7F:
                return "Noto Sans Malayalam"
            if 0x0E00 <= codepoint <= 0x0E7F:
                return "Noto Sans Thai"
            if 0x0E80 <= codepoint <= 0x0EFF:
                return "Noto Sans Lao"
            if 0x1000 <= codepoint <= 0x109F:
                return "Noto Sans Myanmar"
            if 0x1780 <= codepoint <= 0x17FF:
                return "Noto Sans Khmer"
            if 0x0590 <= codepoint <= 0x05FF:
                return "Noto Sans Hebrew"
            if 0x3040 <= codepoint <= 0x30FF:
                return "Noto Sans CJK JP"
            if 0xAC00 <= codepoint <= 0xD7AF:
                return "Noto Sans CJK KR"
            if 0x3400 <= codepoint <= 0x9FFF:
                return "Noto Sans CJK SC"
        return "Noto Sans"

    @staticmethod
    def _safe_color(value: Any) -> str:
        color = str(value)
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color) or re.fullmatch(r"[A-Za-z]{1,20}", color):
            return color
        return "white"

    def _apply_effects(self, audio, effects, timing):
        for effect_type in ("mute", "bleep"):
            intervals = []
            for effect in effects:
                if effect.get("type") != effect_type:
                    continue
                for word_id in effect.get("target_word_ids", []):
                    if word_id in timing:
                        intervals.append(timing[word_id])
            if not intervals:
                continue
            enable = "+".join(f"between(t,{item['start']:.6f},{item['end']:.6f})" for item in intervals)
            audio = audio.filter("volume", 0, enable=enable)
            if effect_type == "bleep":
                final_end = max(item["end"] for item in timing.values())
                tone = ffmpeg.input(
                    f"aevalsrc=0.18*sin(2*PI*1000*t):s=48000:d={max(final_end, 0.01):.6f}",
                    f="lavfi",
                ).audio.filter("volume", 1, enable=enable)
                tone = tone.filter("asetpts", "PTS-STARTPTS")
                audio = ffmpeg.filter([audio, tone], "amix", inputs=2, duration="first", dropout_transition=0)
        return audio

    @staticmethod
    def _mix_background_music(audio, music, duration):
        source = str(music.get("audio_url", "")).strip()
        if not source:
            raise ValueError("Background music has no audio source.")
        bgm = ffmpeg.input(source).audio.filter("atrim", duration=duration).filter("asetpts", "PTS-STARTPTS")
        bgm = bgm.filter("volume", 0.18)
        if music.get("auto_ducking", True):
            bgm = ffmpeg.filter([bgm, audio], "sidechaincompress", threshold=0.025, ratio=8, attack=20, release=400)
        return ffmpeg.filter([audio, bgm], "amix", inputs=2, duration="first", dropout_transition=2)
