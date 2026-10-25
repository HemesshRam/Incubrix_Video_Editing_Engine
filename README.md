# IncuBrix Prompt-Based Video Editing Engine

A self-hosted video editing API and browser Studio. Upload a video, transcribe its speech, request edits in natural language, review the timeline, and render an MP4. The backend uses FastAPI, Redis, FFmpeg, faster-whisper, and either a deterministic mock prompt interpreter or Groq.

> **Project status:** This repository contains an assessment project, not a managed public editing service. The browser workflow and several assessment scenarios still need verification. See [ASSESSMENT_TRACEABILITY.md](ASSESSMENT_TRACEABILITY.md) for assessment coverage and remaining evidence gaps.

## What you need

### Recommended: Docker Compose

- Git
- Docker Desktop (Windows/macOS) or Docker Engine with the Docker Compose plugin (Linux)
- Free disk space for container images, transcription models, uploaded videos, and rendered outputs (the total varies with use)
- A browser

The Docker image includes Python 3.12, FFmpeg, ffprobe, and fonts used for captions. You do not need to install Python, FFmpeg, Redis, or a separate frontend server on the host.

### Optional: run the API and worker directly

- Python 3.12 recommended (the package metadata allows Python 3.10 through 3.14; native dependency support can vary by Python/platform)
- Redis server
- FFmpeg and ffprobe on `PATH`
- Git

## Quick start with Docker

Commands below use PowerShell on Windows. Run them from the project root, the folder containing `docker-compose.yml`.

### 1. Get the source

```powershell
git clone https://github.com/HemesshRam/Incubrix_Video_Editing_Engine.git
cd Incubrix_Video_Editing_Engine
```

If you already have the project folder, open a terminal in that folder instead.

### 2. Create your local configuration

```powershell
cp .env.example .env
```

The example config uses `LLM_PROVIDER="mock"`, which needs no API key and is suitable for trying the supported deterministic prompt patterns. To use Groq, edit `.env` and set:

```dotenv
LLM_PROVIDER="groq"
GROQ_MODEL="openai/gpt-oss-20b"
GROQ_API_KEY="paste-your-own-groq-api-key-here"
```

Get your own key from Groq. Do not commit `.env`, share your key, or put a key in screenshots. Mock mode does not provide LLM caption translation. Each person using Groq needs their own key and is subject to the provider's terms and limits.

### 3. Start Docker Desktop / Docker Engine

Wait until Docker reports that its engine is running. Confirm from the terminal:

```powershell
docker info
```

### 4. Build and start the application

```powershell
docker compose config --quiet
docker compose up --build -d
```

The first build downloads container images and Python dependencies. It can take several minutes. Compose starts the API, background worker, Redis job/session store, and SeaweedFS S3-compatible object store.

### 5. Confirm the services are up

```powershell
docker compose ps
```

The API should show `healthy`; the worker, Redis, and object store should show `Up` (Redis should show `healthy`). Check the API endpoints:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health
Invoke-RestMethod http://localhost:8000/api/v1/ready
```

Health returns `healthy`. Readiness returns `ready` with the job queue and idempotency store connected. If a service is not healthy, inspect its logs:

```powershell
docker compose logs --tail 100 api worker redis objectstore
```

### 6. Open the Studio

Open **http://localhost:8000/** in your browser. The frontend is served by FastAPI; there is no separate Node.js frontend command.

### 7. Try an editing session

1. Upload a video using the Studio. The configured default upload limit is 2 GB.
2. Wait for the transcription job to finish. The first transcription can take longer because faster-whisper may download its model.
3. Review the transcript. Recognition and translation can be inaccurate; inspect them before relying on the output.
4. Enter an edit prompt and review the result or clarification before continuing.
5. Try undo/redo, then request a render.
6. When rendering finishes, preview and download the MP4.

The API reference is available at **http://localhost:8000/docs**. Health and readiness endpoints are `/api/v1/health` and `/api/v1/ready`.

### 8. Stop the services

```powershell
docker compose down
```

This stops containers and preserves named Redis/object-store volumes and files in `storage/`. `docker compose down -v` also deletes the named volumes and their data; use it only if you intend to erase stored sessions/jobs/media.

## Optional: local Python development

Use this path if you want to edit/run Python code outside the containers. Keep Docker available for Redis; local jobs still require Redis. The `.env.example` defaults to local filesystem storage.

### 1. Install Python and FFmpeg

Install Python 3.12 and FFmpeg, then make sure both FFmpeg executables are available. On macOS with Homebrew, install FFmpeg with `brew install ffmpeg`. On Debian/Ubuntu, install it with `sudo apt install ffmpeg`. On Windows, install an FFmpeg build and add its `bin` directory to `PATH`.

```powershell
python --version
ffmpeg -version
ffprobe -version
```

### 2. Create and activate a virtual environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

On macOS or Linux, create and activate the environment with:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

If PowerShell blocks activation, use the Python executable directly instead: `\.venv\Scripts\python.exe`.

### 3. Install the project

```powershell
python -m pip install -e .
python -m pip install pytest
cp .env.example .env
```

For Groq prompts, edit `.env` as described in the Docker setup. Otherwise keep mock mode.

### 4. Start Redis

With Docker running, open a terminal in the project root and run:

```powershell
docker compose up -d redis
```

Check it with `docker compose ps redis`. If you choose S3 storage in `.env`, start the object store too with `docker compose up -d objectstore`; local storage needs only Redis.

### 5. Start the worker and API in separate terminals

In terminal 1, activate `.venv` and run:

```powershell
python -m scripts.job_worker
```

In terminal 2, activate `.venv` and run:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000/**. Stop each foreground process with `Ctrl+C`. Stop local dependencies with `docker compose down` when finished.

## Run automated checks

Install `pytest` as shown above, then from the project root run:

```powershell
python -m pytest -q
```

The render synchronization verification creates temporary media fixtures and requires `ffmpeg` and `ffprobe`:

```powershell
python -m scripts.verify_render_sync
```

To set non-PATH FFmpeg executables in PowerShell for the current terminal:

```powershell
$env:FFMPEG_BINARY = "C:\path\to\ffmpeg.exe"
$env:FFPROBE_BINARY = "C:\path\to\ffprobe.exe"
python -m scripts.verify_render_sync
```

## Run a scripted session

The YAML runner accepts a session file or a directory of YAML files. It needs a running API, worker, and Redis-backed jobs. Supply a YAML session that references a real video file; the checked-in sample uses placeholder media.

```powershell
python -m scripts.session_runner --session path\to\your-session.yaml --url http://localhost:8000/api/v1 --output test-results
```

For a directory, use `--session path\to\session-folder`. Results are written under the selected output directory. The runner may download an HTTP(S) video URL, but the API itself accepts file uploads rather than direct URL input.

## Configuration

Copy `.env.example` to `.env` before changing settings. Common settings:

| Variable | Purpose | Example/default |
|---|---|---|
| `LLM_PROVIDER` | `mock` for keyless deterministic development, `groq` for model-backed interpretation/translation | `mock` |
| `GROQ_MODEL` | Groq model ID | `openai/gpt-oss-20b` |
| `GROQ_API_KEY` | Your private Groq API key; only needed with `LLM_PROVIDER=groq` | unset in example |
| `WHISPER_MODEL_SIZE` | faster-whisper model used for transcription | `base` |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | Transcription runtime device and numeric format | `cpu` / `int8` |
| `MAX_UPLOAD_BYTES` | Maximum uploaded file size | `2000000000` (2 GB) |
| `STORAGE_BACKEND` | `local` or S3-compatible `minio` | `local` in example; Compose can use SeaweedFS when set to `minio` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` locally; Compose sets its internal service URL |

See [.env.example](.env.example) and [app/core/config.py](app/core/config.py) for the full settings list. Never commit `.env`, provider credentials, private footage, or generated media.

## Using the project and public access

Making the **GitHub source repository public** lets anyone view and clone its code. It does not host the Studio or provide a shared service. Each user runs the application on their own computer or deploys their own instance; Groq-backed features require that user to configure their own API key. Mock mode does not require a key.

This application does not provide user sign-in or access control. The Compose setup is for local development and testing. Do not expose it directly to the public Internet or treat CORS as authentication. A shared public deployment needs an authenticated hosting layer, protected secrets, and deployment-specific security configuration.

I cannot change the GitHub repository's visibility from this project folder. This workspace has no `.git` directory, configured remote, or GitHub CLI connection. To make the linked repository accessible to everyone, open its **Settings -> General -> Danger Zone -> Change repository visibility -> Make public** on GitHub. Before doing so, verify that no API keys, `.env` values, private videos, or personal data have been committed. The local `.gitignore` excludes `.env` and `storage/*`, but that does not remove files already committed to GitHub or from its history.

## API and implementation notes

- `POST /api/v1/sessions/upload` accepts a video upload and returns a transcription job. Poll `GET /api/v1/jobs/{job_id}`.
- `GET /api/v1/sessions/{session_id}` returns the transcript and current timeline state.
- `POST /api/v1/sessions/{session_id}/edit` interprets and applies a prompt to a session.
- `POST /api/v1/sessions/{session_id}/render` queues a render. Poll the job and download the result from `GET /api/v1/jobs/{job_id}/result`.
- `POST` requests use idempotency keys. Redis is required for background jobs.
- The timeline supports cut/keep/reorder/speed, mute/bleep, captions, translation, common-filler removal, transcript-gap trimming, background music state, undo/redo, and restore. Transcript-gap trimming uses word timing gaps as a silence proxy; it does not inspect audio waveforms.

See [openapi.yaml](openapi.yaml) for the checked-in API contract, [ASSESSMENT_TRACEABILITY.md](ASSESSMENT_TRACEABILITY.md) for assessment coverage, and [REPORT.md](REPORT.md) for design notes and current limitations.
