from app.core.llm import mock_interpret_prompt


def _words():
    tokens = ["Welcome", "to", "the", "channel", "today", "pricing", "is", "important"]
    return [
        {"id": f"w{i}", "word": token, "current_start": float(i), "current_end": float(i + 1)}
        for i, token in enumerate(tokens)
    ]


def test_sample_caption_wording_resolves_exact_anchor():
    response = mock_interpret_prompt(
        "Add a caption 'Welcome!' when I say welcome to the channel",
        None,
        _words(),
    )
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].target_word_ids == ["w0", "w1", "w2", "w3"]


def test_speed_prompt_uses_current_timeline_range():
    response = mock_interpret_prompt("Speed up from 0:02 to 0:05 by 2x", None, _words())
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "change_speed"
    assert result.operations[0].target_word_ids == ["w2", "w3", "w4"]
    assert result.operations[0].speed_factor == 2


def test_caption_style_prompt_targets_latest_caption():
    response = mock_interpret_prompt(
        "Make that caption red",
        None,
        _words(),
        {"captions": [{"caption_id": "cap_1", "text": "Welcome!"}]},
    )
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "update_caption"
    assert result.operations[0].caption_id == "cap_1"
    assert result.operations[0].style_attributes == {"color": "red"}


def test_auto_caption_intent_returns_deterministic_operation():
    response = mock_interpret_prompt("Auto-caption the video with 4 words per caption", None, _words())
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "auto_caption"
    assert result.operations[0].max_words_per_caption == 4


def test_mock_mode_explains_that_translation_needs_groq():
    response = mock_interpret_prompt("Add Tamil captions", None, _words())
    assert response.result.status == "rejected"
    assert "requires Groq" in response.result.reason


def test_filler_removal_only_targets_supported_filler_tokens():
    words = _words() + [
        {"id": "w8", "word": "um", "current_start": 8.0, "current_end": 8.3},
        {"id": "w9", "word": "like", "current_start": 8.4, "current_end": 8.7},
    ]
    response = mock_interpret_prompt("Remove filler words", None, words)
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "remove_fillers"
    assert result.operations[0].filler_words == ["um"]


def test_long_pause_request_uses_explicit_threshold_or_default():
    explicit = mock_interpret_prompt("Remove pauses over 1.2 seconds", None, _words()).result
    default = mock_interpret_prompt("Remove silence", None, _words()).result
    assert explicit.status == default.status == "applied"
    assert explicit.operations[0].op == "remove_long_pauses"
    assert explicit.operations[0].threshold_s == 1.2
    assert default.operations[0].threshold_s == 0.8
