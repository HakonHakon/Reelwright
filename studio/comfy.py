"""Minimal ComfyUI HTTP client: validate, queue, wait, download, upload, free."""

import json
import os
import time
import uuid

import requests


class ComfyError(RuntimeError):
    pass


def _combo_options(spec):
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    first = spec[0]
    if isinstance(first, list):
        return first
    if first == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


class Comfy:
    def __init__(self, host: str, timeout: int = 30):
        self.host = host
        self.base = f"http://{host}"
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex
        self._object_info = None

    # ── server state ──
    def alive(self) -> bool:
        try:
            requests.get(f"{self.base}/system_stats", timeout=5).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def object_info(self, refresh: bool = False) -> dict:
        if self._object_info is None or refresh:
            r = requests.get(f"{self.base}/object_info", timeout=120)
            r.raise_for_status()
            self._object_info = r.json()
        return self._object_info

    def options(self, node_class: str, input_name: str) -> list[str]:
        info = self.object_info().get(node_class, {}).get("input", {})
        spec = {**(info.get("required") or {}), **(info.get("optional") or {})}.get(input_name)
        return list(_combo_options(spec) or [])

    def has_node(self, node_class: str) -> bool:
        return node_class in self.object_info()

    def validate(self, wf: dict) -> list[str]:
        info_all = self.object_info()
        problems = []
        for nid, node in wf.items():
            cls = node.get("class_type")
            info = info_all.get(cls)
            if info is None:
                problems.append(f"node {nid}: '{cls}' is not installed in ComfyUI")
                continue
            spec = {**(info.get("input", {}).get("required") or {}),
                    **(info.get("input", {}).get("optional") or {})}
            for name, value in node.get("inputs", {}).items():
                if isinstance(value, list):
                    if value and value[0] not in wf:
                        problems.append(f"node {nid}: input {name} links to missing node {value[0]}")
                    continue
                opts = _combo_options(spec.get(name))
                if (opts and isinstance(value, str) and value not in opts
                        and cls not in ("LoadImage", "LoadAudio")
                        and (cls, name) not in (("LoadImageMask", "image"), ("LoadVideo", "file"))):
                    problems.append(f"{cls}.{name}: '{value}' not found in ComfyUI")
        return problems

    def free(self) -> None:
        try:
            requests.post(f"{self.base}/free", json={"unload_models": True, "free_memory": True}, timeout=30)
        except requests.RequestException:
            pass

    # ── files ──
    def upload(self, path: str, name: str | None = None) -> str:
        name = name or os.path.basename(path)
        with open(path, "rb") as f:
            r = requests.post(f"{self.base}/upload/image",
                              files={"image": (name, f, "application/octet-stream")},
                              data={"overwrite": "true", "type": "input"}, timeout=120)
        r.raise_for_status()
        data = r.json()
        sub = data.get("subfolder") or ""
        return f"{sub}/{data['name']}" if sub else data["name"]

    def download(self, item: dict, dest: str) -> str:
        params = {"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                  "type": item.get("type", "output")}
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with requests.get(f"{self.base}/view", params=params, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return dest

    # ── execution ──
    def run(self, wf: dict, log=None, cancel=None, poll: float = 2.0, max_wait: int = 4 * 3600) -> dict:
        """Queue a workflow and wait. Returns images, videos, audios and elapsed seconds."""
        problems = self.validate(wf)
        if problems:
            raise ComfyError("Workflow does not match your ComfyUI:\n  " + "\n  ".join(problems))
        r = requests.post(f"{self.base}/prompt", json={"prompt": wf, "client_id": self.client_id},
                          timeout=self.timeout)
        if r.status_code != 200:
            raise ComfyError(f"ComfyUI rejected the workflow: {r.text[:2000]}")
        pid = r.json()["prompt_id"]
        t0 = time.time()
        while True:
            if cancel and cancel():
                requests.post(f"{self.base}/interrupt", timeout=10)
                raise ComfyError("cancelled")
            if time.time() - t0 > max_wait:
                raise ComfyError("timed out waiting for ComfyUI")
            time.sleep(poll)
            try:
                hist = requests.get(f"{self.base}/history/{pid}", timeout=self.timeout).json().get(pid)
            except (requests.RequestException, ValueError):
                continue
            if not hist:
                continue
            status = hist.get("status", {})
            if status.get("status_str") == "error":
                msg = "unknown error"
                for m in status.get("messages", []):
                    if m and m[0] == "execution_error":
                        d = m[1]
                        msg = f"{d.get('node_type')}: {d.get('exception_message', '').strip()}"
                raise ComfyError(f"ComfyUI failed: {msg}")
            if status.get("completed") or hist.get("outputs"):
                if not status.get("completed", True):
                    continue
                break
        images, videos, audios = [], [], []
        for out in hist.get("outputs", {}).values():
            for key in ("images", "videos", "gifs", "audio"):
                for item in out.get(key, []) or []:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    # LoadVideo reports its input in history too; it is not a generated take.
                    if item.get("type") == "input":
                        continue
                    fn = item["filename"].lower()
                    if fn.endswith((".mp4", ".webm", ".mov", ".mkv")):
                        videos.append(item)
                    elif fn.endswith((".wav", ".flac", ".mp3", ".opus", ".ogg", ".m4a")):
                        audios.append(item)
                    elif fn.endswith((".png", ".jpg", ".jpeg", ".webp")) and item.get("type", "output") == "output":
                        images.append(item)
        return {"images": images, "videos": videos, "audios": audios, "seconds": time.time() - t0}


def load_template(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
