from pathlib import Path

import yaml

from scripts.session_runner import _resolve_clarification


def test_clarification_answer_selects_exact_option():
    options = [
        {"word_snippet": "pricing was mentioned", "start_time_current": "0:12"},
        {"word_snippet": "we should discuss pricing", "start_time_current": "1:05"},
    ]
    prompt = _resolve_clarification("Remove the pricing part", "the second one", options)
    assert prompt == 'Cut the exact phrase "we should discuss pricing"'


def test_sample_session_follows_assessment_yaml_shape():
    sample = Path(__file__).parents[1] / "sessions" / "sample_session.yaml"
    data = yaml.safe_load(sample.read_text(encoding="utf-8"))
    assert data["video"]
    assert isinstance(data["steps"], list)
    assert data["steps"][-1] == "render"
    assert len(data["steps"]) >= 8
