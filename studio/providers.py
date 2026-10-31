"""Video model catalogue and MiniMax's asynchronous image-to-video API."""
import base64
import mimetypes
import os
import time
import uuid

import requests

VIDEO_MODELS = [
    {"engine": "minimax_local", "id": "MiniMax-H3", "name": "MiniMax H3 · local, audio, no API fees"},
    {"engine": "ltx", "id": "ltx", "name": "LTX-2.3 · local, audio"},
    {"engine": "wan", "id": "wan", "name": "Wan 2.2 · local, silent"},
    {"engine": "minimax", "id": "MiniMax-Hailuo-2.3", "name": "MiniMax Hailuo 2.3 · cloud"},
    {"engine": "minimax", "id": "MiniMax-Hailuo-2.3-Fast", "name": "MiniMax Hailuo 2.3 Fast · cloud"},
    {"engine": "minimax", "id": "MiniMax-Hailuo-02", "name": "MiniMax Hailuo 02 · cloud"},
]


def validate_model(engine, model=""):
    if engine not in {m["engine"] for m in VIDEO_MODELS}:
        raise ValueError(f"Unknown video engine: {engine}")
    if model and not any(m["engine"] == engine and m["id"] == model for m in VIDEO_MODELS):
        raise ValueError(f"Unknown {engine} model: {model}")


def minimax_generate(job, settings, first, prompt, seconds, out_dir, model=""):
    cfg = settings["minimax"]
    model = model or cfg["model"]
    validate_model("minimax", model)
    key = os.environ.get(cfg["api_key_env"], "")
    if not key:
        raise ValueError(f"Set {cfg['api_key_env']} on the Studio server to use MiniMax")
    resolution = cfg["resolution"]
    if resolution not in ("768P", "1080P"):
        raise ValueError("MiniMax resolution must be 768P or 1080P")
    duration = 6 if seconds <= 6 or resolution == "1080P" else 10
    mime = mimetypes.guess_type(first)[0] or "image/png"
    with open(first, "rb") as f:
        image = "data:" + mime + ";base64," + base64.b64encode(f.read()).decode("ascii")
    headers = {"Authorization": f"Bearer {key}"}
    base = cfg["base_url"].rstrip("/")

    def request(method, endpoint, **kwargs):
        response = requests.request(method, base + endpoint, headers=headers, timeout=60, **kwargs)
        # Do not expose request headers or the base64 image in job errors.
        if not response.ok:
            raise RuntimeError(f"MiniMax HTTP {response.status_code}")
        data = response.json()
        status = data.get("base_resp") or {}
        if status.get("status_code", 0) != 0:
            raise RuntimeError(f"MiniMax: {status.get('status_msg', 'request failed')}")
        return data

    job.check()
    started = time.monotonic()
    data = request("POST", "/video_generation", json={
        "model": model, "first_frame_image": image, "prompt": prompt[:2000],
        "duration": duration, "resolution": resolution, "prompt_optimizer": False,
    })
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError("MiniMax returned no task ID")
    job.say(f"MiniMax task {task_id}: {model}, {duration}s. Cancellation stops local polling; the cloud task may continue.")
    deadline = started + float(cfg["timeout_seconds"])
    while time.monotonic() < deadline:
        job.check()
        status = request("GET", "/query/video_generation", params={"task_id": task_id})
        if status.get("status") == "Success":
            file_id = status.get("file_id")
            if not file_id:
                raise RuntimeError("MiniMax completed without a file ID")
            break
        if status.get("status") in ("Fail", "Failed"):
            raise RuntimeError("MiniMax video generation failed")
        for _ in range(max(1, int(float(cfg["poll_seconds"]) * 10))):
            job.check()
            time.sleep(0.1)
    else:
        raise TimeoutError(f"MiniMax task {task_id} timed out; it may still finish in the cloud")
    result = request("GET", "/files/retrieve", params={"file_id": file_id})
    url = (result.get("file") or {}).get("download_url")
    if not url or not url.startswith("https://"):
        raise RuntimeError("MiniMax returned no secure download URL")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"minimax_{uuid.uuid4().hex}.mp4")
    try:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(path, "wb") as f:
                for chunk in response.iter_content(1024 * 1024):
                    job.check()
                    f.write(chunk)
        if not os.path.getsize(path):
            raise RuntimeError("MiniMax returned an empty video")
    except BaseException:
        if os.path.exists(path):
            os.unlink(path)
        raise
    return path, time.monotonic() - started, model
