import sys
import os
import pytest

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.engine.timeline import TimelineEngine

def test_timeline_engine_sanity():
    print("Running Timeline Engine Sanity Test...")

    # 1. Mock transcript with word IDs
    mock_transcript = [
        {"word_id": "w_1", "word": "Hello", "start": 0.0, "end": 0.5},
        {"word_id": "w_2", "word": "welcome", "start": 0.6, "end": 1.0},
        {"word_id": "w_3", "word": "to", "start": 1.1, "end": 1.3},
        {"word_id": "w_4", "word": "the", "start": 1.4, "end": 1.5},
        {"word_id": "w_5", "word": "channel", "start": 1.6, "end": 2.0},
    ]
    mock_metadata = {"duration": 2.0, "fps": 30.0}

    # 2. Initialize Engine
    engine = TimelineEngine(mock_transcript, mock_metadata)
    initial_hash = engine.get_current_timeline_hash()
    print(f" Initial Timeline Hash: {initial_hash}")
    assert len(engine.active_word_ids) == 5

    # 3. Perform an edit: Cut the first word ("Hello")
    summary = engine.cut_range(["w_1"])
    print(f" Edit Applied: {summary}")
    print(f" Active Words after cut: {engine.active_word_ids}")
    assert "w_1" not in engine.active_word_ids
    assert len(engine.active_word_ids) == 4

    # 4. Test Undo operation
    undo_msg = engine.undo()
    print(f" Undo Result: {undo_msg}")
    print(f" Active Words after undo: {engine.active_word_ids}")
    assert "w_1" in engine.active_word_ids
    assert len(engine.active_word_ids) == 5

    # 5. Verify Determinism: Hash should match initial state after undo
    restored_hash = engine.get_current_timeline_hash()
    print(f" Restored Timeline Hash: {restored_hash}")
    assert initial_hash == restored_hash
    print(" SUCCESS: Timeline Engine logic and undo/redo mechanics are working perfectly!")


def test_duration_and_prompt_timing_include_unspoken_source_edges():
    transcript = [
        {"word_id": "w1", "word": "start", "start": 30.0, "end": 30.7},
        {"word_id": "w2", "word": "end", "start": 295.8, "end": 296.1},
    ]
    engine = TimelineEngine(transcript, {"duration": 323.988027, "fps": 30.0})

    assert engine.get_duration() == 323.988027
    context = engine.get_active_transcript_context()
    assert context[0]["current_start"] == 30.0
    assert context[1]["current_end"] == 296.1

    engine.cut_range(["w1"])
    # The removed first source anchor removes its leading footage too.
    assert engine.get_duration() == pytest.approx(28.188027)


def test_timeline_duration_matches_renderer_when_word_timestamps_overlap():
    transcript = [
        {"word_id": "w1", "word": "one", "start": 0.0, "end": 1.0},
        {"word_id": "w2", "word": "two", "start": 0.9, "end": 1.5},
        {"word_id": "w3", "word": "three", "start": 2.0, "end": 2.5},
    ]
    engine = TimelineEngine(transcript, {"duration": 3.0, "fps": 30.0})

    assert engine.get_duration() == 3.0
    context = engine.get_active_transcript_context()
    assert context[1]["current_start"] == 0.9
    assert context[1]["current_end"] == 1.5
    assert context[2]["current_start"] == 2.0


def test_timeline_duration_matches_speed_runs_when_overlapping_word_is_zero_length():
    transcript = [
        {"word_id": "w1", "word": "one", "start": 0.0, "end": 1.0},
        {"word_id": "w2", "word": "two", "start": 0.8, "end": 0.9},
        {"word_id": "w3", "word": "three", "start": 0.9, "end": 1.2},
    ]
    engine = TimelineEngine(transcript, {"duration": 1.2, "fps": 30.0})
    engine.change_speed(["w2"], 2.0)
    engine.change_speed(["w3"], 1.5)

    assert engine.get_duration() == pytest.approx(1.133333, abs=1e-6)


def test_cut_prunes_caption_anchors_and_undo_restores_caption():
    engine = TimelineEngine(
        [
            {"word_id": "w1", "word": "Keep", "start": 0.0, "end": 0.4},
            {"word_id": "w2", "word": "this", "start": 0.4, "end": 0.8},
            {"word_id": "w3", "word": "sentence", "start": 0.8, "end": 1.2},
            {"word_id": "w4", "word": "afterward", "start": 1.2, "end": 1.6},
        ],
        {"duration": 1.6, "fps": 30.0},
    )
    engine.add_caption("Keep this sentence", ["w1", "w2", "w3"])

    summary = engine.cut_range(["w1", "w2", "w3"])
    assert "Removed 1 caption" in summary
    assert engine.captions == []
    engine.undo()
    assert engine.captions[0]["text"] == "Keep this sentence"
    assert engine.captions[0]["target_word_ids"] == ["w1", "w2", "w3"]


def test_partial_caption_cut_keeps_only_surviving_word_anchors():
    engine = TimelineEngine(
        [
            {"word_id": "w1", "word": "Keep", "start": 0.0, "end": 0.4},
            {"word_id": "w2", "word": "this", "start": 0.4, "end": 0.8},
            {"word_id": "w3", "word": "sentence", "start": 0.8, "end": 1.2},
        ],
        {"duration": 1.2, "fps": 30.0},
    )
    engine.add_caption("Keep this sentence", ["w1", "w2", "w3"])

    summary = engine.cut_range(["w2"])
    assert "surviving word anchors" in summary
    assert engine.captions[0]["target_word_ids"] == ["w1", "w3"]


def test_caption_style_update_is_historical_and_atomic():
    engine = TimelineEngine(
        [
            {"word_id": "w1", "word": "hello", "start": 0.0, "end": 0.5},
            {"word_id": "w2", "word": "world", "start": 0.5, "end": 1.0},
        ],
        {"duration": 1.0, "fps": 30.0},
    )
    engine.add_caption("Hello", ["w1"])
    caption_id = engine.captions[0]["caption_id"]
    engine.apply_operations([{"op": "update_caption", "caption_id": caption_id, "style_attributes": {"color": "red"}}])
    assert engine.captions[0]["style"]["color"] == "red"
    engine.undo()
    assert engine.captions[0]["style"]["color"] == "white"

    before = engine.get_current_timeline_hash()
    try:
        engine.apply_operations([{"op": "update_caption", "caption_id": caption_id, "style_attributes": {"unknown": "x"}}])
    except ValueError:
        pass
    else:
        raise AssertionError("Unsupported caption style must be rejected")
    assert engine.get_current_timeline_hash() == before


def test_auto_caption_splits_at_sentence_end_and_word_limit():
    transcript = [
        {"word_id": "w1", "word": "Hello", "start": 0.0, "end": 0.3, "speaker": "s1"},
        {"word_id": "w2", "word": "there.", "start": 0.3, "end": 0.6, "speaker": "s1"},
        {"word_id": "w3", "word": "How", "start": 0.7, "end": 0.9, "speaker": "s1"},
        {"word_id": "w4", "word": "are", "start": 0.9, "end": 1.1, "speaker": "s1"},
        {"word_id": "w5", "word": "you?", "start": 1.1, "end": 1.4, "speaker": "s1"},
    ]
    engine = TimelineEngine(transcript, {"duration": 1.4, "fps": 30.0})
    engine.apply_operations([{"op": "auto_caption", "max_words_per_caption": 3}])
    assert [caption["text"] for caption in engine.captions] == ["Hello there.", "How are you?"]
    assert [caption["target_word_ids"] for caption in engine.captions] == [["w1", "w2"], ["w3", "w4", "w5"]]


def test_prompt_caption_translation_preserves_anchors_and_is_undoable(monkeypatch):
    import asyncio

    from app.api import routes
    from app.models.schemas import AutoCaptionOp, TranslateCaptionsOp

    engine = TimelineEngine(
        [
            {"word_id": "w1", "word": "Hello", "start": 0.0, "end": 0.4},
            {"word_id": "w2", "word": "world.", "start": 0.4, "end": 0.8},
        ],
        {"duration": 0.8, "fps": 30.0},
    )
    monkeypatch.setattr(
        routes,
        "translate_captions",
        lambda captions, language: {item["caption_id"]: "வணக்கம் உலகம்." for item in captions},
    )

    summaries = asyncio.run(
        routes._apply_operations(
            engine,
            [AutoCaptionOp(max_words_per_caption=6), TranslateCaptionsOp(language="Tamil")],
        )
    )

    assert len(engine.captions) == 1
    assert engine.captions[0]["text"] == "வணக்கம் உலகம்."
    assert engine.captions[0]["target_word_ids"] == ["w1", "w2"]
    assert "Translated 1 captions into Tamil." in summaries
    engine.undo()
    assert engine.captions == []


def test_filler_removal_is_reversible_and_preserves_similar_words():
    transcript = [
        {"word_id": "w1", "word": "Well", "start": 0.0, "end": 0.3},
        {"word_id": "w2", "word": "um,", "start": 0.4, "end": 0.6},
        {"word_id": "w3", "word": "like", "start": 0.7, "end": 1.0},
        {"word_id": "w4", "word": "yes.", "start": 1.1, "end": 1.3},
    ]
    engine = TimelineEngine(transcript, {"duration": 1.3, "fps": 30.0})
    engine.apply_operations([{"op": "remove_fillers"}])
    assert engine.active_word_ids == ["w1", "w3", "w4"]
    engine.undo()
    assert engine.active_word_ids == ["w1", "w2", "w3", "w4"]


def test_long_pause_edit_updates_duration_and_is_undoable():
    transcript = [
        {"word_id": "w1", "word": "first", "start": 0.0, "end": 0.5},
        {"word_id": "w2", "word": "next", "start": 2.0, "end": 2.5},
    ]
    engine = TimelineEngine(transcript, {"duration": 2.5, "fps": 30.0})
    assert engine.get_duration() == 2.5
    engine.apply_operations([{"op": "remove_long_pauses", "threshold_s": 0.8}])
    assert engine.get_duration() == 1.0
    assert engine.get_active_transcript_context()[-1]["current_start"] == 0.5
    engine.undo()
    assert engine.get_duration() == 2.5


def test_legacy_timeline_document_without_pause_threshold_round_trips():
    engine = TimelineEngine(
        [{"word_id": "w1", "word": "hello", "start": 0.0, "end": 0.5}],
        {"duration": 0.5, "fps": 30.0},
    )
    document = engine.to_document()
    assert "long_pause_threshold_s" not in document["state"]
    restored = TimelineEngine.from_document(document)
    assert restored.long_pause_threshold_s is None
    assert restored.get_current_timeline_hash() == engine.get_current_timeline_hash()

if __name__ == "__main__":
    test_timeline_engine_sanity()
