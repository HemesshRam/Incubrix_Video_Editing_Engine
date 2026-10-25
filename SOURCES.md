# Sources, models, and software

## AI services and models

- Groq API, configured with `LLM_PROVIDER=groq`.
- Language model: `openai/gpt-oss-20b` by default, configurable through `GROQ_MODEL`. The model name is served through Groq; this project code does not call the OpenAI API.
- Speech recognition: Faster-Whisper using the configured Whisper model size. The runtime model weights are downloaded by Faster-Whisper/CTranslate2; exact model revision and licence evidence must be captured in the session manifest before submission.
- `LLM_PROVIDER=mock` is a deterministic local no-key adapter for basic transcript-aware edits; unsupported operations are rejected rather than guessed.

## Libraries and tools

Runtime dependencies are declared in `pyproject.toml`: FastAPI, Uvicorn, Pydantic, pydantic-settings, Redis Python client, Instructor, Groq Python SDK, python-dotenv, structlog, ffmpeg-python, Faster-Whisper, HTTPX, python-multipart, boto3/botocore, and PyYAML. Redis server is used for session, job, and idempotency persistence in Compose. FFmpeg is a system dependency used by the renderer. Compose uses the official SeaweedFS release image for its S3-compatible local object store and pins the pulled image digest for reproducibility. The assessment permits any commercial-use-compatible S3 store. Poetry is used for packaging/dependency installation.

This file is a disclosure list, not a completed commercial-license audit. Before submission, record exact resolved versions and verify the licence of every shipped dependency, downloaded model, and generated output path. No external dataset or assessment media is bundled in this repository.
