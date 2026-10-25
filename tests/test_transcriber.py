from types import SimpleNamespace

from app.engine.transcriber import Transcriber
from app.core.config import Settings


def test_transcribe_preserves_positive_word_duration_after_rounding():
    transcriber = Transcriber.__new__(Transcriber)
    transcriber.language = "ta"
    words = [
        SimpleNamespace(word=" tiny", start=1.2344, end=1.2345, probability=0.9),
        SimpleNamespace(word=" normal", start=1.5, end=1.75, probability=0.8),
    ]
    received = {}

    def fake_transcribe(_path, **kwargs):
        received.update(kwargs)
        return [SimpleNamespace(words=words)], SimpleNamespace(language="ta")

    transcriber.model = SimpleNamespace(
        transcribe=fake_transcribe
    )

    result = transcriber.transcribe("unused.mp4")

    assert result[0]["start"] == 1.234
    assert result[0]["end"] == 1.235
    assert result[0]["end"] > result[0]["start"]
    assert result[1]["end"] > result[1]["start"]
    assert received["language"] == "ta"
    assert transcriber.detected_language == "ta"


def test_whisper_source_language_accepts_supported_codes_only():
    assert Settings(_env_file=None, whisper_language="ta").whisper_language == "ta"
    try:
        Settings(_env_file=None, whisper_language="tamil")
    except ValueError as exc:
        assert "supported Whisper language code" in str(exc)
    else:
        raise AssertionError("A language name must not be passed where Whisper expects a language code.")
