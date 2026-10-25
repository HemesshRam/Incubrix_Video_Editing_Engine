"""Keep API tests independent of a developer's .env and external services."""

import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ["IDEMPOTENCY_BACKEND"] = "memory"


class _ReadyRedisStub:
    async def ping(self):
        return True

    async def aclose(self):
        return None


# Import only after overriding settings so a real API key/provider is never used
# by the test suite. The route tests exercise HTTP behavior, not Redis itself.
from app.main import app, settings  # noqa: E402
from app.core.jobs import JobStore  # noqa: E402
from app.core.config import get_settings  # noqa: E402

_redis_stub = _ReadyRedisStub()
settings.idempotency_backend = "memory"
get_settings().idempotency_backend = "memory"
app.state.jobs_redis = _redis_stub
app.state.job_store = JobStore(_redis_stub)
