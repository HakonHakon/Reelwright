#!/usr/bin/env python3
"""Minimal web interface for KupkaProd Cinema Pipeline."""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from collections.abc import Callable
from urllib.parse import quote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

sys.path.insert(0, BASE_DIR)

import config
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent import load_state, preflight, run, save_state as persist_state
from assembler import concat_scenes
from config import _get, _get_bool, get_settings_snapshot, load_runtime_settings, save_user_settings

app = FastAPI(title="KupkaProd Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@dataclass
class RuntimeOptions:
    lazy: bool
    is_script: bool
    t2v_only: bool
    skip_kf_eval: bool
    subtitle_safe: bool
    character_focus: int
    action_focus: int
    environment_weight: int
    establishing_shot_bias: int
    natural_dialogue: bool
    no_dialogue: bool
    zit_direct_prompt: str
    takes: int
    scene_min: int
    scene_max: int
    kf_width: int
    kf_height: int
    video_width: int
    video_height: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "lazy": self.lazy,
            "is_script": self.is_script,
            "t2v_only": self.t2v_only,
            "skip_kf_eval": self.skip_kf_eval,
            "subtitle_safe": self.subtitle_safe,
            "character_focus": self.character_focus,
            "action_focus": self.action_focus,
            "environment_weight": self.environment_weight,
            "establishing_shot_bias": self.establishing_shot_bias,
            "natural_dialogue": self.natural_dialogue,
            "no_dialogue": self.no_dialogue,
            "zit_direct_prompt": self.zit_direct_prompt,
            "takes": self.takes,
            "scene_min": self.scene_min,
            "scene_max": self.scene_max,
            "kf_width": self.kf_width,
            "kf_height": self.kf_height,
            "video_width": self.video_width,
            "video_height": self.video_height,
        }


DEFAULT_PIPELINE_OPTIONS = {
    "brief": "",
    "is_script": False,
    "lazy": False,
    "t2v_only": False,
    "takes": 3,
    "scene_min": 2,
    "scene_max": 30,
    "skip_kf_eval": True,
    "subtitle_safe": False,
    "final_transition_enabled": False,
    "final_transition_duration": 0.35,
    "character_focus": 68,
    "action_focus": 62,
    "environment_weight": 58,
    "establishing_shot_bias": 32,
    "natural_dialogue": False,
    "no_dialogue": False,
    "zit_direct_prompt": "",
}


def _output_root() -> str:
    return config.get_output_root()


def _ensure_output_root():
    os.makedirs(_output_root(), exist_ok=True)


_ensure_output_root()


def _auto_project_name() -> str:
    """Return the next free date-based project name, e.g. 0515_001."""
    prefix = datetime.now().strftime("%m%d")
    used: set[str] = set()

    output_root = config.get_output_root()
    if output_root and os.path.isdir(output_root):
        for name in os.listdir(output_root):
            if name.startswith(f"{prefix}_"):
                used.add(name)

    used.update(name for name in jobs.all().keys() if name.startswith(f"{prefix}_"))

    for index in range(1, 1000):
        candidate = f"{prefix}_{index:03d}"
        if candidate not in used and not load_state(candidate):
            return candidate
    return f"{prefix}_{int(time.time())}"


class JobRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_project: str | None = None
        self._queue: list[str] = []

    def _cleanup_active_locked(self):
        if not self._active_project:
            return
        active = self._jobs.get(self._active_project, {})
        thread = active.get("thread")
        if thread and thread.is_alive():
            return
        self._active_project = None

    def _refresh_queue_positions_locked(self):
        for index, name in enumerate(self._queue, start=1):
            job = self._jobs.setdefault(name, {})
            job["queued"] = True
            job["queue_position"] = index

    def _begin_locked(self, project_name: str, thread: threading.Thread, kind: str, preserve_logs: bool):
        self._active_project = project_name
        job = self._jobs.setdefault(project_name, {})
        cancel_event = threading.Event()
        logs = job.get("logs", []) if preserve_logs else []
        job.update({
            "thread": thread,
            "cancel_event": cancel_event,
            "running": True,
            "queued": False,
            "queue_position": None,
            "queued_at": None,
            "starter": None,
            "kind": kind,
            "started_at": time.time(),
            "finished_at": None,
            "last_error": None,
            "logs": list(logs),
        })

    def _dispatch_next_locked(self) -> tuple[str, Callable[[], threading.Thread], str] | None:
        self._cleanup_active_locked()
        if self._active_project:
            return None
        while self._queue:
            project_name = self._queue.pop(0)
            job = self._jobs.get(project_name)
            if not job or not job.get("queued"):
                continue
            starter = job.get("starter")
            if not callable(starter):
                job["queued"] = False
                job["queue_position"] = None
                job["queued_at"] = None
                continue
            kind = str(job.get("kind") or "production")
            job["queued"] = False
            job["queue_position"] = None
            job["queued_at"] = None
            self._refresh_queue_positions_locked()
            return project_name, starter, kind
        self._refresh_queue_positions_locked()
        return None

    def snapshot(self, project_name: str) -> dict[str, Any]:
        with self._lock:
            self._cleanup_active_locked()
            job = self._jobs.get(project_name, {}).copy()
            if "logs" in job:
                job["logs"] = list(job["logs"])
            if "cancel_event" in job:
                job["cancel_requested"] = job["cancel_event"].is_set()
                job.pop("cancel_event", None)
            return job

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._cleanup_active_locked()
            result = {}
            for name, job in self._jobs.items():
                result[name] = {
                    "running": job.get("running", False),
                    "queued": job.get("queued", False),
                    "queue_position": job.get("queue_position"),
                    "queued_at": job.get("queued_at"),
                    "kind": job.get("kind"),
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                    "last_error": job.get("last_error"),
                    "cancel_requested": job.get("cancel_event").is_set() if job.get("cancel_event") else False,
                }
            return result

    def queue_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            self._cleanup_active_locked()
            self._refresh_queue_positions_locked()
            items = []
            for name in self._queue:
                job = self._jobs.get(name, {})
                items.append({
                    "project_name": name,
                    "queue_position": job.get("queue_position"),
                    "queued_at": job.get("queued_at"),
                    "kind": job.get("kind"),
                })
            return items

    def can_start(self, project_name: str) -> tuple[bool, str | None]:
        with self._lock:
            self._cleanup_active_locked()
            if self._active_project and self._active_project != project_name:
                active = self._jobs.get(self._active_project, {})
                thread = active.get("thread")
                if thread and thread.is_alive():
                    return False, self._active_project
            if self._queue:
                return False, self._queue[0]
            return True, None

    def begin(self, project_name: str, thread: threading.Thread, kind: str = "task", preserve_logs: bool = False):
        with self._lock:
            self._begin_locked(project_name, thread, kind=kind, preserve_logs=preserve_logs)

    def enqueue(self, project_name: str, starter: Callable[[], threading.Thread], kind: str = "production") -> tuple[str, int | None]:
        should_start_now = False
        queue_position = None
        with self._lock:
            self._cleanup_active_locked()
            job = self._jobs.setdefault(project_name, {})
            thread = job.get("thread")
            if job.get("running") and thread and thread.is_alive():
                return "running", None
            if job.get("queued"):
                job["starter"] = starter
                job["kind"] = kind
                return "queued", job.get("queue_position")

            job["starter"] = starter
            job["kind"] = kind
            job["last_error"] = None

            if not self._active_project:
                self._active_project = project_name
                should_start_now = True
            else:
                if project_name not in self._queue:
                    self._queue.append(project_name)
                job["queued"] = True
                job["queued_at"] = time.time()
                self._refresh_queue_positions_locked()
                queue_position = job.get("queue_position")

        if should_start_now:
            try:
                thread = starter()
            except Exception:
                with self._lock:
                    if self._active_project == project_name:
                        self._active_project = None
                raise
            with self._lock:
                self._begin_locked(project_name, thread, kind=kind, preserve_logs=False)
            thread.start()
            return "started", None
        return "queued", queue_position

    def append_log(self, project_name: str, line: str):
        with self._lock:
            job = self._jobs.setdefault(project_name, {"logs": []})
            logs = job.setdefault("logs", [])
            logs.append(line)
            if len(logs) > 800:
                del logs[: len(logs) - 800]

    def finish(self, project_name: str, error: str | None = None):
        queued_to_start = None
        with self._lock:
            job = self._jobs.setdefault(project_name, {})
            job["running"] = False
            job["finished_at"] = time.time()
            job["last_error"] = error
            if self._active_project == project_name:
                self._active_project = None
            queued_to_start = self._dispatch_next_locked()
        if queued_to_start:
            next_project, starter, kind = queued_to_start
            try:
                thread = starter()
            except Exception as exc:
                self.append_log(next_project, f"ERROR: could not start queued job: {exc}")
                with self._lock:
                    job = self._jobs.setdefault(next_project, {})
                    job["running"] = False
                    job["finished_at"] = time.time()
                    job["last_error"] = str(exc)
                    self._active_project = None
                self.finish(next_project, str(exc))
                return
            with self._lock:
                self._begin_locked(next_project, thread, kind=kind, preserve_logs=True)
            self.append_log(next_project, "Queue slot opened. Starting production now.")
            thread.start()

    def request_cancel(self, project_name: str) -> bool:
        with self._lock:
            job = self._jobs.get(project_name)
            if not job:
                return False
            if job.get("queued"):
                job["queued"] = False
                job["queued_at"] = None
                job["queue_position"] = None
                job["starter"] = None
                job["finished_at"] = time.time()
                if project_name in self._queue:
                    self._queue = [name for name in self._queue if name != project_name]
                self._refresh_queue_positions_locked()
                return True
            cancel_event = job.get("cancel_event")
            if not cancel_event:
                cancel_event = threading.Event()
                job["cancel_event"] = cancel_event
            cancel_event.set()
            return True

    def is_cancel_requested(self, project_name: str) -> bool:
        with self._lock:
            job = self._jobs.get(project_name)
            if not job:
                return False
            cancel_event = job.get("cancel_event")
            return bool(cancel_event and cancel_event.is_set())


jobs = JobRegistry()


class ProjectLogHandler(logging.Handler):
    def __init__(self, project_name: str):
        super().__init__()
        self.project_name = project_name

    def emit(self, record):
        try:
            msg = self.format(record)
            jobs.append_log(self.project_name, msg)
        except Exception:
            self.handleError(record)


def _bool_from_form(form, key: str, default: bool = False) -> bool:
    if key not in form:
        return default
    value = str(form.get(key, "")).strip().lower()
    return value not in ("0", "false", "off", "no", "")


def _int_from_form(form, key: str, default: int) -> int:
    value = form.get(key)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_from_form(form, key: str, default: float) -> float:
    value = form.get(key)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _hosts_from_form(form, key: str, fallback: list[str]) -> list[str]:
    raw = str(form.get(key, "") or "")
    candidates = raw.splitlines() if raw.strip() else list(fallback)
    hosts: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        host = str(item).strip()
        if not host or host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return hosts


def _media_url(path: str | None) -> str | None:
    if not path:
        return None
    abs_path = os.path.abspath(path)
    try:
        rel_path = os.path.relpath(abs_path, _output_root())
    except ValueError:
        return None
    if rel_path.startswith(".."):
        return None
    return f"/media/{quote(rel_path.replace(os.sep, '/'))}"


def _state_file(project_name: str) -> str:
    return config.project_state_path(project_name)


def _save_state(project_name: str, state: dict):
    payload = dict(state)
    payload["project_name"] = project_name
    persist_state(payload)


def _first_generated_candidate(scene: dict) -> str | None:
    for candidate in scene.get("keyframe_candidates", []):
        if candidate.get("status") == "generated" and candidate.get("path"):
            return candidate.get("path")
    return None


def _first_generated_take(scene: dict) -> str | None:
    for take in scene.get("takes", []):
        if take.get("status") == "generated" and take.get("path"):
            return take.get("path")
    return None


def _bulk_select_storyboards(state: dict) -> int:
    changed = 0
    for scene in state.get("scenes", []):
        selected = scene.get("selected_keyframe")
        if selected:
            continue
        candidate_path = _first_generated_candidate(scene)
        if not candidate_path:
            continue
        scene["selected_keyframe"] = candidate_path
        scene["keyframe_approved"] = True
        changed += 1
    return changed


def _bulk_select_takes(state: dict) -> int:
    changed = 0
    for scene in state.get("scenes", []):
        selected = scene.get("selected_take")
        if selected:
            continue
        take_path = _first_generated_take(scene)
        if not take_path:
            continue
        scene["selected_take"] = take_path
        scene["status"] = "approved"
        changed += 1
    return changed


def _list_projects() -> list[dict[str, Any]]:
    project_names: set[str] = set()
    output_root = _output_root()
    if os.path.exists(output_root):
        for name in os.listdir(output_root):
            if os.path.exists(_state_file(name)):
                project_names.add(name)
    project_names.update(jobs.all().keys())

    summaries = []
    for name in sorted(project_names):
        summaries.append(_project_summary(name))
    summaries.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    return summaries


def _derive_phase(state: dict | None, job: dict[str, Any]) -> str:
    if job.get("running") and job.get("cancel_requested"):
        return "Stopping"
    if job.get("running"):
        return "Running"
    if job.get("queued"):
        position = job.get("queue_position")
        if position:
            return f"Queued #{position}"
        return "Queued"
    if job.get("last_error"):
        return "Paused with error"
    if not state:
        return "Not started"
    if state.get("cancelled_at"):
        return "Cancelled"
    if state.get("completed_at"):
        return "Completed"
    if state.get("generation_completed_at"):
        return "Ready for take review"
    if state.get("storyboard_approved"):
        return "Storyboard approved"
    scenes = state.get("scenes", [])
    if any(scene.get("keyframe_candidates") for scene in scenes):
        return "Storyboard review"
    if scenes:
        return "Scenes planned"
    return "Created"


def _project_summary(project_name: str) -> dict[str, Any]:
    state = load_state(project_name)
    job = jobs.snapshot(project_name)
    scenes = state.get("scenes", []) if state else []
    keyframes_done = sum(1 for scene in scenes if scene.get("keyframe_approved"))
    takes_done = sum(1 for scene in scenes if scene.get("selected_take"))

    updated_at = 0.0
    if state:
        for key in ("completed_at", "generation_completed_at", "storyboard_generated_at", "created_at"):
            value = state.get(key)
            if value:
                try:
                    updated_at = max(updated_at, time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")))
                except Exception:
                    pass
    updated_at = max(updated_at, job.get("started_at") or 0, job.get("finished_at") or 0, job.get("queued_at") or 0)

    return {
        "name": project_name,
        "state": state,
        "job": job,
        "phase": _derive_phase(state, job),
        "total_scenes": len(scenes),
        "keyframes_done": keyframes_done,
        "takes_done": takes_done,
        "final_url": _media_url(state.get("final_path")) if state else None,
        "updated_at": updated_at,
    }


def _project_context(project_name: str) -> dict[str, Any]:
    state = load_state(project_name)
    job = jobs.snapshot(project_name)
    if not state and not job:
        raise HTTPException(status_code=404, detail="Project not found")
    if not state:
        state = {
            "project_name": project_name,
            "brief": "",
            "scenes": [],
        }

    scenes = state.get("scenes", [])
    project_runtime = dict(_current_pipeline_defaults())
    project_runtime.update(state.get("runtime_options", {}))
    storyboard_ready = any(scene.get("keyframe_candidates") for scene in scenes)
    take_review_ready = any(scene.get("takes") for scene in scenes)

    storyboard_scenes = []
    for scene in scenes:
        candidates = []
        for candidate in scene.get("keyframe_candidates", []):
            candidate = dict(candidate)
            candidate["url"] = _media_url(candidate.get("path"))
            candidate["selected"] = scene.get("selected_keyframe") == candidate.get("path")
            candidates.append(candidate)
        storyboard_scenes.append({
            "scene": scene,
            "candidates": candidates,
        })

    take_scenes = []
    for scene in scenes:
        takes = []
        for take in scene.get("takes", []):
            if take.get("status") != "generated":
                continue
            take = dict(take)
            take["url"] = _media_url(take.get("path"))
            take["selected"] = scene.get("selected_take") == take.get("path")
            takes.append(take)
        take_scenes.append({
            "scene": scene,
            "takes": takes,
        })

    approved_keyframes = sum(1 for scene in scenes if scene.get("keyframe_approved"))
    selected_takes = sum(1 for scene in scenes if scene.get("selected_take"))

    return {
        "project_name": project_name,
        "state": state,
        "job": job,
        "phase": _derive_phase(state, job),
        "queue_position": job.get("queue_position"),
        "storyboard_ready": storyboard_ready,
        "take_review_ready": take_review_ready,
        "project_runtime": project_runtime,
        "storyboard_scenes": storyboard_scenes,
        "take_scenes": take_scenes,
        "approved_keyframes": approved_keyframes,
        "selected_takes": selected_takes,
        "final_url": _media_url(state.get("final_path")),
        "logs": "\n".join(job.get("logs", [])),
    }


def _sync_runtime_modules():
    import agent as agent_mod
    import assembler as assembler_mod
    import comfyui_client
    import config
    import director as director_mod
    import evaluator as evaluator_mod
    import keyframe_gen as keyframe_mod
    import llm_client

    snapshot = load_runtime_settings()
    llm_client.clear_model_cache()

    agent_mod.COMFYUI_HOST = config.COMFYUI_HOST
    agent_mod.OLLAMA_MODEL = config.OLLAMA_MODEL
    agent_mod.COMFYUI_LAUNCHER = config.COMFYUI_LAUNCHER
    agent_mod.COMFYUI_STARTUP_TIMEOUT = config.COMFYUI_STARTUP_TIMEOUT
    agent_mod.FFMPEG_PATH = config.FFMPEG_PATH
    agent_mod.OUTPUT_DIR = config.OUTPUT_DIR

    comfyui_client.COMFYUI_HOST = config.COMFYUI_HOST
    comfyui_client.COMFYUI_OUTPUT_DIR = config.COMFYUI_OUTPUT_DIR
    comfyui_client.VIDEO_WIDTH = config.VIDEO_WIDTH
    comfyui_client.VIDEO_HEIGHT = config.VIDEO_HEIGHT
    comfyui_client.NEGATIVE_PROMPT = config.effective_negative_prompt()

    director_mod.OLLAMA_MODEL_CREATIVE = config.OLLAMA_MODEL_CREATIVE
    director_mod.SUBTITLE_SAFE_MODE = config.SUBTITLE_SAFE_MODE
    director_mod.NO_DIALOGUE = config.NO_DIALOGUE
    evaluator_mod.OLLAMA_MODEL_FAST = config.OLLAMA_MODEL_FAST
    keyframe_mod.OLLAMA_MODEL_CREATIVE = config.OLLAMA_MODEL_CREATIVE
    keyframe_mod.OLLAMA_MODEL_FAST = config.OLLAMA_MODEL_FAST
    keyframe_mod.COMFYUI_OUTPUT_DIR = config.COMFYUI_OUTPUT_DIR
    keyframe_mod.KF_PROMPT_NODE_ID = config.KF_PROMPT_NODE_ID
    keyframe_mod.KF_PROMPT_INPUT_NAME = config.KF_PROMPT_INPUT_NAME
    keyframe_mod.KF_WIDTH = config.KF_WIDTH
    keyframe_mod.KF_HEIGHT = config.KF_HEIGHT
    keyframe_mod.ZIT_DIRECT_PROMPT = config.ZIT_DIRECT_PROMPT
    assembler_mod.OLLAMA_MODEL_FAST = config.OLLAMA_MODEL_FAST
    assembler_mod.FFMPEG_PATH = config.FFMPEG_PATH
    assembler_mod.FFPROBE_PATH = config.FFPROBE_PATH
    assembler_mod.FINAL_TRANSITION_ENABLED = config.FINAL_TRANSITION_ENABLED
    assembler_mod.FINAL_TRANSITION_DURATION = config.FINAL_TRANSITION_DURATION
    _ensure_output_root()

    return snapshot


def _current_pipeline_defaults() -> dict[str, Any]:
    snapshot = get_settings_snapshot()
    return {
        "brief": snapshot.get("default_brief", DEFAULT_PIPELINE_OPTIONS["brief"]),
        "is_script": snapshot.get("default_is_script", DEFAULT_PIPELINE_OPTIONS["is_script"]),
        "lazy": snapshot.get("lazy_mode", DEFAULT_PIPELINE_OPTIONS["lazy"]),
        "t2v_only": snapshot.get("t2v_only", DEFAULT_PIPELINE_OPTIONS["t2v_only"]),
        "takes": snapshot.get("takes_per_scene", DEFAULT_PIPELINE_OPTIONS["takes"]),
        "scene_min": snapshot.get("scene_min_sec", DEFAULT_PIPELINE_OPTIONS["scene_min"]),
        "scene_max": snapshot.get("scene_max_sec", DEFAULT_PIPELINE_OPTIONS["scene_max"]),
        "skip_kf_eval": snapshot.get("skip_kf_eval", DEFAULT_PIPELINE_OPTIONS["skip_kf_eval"]),
        "subtitle_safe": snapshot.get("subtitle_safe_mode", DEFAULT_PIPELINE_OPTIONS["subtitle_safe"]),
        "final_transition_enabled": snapshot.get("final_transition_enabled", DEFAULT_PIPELINE_OPTIONS["final_transition_enabled"]),
        "final_transition_duration": snapshot.get("final_transition_duration", DEFAULT_PIPELINE_OPTIONS["final_transition_duration"]),
        "character_focus": snapshot.get("character_focus", DEFAULT_PIPELINE_OPTIONS["character_focus"]),
        "action_focus": snapshot.get("action_focus", DEFAULT_PIPELINE_OPTIONS["action_focus"]),
        "environment_weight": snapshot.get("environment_weight", DEFAULT_PIPELINE_OPTIONS["environment_weight"]),
        "establishing_shot_bias": snapshot.get("establishing_shot_bias", DEFAULT_PIPELINE_OPTIONS["establishing_shot_bias"]),
        "natural_dialogue": snapshot.get("natural_dialogue", DEFAULT_PIPELINE_OPTIONS["natural_dialogue"]),
        "no_dialogue": snapshot.get("no_dialogue", DEFAULT_PIPELINE_OPTIONS["no_dialogue"]),
        "zit_direct_prompt": snapshot.get("zit_direct_prompt", DEFAULT_PIPELINE_OPTIONS["zit_direct_prompt"]),
        "kf_width": snapshot["kf_width"],
        "kf_height": snapshot["kf_height"],
        "video_width": snapshot["video_width"],
        "video_height": snapshot["video_height"],
    }


def _apply_runtime_overrides(options: RuntimeOptions):
    import agent as agent_mod
    import comfyui_client
    import config
    import director as director_mod
    import keyframe_gen as keyframe_mod

    director_mod.CHARACTER_FOCUS = options.character_focus
    director_mod.ACTION_FOCUS = options.action_focus
    director_mod.ENVIRONMENT_WEIGHT = options.environment_weight
    director_mod.ESTABLISHING_SHOT_BIAS = options.establishing_shot_bias
    director_mod.NATURAL_DIALOGUE = options.natural_dialogue
    director_mod.NO_DIALOGUE = options.no_dialogue
    config.ZIT_DIRECT_PROMPT = options.zit_direct_prompt
    keyframe_mod.ZIT_DIRECT_PROMPT = options.zit_direct_prompt

    config.KF_WIDTH = options.kf_width
    config.KF_HEIGHT = options.kf_height
    config.VIDEO_WIDTH = options.video_width
    config.VIDEO_HEIGHT = options.video_height
    keyframe_mod.KF_WIDTH = options.kf_width
    keyframe_mod.KF_HEIGHT = options.kf_height
    comfyui_client.VIDEO_WIDTH = options.video_width
    comfyui_client.VIDEO_HEIGHT = options.video_height

    config.TAKES_PER_SCENE = options.takes
    agent_mod.TAKES_PER_SCENE = options.takes
    config.SCENE_MIN_SEC = options.scene_min
    config.SCENE_MAX_SEC = options.scene_max
    director_mod.SCENE_MIN_SEC = options.scene_min
    director_mod.SCENE_MAX_SEC = options.scene_max

    config.USE_KEYFRAMES = not options.t2v_only
    agent_mod.USE_KEYFRAMES = not options.t2v_only
    config.SKIP_KF_EVAL = options.skip_kf_eval
    config.SUBTITLE_SAFE_MODE = options.subtitle_safe
    config.NATURAL_DIALOGUE = options.natural_dialogue
    config.NO_DIALOGUE = options.no_dialogue
    keyframe_mod.SKIP_KF_EVAL = options.skip_kf_eval
    director_mod.SUBTITLE_SAFE_MODE = options.subtitle_safe
    comfyui_client.NEGATIVE_PROMPT = (
        f"{config.VISUAL_NEGATIVE_PROMPT}, {config.NO_DIALOGUE_NEGATIVE_PROMPT}"
        if options.no_dialogue else config.effective_negative_prompt()
    )
    comfyui_client.COMFYUI_HOST = config.COMFYUI_HOST
    comfyui_client.COMFYUI_OUTPUT_DIR = config.COMFYUI_OUTPUT_DIR
    keyframe_mod.COMFYUI_OUTPUT_DIR = config.COMFYUI_OUTPUT_DIR


def _restart_ollama_if_needed(log: logging.Logger):
    import subprocess
    import urllib.request

    from llm_client import is_ollama_provider, provider_label

    if not is_ollama_provider():
        log.info("Skipping Ollama restart because LLM provider is %s.", provider_label())
        return

    log.info("Restarting Ollama...")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/f", "/im", "ollama.exe"], capture_output=True, timeout=10)
            subprocess.run(["taskkill", "/f", "/im", "ollama_llama_server.exe"], capture_output=True, timeout=10)
        else:
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True, timeout=10)
        time.sleep(2)
    except Exception as exc:
        log.warning("Could not stop Ollama cleanly: %s", exc)

    try:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except FileNotFoundError:
        log.warning("ollama not found on PATH, skipping restart")
        return

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as response:
                if response.status == 200:
                    log.info("Ollama restarted and ready.")
                    return
        except Exception:
            time.sleep(1)
    log.warning("Ollama did not respond within 30s after restart")


def _run_project_background(project_name: str, brief: str, options: RuntimeOptions):
    handler = ProjectLogHandler(project_name)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    error_message = None
    try:
        from comfyui_client import ComfyUIClient

        _apply_runtime_overrides(options)
        log = logging.getLogger("agent")
        _restart_ollama_if_needed(log)
        client = ComfyUIClient()
        preflight(client, log)
        run(
            brief,
            project_name,
            log,
            is_script=options.is_script,
            lazy=options.lazy,
            should_cancel=lambda: jobs.is_cancel_requested(project_name),
        )
    except SystemExit:
        error_message = "Preflight failed"
        jobs.append_log(project_name, error_message)
    except Exception as exc:
        error_message = str(exc)
        jobs.append_log(project_name, f"ERROR: {exc}")
    finally:
        root_logger.removeHandler(handler)
        jobs.finish(project_name, error_message)


def _start_project(project_name: str, brief: str, options: RuntimeOptions) -> tuple[str, int | None]:
    state = load_state(project_name) or {
        "project_name": project_name,
        "brief": brief,
        "created_at": datetime.now().isoformat(),
        "total_scenes": 0,
        "scenes": [],
    }
    state["brief"] = brief
    state.setdefault("created_at", datetime.now().isoformat())
    state.setdefault("total_scenes", len(state.get("scenes", [])))
    state.setdefault("scenes", [])
    state.setdefault("is_script", options.is_script)
    state.pop("cancel_requested_at", None)
    state.pop("cancelled_at", None)
    snapshot = get_settings_snapshot()
    state["llm_runtime"] = {
        "provider": snapshot.get("llm_provider"),
        "base_url": snapshot.get("llm_base_url"),
        "enable_thinking": snapshot.get("llm_enable_thinking"),
        "reasoning_breakdown_only": snapshot.get("llm_reasoning_breakdown_only"),
        "reasoning_format": snapshot.get("llm_reasoning_format"),
        "creative_drafting_mode": snapshot.get("llm_creative_drafting_mode"),
        "model_creative": snapshot.get("ollama_model_creative"),
        "model_fast": snapshot.get("ollama_model_fast"),
    }
    state["runtime_options"] = options.as_dict()
    _save_state(project_name, state)

    def starter() -> threading.Thread:
        return threading.Thread(
            target=_run_project_background,
            args=(project_name, brief, options),
            daemon=True,
            name=f"kupka-web-{project_name}",
        )

    status, queue_position = jobs.enqueue(project_name, starter, kind="production")
    if status == "queued":
        jobs.append_log(project_name, f"Queued from web UI. Waiting for {queue_position or '?'} job(s) ahead to finish.")
    return status, queue_position


def _build_runtime_options(form) -> RuntimeOptions:
    return RuntimeOptions(
        lazy=_bool_from_form(form, "lazy", False),
        is_script=_bool_from_form(form, "is_script", False),
        t2v_only=_bool_from_form(form, "t2v_only", False),
        skip_kf_eval=_bool_from_form(form, "skip_kf_eval", True),
        subtitle_safe=_bool_from_form(form, "subtitle_safe", False),
        character_focus=_int_from_form(form, "character_focus", DEFAULT_PIPELINE_OPTIONS["character_focus"]),
        action_focus=_int_from_form(form, "action_focus", DEFAULT_PIPELINE_OPTIONS["action_focus"]),
        environment_weight=_int_from_form(form, "environment_weight", DEFAULT_PIPELINE_OPTIONS["environment_weight"]),
        establishing_shot_bias=_int_from_form(form, "establishing_shot_bias", DEFAULT_PIPELINE_OPTIONS["establishing_shot_bias"]),
        natural_dialogue=_bool_from_form(form, "natural_dialogue", DEFAULT_PIPELINE_OPTIONS["natural_dialogue"]),
        no_dialogue=_bool_from_form(form, "no_dialogue", DEFAULT_PIPELINE_OPTIONS["no_dialogue"]),
        zit_direct_prompt=str(form.get("zit_direct_prompt", DEFAULT_PIPELINE_OPTIONS["zit_direct_prompt"]) or "").strip(),
        takes=_int_from_form(form, "takes", 3),
        scene_min=_int_from_form(form, "scene_min", 2),
        scene_max=_int_from_form(form, "scene_max", 30),
        kf_width=_int_from_form(form, "kf_width", 2048),
        kf_height=_int_from_form(form, "kf_height", 1024),
        video_width=_int_from_form(form, "video_width", 1024),
        video_height=_int_from_form(form, "video_height", 432),
    )


def _save_settings_from_form(form):
    existing_hosts = get_settings_snapshot().get("comfyui_hosts", [])
    comfyui_hosts = _hosts_from_form(form, "comfyui_hosts_text", existing_hosts)
    active_comfyui_host = str(form.get("comfyui_host", "") or "").strip()
    if not active_comfyui_host:
        active_comfyui_host = comfyui_hosts[0] if comfyui_hosts else _get("comfyui_host")
    if active_comfyui_host and active_comfyui_host not in comfyui_hosts:
        comfyui_hosts.insert(0, active_comfyui_host)

    settings = {
        "comfyui_root": form.get("comfyui_root", _get("comfyui_root")),
        "comfyui_output_dir": form.get("comfyui_output_dir", get_settings_snapshot().get("comfyui_output_dir", "")),
        "project_output_root": form.get("project_output_root", _get("project_output_root")),
        "comfyui_host": active_comfyui_host,
        "comfyui_hosts": comfyui_hosts,
        "comfyui_launcher": form.get("comfyui_launcher", _get("comfyui_launcher")),
        "default_brief": str(form.get("brief", _get("default_brief"))),
        "default_is_script": _bool_from_form(form, "is_script", _get_bool("default_is_script")),
        "llm_provider": form.get("llm_provider", _get("llm_provider")),
        "llm_base_url": form.get("llm_base_url", _get("llm_base_url")),
        "llm_enable_thinking": _bool_from_form(form, "llm_enable_thinking", False),
        "llm_reasoning_breakdown_only": _bool_from_form(form, "llm_reasoning_breakdown_only", False),
        "llm_reasoning_format": form.get("llm_reasoning_format", _get("llm_reasoning_format")),
        "llm_creative_drafting_mode": _bool_from_form(form, "llm_creative_drafting_mode", False),
        "ollama_host": form.get("llm_base_url", _get("llm_base_url")),
        "ollama_model_creative": form.get("llm_model_creative", _get("ollama_model_creative")),
        "ollama_model_fast": form.get("llm_model_fast", _get("ollama_model_fast")),
        "lazy_mode": _bool_from_form(form, "lazy", _get_bool("lazy_mode")),
        "t2v_only": _bool_from_form(form, "t2v_only", _get_bool("t2v_only")),
        "takes_per_scene": _int_from_form(form, "takes", int(_get("takes_per_scene"))),
        "scene_min_sec": _int_from_form(form, "scene_min", int(_get("scene_min_sec"))),
        "scene_max_sec": _int_from_form(form, "scene_max", int(_get("scene_max_sec"))),
        "skip_kf_eval": _bool_from_form(form, "skip_kf_eval", _get_bool("skip_kf_eval")),
        "subtitle_safe_mode": _bool_from_form(form, "subtitle_safe", _get_bool("subtitle_safe_mode")),
        "final_transition_enabled": _bool_from_form(form, "final_transition_enabled", _get_bool("final_transition_enabled")),
        "final_transition_duration": max(0.0, min(2.0, _float_from_form(form, "final_transition_duration", float(_get("final_transition_duration"))))),
        "character_focus": _int_from_form(form, "character_focus", int(_get("character_focus"))),
        "action_focus": _int_from_form(form, "action_focus", int(_get("action_focus"))),
        "environment_weight": _int_from_form(form, "environment_weight", int(_get("environment_weight"))),
        "establishing_shot_bias": _int_from_form(form, "establishing_shot_bias", int(_get("establishing_shot_bias"))),
        "natural_dialogue": _bool_from_form(form, "natural_dialogue", _get_bool("natural_dialogue")),
        "no_dialogue": _bool_from_form(form, "no_dialogue", False),
        "zit_direct_prompt": str(form.get("zit_direct_prompt", _get("zit_direct_prompt")) or "").strip(),
        "kf_prompt_node_id": str(form.get("kf_prompt_node_id", _get("kf_prompt_node_id")) or "").strip(),
        "kf_prompt_input_name": str(form.get("kf_prompt_input_name", _get("kf_prompt_input_name")) or "text").strip() or "text",
        "kf_width": _int_from_form(form, "kf_width", int(_get("kf_width"))),
        "kf_height": _int_from_form(form, "kf_height", int(_get("kf_height"))),
        "video_width": _int_from_form(form, "video_width", int(_get("video_width"))),
        "video_height": _int_from_form(form, "video_height", int(_get("video_height"))),
    }
    save_user_settings(settings)
    return _sync_runtime_modules()


def _base_template_context(request: Request) -> dict[str, Any]:
    settings = get_settings_snapshot()
    queue_items = jobs.queue_snapshot()
    return {
        "request": request,
        "settings": {
            "comfyui_root": settings["comfyui_root"],
            "comfyui_output_dir": settings["comfyui_output_dir"],
            "comfyui_host": settings["comfyui_host"],
            "comfyui_hosts": settings["comfyui_hosts"],
            "comfyui_hosts_text": settings["comfyui_hosts_text"],
            "project_output_root": settings["project_output_root"],
            "comfyui_launcher": settings["comfyui_launcher"],
            "llm_provider": settings["llm_provider"],
            "llm_base_url": settings["llm_base_url"],
            "llm_enable_thinking": settings["llm_enable_thinking"],
            "llm_reasoning_breakdown_only": settings["llm_reasoning_breakdown_only"],
            "llm_reasoning_format": settings["llm_reasoning_format"],
            "llm_creative_drafting_mode": settings["llm_creative_drafting_mode"],
            "zit_direct_prompt": settings["zit_direct_prompt"],
            "kf_prompt_node_id": settings["kf_prompt_node_id"],
            "kf_prompt_input_name": settings["kf_prompt_input_name"],
            "llm_model_creative": settings["ollama_model_creative"],
            "llm_model_fast": settings["ollama_model_fast"],
        },
        "runtime_defaults": _current_pipeline_defaults(),
        "queue_items": queue_items,
        "queue_count": len(queue_items),
    }


@app.get("/media/{media_path:path}")
def media_file(media_path: str):
    base = _output_root()
    abs_path = os.path.abspath(os.path.join(base, media_path))
    try:
        if os.path.commonpath([base, abs_path]) != base:
            raise HTTPException(status_code=404, detail="Media not found")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(abs_path)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str | None = None, error: str | None = None):
    context = _base_template_context(request)
    context.update({
        "projects": _list_projects(),
        "message": message,
        "error": error,
    })
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.post("/settings/save")
async def save_settings(request: Request):
    form = await request.form()
    _save_settings_from_form(form)
    return RedirectResponse(url="/?message=Settings+saved", status_code=303)


@app.post("/projects/start")
async def start_project(request: Request):
    form = await request.form()
    _save_settings_from_form(form)

    brief = str(form.get("brief", "")).strip()
    project_name = str(form.get("project_name", "")).strip()
    project_name = project_name or (_auto_project_name() if brief else "")

    if not project_name:
        return RedirectResponse(url="/?error=Please+provide+a+project+name+or+brief.", status_code=303)

    options = _build_runtime_options(form)

    if not brief:
        existing_state = load_state(project_name)
        if not existing_state:
            return RedirectResponse(url=f"/?error=Project+{quote(project_name)}+has+no+saved+state+to+resume.", status_code=303)
        brief = existing_state["brief"]

    try:
        status, queue_position = _start_project(project_name, brief, options)
    except RuntimeError as exc:
        return RedirectResponse(url=f"/?error={quote(str(exc))}", status_code=303)

    if status == "queued":
        message = f"Production queued at position {queue_position or '?'}"
    elif status == "running":
        message = "Production is already running"
    else:
        message = "Production started"

    return RedirectResponse(url=f"/projects/{quote(project_name)}?message={quote(message)}", status_code=303)


@app.get("/projects/{project_name}", response_class=HTMLResponse)
def project_detail(request: Request, project_name: str, message: str | None = None):
    context = _base_template_context(request)
    context.update(_project_context(project_name))
    context["message"] = message
    return templates.TemplateResponse(request=request, name="project.html", context=context)


@app.get("/projects/{project_name}/api")
def project_api(project_name: str):
    context = _project_context(project_name)
    state = context["state"]
    scenes = state.get("scenes", [])
    approved_keyframes = sum(1 for scene in scenes if scene.get("keyframe_approved"))
    selected_takes = sum(1 for scene in scenes if scene.get("selected_take"))
    return JSONResponse({
        "phase": context["phase"],
        "running": context["job"].get("running", False),
        "queued": context["job"].get("queued", False),
        "queue_position": context["job"].get("queue_position"),
        "cancel_requested": context["job"].get("cancel_requested", False),
        "error": context["job"].get("last_error"),
        "logs": context["logs"],
        "storyboard_approved": bool(state.get("storyboard_approved")),
        "generation_completed": bool(state.get("generation_completed_at")),
        "completed": bool(state.get("completed_at")),
        "total_scenes": len(scenes),
        "approved_keyframes": approved_keyframes,
        "selected_takes": selected_takes,
        "final_url": context["final_url"],
    })


def _generate_redo_takes(project_name: str, scene_number: int, count: int, clear_old: bool):
    from comfyui_client import (
        ComfyUIClient, build_i2v_workflow, build_workflow, calc_frames,
        load_i2v_template, load_workflow_template,
    )
    from config import COMFYUI_LAUNCHER, COMFYUI_STARTUP_TIMEOUT, LTX_FPS

    handler = ProjectLogHandler(project_name)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    error_message = None
    client = None
    try:
        _sync_runtime_modules()
        log = logging.getLogger("web.redo")
        state = load_state(project_name)
        if not state:
            raise RuntimeError("Project state not found")

        scene = next((item for item in state.get("scenes", []) if int(item.get("scene_number", 0)) == int(scene_number)), None)
        if scene is None:
            raise RuntimeError(f"Scene {scene_number} not found")

        if clear_old:
            scene["takes"] = []
        scene.pop("selected_take", None)
        scene.pop("status", None)
        scene["takes_done"] = False
        scene["redo_takes"] = count
        _save_state(project_name, state)

        jobs.append_log(project_name, f"Generating {count} new take(s) for scene {scene_number}...")
        client = ComfyUIClient()

        if not client.check_alive():
            import subprocess as _sp

            jobs.append_log(project_name, "ComfyUI not running. Launching...")
            kwargs = {"cwd": os.path.dirname(COMFYUI_LAUNCHER)}
            if os.name == "nt":
                kwargs["creationflags"] = _sp.CREATE_NEW_PROCESS_GROUP
            _sp.Popen(COMFYUI_LAUNCHER, **kwargs)
            deadline = time.time() + COMFYUI_STARTUP_TIMEOUT
            while time.time() < deadline:
                if client.check_alive():
                    break
                time.sleep(3)
            else:
                raise RuntimeError("ComfyUI did not start in time")

        client.connect()
        template = load_workflow_template()
        i2v_template = load_i2v_template()
        project_dir = config.project_dir(project_name)
        scenes_dir = os.path.join(project_dir, "scenes")
        os.makedirs(scenes_dir, exist_ok=True)

        frames = calc_frames(scene["duration_seconds"], LTX_FPS)
        keyframe_path = scene.get("selected_keyframe")
        use_i2v = i2v_template and keyframe_path and os.path.exists(keyframe_path)
        uploaded_kf_name = None
        if use_i2v:
            try:
                uploaded_kf_name = client.upload_image(keyframe_path)
                log.info("Using I2V mode with keyframe %s", os.path.basename(keyframe_path))
            except Exception as exc:
                log.warning("Failed to upload keyframe: %s — falling back to T2V", exc)
                use_i2v = False

        existing_count = len(scene.get("takes", []))
        new_takes = []
        for index in range(1, count + 1):
            if jobs.is_cancel_requested(project_name):
                jobs.append_log(project_name, f"Cancellation requested. Stopping take regeneration for scene {scene_number} after the current completed step.")
                state["cancelled_at"] = datetime.now().isoformat()
                _save_state(project_name, state)
                break
            take_num = existing_count + index
            seed = random.randint(0, 2**32 - 1)
            jobs.append_log(project_name, f"Scene {scene_number}: generating take {index}/{count} ({'i2v' if use_i2v else 't2v'})")

            if use_i2v:
                workflow = build_i2v_workflow(i2v_template, scene["ltx_prompt"], frames, seed, uploaded_kf_name)
            else:
                workflow = build_workflow(template, scene["ltx_prompt"], frames, seed)

            try:
                prompt_id = client.queue_prompt(workflow)
                history = client.wait_for_completion(prompt_id, timeout=900)
                raw_output = client.get_output_path(history)
            except Exception as exc:
                log.error("Redo take %d failed: %s", take_num, exc)
                new_takes.append({"take": take_num, "status": "failed", "error": str(exc)})
                continue

            take_filename = f"scene_{scene_number:03d}_take_{take_num}.mp4"
            take_path = os.path.join(scenes_dir, take_filename)
            shutil.copy2(raw_output, take_path)
            new_takes.append({
                "take": take_num,
                "status": "generated",
                "path": take_path,
                "seed": seed,
            })

        scene.setdefault("takes", []).extend(new_takes)
        if not jobs.is_cancel_requested(project_name):
            scene["takes_done"] = True
        scene.pop("redo_takes", None)
        _save_state(project_name, state)
        generated = sum(1 for take in new_takes if take.get("status") == "generated")
        jobs.append_log(project_name, f"Scene {scene_number}: done, {generated} new take(s) generated.")
    except Exception as exc:
        error_message = str(exc)
        jobs.append_log(project_name, f"ERROR: {exc}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass
        root_logger.removeHandler(handler)
        jobs.finish(project_name, error_message)


@app.post("/projects/{project_name}/storyboard/select")
async def storyboard_select(project_name: str, request: Request):
    form = await request.form()
    selected_path = str(form.get("candidate_path", ""))
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")

    for scene in state.get("scenes", []):
        if str(scene.get("scene_number")) == str(form.get("scene_number")):
            scene["selected_keyframe"] = selected_path
            scene["keyframe_approved"] = True
            break
    _save_state(project_name, state)
    return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Keyframe+selected", status_code=303)


@app.post("/projects/{project_name}/storyboard/reject")
async def storyboard_reject(project_name: str, request: Request):
    form = await request.form()
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")

    scene_number = str(form.get("scene_number"))
    notes = str(form.get("rejection_notes", "")).strip()
    for scene in state.get("scenes", []):
        if str(scene.get("scene_number")) == scene_number:
            scene["rejection_notes"] = notes or "Rejected from web UI"
            scene["keyframe_approved"] = False
            scene.pop("selected_keyframe", None)
            scene.pop("keyframe_candidates", None)
            break
    _save_state(project_name, state)
    return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Scene+marked+for+keyframe+regen", status_code=303)


@app.post("/projects/{project_name}/storyboard/proceed")
def storyboard_proceed(project_name: str):
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")
    state["storyboard_approved"] = True
    _save_state(project_name, state)
    return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Storyboard+approved.+Resume+production+to+generate+takes.", status_code=303)


@app.post("/projects/{project_name}/storyboard/select-all")
def storyboard_select_all(project_name: str):
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")
    changed = _bulk_select_storyboards(state)
    _save_state(project_name, state)
    return RedirectResponse(
        url=f"/projects/{quote(project_name)}?message={quote(f'Selected first available keyframe for {changed} scene(s).')}",
        status_code=303,
    )


@app.post("/projects/{project_name}/takes/select")
async def take_select(project_name: str, request: Request):
    form = await request.form()
    take_path = str(form.get("take_path", ""))
    scene_number = str(form.get("scene_number"))
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")

    for scene in state.get("scenes", []):
        if str(scene.get("scene_number")) == scene_number:
            scene["selected_take"] = take_path
            scene["status"] = "approved"
            break
    _save_state(project_name, state)
    return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Take+selected", status_code=303)


@app.post("/projects/{project_name}/takes/select-all")
def take_select_all(project_name: str):
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")
    changed = _bulk_select_takes(state)
    _save_state(project_name, state)
    return RedirectResponse(
        url=f"/projects/{quote(project_name)}?message={quote(f'Selected first available take for {changed} scene(s).')}",
        status_code=303,
    )


@app.post("/projects/{project_name}/cancel")
def cancel_project(project_name: str):
    job = jobs.snapshot(project_name)
    if not job.get("running") and not job.get("queued"):
        return RedirectResponse(
            url=f"/projects/{quote(project_name)}?message=No+active+job+to+cancel.",
            status_code=303,
        )

    jobs.request_cancel(project_name)
    state = load_state(project_name)
    if state and job.get("running"):
        state["cancel_requested_at"] = datetime.now().isoformat()
        _save_state(project_name, state)
    if job.get("queued"):
        jobs.append_log(project_name, "Removed from queue from web UI before production started.")
        message = "Queued production removed"
    else:
        jobs.append_log(project_name, "Cancellation requested from web UI. The pipeline will stop after the current step completes.")
        message = "Cancellation requested. The pipeline will stop after the current step."
    return RedirectResponse(
        url=f"/projects/{quote(project_name)}?message={quote(message)}",
        status_code=303,
    )


@app.post("/projects/{project_name}/takes/regenerate")
async def regenerate_takes(project_name: str, request: Request):
    form = await request.form()
    scene_number = _int_from_form(form, "scene_number", 0)
    count = _int_from_form(form, "redo_count", 3)
    clear_old = _bool_from_form(form, "clear_old", True)

    if scene_number <= 0:
        return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Invalid+scene+for+take+regen", status_code=303)

    allowed, active_project = jobs.can_start(project_name)
    if not allowed:
        return RedirectResponse(
            url=f"/projects/{quote(project_name)}?message=Project+{quote(active_project)}+is+already+running",
            status_code=303,
        )

    worker = threading.Thread(
        target=_generate_redo_takes,
        args=(project_name, scene_number, count, clear_old),
        daemon=True,
        name=f"kupka-redo-{project_name}-{scene_number}",
    )
    jobs.begin(project_name, worker)
    worker.start()
    return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Take+regeneration+started", status_code=303)


@app.post("/projects/{project_name}/assemble")
def assemble_project(project_name: str):
    state = load_state(project_name)
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")

    paths = []
    for scene in state.get("scenes", []):
        path = scene.get("selected_take")
        if not path or not os.path.exists(path):
            return RedirectResponse(
                url=f"/projects/{quote(project_name)}?message=Every+scene+needs+a+selected+take+before+assembly.",
                status_code=303,
            )
        paths.append(path)

    final_dir = config.project_dir(project_name)
    final_path = os.path.join(final_dir, "final.mp4")
    concat_scenes(paths, final_path)
    state["final_path"] = final_path
    state["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_state(project_name, state)
    return RedirectResponse(url=f"/projects/{quote(project_name)}?message=Final+film+assembled", status_code=303)
