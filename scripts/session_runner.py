"""Run assessment YAML sessions against the HTTP API and archive all outputs."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
import uuid
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml


def _request(client, method, url, *, key=None, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    if key:
        headers["Idempotency-Key"] = key
    response = client.request(method, url, headers=headers, **kwargs)
    if response.status_code >= 400:
        try:
            body = response.json()
            message = body.get("error", {}).get("message", response.text)
        except Exception:
            message = response.text
        raise RuntimeError(f"HTTP {response.status_code}: {message}")
    return response


def _poll_job(client, api_url, job_id, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = _request(client, "GET", f"{api_url}/jobs/{job_id}").json()
        if job["status"] == "succeeded":
            return job
        if job["status"] == "failed":
            raise RuntimeError(job.get("error", {}).get("message", "Job failed."))
        time.sleep(1)
    raise TimeoutError(f"Job {job_id} exceeded {timeout} seconds.")


def _upload_video(client, api_url, video, run_id, timeout, base_dir):
    parsed = urlparse(str(video))
    if parsed.scheme in {"http", "https"}:
        with tempfile.TemporaryDirectory(prefix="incubrix-session-") as temporary:
            local = Path(temporary) / (Path(parsed.path).name or "source.mp4")
            with httpx.stream("GET", str(video), follow_redirects=True, timeout=timeout) as response:
                response.raise_for_status()
                with local.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            return _post_upload(client, api_url, local, run_id, timeout)
    local = Path(video).expanduser()
    if not local.is_absolute():
        local = base_dir / local
    local = local.resolve()
    if not local.is_file():
        raise FileNotFoundError(f"Video file not found: {local}")
    return _post_upload(client, api_url, local, run_id, timeout)


def _post_upload(client, api_url, local, run_id, timeout):
    with local.open("rb") as video_file:
        response = _request(
            client,
            "POST",
            f"{api_url}/sessions/upload",
            key=f"{run_id}:upload",
            files={"file": (local.name, video_file, mimetypes.guess_type(local.name)[0] or "application/octet-stream")},
            timeout=timeout,
        )
    return response.json()


def _resolve_clarification(prompt, answer, options):
    if not options:
        return None
    lowered = str(answer).strip().lower()
    if re.search(r"\ball\b", lowered):
        return f"{prompt}. Apply this to all matching occurrences."
    ordinal = re.search(r"\b(first|second|third|fourth|fifth|last)\b|\b(\d+)(?:st|nd|rd|th)?\b", lowered)
    if not ordinal:
        return None
    names = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
    index = names.get(ordinal.group(1), len(options) - 1 if ordinal.group(1) == "last" else int(ordinal.group(2)) - 1 if ordinal.group(2) else -1)
    if not 0 <= index < len(options):
        return None
    option = options[index]
    snippet = option.get("word_snippet", "").strip()
    if not snippet:
        return None
    # Convert the selected alternative into an exact phrase operation that the
    # interpreter can resolve against the current transcript deterministically.
    action = "Keep" if re.search(r"\bkeep\b", prompt, re.I) else "Cut"
    return f'{action} the exact phrase "{snippet}"'


def run_session(session_file: str, api_url: str = "http://localhost:8000/api/v1", output_dir: str | None = None, timeout: int = 3600):
    session_path = Path(session_file).resolve()
    spec = yaml.safe_load(session_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or "video" not in spec or not isinstance(spec.get("steps"), list):
        raise ValueError("Session YAML must contain `video` and a list of `steps`.")
    run_id = str(uuid.uuid4())
    result_dir = Path(output_dir).resolve() if output_dir else Path("test-results") / session_path.stem
    result_dir.mkdir(parents=True, exist_ok=True)
    api_url = api_url.rstrip("/")
    results = []
    results_path = result_dir / "results.json"
    session_id = None
    final_video = None

    with httpx.Client(timeout=timeout) as client:
        accepted = _upload_video(client, api_url, spec["video"], run_id, timeout, session_path.parent)
        upload_job = _poll_job(client, api_url, accepted["job_id"], timeout)
        session_id = upload_job["result"]["session_id"]
        step_index = 0

        for step in spec["steps"]:
            step_index += 1
            if isinstance(step, str):
                step = {"say": step}
            if not isinstance(step, dict):
                raise ValueError(f"Step {step_index} must be a mapping.")

            if step.get("render") is True or step.get("say") == "render":
                accepted_render = _request(client, "POST", f"{api_url}/sessions/{session_id}/render", key=f"{run_id}:step:{step_index}").json()
                completed = _poll_job(client, api_url, accepted_render["job_id"], timeout)
                output = _request(client, "GET", f"{api_url}/jobs/{accepted_render['job_id']}/result")
                final_video = result_dir / "final_video.mp4"
                final_video.write_bytes(output.content)
                results.append({"step": step_index, "status": "applied", "operations": [], "summary": "Rendered the current timeline.", "question": None, "options": [], "timeline_hash": completed["result"].get("timeline_hash"), "duration_s": None, "render_job_id": accepted_render["job_id"], "video": str(final_video)})
                continue

            prompt = step.get("say")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"Step {step_index} must have a non-empty `say` prompt or request render.")
            response = _request(
                client,
                "POST",
                f"{api_url}/sessions/{session_id}/edit",
                key=f"{run_id}:step:{step_index}",
                json={"prompt": prompt},
            ).json()
            responses = [response]
            result = response["result"]
            if result["status"] == "needs_clarification" and step.get("answer"):
                resolved = _resolve_clarification(prompt, step["answer"], result.get("options", []))
                if resolved:
                    response = _request(
                        client,
                        "POST",
                        f"{api_url}/sessions/{session_id}/edit",
                        key=f"{run_id}:step:{step_index}:answer",
                        json={"prompt": resolved},
                    ).json()
                    responses.append(response)
                    result = response["result"]
            state = _request(client, "GET", f"{api_url}/sessions/{session_id}").json()
            results.append({
                "step": step_index,
                "prompt": prompt,
                "answer": step.get("answer"),
                "status": result["status"],
                "operations": result.get("operations", []),
                "summary": result.get("summary"),
                "question": result.get("question"),
                "options": result.get("options", []),
                "reason": result.get("reason"),
                "timeline_hash": state["timeline_hash"],
                "duration_s": state["duration_s"],
                "revision": state["revision"],
                "responses": responses,
            })
            results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with httpx.Client(timeout=timeout) as client:
        final_state = _request(client, "GET", f"{api_url}/sessions/{session_id}").json()
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (result_dir / "final_timeline.json").write_text(json.dumps(final_state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if final_video is None:
        (result_dir / "final_video.json").write_text(json.dumps({"status": "not_rendered", "message": "Add a render step to the session to produce final_video.mp4."}, indent=2) + "\n", encoding="utf-8")
    return result_dir


def main():
    parser = argparse.ArgumentParser(description="Run one assessment YAML session or all YAML files in a directory.")
    parser.add_argument("--session", required=True, help="YAML file or folder of YAML sessions")
    parser.add_argument("--url", default="http://localhost:8000/api/v1", help="Versioned API base URL")
    parser.add_argument("--output", default="test-results", help="Directory for session result packs")
    parser.add_argument("--timeout", type=int, default=3600, help="Upload, job, and HTTP timeout in seconds")
    args = parser.parse_args()
    path = Path(args.session).resolve()
    files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")) if path.is_dir() else [path]
    if not files:
        parser.error(f"No YAML session files found in {path}")
    failed = False
    for session in files:
        try:
            output = run_session(str(session), args.url, str(Path(args.output) / session.stem), args.timeout)
            print(f"{session.name}: result pack written to {output}")
        except Exception as exc:
            failed = True
            error_dir = Path(args.output).resolve() / session.stem
            error_dir.mkdir(parents=True, exist_ok=True)
            (error_dir / "run_error.json").write_text(
                json.dumps({"session": str(session), "status": "failed", "error": str(exc)}, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"{session.name}: FAILED: {exc}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
