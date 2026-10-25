"""Correlation IDs and response caching for retry-safe prompt requests."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import structlog
from redis.asyncio import Redis

from app.core.config import Settings

logger = structlog.get_logger(__name__)
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestMiddleware:
    """ASGI middleware with bounded local or Redis-backed edit idempotency.

    The current edit endpoint only interprets a prompt, so successful JSON
    responses can be cached directly. Future asynchronous edit/job routes
    should cache the accepted job ID using the same key/fingerprint contract.
    """

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.redis: Optional[Redis] = (
            Redis.from_url(settings.redis_url, decode_responses=True)
            if settings.idempotency_backend == "redis"
            else None
        )
        self._entries: OrderedDict[str, Dict[str, Any]] = OrderedDict()

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        correlation_id = self._correlation_id(headers.get(b"x-correlation-id"))
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        if scope["method"] != "POST" or not self._uses_idempotency(scope["path"]):
            await self._call_with_logging(scope, receive, send, correlation_id, None)
            return

        raw_key = headers.get(b"idempotency-key", b"").decode("ascii", errors="ignore")
        if not _KEY_PATTERN.fullmatch(raw_key):
            await self._json_error(
                send,
                400,
                "IDEMPOTENCY_KEY_REQUIRED",
                "A valid Idempotency-Key header is required for POST requests.",
                False,
                correlation_id,
            )
            return

        request_body = await self._read_body(receive, self.settings.max_idempotency_body_bytes)
        if request_body is None:
            await self._json_error(send, 413, "REQUEST_TOO_LARGE", "Request body exceeds the configured limit.", False, correlation_id)
            return
        query = scope.get("query_string", b"")
        fingerprint = hashlib.sha256(
            scope["method"].encode() + b"\n" + scope["path"].encode() + b"\n" + query + b"\n" + request_body
        ).hexdigest()
        store_key = hashlib.sha256(raw_key.encode("ascii")).hexdigest()

        try:
            owned, entry = await self._claim(store_key, fingerprint)
        except Exception:
            await self._json_error(send, 503, "IDEMPOTENCY_STORE_UNAVAILABLE", "Retry safety storage is unavailable.", True, correlation_id)
            return

        if not owned:
            if entry.get("fingerprint") != fingerprint:
                await self._json_error(send, 409, "IDEMPOTENCY_KEY_CONFLICT", "This key was already used for a different request.", False, correlation_id)
            elif entry.get("state") == "complete":
                logger.info("idempotency_replayed", correlation_id=correlation_id)
                await self._replay(send, entry, correlation_id)
            else:
                await self._json_error(send, 409, "IDEMPOTENCY_IN_PROGRESS", "A request with this key is still processing; retry shortly.", True, correlation_id)
            return

        request_sent = False

        async def replay_receive():
            nonlocal request_sent
            if request_sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            request_sent = True
            return {"type": "http.request", "body": request_body, "more_body": False}

        response_state: Dict[str, Any] = {"status": 500, "content_type": "", "body": bytearray(), "messages": []}
        completed = False

        async def capture_send(message):
            nonlocal completed
            if message["type"] == "http.response.start":
                response_state["status"] = message["status"]
                response_headers = list(message.get("headers", []))
                response_state["content_type"] = next(
                    (v.decode("latin1") for k, v in response_headers if k.lower() == b"content-type"), ""
                )
                if not any(k.lower() == b"x-correlation-id" for k, _ in response_headers):
                    response_headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            elif message["type"] == "http.response.body":
                response_state["body"].extend(message.get("body", b""))
                if not message.get("more_body", False):
                    completed = True
            response_state["messages"].append(message)

        try:
            await self._call_with_logging(scope, replay_receive, capture_send, correlation_id, raw_key)
            if completed and response_state["status"] < 400 and response_state["content_type"].startswith("application/json"):
                try:
                    await self._complete(store_key, fingerprint, response_state)
                except Exception:
                    await self._release_safely(store_key)
                    await self._json_error(send, 503, "IDEMPOTENCY_STORE_UNAVAILABLE", "Retry safety storage is unavailable.", True, correlation_id)
                    return
            else:
                await self._release_safely(store_key)
            for response_message in response_state["messages"]:
                await send(response_message)
        except Exception:
            await self._release_safely(store_key)
            raise

    async def _read_body(self, receive, maximum: int) -> Optional[bytes]:
        parts = bytearray()
        too_large = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None if too_large else bytes(parts)
            if not too_large:
                parts.extend(message.get("body", b""))
                if len(parts) > maximum:
                    too_large = True
                    parts.clear()
            if not message.get("more_body", False):
                return None if too_large else bytes(parts)

    async def _claim(self, key: str, fingerprint: str) -> Tuple[bool, Dict[str, Any]]:
        now = time.time()
        if self.redis is not None:
            existing = await self.redis.get("incubrix:idempotency:" + key)
            if existing is None:
                pending = json.dumps({"fingerprint": fingerprint, "state": "processing"})
                if await self.redis.set("incubrix:idempotency:" + key, pending, ex=self.settings.idempotency_processing_ttl_seconds, nx=True):
                    return True, {"fingerprint": fingerprint, "state": "processing"}
                existing = await self.redis.get("incubrix:idempotency:" + key)
            return False, json.loads(existing) if existing else {"fingerprint": "", "state": "processing"}

        for entry_key in list(self._entries):
            if self._entries[entry_key]["expires_at"] <= now:
                del self._entries[entry_key]
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            return False, entry
        self._entries[key] = {
            "fingerprint": fingerprint,
            "state": "processing",
            "expires_at": now + self.settings.idempotency_processing_ttl_seconds,
        }
        self._trim_memory()
        return True, self._entries[key]

    async def _complete(self, key: str, fingerprint: str, response: Dict[str, Any]) -> None:
        entry = {
            "fingerprint": fingerprint,
            "state": "complete",
            "status": response["status"],
            "body": bytes(response["body"]).decode("utf-8"),
        }
        if self.redis is not None:
            await self.redis.set(
                "incubrix:idempotency:" + key,
                json.dumps(entry),
                ex=self.settings.idempotency_ttl_seconds,
            )
        else:
            entry["expires_at"] = time.time() + self.settings.idempotency_ttl_seconds
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self._trim_memory()

    async def _release(self, key: str) -> None:
        if self.redis is not None:
            await self.redis.delete("incubrix:idempotency:" + key)
        else:
            self._entries.pop(key, None)

    async def _release_safely(self, key: str) -> None:
        try:
            await self._release(key)
        except Exception:
            logger.error("idempotency_release_failed")

    def _trim_memory(self) -> None:
        while len(self._entries) > self.settings.idempotency_memory_limit:
            self._entries.popitem(last=False)

    async def _call_with_logging(self, scope, receive, send, correlation_id: str, idempotency_key: Optional[str]) -> None:
        started = time.perf_counter()
        response_status = 500

        async def with_correlation(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = list(message.get("headers", []))
                if not any(key.lower() == b"x-correlation-id" for key, _ in response_headers):
                    response_headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, with_correlation)
        finally:
            logger.info(
                "http_request",
                path=scope["path"],
                method=scope["method"],
                status_code=response_status,
                correlation_id=correlation_id,
                idempotency_key_present=idempotency_key is not None,
                job_id=None,
                duration_s=round(time.perf_counter() - started, 4),
            )

    async def _replay(self, send, entry: Dict[str, Any], correlation_id: str) -> None:
        body = entry["body"].encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": entry["status"],
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-correlation-id", correlation_id.encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _json_error(self, send, status: int, code: str, message: str, retryable: bool, correlation_id: str) -> None:
        logger.warning("request_rejected", status_code=status, error_code=code, correlation_id=correlation_id)
        body = json.dumps({"error": {"code": code, "message": message, "retryable": retryable, "details": {}}}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-correlation-id", correlation_id.encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

    @staticmethod
    def _correlation_id(value: Optional[bytes]) -> str:
        if value is not None:
            candidate = value.decode("ascii", errors="ignore")
            if candidate and len(candidate) <= 128 and all(32 < ord(char) < 127 for char in candidate):
                return candidate
        return str(uuid.uuid4())

    @staticmethod
    def _uses_idempotency(path: str) -> bool:
        return path == "/api/v1/edit" or path == "/api/v1/sessions" or bool(
            re.fullmatch(r"/api/v1/sessions/[0-9a-fA-F-]+/(?:operations|edit|render)", path)
        )
