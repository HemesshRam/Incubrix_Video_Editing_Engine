import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import _looks_like_caption_translation

client = TestClient(app)


@pytest.mark.parametrize("prompt", [
    "Translate the existing captions into Tamil",
    "Traduce los subt\u00edtulos al espa\u00f1ol",
    "字幕を日本語に翻訳して",
    "한국어 자막으로 번역해 줘",
    "தமிழ் வசனங்களை மொழிபெயர்க்கவும்",
])
def test_caption_translation_intent_detected_across_languages(prompt):
    assert _looks_like_caption_translation(prompt)


def test_non_translation_prompt_does_not_trigger_compact_translation_context():
    assert not _looks_like_caption_translation("Cut the first 10 seconds")

def test_health_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "incubrix-video-engine"


def test_studio_frontend_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "IncuBrix Studio" in response.text
    assert client.get("/assets/studio.js").status_code == 200


def test_openapi_lists_all_versioned_routes():
    schema = client.get("/openapi.yaml").json()
    assert schema["openapi"] == "3.1.0"
    assert "/api/v1/sessions/upload" in schema["paths"]
    assert "/api/v1/jobs/{job_id}/result" in schema["paths"]
    assert all(path.startswith("/api/v1/") for path in schema["paths"])

def test_readiness_endpoint():
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"

def test_edit_endpoint_mock_fallback():
    # Test with mock fallback or Groq client
    response = client.post(
        "/api/v1/edit",
        params={"prompt": "Cut the first 30 seconds"},
        headers={"Idempotency-Key": "test_key_123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    assert data["result"]["status"] in ["applied", "needs_clarification", "rejected"]
