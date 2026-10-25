"""Redis-backed job records and queue primitives."""

from __future__ import annotations

import json
import hashlib
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from redis.exceptions import WatchError


class JobIdempotencyConflict(Exception):
    pass


class JobStore:
    STREAM = "incubrix:jobs:stream"
    GROUP = "incubrix-workers"

    def __init__(self, redis: Redis, processing_ttl_seconds: int = 300, job_ttl_seconds: int = 604800, idempotency_ttl_seconds: int = 86400):
        self.redis = redis
        self.processing_ttl_seconds = processing_ttl_seconds
        self.job_ttl_seconds = job_ttl_seconds
        self.idempotency_ttl_seconds = idempotency_ttl_seconds

    async def enqueue(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        job = {"job_id": str(uuid.uuid4()), "kind": kind, "status": "queued", "progress": 0, "created_at": time.time(), "updated_at": time.time(), "payload": payload, "result": None, "error": None}
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.set(self._key(job["job_id"]), json.dumps(job), ex=self.job_ttl_seconds)
            pipeline.xadd(self.STREAM, {"job_id": job["job_id"]})
            await pipeline.execute()
        return job

    async def enqueue_idempotent(self, key: str, fingerprint: str, kind: str, payload: dict[str, Any]):
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        mapping_key = f"incubrix:job-idempotency:{digest}"
        while True:
            async with self.redis.pipeline(transaction=True) as pipeline:
                try:
                    await pipeline.watch(mapping_key)
                    current = await pipeline.get(mapping_key)
                    if current:
                        record = json.loads(current)
                        if record["fingerprint"] != fingerprint:
                            raise JobIdempotencyConflict("This idempotency key was already used for different media.")
                        existing = await self.get(record["job_id"])
                        if existing is not None:
                            return existing, False
                        # The job expired before its idempotency record; allow a fresh accepted job.
                    job = {"job_id": str(uuid.uuid4()), "kind": kind, "status": "queued", "progress": 0, "created_at": time.time(), "updated_at": time.time(), "payload": payload, "result": None, "error": None}
                    pipeline.multi()
                    pipeline.set(mapping_key, json.dumps({"fingerprint": fingerprint, "job_id": job["job_id"]}), ex=self.idempotency_ttl_seconds)
                    pipeline.set(self._key(job["job_id"]), json.dumps(job), ex=self.job_ttl_seconds)
                    pipeline.xadd(self.STREAM, {"job_id": job["job_id"]})
                    await pipeline.execute()
                    return job, True
                except WatchError:
                    continue

    async def get(self, job_id: str) -> dict[str, Any] | None:
        value = await self.redis.get(self._key(job_id))
        return json.loads(value) if value else None

    async def update(self, job_id: str, **changes) -> None:
        job = await self.get(job_id)
        if job is None:
            return
        job.update(changes, updated_at=time.time())
        await self.redis.set(self._key(job_id), json.dumps(job), ex=self.job_ttl_seconds)

    async def reserve(self, consumer: str, timeout: int = 5) -> dict[str, Any] | None:
        try:
            await self.redis.xgroup_create(self.STREAM, self.GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        # Reclaim jobs abandoned by workers that died while holding their claim.
        reclaimed = await self.redis.xautoclaim(
            self.STREAM,
            self.GROUP,
            consumer,
            min_idle_time=self.processing_ttl_seconds * 1000,
            start_id="0-0",
            count=1,
        )
        messages = reclaimed[1] if reclaimed and len(reclaimed) > 1 else []
        if not messages:
            batches = await self.redis.xreadgroup(
                self.GROUP, consumer, {self.STREAM: ">"}, count=1, block=timeout * 1000
            )
            messages = batches[0][1] if batches else []
        if not messages:
            return None
        message_id, fields = messages[0]
        job = await self.get(fields["job_id"])
        if job is None:
            await self.redis.xack(self.STREAM, self.GROUP, message_id)
            return None
        job["_message_id"] = message_id
        job["_consumer"] = consumer
        return job

    async def acknowledge(self, job: dict[str, Any]) -> None:
        message_id = job.get("_message_id")
        if message_id:
            await self.redis.xack(self.STREAM, self.GROUP, message_id)

    async def refresh_claim(self, job: dict[str, Any]) -> None:
        message_id = job.get("_message_id")
        consumer = job.get("_consumer")
        if message_id and consumer:
            await self.redis.xclaim(self.STREAM, self.GROUP, consumer, 0, [message_id], idle=0, justid=True)

    @staticmethod
    def _key(job_id: str) -> str:
        return f"incubrix:job:{job_id}"
