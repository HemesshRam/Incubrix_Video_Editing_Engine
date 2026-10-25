"""Tests for production readiness features: broader mock coverage, security, reliability."""

import pytest
from fastapi.testclient import TestClient
from app.main import app, settings
from app.core.llm import mock_interpret_prompt
from app.core.config import Settings


client = TestClient(app)


# ─── Broader Mock Interpreter Coverage ───


def _words():
    tokens = ["Welcome", "to", "the", "channel", "today", "pricing", "is", "important"]
    return [
        {"id": f"w{i}", "word": token, "current_start": float(i), "current_end": float(i + 1)}
        for i, token in enumerate(tokens)
    ]


def test_mock_reorder_move_after_pattern():
    response = mock_interpret_prompt(
        "Move 'Welcome to' after 'channel'",
        None,
        _words(),
    )
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "reorder"
    assert result.operations[0].source_word_ids == ["w0", "w1"]
    assert result.operations[0].insert_after_word_id == "w3"


def test_mock_reorder_phrase_not_found():
    response = mock_interpret_prompt(
        "Move 'nonexistent' after 'channel'",
        None,
        _words(),
    )
    assert response.result.status == "rejected"
    assert "not found" in response.result.reason.lower()


def test_mock_reorder_anchor_not_found():
    response = mock_interpret_prompt(
        "Move 'Welcome' after 'nonexistent'",
        None,
        _words(),
    )
    assert response.result.status == "rejected"
    assert "anchor" in response.result.reason.lower() or "not found" in response.result.reason.lower()


def test_mock_rejection_lists_available_operations():
    response = mock_interpret_prompt(
        "Make the video look cinematic",
        None,
        _words(),
    )
    assert response.result.status == "rejected"
    assert "cut" in response.result.reason.lower()
    assert "caption" in response.result.reason.lower()
    assert "undo" in response.result.reason.lower()


def test_mock_time_cut_with_compound_minutes_seconds():
    response = mock_interpret_prompt(
        "Cut the first 0 minutes 3 seconds",
        None,
        _words(),
    )
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "cut_range"


def test_mock_restore_step():
    response = mock_interpret_prompt("Restore timeline to step 0", None, _words())
    result = response.result
    assert result.status == "applied"
    assert result.operations[0].op == "history_control"
    assert result.operations[0].action == "restore_step"
    assert result.operations[0].target_step_index == 0


# ─── Security Headers ───


def test_security_headers_present():
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in response.headers


def test_cors_headers_not_on_arbitrary_origin():
    response = client.options(
        "/api/v1/health",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )
    # Should NOT include the evil origin in allowed origins
    allow_origin = response.headers.get("access-control-allow-origin", "")
    assert "evil.example.com" not in allow_origin


def test_cors_allows_an_explicit_configured_origin():
    origin = settings.cors_allowed_origins[0]
    response = client.options(
        "/api/v1/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_cors_configuration_normalizes_and_rejects_wildcards():
    configured = Settings(_env_file=None, cors_allowed_origins=["https://studio.example.com/"])
    assert configured.cors_allowed_origins == ["https://studio.example.com"]
    with pytest.raises(ValueError, match=r"explicit HTTP\(S\) origins"):
        Settings(_env_file=None, cors_allowed_origins=["*"])


# ─── Catch-all Exception Handler ───


async def _raise_internal_error():
    raise RuntimeError("private diagnostic detail")


app.add_api_route("/__test__/unhandled-error", _raise_internal_error, methods=["GET"], include_in_schema=False)


def test_unhandled_error_returns_structured_json_without_traceback():
    """Exercise the 500 handler itself, not a route-level 404."""
    origin = settings.cors_allowed_origins[0]
    response = TestClient(app, raise_server_exceptions=False).get(
        "/__test__/unhandled-error", headers={"Origin": origin}
    )
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"
    assert data["error"]["retryable"] is True
    assert "private diagnostic detail" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Access-Control-Allow-Origin"] == origin


# ─── Frontend Serving ───


def test_studio_html_served_at_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "IncuBrix Studio" in response.text


def test_studio_js_served():
    response = client.get("/assets/studio.js")
    assert response.status_code == 200
    assert "uploadVideo" in response.text


def test_studio_css_served():
    response = client.get("/assets/studio.css")
    assert response.status_code == 200
    assert "drag-over" in response.text


# ─── Timeline Engine: Reorder Validation ───


def test_timeline_reorder_roundtrip():
    from app.engine.timeline import TimelineEngine

    transcript = [
        {"word_id": "w1", "word": "Hello", "start": 0.0, "end": 0.5},
        {"word_id": "w2", "word": "beautiful", "start": 0.5, "end": 1.0},
        {"word_id": "w3", "word": "world", "start": 1.0, "end": 1.5},
    ]
    engine = TimelineEngine(transcript, {"duration": 1.5, "fps": 30.0})
    engine.reorder(["w1"], "w3")
    assert engine.active_word_ids == ["w2", "w3", "w1"]
    engine.undo()
    assert engine.active_word_ids == ["w1", "w2", "w3"]


def test_timeline_reorder_anchor_in_source_raises():
    from app.engine.timeline import TimelineEngine

    transcript = [
        {"word_id": "w1", "word": "Hello", "start": 0.0, "end": 0.5},
        {"word_id": "w2", "word": "world", "start": 0.5, "end": 1.0},
    ]
    engine = TimelineEngine(transcript, {"duration": 1.0, "fps": 30.0})
    with pytest.raises(ValueError, match="anchor"):
        engine.reorder(["w1"], "w1")


# ─── Session API: Edit + Undo/Redo Cycle ───


def test_session_create_edit_undo_redo_cycle():
    """End-to-end test: create session → edit → undo → redo through the HTTP API."""
    transcript = [
        {"word_id": f"w_{i+1:08d}", "word": w, "start": float(i), "end": float(i + 1), "confidence": 0.95}
        for i, w in enumerate(["Hello", "world", "this", "is", "a", "test"])
    ]
    metadata = {
        "source_sha256": "a" * 64,
        "duration": 6.0,
        "fps": 30.0,
        "width": 1280,
        "height": 720,
        "has_audio": True,
    }
    # Create session
    create_response = client.post(
        "/api/v1/sessions",
        json={"transcript": transcript, "video_metadata": metadata},
        headers={"Idempotency-Key": "test-create-001"},
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]
    assert create_response.json()["revision"] == 0
    assert create_response.json()["history_depth"] == 0
    assert create_response.json()["redo_depth"] == 0

    # Edit: cut first 3 seconds
    edit_response = client.post(
        f"/api/v1/sessions/{session_id}/edit",
        json={"prompt": "Cut the first 3 seconds"},
        headers={"Idempotency-Key": "test-edit-001"},
    )
    assert edit_response.status_code == 200
    edit_data = edit_response.json()
    assert edit_data["result"]["status"] == "applied"
    assert edit_data["revision"] == 1
    edited_session = client.get(f"/api/v1/sessions/{session_id}").json()
    assert edited_session["history_depth"] == 1
    assert edited_session["redo_depth"] == 0

    # Verify words were cut
    get_response = client.get(f"/api/v1/sessions/{session_id}")
    session_state = get_response.json()
    assert "w_00000001" not in session_state["active_word_ids"]

    # Undo
    undo_response = client.post(
        f"/api/v1/sessions/{session_id}/edit",
        json={"prompt": "Undo"},
        headers={"Idempotency-Key": "test-undo-001"},
    )
    assert undo_response.status_code == 200
    assert undo_response.json()["result"]["status"] == "applied"

    # Verify undo restored all words
    get_response2 = client.get(f"/api/v1/sessions/{session_id}")
    assert len(get_response2.json()["active_word_ids"]) == 6
    assert get_response2.json()["history_depth"] == 0
    assert get_response2.json()["redo_depth"] == 1

    # Redo
    redo_response = client.post(
        f"/api/v1/sessions/{session_id}/edit",
        json={"prompt": "Redo"},
        headers={"Idempotency-Key": "test-redo-001"},
    )
    assert redo_response.status_code == 200
    assert redo_response.json()["result"]["status"] == "applied"

    # Verify redo re-applied the cut
    get_response3 = client.get(f"/api/v1/sessions/{session_id}")
    assert "w_00000001" not in get_response3.json()["active_word_ids"]
    assert get_response3.json()["history_depth"] == 1
    assert get_response3.json()["redo_depth"] == 0


# ─── Session API: Auto-Caption + Undo ───


def test_session_auto_caption_and_undo():
    transcript = [
        {"word_id": f"w_{i+1:08d}", "word": w, "start": float(i) * 0.5, "end": float(i + 1) * 0.5, "confidence": 0.9}
        for i, w in enumerate(["The", "quick", "brown", "fox"])
    ]
    metadata = {
        "source_sha256": "b" * 64,
        "duration": 2.0,
        "fps": 30.0,
        "width": 1920,
        "height": 1080,
        "has_audio": True,
    }
    create = client.post(
        "/api/v1/sessions",
        json={"transcript": transcript, "video_metadata": metadata},
        headers={"Idempotency-Key": "test-cap-create-001"},
    )
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    # Auto-caption
    edit = client.post(
        f"/api/v1/sessions/{session_id}/edit",
        json={"prompt": "Generate captions with 4 words per caption"},
        headers={"Idempotency-Key": "test-cap-edit-001"},
    )
    assert edit.status_code == 200
    assert edit.json()["result"]["status"] == "applied"

    session = client.get(f"/api/v1/sessions/{session_id}").json()
    assert len(session["captions"]) >= 1
    assert session["captions"][0]["text"]

    # Undo caption
    undo = client.post(
        f"/api/v1/sessions/{session_id}/edit",
        json={"prompt": "Undo"},
        headers={"Idempotency-Key": "test-cap-undo-001"},
    )
    assert undo.status_code == 200
    session_after = client.get(f"/api/v1/sessions/{session_id}").json()
    assert len(session_after["captions"]) == 0


def test_session_cut_prunes_caption_anchors_and_undo_restores_caption():
    transcript = [
        {"word_id": "w1", "word": "Keep", "start": 0.0, "end": 0.4},
        {"word_id": "w2", "word": "this", "start": 0.4, "end": 0.8},
        {"word_id": "w3", "word": "sentence", "start": 0.8, "end": 1.2},
        {"word_id": "w4", "word": "afterward", "start": 1.2, "end": 1.6},
    ]
    metadata = {
        "source_sha256": "c" * 64,
        "duration": 1.6,
        "fps": 30.0,
        "width": 640,
        "height": 360,
        "has_audio": True,
    }
    created = client.post(
        "/api/v1/sessions",
        json={"transcript": transcript, "video_metadata": metadata},
        headers={"Idempotency-Key": "caption-cut-create-001"},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    added = client.post(
        f"/api/v1/sessions/{session_id}/operations",
        json={"operations": [{"op": "add_caption", "text": "Keep this sentence", "target_word_ids": ["w1", "w2", "w3"]}]},
        headers={"Idempotency-Key": "caption-cut-add-001"},
    )
    assert added.status_code == 200
    caption_id = added.json()["captions"][0]["caption_id"]

    effect_added = client.post(
        f"/api/v1/sessions/{session_id}/operations",
        json={"operations": [{"op": "audio_effect", "effect_type": "bleep", "target_word_ids": ["w1", "w2"]}]},
        headers={"Idempotency-Key": "caption-cut-effect-add-001"},
    )
    assert effect_added.status_code == 200

    partial = client.post(
        f"/api/v1/sessions/{session_id}/operations",
        json={"operations": [{"op": "cut_range", "target_word_ids": ["w1", "w2"]}]},
        headers={"Idempotency-Key": "caption-cut-partial-001"},
    )
    assert partial.status_code == 200
    assert "surviving word anchors" in " ".join(partial.json()["operation_summaries"])
    assert partial.json()["captions"][0]["target_word_ids"] == ["w3"]
    assert any("Removed 1 audio effect" in summary for summary in partial.json()["operation_summaries"])
    assert partial.json()["effects"] == []

    removed = client.post(
        f"/api/v1/sessions/{session_id}/edit",
        json={"prompt": 'Cut "sentence"'},
        headers={"Idempotency-Key": "caption-cut-remove-001"},
    )
    assert removed.status_code == 200
    assert "Removed 1 caption" in removed.json()["result"]["summary"]
    assert client.get(f"/api/v1/sessions/{session_id}").json()["captions"] == []

    undo = client.post(
        f"/api/v1/sessions/{session_id}/operations",
        json={"operations": [{"op": "history_control", "action": "undo"}]},
        headers={"Idempotency-Key": "caption-cut-undo-001"},
    )
    assert undo.status_code == 200
    assert undo.json()["captions"][0]["caption_id"] == caption_id
    assert undo.json()["captions"][0]["target_word_ids"] == ["w3"]


def test_interrupted_worker_job_remains_unacked(monkeypatch):
    import asyncio
    from scripts import job_worker

    class FakeJobs:
        processing_ttl_seconds = 3

        def __init__(self):
            self.acked = False

        async def update(self, *_args, **_kwargs):
            return None

        async def refresh_claim(self, *_args, **_kwargs):
            return None

        async def acknowledge(self, *_args, **_kwargs):
            self.acked = True

    class FakeStorage:
        client = None

        async def local_source_path(self, _key):
            return "unused-source.mp4"

    async def verify_interruption():
        started = asyncio.Event()

        async def blocked_to_thread(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(job_worker, "MediaStorage", FakeStorage)
        monkeypatch.setattr(job_worker.asyncio, "to_thread", blocked_to_thread)
        jobs = FakeJobs()
        task = asyncio.create_task(job_worker.execute(
            {
                "job_id": "interrupted-job",
                "kind": "transcribe",
                "status": "queued",
                "progress": 0,
                "payload": {"storage_key": "upload.mp4"},
                "_message_id": "1-0",
                "_consumer": "test-worker",
            },
            jobs,
            object(),
        ))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not jobs.acked

    asyncio.run(verify_interruption())
