"""Versioned storage for serialized editing sessions."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple

from redis.asyncio import Redis
from redis.exceptions import WatchError


class SessionConflict(Exception):
    """The session changed after the caller loaded its previous revision."""


class SessionStore:
    def __init__(self, redis_client: Optional[Redis] = None):
        self.redis = redis_client
        self._documents: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, document: Dict[str, Any]) -> None:
        row = {"revision": 0, "timeline": document}
        key = self._key(session_id)
        if self.redis is not None:
            created = await self.redis.set(key, json.dumps(row), nx=True)
            if not created:
                raise SessionConflict("Session ID already exists.")
            return
        async with self._lock:
            if session_id in self._documents:
                raise SessionConflict("Session ID already exists.")
            self._documents[session_id] = row

    async def load(self, session_id: str) -> Optional[Tuple[int, Dict[str, Any]]]:
        key = self._key(session_id)
        if self.redis is not None:
            value = await self.redis.get(key)
            if value is None:
                return None
            row = json.loads(value)
            return row["revision"], row["timeline"]
        async with self._lock:
            row = self._documents.get(session_id)
            return (row["revision"], row["timeline"]) if row else None

    async def save(self, session_id: str, expected_revision: int, document: Dict[str, Any]) -> int:
        key = self._key(session_id)
        if self.redis is not None:
            async with self.redis.pipeline(transaction=True) as pipeline:
                while True:
                    try:
                        await pipeline.watch(key)
                        current_value = await pipeline.get(key)
                        if current_value is None:
                            raise KeyError(session_id)
                        current = json.loads(current_value)
                        if current["revision"] != expected_revision:
                            raise SessionConflict("Session changed concurrently; reload before editing.")
                        new_revision = expected_revision + 1
                        pipeline.multi()
                        pipeline.set(key, json.dumps({"revision": new_revision, "timeline": document}))
                        await pipeline.execute()
                        return new_revision
                    except SessionConflict:
                        raise
                    except KeyError:
                        raise
                    except WatchError:
                        continue

        async with self._lock:
            current = self._documents.get(session_id)
            if current is None:
                raise KeyError(session_id)
            if current["revision"] != expected_revision:
                raise SessionConflict("Session changed concurrently; reload before editing.")
            new_revision = expected_revision + 1
            self._documents[session_id] = {"revision": new_revision, "timeline": document}
            return new_revision

    @staticmethod
    def _key(session_id: str) -> str:
        return f"incubrix:session:{session_id}"
