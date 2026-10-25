import pytest
import ffmpeg

from app.engine.renderer import VideoRenderer


def test_render_segments_preserve_order_and_apply_speed():
    transcript = [
        {"word_id": "w1", "start": 0.0, "end": 1.0},
        {"word_id": "w2", "start": 1.2, "end": 2.0},
        {"word_id": "w3", "start": 3.0, "end": 4.0},
    ]
    words = {word["word_id"]: word for word in transcript}
    runs = VideoRenderer._segment_runs(["w3", "w1", "w2"], words, {"w1": 2.0})
    assert runs == [
        {"start": 3.0, "end": 4.0, "speed": 1.0},
        {"start": 0.0, "end": 1.0, "speed": 2.0},
        {"start": 1.0, "end": 2.0, "speed": 1.0},
    ]


def test_current_times_follow_reordered_content_and_speed():
    transcript = [
        {"word_id": "w1", "start": 0.0, "end": 1.0},
        {"word_id": "w2", "start": 1.5, "end": 2.5},
        {"word_id": "w3", "start": 3.0, "end": 4.0},
    ]
    words = {word["word_id"]: word for word in transcript}
    timeline = VideoRenderer._current_word_times(["w2", "w1", "w3"], words, {"w1": 2.0})
    assert timeline["w2"] == {"start": 0.0, "end": 1.0}
    assert timeline["w1"] == {"start": 1.0, "end": 1.5}
    assert timeline["w3"] == {"start": 1.5, "end": 2.5}


def test_source_edges_are_kept_only_at_surviving_timeline_boundaries():
    transcript = [
        {"word_id": "w1", "start": 30.0, "end": 30.7},
        {"word_id": "w2", "start": 31.0, "end": 31.5},
        {"word_id": "w3", "start": 295.8, "end": 296.1},
    ]
    words = {word["word_id"]: word for word in transcript}
    source_duration = 323.988027

    full = VideoRenderer._segment_runs(["w1", "w2", "w3"], words, {}, source_duration=source_duration)
    assert full[0]["start"] == 0.0
    assert full[-1]["end"] == source_duration
    assert VideoRenderer._current_word_times(["w1", "w2", "w3"], words, {})["w1"]["start"] == 30.0

    cut_first = VideoRenderer._segment_runs(["w2", "w3"], words, {}, source_duration=source_duration)
    assert cut_first[0]["start"] == 31.0
    assert cut_first[-1]["end"] == source_duration

    cut_last = VideoRenderer._segment_runs(["w1", "w2"], words, {}, source_duration=source_duration)
    assert cut_last[0]["start"] == 0.0
    assert cut_last[-1]["end"] == 31.5

    only_middle = VideoRenderer._segment_runs(["w2"], words, {}, source_duration=source_duration)
    assert only_middle == [{"start": 31.0, "end": 31.5, "speed": 1.0}]


def test_overlapping_transcript_words_do_not_extend_the_rendered_source_run():
    transcript = [
        {"word_id": "w1", "word": "one", "start": 0.0, "end": 1.0},
        {"word_id": "w2", "word": "two", "start": 0.9, "end": 1.5},
        {"word_id": "w3", "word": "three", "start": 2.0, "end": 2.5},
    ]
    words = {word["word_id"]: word for word in transcript}
    timing = VideoRenderer._current_word_times(["w1", "w2", "w3"], words, {})
    runs = VideoRenderer._segment_runs(["w1", "w2", "w3"], words, {}, source_duration=3.0)

    assert timing["w2"] == {"start": 0.9, "end": 1.5}
    assert timing["w3"] == {"start": 2.0, "end": 2.5}
    assert runs == [{"start": 0.0, "end": 3.0, "speed": 1.0}]


def test_speed_boundary_overlapping_words_never_creates_negative_or_duplicate_spans():
    transcript = [
        {"word_id": "w1", "word": "one", "start": 0.0, "end": 1.0},
        {"word_id": "w2", "word": "two", "start": 0.8, "end": 0.9},
        {"word_id": "w3", "word": "three", "start": 0.9, "end": 1.2},
    ]
    words = {word["word_id"]: word for word in transcript}
    runs = VideoRenderer._segment_runs(["w1", "w2", "w3"], words, {"w2": 2.0, "w3": 1.5})
    timing = VideoRenderer._current_word_times(["w1", "w2", "w3"], words, {"w2": 2.0, "w3": 1.5})

    assert runs == [
        {"start": 0.0, "end": 1.0, "speed": 1.0},
        {"start": 1.0, "end": 1.2, "speed": 1.5},
    ]
    assert timing["w2"] == {"start": 1.0, "end": 1.0}
    assert timing["w3"] == {"start": 1.0, "end": pytest.approx(1.1333333333)}


def test_long_pause_threshold_is_consistent_in_source_runs_and_timeline():
    transcript = [
        {"word_id": "w1", "start": 0.0, "end": 0.5},
        {"word_id": "w2", "start": 0.7, "end": 1.0},
        {"word_id": "w3", "start": 2.2, "end": 2.5},
    ]
    words = {word["word_id"]: word for word in transcript}
    runs = VideoRenderer._segment_runs(["w1", "w2", "w3"], words, {}, 0.8)
    timeline = VideoRenderer._current_word_times(["w1", "w2", "w3"], words, {}, 0.8)
    assert runs == [
        {"start": 0.0, "end": 1.0, "speed": 1.0},
        {"start": 2.2, "end": 2.5, "speed": 1.0},
    ]
    assert timeline["w3"]["start"] == 1.0
    assert timeline["w3"]["end"] == pytest.approx(1.3)


def test_caption_color_rejects_filter_injection():
    assert VideoRenderer._safe_color("#ff00aa") == "#ff00aa"
    assert VideoRenderer._safe_color("white") == "white"
    assert VideoRenderer._safe_color("red:enable=1") == "white"


def test_silent_render_command_does_not_emit_empty_audio_codec_option():
    stream = ffmpeg.input("silent-source.mp4")
    output = ffmpeg.output(
        stream.video,
        "silent-output.mp4",
        vcodec="libx264",
        pix_fmt="yuv420p",
        movflags="+faststart",
        fps_mode="vfr",
    )

    args = ffmpeg.compile(output)
    assert "-acodec" not in args
    assert args[-1] == "silent-output.mp4"
