import uuid
import structlog

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.llm import LLMConfigurationError, interpret_prompt, translate_captions
from app.core.jobs import JobIdempotencyConflict, JobStore
from app.core.media_storage import MediaStorage, safe_media_extension
from app.core.session_store import SessionConflict
from app.engine.timeline import TimelineEngine
from app.engine.transcriber import Transcriber
from app.models.schemas import (
    AppliedResponse,
    ApplyOperationsRequest,
    CreateSessionRequest,
    PromptInterpretationResult,
    RejectedResponse,
    SessionEditResponse,
    SessionPromptRequest,
    TimelineSessionResponse,
    JobAcceptedResponse,
    JobStatusResponse,
)

router = APIRouter(prefix="/api/v1")
logger = structlog.get_logger(__name__)


def _transcribe_media(video_path: str):
    transcriber = Transcriber()
    metadata = transcriber.extract_metadata(video_path)
    transcript = transcriber.transcribe(video_path)
    if not transcript:
        raise ValueError("No speech was detected; create a session with a prepared transcript instead.")
    metadata["transcript_language"] = transcriber.detected_language
    return metadata, transcript

@router.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint required by Integration Standard #8."""
    return {"status": "healthy", "service": "incubrix-video-engine"}

@router.get("/ready", tags=["System"])
async def readiness_check(request: Request):
    """Check Redis when it is configured as the retry-safety store."""
    settings = get_settings()
    dependencies = {}
    try:
        await request.app.state.jobs_redis.ping()
        dependencies["job_queue"] = "connected"
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "DEPENDENCY_NOT_READY", "message": "The Redis job queue is unavailable.", "retryable": True, "details": {}}},
        ) from exc
    if settings.idempotency_backend == "redis":
        dependencies["idempotency_store"] = "connected"
    else:
        dependencies["idempotency_store"] = "process_local"
    return {"status": "ready", "service": "incubrix-video-engine", "dependencies": dependencies}

@router.post("/edit", response_model=PromptInterpretationResult, tags=["Editing"])
async def process_prompt(
    prompt: str = Query(..., min_length=1, max_length=4000),
    _idempotency_key: str = Header(..., alias="Idempotency-Key")
):
    """
    Translates natural language editing instructions into structured operations
    returning Applied, Needs clarification, or Rejected.
    """
    try:
        return await run_in_threadpool(interpret_prompt, prompt, PromptInterpretationResult)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "LLM_CONFIGURATION_ERROR", "message": str(exc), "retryable": False, "details": {}}}
        ) from exc
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "LLM_PARSING_FAILED", "message": "The configured language model could not interpret this instruction.", "retryable": True, "details": {}}}
        )


def _session_response(session_id: str, revision: int, engine: TimelineEngine, summaries=None):
    return TimelineSessionResponse(
        session_id=session_id,
        revision=revision,
        history_depth=max(0, len(engine.history) - 1),
        redo_depth=len(engine.redo_stack),
        timeline_hash=engine.get_current_timeline_hash(),
        duration_s=engine.get_duration(),
        active_word_ids=list(engine.active_word_ids),
        captions=engine.captions,
        effects=engine.effects,
        speed_map=engine.speed_map,
        background_music=engine.background_music,
        long_pause_threshold_s=engine.long_pause_threshold_s,
        operation_summaries=summaries or [],
        transcript=engine.raw_transcript,
        video_metadata=engine.metadata,
    )


async def _apply_operations(engine: TimelineEngine, operations):
    """Apply timeline operations and run requested caption translation safely."""
    translation_ops = [item for item in operations if getattr(item, "op", None) == "translate_captions"]
    timeline_ops = [item for item in operations if getattr(item, "op", None) != "translate_captions"]
    if len(translation_ops) > 1:
        raise ValueError("Only one caption translation language can be requested per edit.")
    if translation_ops and any(getattr(item, "op", None) == "history_control" for item in timeline_ops):
        raise ValueError("Caption translation cannot be combined with undo, redo, or restore.")

    had_timeline_ops = bool(timeline_ops)
    summaries = engine.apply_operations(timeline_ops) if timeline_ops else []
    for operation in translation_ops:
        if not engine.captions:
            raise ValueError("There are no captions to translate. Generate captions first.")
        source = [{"caption_id": item["caption_id"], "text": item["text"]} for item in engine.captions]
        translated = await run_in_threadpool(translate_captions, source, operation.language)
        summaries.append(engine.apply_caption_translations(operation.language, translated, record_history=False))
    if translation_ops:
        engine.record_composite_edit("; ".join(summaries), replace_latest=had_timeline_ops)
    return summaries


@router.post(
    "/sessions",
    response_model=TimelineSessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Sessions"],
    summary="Create a timeline session from a transcript",
)
async def create_session(
    payload: CreateSessionRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    """Accept a prepared transcript; media upload and transcription jobs are separate work."""
    try:
        transcript = [word.model_dump() for word in payload.transcript]
        engine = TimelineEngine(transcript, payload.video_metadata.model_dump())
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "INVALID_TRANSCRIPT", "message": str(exc), "retryable": False, "details": {}}},
        ) from exc

    session_id = str(uuid.uuid4())
    try:
        await request.app.state.session_store.create(session_id, engine.to_document())
    except SessionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "SESSION_ID_CONFLICT", "message": str(exc), "retryable": True, "details": {}}},
        ) from exc
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "SESSION_STORE_UNAVAILABLE", "message": "The session store is unavailable.", "retryable": True, "details": {}}},
        ) from exc
    return _session_response(session_id, 0, engine)


@router.post(
    "/sessions/upload",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Sessions"],
    summary="Upload a video, transcribe it, and create an editing session",
)
async def create_session_from_upload(
    request: Request,
    file: UploadFile = File(...),
    language: str | None = Form(default=None, description="Whisper language code; omit to detect automatically"),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    settings = get_settings()
    import re
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        raise HTTPException(status_code=400, detail={"error": {"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Provide a valid Idempotency-Key header.", "retryable": False, "details": {}}})
    if file.content_type and not (file.content_type.startswith("video/") or file.content_type == "application/octet-stream"):
        raise HTTPException(status_code=415, detail={"error": {"code": "UNSUPPORTED_MEDIA_TYPE", "message": "Upload a video file.", "retryable": False, "details": {}}})
    language = language.strip().lower() if language and language.strip() else settings.whisper_language
    if language:
        from faster_whisper.tokenizer import _LANGUAGE_CODES
        if language not in _LANGUAGE_CODES:
            raise HTTPException(status_code=422, detail={"error": {"code": "UNSUPPORTED_TRANSCRIPTION_LANGUAGE", "message": "Choose a Whisper language code or leave it blank for automatic detection.", "retryable": False, "details": {}}})

    storage_key = f"uploads/{uuid.uuid4()}{safe_media_extension(file.filename)}"
    storage = MediaStorage()
    byte_count = 0
    stored_media = False
    try:
        from tempfile import SpooledTemporaryFile
        import hashlib
        staged = SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b")
        upload_digest = hashlib.sha256()
        try:
            while chunk := await file.read(1024 * 1024):
                byte_count += len(chunk)
                if byte_count > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail={"error": {"code": "UPLOAD_TOO_LARGE", "message": "The uploaded file exceeds MAX_UPLOAD_BYTES.", "retryable": False, "details": {}}})
                staged.write(chunk)
                upload_digest.update(chunk)
            staged.seek(0)
            await storage.ensure_bucket()
            await storage.save_upload(staged, storage_key)
            stored_media = True
        finally:
            staged.close()

        session_id = str(uuid.uuid4())
        source_uri = f"s3://{settings.minio_bucket_name}/{storage_key}" if settings.storage_backend in {"minio", "s3"} else str(storage._local_path(storage_key).resolve())
        fingerprint = f"{upload_digest.hexdigest()}:{byte_count}:{language or 'auto'}"
        job, created = await request.app.state.job_store.enqueue_idempotent(
            idempotency_key,
            fingerprint,
            "transcribe",
            {"storage_key": storage_key, "source_uri": source_uri, "session_id": session_id, "language": language},
        )
        if not created:
            await storage.delete(storage_key)
            stored_media = False
        return JobAcceptedResponse(job_id=job["job_id"], status="queued", status_url=f"/api/v1/jobs/{job['job_id']}")
    except HTTPException:
        if stored_media:
            await storage.delete(storage_key)
        raise
    except ValueError as exc:
        if stored_media:
            await storage.delete(storage_key)
        raise HTTPException(status_code=422, detail={"error": {"code": "MEDIA_PROCESSING_FAILED", "message": str(exc), "retryable": False, "details": {}}}) from exc
    except SessionConflict as exc:
        if stored_media:
            await storage.delete(storage_key)
        raise HTTPException(status_code=409, detail={"error": {"code": "SESSION_ID_CONFLICT", "message": str(exc), "retryable": True, "details": {}}}) from exc
    except JobIdempotencyConflict as exc:
        if stored_media:
            await storage.delete(storage_key)
        raise HTTPException(status_code=409, detail={"error": {"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(exc), "retryable": False, "details": {}}}) from exc
    except Exception as exc:
        if stored_media:
            await storage.delete(storage_key)
        raise HTTPException(status_code=422, detail={"error": {"code": "MEDIA_PROCESSING_FAILED", "message": "The uploaded media could not be stored or transcribed.", "retryable": False, "details": {}}}) from exc
    finally:
        await file.close()


@router.get("/jobs/{job_id}", response_model=JobStatusResponse, tags=["Jobs"])
async def get_job(job_id: str, request: Request):
    job = await request.app.state.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "JOB_NOT_FOUND", "message": "The requested job does not exist.", "retryable": False, "details": {}}})
    return JobStatusResponse(**{key: job[key] for key in ("job_id", "kind", "status", "progress", "created_at", "updated_at", "result", "error")})


@router.get("/jobs/{job_id}/result", tags=["Jobs"], summary="Download a completed render")
async def get_job_result(job_id: str, request: Request):
    job = await request.app.state.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "JOB_NOT_FOUND", "message": "The requested job does not exist.", "retryable": False, "details": {}}})
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail={"error": {"code": "JOB_RESULT_NOT_READY", "message": "The job has no completed result yet.", "retryable": True, "details": {}}})
    if job["kind"] != "render" or not (job.get("result", {}).get("output_path") or job.get("result", {}).get("output_storage_key")):
        return {"result": job.get("result")}
    from pathlib import Path
    from app.core.config import get_settings
    if job["result"].get("output_storage_key"):
        output = Path(await MediaStorage().local_source_path(job["result"]["output_storage_key"])).resolve()
    else:
        output = Path(job["result"]["output_path"]).resolve()
    root = get_settings().storage_path.resolve()
    if root not in output.parents or not output.is_file():
        raise HTTPException(status_code=404, detail={"error": {"code": "JOB_OUTPUT_NOT_FOUND", "message": "The rendered output is unavailable.", "retryable": False, "details": {}}})
    return FileResponse(output, media_type="video/mp4", filename=output.name)


@router.post("/sessions/{session_id}/render", response_model=JobAcceptedResponse, status_code=202, tags=["Jobs"])
async def enqueue_render(session_id: str, request: Request, _idempotency_key: str = Header(..., alias="Idempotency-Key")):
    stored = await request.app.state.session_store.load(session_id)
    if stored is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "The editing session does not exist.", "retryable": False, "details": {}}})
    _, document = stored
    source_uri = document.get("video_metadata", {}).get("source_uri")
    if not source_uri:
        raise HTTPException(status_code=409, detail={"error": {"code": "SOURCE_MEDIA_MISSING", "message": "This session has no stored source media to render.", "retryable": False, "details": {}}})
    storage_key = source_uri.split("/", 3)[-1] if source_uri.startswith("s3://") else None
    if storage_key is None:
        from pathlib import Path
        path = Path(source_uri)
        try:
            storage_key = str(path.resolve().relative_to(get_settings().storage_path.resolve()))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"error": {"code": "SOURCE_MEDIA_MISSING", "message": "The source media path is not in managed storage.", "retryable": False, "details": {}}}) from exc
    job = await request.app.state.job_store.enqueue("render", {"storage_key": storage_key, "timeline": document})
    return JobAcceptedResponse(job_id=job["job_id"], status="queued", status_url=f"/api/v1/jobs/{job['job_id']}")


@router.get("/sessions/{session_id}", response_model=TimelineSessionResponse, tags=["Sessions"])
async def get_session(session_id: str, request: Request):
    try:
        stored = await request.app.state.session_store.load(session_id)
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "SESSION_STORE_UNAVAILABLE", "message": "The session store is unavailable.", "retryable": True, "details": {}}},
        ) from exc
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "SESSION_NOT_FOUND", "message": "The editing session does not exist.", "retryable": False, "details": {}}},
        )
    revision, document = stored
    try:
        engine = TimelineEngine.from_document(document)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "SESSION_STATE_INVALID", "message": "The saved timeline state could not be loaded.", "retryable": False, "details": {}}},
        ) from exc
    return _session_response(session_id, revision, engine)


@router.get("/sessions/{session_id}/source", tags=["Sessions"], summary="Stream the source media for a session")
async def get_session_source(session_id: str, request: Request):
    stored = await request.app.state.session_store.load(session_id)
    if stored is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "The editing session does not exist.", "retryable": False, "details": {}}})
    _, document = stored
    source_uri = document.get("video_metadata", {}).get("source_uri")
    if not source_uri:
        raise HTTPException(status_code=404, detail={"error": {"code": "SOURCE_MEDIA_MISSING", "message": "This session has no stored source video.", "retryable": False, "details": {}}})
    from pathlib import Path
    settings = get_settings()
    if source_uri.startswith("s3://"):
        storage_key = source_uri.split("/", 3)[-1]
        source_path = Path(await MediaStorage().local_source_path(storage_key))
    else:
        source_path = Path(source_uri).resolve()
        storage_root = settings.storage_path.resolve()
        if storage_root not in source_path.parents:
            raise HTTPException(status_code=404, detail={"error": {"code": "SOURCE_MEDIA_MISSING", "message": "The source video is outside managed storage.", "retryable": False, "details": {}}})
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail={"error": {"code": "SOURCE_MEDIA_MISSING", "message": "The source video is unavailable.", "retryable": False, "details": {}}})
    return FileResponse(source_path, media_type="video/mp4", filename=source_path.name)


@router.post("/sessions/{session_id}/operations", response_model=TimelineSessionResponse, tags=["Sessions"])
async def apply_session_operations(
    session_id: str,
    payload: ApplyOperationsRequest,
    request: Request,
    _idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        stored = await request.app.state.session_store.load(session_id)
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "SESSION_STORE_UNAVAILABLE", "message": "The session store is unavailable.", "retryable": True, "details": {}}},
        ) from exc
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "SESSION_NOT_FOUND", "message": "The editing session does not exist.", "retryable": False, "details": {}}},
        )

    revision, document = stored
    try:
        engine = TimelineEngine.from_document(document)
        summaries = await _apply_operations(engine, payload.operations)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "LLM_CONFIGURATION_ERROR", "message": str(exc), "retryable": False, "details": {}}},
        ) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "INVALID_TIMELINE_OPERATION", "message": str(exc), "retryable": False, "details": {}}},
        ) from exc
    except Exception as exc:
        logger.error("caption_translation_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "CAPTION_TRANSLATION_FAILED", "message": "The requested caption translation could not be completed.", "retryable": True, "details": {}}},
        ) from exc

    try:
        next_revision = await request.app.state.session_store.save(session_id, revision, engine.to_document())
    except SessionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "SESSION_REVISION_CONFLICT", "message": str(exc), "retryable": True, "details": {"revision": revision}}},
        ) from exc
    except (KeyError, RedisError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "SESSION_STORE_UNAVAILABLE", "message": "The session could not be saved.", "retryable": True, "details": {}}},
        ) from exc
    return _session_response(session_id, next_revision, engine, summaries)


@router.post("/sessions/{session_id}/edit", response_model=SessionEditResponse, tags=["Sessions", "Editing"])
async def edit_session_from_prompt(
    session_id: str,
    payload: SessionPromptRequest,
    request: Request,
    _idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        stored = await request.app.state.session_store.load(session_id)
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "SESSION_STORE_UNAVAILABLE", "message": "The session store is unavailable.", "retryable": True, "details": {}}},
        ) from exc
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "SESSION_NOT_FOUND", "message": "The editing session does not exist.", "retryable": False, "details": {}}},
        )

    revision, document = stored
    try:
        engine = TimelineEngine.from_document(document)
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "SESSION_STATE_INVALID", "message": "The saved timeline state could not be loaded.", "retryable": False, "details": {}}},
        ) from exc
    # Translation needs the current caption text and IDs, not the entire word
    # transcript. Keep this context small so larger uploads stay within the
    # model provider's input budget. Other edits retain word-level anchors.
    translation_intent = _looks_like_caption_translation(payload.prompt)
    try:
        transcript_context = None if translation_intent else engine.get_active_transcript_context(get_settings().llm_context_max_words)
    except ValueError as exc:
        return SessionEditResponse(
            result=RejectedResponse(reason=str(exc)),
            revision=revision,
            timeline_hash=engine.get_current_timeline_hash(),
            duration_s=engine.get_duration(),
        )

    try:
        interpreted = await run_in_threadpool(
            interpret_prompt,
            payload.prompt,
            PromptInterpretationResult,
            transcript_context,
            _prompt_timeline_context(engine, compact=translation_intent),
        )
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "LLM_CONFIGURATION_ERROR", "message": str(exc), "retryable": False, "details": {}}},
        ) from exc
    except Exception as exc:
        logger.error("prompt_interpretation_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "LLM_PARSING_FAILED", "message": "The configured language model could not interpret this instruction.", "retryable": True, "details": {}}},
        ) from exc

    result = interpreted.result
    if isinstance(result, AppliedResponse):
        try:
            operation_summaries = await _apply_operations(engine, result.operations)
        except LLMConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": {"code": "LLM_CONFIGURATION_ERROR", "message": str(exc), "retryable": False, "details": {}}},
            ) from exc
        except (KeyError, ValueError) as exc:
            result = RejectedResponse(reason=f"The proposed edit was not safe to apply: {exc}")
        except Exception as exc:
            logger.error("caption_translation_failed", error_type=type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": {"code": "CAPTION_TRANSLATION_FAILED", "message": "The requested caption translation could not be completed.", "retryable": True, "details": {}}},
            ) from exc
        else:
            caption_notices = []
            for summary in operation_summaries:
                lowered = summary.lower()
                for marker in ("removed ", "updated "):
                    index = lowered.find(marker)
                    if index >= 0 and any(token in lowered[index:] for token in ("caption", "effect", "bleep", "mute")):
                        caption_notices.append(summary[index:])
                        break
            if caption_notices:
                result.summary = f"{result.summary} {' '.join(caption_notices)}"
            try:
                revision = await request.app.state.session_store.save(session_id, revision, engine.to_document())
            except SessionConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"error": {"code": "SESSION_REVISION_CONFLICT", "message": str(exc), "retryable": True, "details": {}}},
                ) from exc
            except (KeyError, RedisError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"error": {"code": "SESSION_STORE_UNAVAILABLE", "message": "The edited session could not be saved.", "retryable": True, "details": {}}},
                ) from exc
    return SessionEditResponse(
        result=result,
        revision=revision,
        timeline_hash=engine.get_current_timeline_hash(),
        duration_s=engine.get_duration(),
    )


def _looks_like_caption_translation(prompt: str) -> bool:
    """Recognize clear translation intent across Latin and common non-Latin scripts."""
    text = prompt.casefold()
    translation_terms = (
        "translate", "translation", "localize", "localise", "localization", "localisation",
        "traducir", "traduce", "traducción", "traduction", "übersetz", "traduz", "traduzione",
    )
    if any(term in text for term in translation_terms):
        return True

    has_non_latin_script = any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        or "\uac00" <= char <= "\ud7af" or "\u0600" <= char <= "\u06ff"
        or "\u0900" <= char <= "\u097f" or "\u0b80" <= char <= "\u0bff"
        or "\u0c00" <= char <= "\u0c7f" or "\u0c80" <= char <= "\u0cff"
        or "\u0d00" <= char <= "\u0d7f" or "\u0e00" <= char <= "\u0e7f"
        or "\u0400" <= char <= "\u052f"
        for char in text
    )
    has_caption_term = any(term in text for term in (
        "caption", "subtitle", "subtitles", "captions", "\u5b57\u5e55", "\uc790\ub9c9",
        "\u062a\u0639\u0644\u064a\u0642\u0627\u062a", "\u0909\u092a\u0936\u0940\u0930\u094d\u0937\u0915",
        "\u0ba4\u0bb2\u0bc8\u0baa\u0bcd\u0baa\u0bc1", "\u0bb5\u0b9a\u0ba9", "\u7ffb\u8a33", "\u7ffb\u8bd1",
    ))
    return has_non_latin_script and has_caption_term


def _prompt_timeline_context(engine: TimelineEngine, *, compact: bool = False):
    if compact:
        return {
            "captions": [
                {"caption_id": item["caption_id"], "text": item["text"]}
                for item in engine.captions
            ],
            "transcript_language": engine.metadata.get("transcript_language"),
        }
    return {
        "captions": engine.captions,
        "transcript_language": engine.metadata.get("transcript_language"),
        "history": [
            {"step_index": index, "summary": item.get("summary", "")}
            for index, item in enumerate(engine.history)
        ],
    }
