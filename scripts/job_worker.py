"""Run one or more Redis queue workers: python -m scripts.job_worker"""

import asyncio
import json
import logging
import signal
import uuid
from contextlib import suppress
from pathlib import Path

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError

from app.core.config import get_settings
from app.core.jobs import JobStore
from app.core.media_storage import MediaStorage
from app.core.session_store import SessionStore
from app.engine.renderer import VideoRenderer
from app.engine.timeline import TimelineEngine
from app.engine.transcriber import Transcriber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("incubrix.worker")

# Graceful shutdown flag
_shutdown_event = asyncio.Event()


def _transcribe(path: str, language: str | None = None):
    transcriber = Transcriber(language=language)
    metadata = transcriber.extract_metadata(path)
    transcript = transcriber.transcribe(path)
    if not transcript:
        raise ValueError("No speech was detected in the uploaded video.")
    metadata["transcript_language"] = transcriber.detected_language
    return metadata, transcript


def _render(source: str, document: dict, output: str):
    engine = TimelineEngine.from_document(document)
    renderer = VideoRenderer()
    output_path = renderer.render_timeline(
        source,
        engine.active_word_ids,
        engine.raw_transcript,
        engine.captions,
        output,
        effects=engine.effects,
        speed_map=engine.speed_map,
        background_music=engine.background_music,
        has_audio=engine.metadata.get("has_audio"),
        long_pause_threshold_s=engine.long_pause_threshold_s,
        source_duration=engine.metadata.get("duration"),
    )
    return output_path, renderer.warnings


async def execute(job: dict, jobs: JobStore, sessions: SessionStore):
    if job["status"] in {"succeeded", "failed"}:
        logger.info("skipping_completed_job job_id=%s status=%s", job["job_id"], job["status"])
        await jobs.acknowledge(job)
        return

    async def keep_lease():
        interval = max(1, jobs.processing_ttl_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await jobs.refresh_claim(job)
            except RedisError:
                logger.warning("lease_refresh_failed job_id=%s", job["job_id"])

    lease_task = asyncio.create_task(keep_lease())
    await jobs.update(job["job_id"], status="running", progress=max(job.get("progress", 0), 5))
    terminal_state_saved = False
    try:
        payload = job["payload"]
        storage = MediaStorage()
        source = await storage.local_source_path(payload["storage_key"])
        try:
            if job["kind"] == "transcribe":
                await jobs.update(job["job_id"], progress=15)
                metadata, transcript = await asyncio.to_thread(_transcribe, source, payload.get("language"))
                await jobs.update(job["job_id"], progress=90)
                metadata["source_uri"] = payload["source_uri"]
                session_id = payload["session_id"]
                engine = TimelineEngine(transcript, metadata)
                existing = await sessions.load(session_id)
                if existing is None:
                    await sessions.create(session_id, engine.to_document())
                else:
                    engine = TimelineEngine.from_document(existing[1])
                result = {"session_id": session_id, "revision": 0, "timeline_hash": engine.get_current_timeline_hash(), "result_url": f"/api/v1/sessions/{session_id}"}
            elif job["kind"] == "render":
                await jobs.update(job["job_id"], progress=15)
                output_name = f"renders/{job['job_id']}.mp4"
                local_output, warnings = await asyncio.to_thread(_render, source, payload["timeline"], output_name)
                output_storage_key = None
                output_path = str(Path(local_output).resolve())
                if storage.client is not None:
                    output_storage_key = output_name
                    await storage.ensure_bucket()
                    with open(local_output, "rb") as rendered_file:
                        await storage.save_upload(rendered_file, output_storage_key)
                    Path(local_output).unlink(missing_ok=True)
                    output_path = None
                result = {
                    "output_path": output_path,
                    "output_storage_key": output_storage_key,
                    "result_url": f"/api/v1/jobs/{job['job_id']}/result",
                    "timeline_hash": TimelineEngine.from_document(payload["timeline"]).get_current_timeline_hash(),
                    "manifest": {
                        "renderer": "ffmpeg",
                        "source_sha256": payload["timeline"]["video_metadata"].get("source_sha256"),
                        "timeline_hash": TimelineEngine.from_document(payload["timeline"]).get_current_timeline_hash(),
                        "warnings": warnings,
                        "rights": "User-supplied media; rights not verified by this service.",
                        "ai_generated_media": False,
                    },
                }
            else:
                raise ValueError(f"Unsupported job type: {job['kind']}")
        finally:
            if storage.client is not None:
                Path(source).unlink(missing_ok=True)
        await jobs.update(job["job_id"], status="succeeded", progress=100, result=result)
        terminal_state_saved = True
        logger.info("job_succeeded job_id=%s kind=%s", job["job_id"], job["kind"])
    except Exception as exc:
        logger.exception("job_failed job_id=%s: %s", job["job_id"], exc)
        await jobs.update(job["job_id"], status="failed", error={"message": str(exc)[:1000]})
        terminal_state_saved = True
    finally:
        lease_task.cancel()
        with suppress(asyncio.CancelledError):
            await lease_task
        if terminal_state_saved:
            await jobs.acknowledge(job)


async def main():
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    jobs = JobStore(redis, settings.job_processing_ttl_seconds, settings.job_retention_seconds)
    sessions = SessionStore(redis)

    # Register graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown_event.set)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals
            pass

    logger.info(
        "worker_started concurrency=%d processing_ttl=%ds",
        settings.max_concurrent_jobs, settings.job_processing_ttl_seconds,
    )

    try:
        async def worker_loop(worker_id: int):
            consumer = f"{__import__('socket').gethostname()}-{uuid.uuid4()}"
            reconnect_delay = 1
            while not _shutdown_event.is_set():
                try:
                    job = await jobs.reserve(consumer)
                    reconnect_delay = 1  # Reset on success
                    if job:
                        logger.info("job_claimed worker=%d job_id=%s kind=%s", worker_id, job["job_id"], job["kind"])
                        await execute(job, jobs, sessions)
                except RedisConnectionError:
                    logger.warning("redis_connection_lost worker=%d, retrying in %ds", worker_id, reconnect_delay)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30)
                except RedisError as exc:
                    logger.error("redis_error worker=%d: %s", worker_id, exc)
                    await asyncio.sleep(2)
                except Exception as exc:
                    logger.exception("worker_error worker=%d: %s", worker_id, exc)
                    await asyncio.sleep(1)

        tasks = [asyncio.create_task(worker_loop(i)) for i in range(settings.max_concurrent_jobs)]

        # Wait for shutdown signal or all tasks to complete
        shutdown_task = asyncio.create_task(_shutdown_event.wait())
        done, pending = await asyncio.wait(
            [shutdown_task] + tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not _shutdown_event.is_set() and any(task in tasks for task in done):
            logger.error("worker_loop_stopped_early, stopping remaining workers")
            _shutdown_event.set()
        if _shutdown_event.is_set():
            logger.info("shutdown_signal_received, waiting for active jobs to finish")
            await asyncio.gather(*tasks, return_exceptions=True)
        shutdown_task.cancel()
        with suppress(asyncio.CancelledError):
            await shutdown_task
    finally:
        await redis.aclose()
        logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
