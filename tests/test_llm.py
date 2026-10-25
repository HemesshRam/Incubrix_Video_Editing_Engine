from types import SimpleNamespace

import pytest

from app.core import llm
from app.models.schemas import CaptionTranslation, CaptionTranslationResult


def test_caption_translation_preserves_ids_and_order(monkeypatch):
    expected = CaptionTranslationResult(
        captions=[
            CaptionTranslation(caption_id="cap_1", text="வணக்கம்"),
            CaptionTranslation(caption_id="cap_2", text="நன்றி"),
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: expected)
        )
    )
    monkeypatch.setattr(llm, "get_llm_client", lambda: fake_client)
    monkeypatch.setattr(llm, "get_model_name", lambda: "openai/gpt-oss-20b")

    translated = llm.translate_captions(
        [{"caption_id": "cap_1", "text": "Hello"}, {"caption_id": "cap_2", "text": "Thanks"}],
        "Tamil",
    )

    assert translated == {"cap_1": "வணக்கம்", "cap_2": "நன்றி"}


def test_caption_translation_rejects_reordered_ids(monkeypatch):
    result = CaptionTranslationResult(
        captions=[
            CaptionTranslation(caption_id="cap_2", text="நன்றி"),
            CaptionTranslation(caption_id="cap_1", text="வணக்கம்"),
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: result)
        )
    )
    monkeypatch.setattr(llm, "get_llm_client", lambda: fake_client)
    monkeypatch.setattr(llm, "get_model_name", lambda: "openai/gpt-oss-20b")

    with pytest.raises(ValueError, match="did not preserve"):
        llm.translate_captions(
            [{"caption_id": "cap_1", "text": "Hello"}, {"caption_id": "cap_2", "text": "Thanks"}],
            "Tamil",
        )
