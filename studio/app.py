"""Studio web app: JSON API + a single-page UI (static/index.html)."""

import json
import math
import os
import re
import shutil
import time

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import comfy as comfy_mod
from . import jobs, llm, media, planner, prompts, store, providers, timeline
from . import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Reelwright")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

EDITABLE_SHOT_FIELDS = {
    "scene_id", "location_id", "time_of_day", "framing", "camera", "characters", "action", "dialogue",
    "sound", "action_seconds", "duration_s", "duration_locked", "start_mode", "frame_source", "engine",
    "video_model", "image_prompt_extra", "motion_prompt_extra", "image_prompt_override", "motion_prompt_override", "notes",
}


def _s():
    return settings_mod.load()


def _404(what):
    raise HTTPException(404, f"{what} not found")


def _ep(sid, eid):
    e = store.get_episode(sid, eid)
    if not e:
        _404("episode")
    return e


async def _save_upload(f: UploadFile, dest_dir: str, name: str | None = None) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name or f.filename or "upload")
    dest = os.path.join(dest_dir, base)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        dest = os.path.join(dest_dir, f"{stem}_{int(time.time() * 1000) % 100000}{ext}")
    with open(dest, "wb") as out:
        shutil.copyfileobj(f.file, out)
    return dest


# ── pages / media ───────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/media/{path:path}")
def media_file(path: str):
    try:
        p = store.abs_media(path)
    except ValueError:
        _404("file")
    if not os.path.isfile(p):
        _404("file")
    return FileResponse(p)


# ── status / settings / jobs ────────────────────────────────────────────

def _required_model_options(s):
    """Check configured render families, including existing project/shot overrides."""
    sources, engines = set(), set()

    def include_style(b):
        style = b["style"]
        sources.add(jobs._resolve_source({"frame_source": style["default_frame_source"], "characters": []}, b))
        engines.add(style["default_engine"])

    include_style({"style": {**store.DEFAULT_STYLE, **s.get("style_defaults", {})}, "characters": {}})
    for series in store.list_series():
        sid = series["id"]
        b = store.bible(sid)
        include_style(b)
        for character in b["characters"].values():
            if character.get("frame_source"):
                sources.add(jobs._resolve_source({"characters": [{"id": character["id"]}]}, b))
        for episode in store.list_episodes(sid):
            for shot in (store.get_episode(sid, episode["id"]) or {}).get("shots", []):
                sources.add(jobs._resolve_source(shot, b))
                engines.add(shot.get("engine") or b["style"]["default_engine"])

    needed = {}

    def require(node, field, *names):
        needed.setdefault((node, field), set()).update(names)

    if "flux_lora" in sources:
        require("UNETLoader", "unet_name", s["flux"]["unet"])
        require("VAELoader", "vae_name", s["flux"]["vae"])
    if "zimage" in sources:
        require("UNETLoader", "unet_name", s["zimage"]["unet"])
        require("CLIPLoader", "clip_name", s["zimage"]["clip"])
        require("VAELoader", "vae_name", s["zimage"]["vae"])
    if "qwen_refs" in sources or s["face_correction"]["model"] == "qwen_identity":
        require("UNETLoader", "unet_name", s["qwen_edit"]["unet"])
        require("CLIPLoader", "clip_name", s["qwen_edit"]["clip"])
        require("VAELoader", "vae_name", s["qwen_edit"]["vae"])
        require("LoraLoaderModelOnly", "lora_name", s["qwen_edit"]["lora"])
    if "wan" in engines:
        require("UNETLoader", "unet_name", s["wan"]["high"], s["wan"]["low"])
        require("CLIPLoader", "clip_name", s["wan"]["text_encoder"])
        require("VAELoader", "vae_name", s["wan"]["vae"])
        require("LoraLoaderModelOnly", "lora_name", s["wan"]["high_lightning"], s["wan"]["low_lightning"])
    if "minimax_local" in engines:
        h = s["minimax_local"]
        require("UNETLoader", "unet_name", h["unet"])
        require("CLIPLoader", "clip_name", h["clip"])
        require("VAELoader", "vae_name", h["video_vae"], h["audio_vae"])
        if h.get("lora"):
            require("LoraLoaderModelOnly", "lora_name", h["lora"])
    if s["face_correction"]["model"] == "minimax_identity":
        face, h = s["face_correction"], s["minimax_local"]
        require("UNETLoader", "unet_name", face["identity_unet"])
        require("CLIPLoader", "clip_name", h["clip"])
        require("VAELoader", "vae_name", h["video_vae"])
        require("LoraLoaderModelOnly", "lora_name", face["identity_lora"], face["identity_turbo_lora"])
    return needed


@app.get("/api/status")
def status():
    s = _s()
    c = comfy_mod.Comfy(s["comfy_host"])
    alive = c.alive()
    out = {"comfy": alive, "comfy_host": s["comfy_host"], "ollama_models": llm.installed(s["ollama_host"]),
           "planner_model": s["planner_model"], "vision_model": s["vision_model"], "loras": [], "missing": []}
    if alive:
        try:
            out["loras"] = c.options("LoraLoaderModelOnly", "lora_name")
            needed = _required_model_options(s)
            for (node, inp), names in needed.items():
                opts = set(c.options(node, inp))
                out["missing"] += sorted(n for n in names if n not in opts and n not in out["missing"])
        except Exception as exc:
            out["error"] = str(exc)
    for m in {s["planner_model"], s["vision_model"]}:
        names = out["ollama_models"]
        if m not in names and f"{m}:latest" not in names:
            out["missing"].append(f"ollama:{m}")
    return out


@app.get("/api/settings")
def get_settings():
    return {"effective": _s(), "user": settings_mod.raw_user()}


@app.put("/api/settings")
def put_settings(user: dict = Body(...)):
    return settings_mod.save(user)


@app.get("/api/jobs")
def list_jobs():
    return jobs.QUEUE.list()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return {"ok": jobs.QUEUE.cancel(job_id)}


def _submit(kind, title, fn, **params):
    return jobs.QUEUE.submit(kind, title, fn, **params).public()


# ── series & bible ──────────────────────────────────────────────────────

@app.get("/api/series")
def series_list():
    return store.list_series()


@app.post("/api/series")
def series_create(body: dict = Body(...)):
    return store.create_series(body.get("name", "").strip() or "My series")


@app.get("/api/s/{sid}")
def series_get(sid: str):
    s = store.get_series(sid) or _404("series")
    return {"series": s, "bible": store.bible(sid), "episodes": store.list_episodes(sid)}


@app.put("/api/s/{sid}/style")
def style_put(sid: str, body: dict = Body(...)):
    try:
        providers.validate_model(body.get("default_engine") or store.get_style(sid)["default_engine"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store.save_style(sid, body)


@app.put("/api/s/{sid}/props")
def props_put(sid: str, body: list = Body(...)):
    store.save_props(sid, body)
    return body


@app.post("/api/s/{sid}/characters")
def char_create(sid: str, body: dict = Body(...)):
    c = store.new_character(body.get("name", "New character"))
    c["id"] = ""
    return store.save_character(sid, c, bump=False)


@app.put("/api/s/{sid}/characters/{cid}")
def char_put(sid: str, cid: str, body: dict = Body(...)):
    cur = store.get_character(sid, cid) or _404("character")
    keep = {k: cur.get(k) for k in ("id", "refs", "sheet", "version")}
    merged = {**cur, **body, **keep, "draft": False}
    return store.save_character(sid, merged, bump=True)


@app.delete("/api/s/{sid}/characters/{cid}")
def char_delete(sid: str, cid: str):
    _ensure_idle(sid)
    store.get_character(sid, cid) or _404("character")
    store.archive_dir(store.char_dir(sid, cid))
    return {"ok": True}


@app.post("/api/s/{sid}/characters/{cid}/upload")
async def char_upload(sid: str, cid: str, kind: str = Form("ref"), file: UploadFile = File(...)):
    c = store.get_character(sid, cid) or _404("character")
    if kind == "voice":
        p = await _save_upload(file, store.char_dir(sid, cid), "voice" + os.path.splitext(file.filename or ".wav")[1])
        c["voice"]["file"] = store.rel(p)
        return store.save_character(sid, c, bump=True)
    p = await _save_upload(file, os.path.join(store.char_dir(sid, cid), "refs"))
    c["refs"] = (c.get("refs") or []) + [store.rel(p)]
    return store.save_character(sid, c, bump=False)


@app.post("/api/s/{sid}/characters/{cid}/refs")
def char_refs(sid: str, cid: str, body: dict = Body(...)):
    """{add: file} copies a sheet image into refs; {remove: file} drops it."""
    c = store.get_character(sid, cid) or _404("character")
    refs = c.get("refs") or []
    if body.get("add") and body["add"] not in refs:
        refs.append(body["add"])
    if body.get("remove"):
        refs = [r for r in refs if r != body["remove"]]
    c["refs"] = refs
    return store.save_character(sid, c, bump=False)


@app.post("/api/s/{sid}/characters/{cid}/sheet")
def char_sheet(sid: str, cid: str, body: dict = Body(default={})):
    src = body.get("source") or "flux_lora"
    return _submit("sheet", f"Character sheet: {cid}", jobs.job_character_sheet, sid=sid, cid=cid, source=src)


@app.post("/api/s/{sid}/locations")
def loc_create(sid: str, body: dict = Body(...)):
    loc = store.new_location(body.get("name", "New location"))
    loc["id"] = ""
    return store.save_location(sid, loc, bump=False)


@app.put("/api/s/{sid}/locations/{lid}")
def loc_put(sid: str, lid: str, body: dict = Body(...)):
    cur = store.get_location(sid, lid) or _404("location")
    keep = {k: cur.get(k) for k in ("id", "plates", "candidates", "version")}
    return store.save_location(sid, {**cur, **body, **keep, "draft": False}, bump=True)


@app.delete("/api/s/{sid}/locations/{lid}")
def loc_delete(sid: str, lid: str):
    _ensure_idle(sid)
    store.get_location(sid, lid) or _404("location")
    store.archive_dir(store.loc_dir(sid, lid))
    return {"ok": True}


@app.post("/api/s/{sid}/locations/{lid}/plates")
def loc_plates(sid: str, lid: str, body: dict = Body(...)):
    return _submit("plates", f"Plates: {lid}/{body.get('variant')}", jobs.job_location_plate, sid=sid, lid=lid,
                   variant=body.get("variant") or "day", source=body.get("source") or "zimage")


@app.post("/api/s/{sid}/locations/{lid}/upload")
async def loc_upload(sid: str, lid: str, variant: str = Form("day"), file: UploadFile = File(...)):
    loc = store.get_location(sid, lid) or _404("location")
    p = await _save_upload(file, os.path.join(store.loc_dir(sid, lid), "candidates"))
    loc["candidates"] = (loc.get("candidates") or []) + [{"file": store.rel(p), "variant": variant, "source": "upload"}]
    return store.save_location(sid, loc, bump=False)


@app.post("/api/s/{sid}/locations/{lid}/approve")
def loc_approve(sid: str, lid: str, body: dict = Body(...)):
    loc = store.get_location(sid, lid) or _404("location")
    loc.setdefault("plates", {})[body.get("variant") or "day"] = body["file"]
    return store.save_location(sid, loc, bump=False)


# ── episodes ────────────────────────────────────────────────────────────

@app.post("/api/s/{sid}/episodes")
def ep_create(sid: str, body: dict = Body(...)):
    return store.create_episode(sid, body.get("title", "").strip() or "Episode 1")


def _decorate(sid: str, e: dict) -> dict:
    s = settings_mod.for_episode(_s(), e)
    b = store.bible(sid)
    shots = sorted(e["shots"], key=lambda x: x.get("order", 0))
    for sh in shots:
        engine = sh.get("engine") or b["style"].get("default_engine") or "ltx"
        sh["_engine"] = engine
        sh["_source"] = prompts.frame_source(sh, b) if sh.get("start_mode") == "new" else sh.get("start_mode")
        sh["_image_prompt"] = prompts.image_prompt(sh, b)
        sh["_motion_prompt"] = prompts.motion_prompt(sh, b, engine)
        sig = store.bible_signature(b, sh)
        sh["_stale"] = bool(sh["frame"].get("candidates")) and sh["frame"].get("bible_sig") not in ("", sig)
        sh["_loras"] = prompts.image_loras(sh, b, float(s["flux"]["second_lora_scale"]))
    e["shots"] = shots
    e["_runtime"] = planner.runtime_seconds(shots)
    e["_estimate_s"] = round(planner.estimate_render_seconds(shots, e["stats"].get("spf", {}), int(s["takes_per_shot"])))
    e["_settings"] = {"takes_per_shot": s["takes_per_shot"], "candidates_per_shot": s["candidates_per_shot"],
                      "video": s["video"], "image": s["image"], "face_model": s["face_correction"]["model"], "ltx_max": s["ltx"]["max_seconds"], "wan_max": s["wan"]["max_seconds"]}
    return e


@app.get("/api/s/{sid}/e/{eid}")
def ep_get(sid: str, eid: str):
    return _decorate(sid, _ep(sid, eid))


@app.put("/api/s/{sid}/e/{eid}/story")
def ep_story(sid: str, eid: str, body: dict = Body(...)):
    def apply(e):
        if "story" in body:
            e["story"] = body["story"]
        if body.get("title"):
            e["title"] = body["title"]
    return store.update_episode(sid, eid, apply)


@app.post("/api/s/{sid}/e/{eid}/draft_bible")
def ep_draft_bible(sid: str, eid: str):
    e = _ep(sid, eid)
    return _submit("draft_bible", "Draft bible from story", jobs.job_draft_bible, sid=sid, story=e.get("story", ""))


@app.post("/api/s/{sid}/e/{eid}/plan")
def ep_plan(sid: str, eid: str):
    return _submit("plan", f"Plan {eid}", jobs.job_plan, sid=sid, eid=eid)


@app.post("/api/s/{sid}/e/{eid}/replan/{scene_id}")
def ep_replan(sid: str, eid: str, scene_id: str):
    return _submit("replan", f"Re-plan {scene_id}", jobs.job_replan_scene, sid=sid, eid=eid, scene_id=scene_id)


@app.put("/api/s/{sid}/e/{eid}/shots")
def ep_shots(sid: str, eid: str, body: list = Body(...)):
    s = _s()
    b = store.bible(sid)

    for inc in body:
        try:
            providers.validate_model(inc.get("engine") or b["style"].get("default_engine") or "ltx", inc.get("video_model") or "")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    def apply(e):
        old = {sh["id"]: sh for sh in e["shots"]}
        new_list = []
        for inc in body:
            base = old.get(inc.get("id") or "") or planner.new_shot()
            if inc.get("_copy_of") and inc["_copy_of"] in old and not inc.get("id"):
                base = planner.new_shot(**{k: json.loads(json.dumps(v)) for k, v in old[inc["_copy_of"]].items()
                                           if k in EDITABLE_SHOT_FIELDS})
            for k in EDITABLE_SHOT_FIELDS:
                if k in inc:
                    base[k] = inc[k]
            if not inc.get("duration_locked"):
                eng = base.get("engine") or b["style"].get("default_engine") or "ltx"
                cap = s[eng]["max_seconds"]
                base["duration_s"] = planner.compute_duration(base, b, cap)
            new_list.append(base)
        if {sh["id"] for sh in e["shots"]} - {sh.get("id") for sh in new_list}:
            _ensure_idle(sid, eid)
            e["gates"] = dict.fromkeys(e["gates"], False)
            e["outputs"] = {}
        e["shots"] = new_list
        store.renumber_shots(e)
    return _decorate(sid, store.update_episode(sid, eid, apply))


@app.post("/api/s/{sid}/e/{eid}/continuity")
def ep_continuity(sid: str, eid: str):
    def apply(e):
        planner.assign_continuity(e["shots"])
    return _decorate(sid, store.update_episode(sid, eid, apply))


@app.post("/api/s/{sid}/e/{eid}/gate")
def ep_gate(sid: str, eid: str, body: dict = Body(...)):
    return store.update_episode(sid, eid, lambda e: e["gates"].update({body["gate"]: bool(body.get("value"))}))["gates"]


# frames
@app.post("/api/s/{sid}/e/{eid}/frames")
def ep_frames(sid: str, eid: str, body: dict = Body(default={})):
    return _submit("frames", f"First frames {eid}", jobs.job_frames, sid=sid, eid=eid,
                   shot_ids=body.get("shot_ids") or [], force=bool(body.get("force")),
                   source_override=body.get("source") or "")


@app.post("/api/s/{sid}/e/{eid}/vision")
def ep_vision(sid: str, eid: str, body: dict = Body(default={})):
    e = _ep(sid, eid)
    ids = body.get("shot_ids") or [sh["id"] for sh in e["shots"]]
    return _submit("vision", f"Vision check {eid}", jobs.job_vision, sid=sid, eid=eid, shot_ids=ids)


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/upload_frame")
async def shot_upload_frame(sid: str, eid: str, shot_id: str, file: UploadFile = File(...),
                            crop: str = Form(""), end: str = Form("")):
    s = _s()
    e = _ep(sid, eid)
    s = settings_mod.for_episode(s, e)
    store.find_shot(e, shot_id)
    raw = await _save_upload(file, os.path.join(store.ep_dir(sid, eid), "uploads"))
    out_dir = os.path.join(store.ep_dir(sid, eid), "frames", shot_id)
    dst = os.path.join(out_dir, f"{'end' if end else 'u'}_{int(time.time() * 1000)}.png")
    try:
        media.fit_image(raw, dst, s["image"]["width"], s["image"]["height"], json.loads(crop) if crop else None)
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(400, "Could not read this photo or crop. Choose a valid JPEG, PNG, WebP or HEIC image.") from exc
    b = store.bible(sid)

    def apply(ep):
        sh = store.find_shot(ep, shot_id)
        if end:
            sh["end_frame"] = store.rel(dst)
        else:
            sh["frame"]["candidates"].append({"file": store.rel(dst), "source": "upload", "seed": None,
                                              "sig": store.bible_signature(b, sh), "original": store.rel(raw),
                                              "check": None})
            sh["frame"]["selected"] = len(sh["frame"]["candidates"]) - 1
    return _decorate(sid, store.update_episode(sid, eid, apply))


@app.post("/api/s/{sid}/e/{eid}/bulk_upload")
async def bulk_upload(sid: str, eid: str, files: list[UploadFile] = File(...)):
    """Files named by shot id (S03_SH02.png) become uploaded first frames."""
    s = _s()
    e = _ep(sid, eid)
    s = settings_mod.for_episode(s, e)
    ids = {sh["id"].upper(): sh["id"] for sh in e["shots"]}
    matched, skipped = [], []
    b = store.bible(sid)
    for f in files:
        stem = os.path.splitext(os.path.basename(f.filename or ""))[0].upper()
        shot_id = ids.get(stem)
        if not shot_id:
            skipped.append(f.filename)
            continue
        raw = await _save_upload(f, os.path.join(store.ep_dir(sid, eid), "uploads"))
        dst = os.path.join(store.ep_dir(sid, eid), "frames", shot_id, f"u_{int(time.time() * 1000)}.png")
        media.fit_image(raw, dst, s["image"]["width"], s["image"]["height"])

        def apply(ep, shot_id=shot_id, dst=dst, raw=raw):
            sh = store.find_shot(ep, shot_id)
            sh["frame"]["candidates"].append({"file": store.rel(dst), "source": "upload", "seed": None,
                                              "sig": store.bible_signature(b, sh), "original": store.rel(raw),
                                              "check": None})
            sh["frame"]["selected"] = len(sh["frame"]["candidates"]) - 1
        store.update_episode(sid, eid, apply)
        matched.append(shot_id)
    return {"matched": matched, "skipped": skipped}


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/frame")
def shot_frame(sid: str, eid: str, shot_id: str, body: dict = Body(...)):
    """{select: idx} | {lock: bool} | {clear_end: true} | {remove: idx}"""
    def apply(ep):
        sh = store.find_shot(ep, shot_id)
        if "select" in body:
            sh["frame"]["selected"] = body["select"]
        if "lock" in body:
            sh["frame"]["locked"] = bool(body["lock"])
        if body.get("clear_end"):
            sh["end_frame"] = ""
        if "remove" in body:
            _ensure_idle(sid, eid)
            store.remove_assets(sh["frame"], "candidates", [int(body["remove"])])
            ep["gates"].update({"frames": False, "animatic": False, "takes": False})
            ep["outputs"] = {}
        if "set_end" in body:
            sh["end_frame"] = body["set_end"]
    try:
        return _decorate(sid, store.update_episode(sid, eid, apply))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/refine")
def shot_refine(sid: str, eid: str, shot_id: str, body: dict = Body(...)):
    return _submit("refine", f"Refine {shot_id}", jobs.job_refine, sid=sid, eid=eid, shot_id=shot_id,
                   cand_index=int(body.get("index", 0)), engine=body.get("engine") or "flux_lora",
                   denoise=float(body.get("denoise") or 0.3))


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/frame_correction")
def frame_correction(sid: str, eid: str, shot_id: str, body: dict = Body(default={})):
    sh = store.find_shot(_ep(sid, eid), shot_id)
    index = body.get("index", sh["frame"].get("selected"))
    if type(index) is not int or not 0 <= index < len(sh["frame"]["candidates"]):
        raise HTTPException(400, "Choose an existing frame first")
    _correction_character(sid, sh)
    return _submit("frame_correction", f"Qwen face correction: {shot_id}", jobs.job_frame_correction,
                   sid=sid, eid=eid, shot_id=shot_id,
                   source_file=sh["frame"]["candidates"][index]["file"])


def _correction_character(sid, sh):
    if len(sh.get("characters", [])) != 1:
        raise HTTPException(400, "Character reference correction needs a single-character shot")
    character = store.get_character(sid, sh["characters"][0]["id"])
    if not character or not any(os.path.isfile(store.abs_media(p)) for p in character.get("refs", []) if p):
        raise HTTPException(400, "Upload a reference photo for this character in Characters first")
    return character


# takes
@app.get('/api/s/{sid}/e/{eid}/timeline')
def get_timeline(sid: str, eid: str, selected_takes: bool = False):
    episode = _ep(sid, eid)
    if selected_takes:
        episode = {**episode, 'timeline': timeline.default_edit(episode, _s(), selected_takes=True)}
    return timeline.view(episode, _s())


@app.get('/api/s/{sid}/e/{eid}/timeline/source')
def timeline_source(sid: str, eid: str, file: str, audio: bool = False):
    try:
        return timeline.source_info(_ep(sid, eid), file, _s(), audio=audio)
    except (ValueError, media.MediaError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/s/{sid}/e/{eid}/timeline/waveform')
def timeline_waveform(sid: str, eid: str, file: str):
    try:
        info = timeline.source_info(_ep(sid, eid), file, _s(), audio=True)
        return {'duration': info['duration'], 'peaks': media.audio_peaks(_s()['ffmpeg'], store.abs_media(file))}
    except (ValueError, media.MediaError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put('/api/s/{sid}/e/{eid}/timeline')
def save_timeline(sid: str, eid: str, body: dict = Body(...)):
    _ep(sid, eid)
    try:
        episode = timeline.save(sid, eid, body, _s())
        return timeline.view(episode, _s())
    except timeline.EditConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError, media.MediaError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _timeline_export(sid, eid):
    episode = _ep(sid, eid)
    cfg = settings_mod.for_episode(_s(), episode)
    try:
        edit = timeline.validate(episode, episode.get('timeline') or timeline.default_edit(episode, cfg), cfg)
        if not edit['clips']:
            raise ValueError('Add clips to the timeline before rendering')
    except (ValueError, TypeError, media.MediaError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _submit('timeline', f'Timeline export {eid}', timeline.render, sid=sid, eid=eid,
                   edit=edit, video=dict(cfg['video']))


@app.post('/api/s/{sid}/e/{eid}/timeline/export')
def export_timeline(sid: str, eid: str):
    return _timeline_export(sid, eid)


@app.post("/api/s/{sid}/e/{eid}/takes")
def ep_takes(sid: str, eid: str, body: dict = Body(default={})):
    _ep(sid, eid)
    try:
        if body.get("engine"):
            providers.validate_model(body["engine"], body.get("model") or "")
        elif body.get("model"):
            raise ValueError("Choose an engine with the model")
        if not 1 <= int(body.get("takes") or _s()["takes_per_shot"]) <= 8:
            raise ValueError("Takes per shot must be between 1 and 8")
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _submit("takes", f"Takes {eid}", jobs.job_takes, sid=sid, eid=eid,
                   shot_ids=body.get("shot_ids") or [], takes=body.get("takes"), force=bool(body.get("force")),
                   engine_override=body.get("engine") or "", model_override=body.get("model") or "")


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/take")
def shot_take(sid: str, eid: str, shot_id: str, body: dict = Body(...)):
    """{select: idx} | {lock: bool} | {trim_in, trim_out} | {remove: idx}"""
    def apply(ep):
        sh = store.find_shot(ep, shot_id)
        t = sh["takes"]
        if "select" in body:
            t["selected"] = body["select"]
        if "lock" in body:
            t["locked"] = bool(body["lock"])
        for k in ("trim_in", "trim_out"):
            if k in body:
                t[k] = max(0.0, float(body[k] or 0))
        if "remove" in body:
            _ensure_idle(sid, eid)
            store.remove_assets(t, "items", [int(body["remove"])])
            ep["gates"]["takes"] = False
            ep["outputs"] = {}
    try:
        return _decorate(sid, store.update_episode(sid, eid, apply))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/next_from_last")
def next_from_last(sid: str, eid: str, shot_id: str):
    """Use the selected take's last frame as the next shot's first frame."""
    s = _s()
    e = _ep(sid, eid)
    shots = sorted(e["shots"], key=lambda x: x["order"])
    i = next(k for k, x in enumerate(shots) if x["id"] == shot_id)
    if i + 1 >= len(shots):
        raise HTTPException(400, "This is the last shot.")
    take = jobs._selected_take(shots[i])
    if not take:
        raise HTTPException(400, "No take on this shot yet.")
    nxt = shots[i + 1]["id"]
    dst = os.path.join(store.ep_dir(sid, eid), "frames", nxt, f"chain_{int(time.time())}.png")
    media.last_frame(s["ffmpeg"], store.abs_media(take["file"]), dst)

    def apply(ep):
        sh = store.find_shot(ep, nxt)
        sh["frame"]["candidates"].append({"file": store.rel(dst), "source": "chain", "seed": None,
                                          "from": take["file"], "check": None})
        sh["frame"]["selected"] = len(sh["frame"]["candidates"]) - 1
    return _decorate(sid, store.update_episode(sid, eid, apply))


# animatic / cut
@app.post("/api/s/{sid}/e/{eid}/animatic")
def ep_animatic(sid: str, eid: str):
    return _submit("animatic", f"Animatic {eid}", jobs.job_animatic, sid=sid, eid=eid)


@app.post("/api/s/{sid}/e/{eid}/cut")
def ep_cut(sid: str, eid: str):
    if _ep(sid, eid).get('timeline'):
        return _timeline_export(sid, eid)
    return _submit("cut", f"Final cut {eid}", jobs.job_cut, sid=sid, eid=eid)


@app.post("/api/s/{sid}/e/{eid}/music")
async def ep_music(sid: str, eid: str, file: UploadFile = File(None), volume: float = Form(0.25),
                   duck: str = Form("true")):
    _ep(sid, eid)
    rel = None
    if file is not None and file.filename:
        p = await _save_upload(file, os.path.join(store.ep_dir(sid, eid), "uploads"))
        try:
            info = media.probe(_s()['ffprobe'], p)
            if not info['has_audio'] or not math.isfinite(info['duration']) or info['duration'] <= 0:
                raise ValueError('Upload a playable music or voice file')
        except (ValueError, media.MediaError) as exc:
            os.unlink(p)
            raise HTTPException(400, 'Upload a playable music or voice file') from exc
        rel = store.rel(p)

    def apply(e):
        m = e.setdefault("music", {})
        if rel:
            assets = e.setdefault('audio_assets', [])
            for path in (m.get('file'), rel):
                if path and path not in assets:
                    assets.append(path)
            m["file"] = rel
        m["volume"] = float(volume)
        m["duck"] = duck.lower() in ("1", "true", "yes", "on")
    return store.update_episode(sid, eid, apply)["music"]


@app.post('/api/s/{sid}/e/{eid}/lipsync')
def ep_lipsync(sid: str, eid: str, body: dict = Body(default={})):
    from . import lipsync
    episode = _ep(sid, eid)
    if episode.get('timeline'):
        raise HTTPException(400, 'Choose lip-sync clips and render the saved timeline in Takes')
    audio_file = (episode.get('music') or {}).get('file')
    if not audio_file or not os.path.isfile(store.abs_media(audio_file)):
        raise HTTPException(400, 'Upload music or a voice recording in Cut first')
    try:
        offset = float(body.get('audio_offset', 0))
        info = media.probe(_s()['ffprobe'], store.abs_media(audio_file))
        if not info['has_audio'] or not math.isfinite(offset) or not 0 <= offset < info['duration']:
            raise ValueError('Audio start must be within the uploaded recording')
        timeline = lipsync.plan_episode(episode, body.get('shot_ids'), _s())
        isolate = body.get('isolate_vocals', True)
        if type(isolate) is not bool:
            raise ValueError('Vocal isolation must be on or off')
    except (ValueError, TypeError, media.MediaError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _submit('lipsync', f'Lip-sync {eid}', lipsync.render, sid=sid, eid=eid, timeline=timeline,
                   audio_file=audio_file, audio_offset=offset, isolate_vocals=isolate)


@app.post('/api/s/{sid}/e/{eid}/lipsync/select')
def select_lipsync(sid: str, eid: str, body: dict = Body(...)):
    episode = _ep(sid, eid)
    versions = episode.get('outputs', {}).get('lipsync_versions', [])
    index = body.get('index')
    if type(index) is not int or not 0 <= index < len(versions):
        raise HTTPException(400, 'Choose an existing lip-sync preview')
    version = versions[index]
    if not os.path.isfile(store.abs_media(version['file'])):
        raise HTTPException(400, 'The lip-sync preview file is missing')
    def apply(ep):
        outputs = ep.setdefault('outputs', {})
        outputs.setdefault('before_lipsync', outputs.get('final'))
        outputs.update(final=version['file'], runtime=version['runtime'])
    return store.update_episode(sid, eid, apply)['outputs']


# Deletion is recoverable and blocked while jobs can still write into the project.
def _ensure_idle(sid, eid=None):
    for job in jobs.QUEUE.list():
        params = job.get("params", {})
        if job["status"] in ("queued", "running") and params.get("sid") == sid:
            if eid is None or params.get("eid") in (None, eid):
                raise HTTPException(409, "Cancel or finish this project's active jobs before deleting.")


@app.delete("/api/s/{sid}")
def series_delete(sid: str):
    _ensure_idle(sid)
    store.get_series(sid) or _404("project")
    store.archive_dir(store.series_dir(sid))
    return {"ok": True}


@app.delete("/api/s/{sid}/e/{eid}")
def episode_delete(sid: str, eid: str):
    _ensure_idle(sid, eid)
    _ep(sid, eid)
    store.archive_dir(store.ep_dir(sid, eid))
    return {"ok": True}


@app.post("/api/s/{sid}/e/{eid}/delete_assets")
def delete_assets(sid: str, eid: str, body: dict = Body(...)):
    _ensure_idle(sid, eid)
    kind = body.get("kind")
    if kind not in ("frame", "take"):
        raise HTTPException(400, "kind must be frame or take")
    def apply(ep):
        # Validate the entire request before changing any shot.
        for shot_id, indices in body.get("items", {}).items():
            sh = store.find_shot(ep, shot_id)
            group = sh["frame" if kind == "frame" else "takes"]
            values = group["candidates" if kind == "frame" else "items"]
            if any(type(i) is not int or i < 0 or i >= len(values) for i in indices):
                raise ValueError("invalid asset index")
        for shot_id, indices in body.get("items", {}).items():
            sh = store.find_shot(ep, shot_id)
            store.remove_assets(sh["frame" if kind == "frame" else "takes"],
                                "candidates" if kind == "frame" else "items", indices)
        ep["gates"].update({"frames": False, "animatic": False, "takes": False} if kind == "frame" else {"takes": False})
        ep["outputs"] = {}
    try:
        return _decorate(sid, store.update_episode(sid, eid, apply))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/face_correction")
def face_correction(sid: str, eid: str, shot_id: str, body: dict = Body(default={})):
    sh = store.find_shot(_ep(sid, eid), shot_id)
    index = body.get("index", sh["takes"].get("selected"))
    index = 0 if index is None else index
    if type(index) is not int or not 0 <= index < len(sh["takes"]["items"]):
        raise HTTPException(400, "Choose an existing take first")
    model = body.get("model") or _s()["face_correction"]["model"]
    if model not in ("keep", "codeformer", "minimax_identity", "qwen_identity"):
        raise HTTPException(400, "Unknown face correction model")
    if model in ("minimax_identity", "qwen_identity"):
        _correction_character(sid, sh)
    return _submit("face_correction", f"Face correction: {shot_id}", jobs.job_face_correction,
                   sid=sid, eid=eid, shot_id=shot_id,
                   source_file=sh["takes"]["items"][index]["file"], model=model)


@app.get("/api/models")
def video_models():
    from .providers import VIDEO_MODELS
    return VIDEO_MODELS



@app.post("/api/image_preview")
async def image_preview(file: UploadFile = File(...)):
    data = await file.read(30 * 1024 * 1024 + 1)
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "Choose a photo smaller than 30 MB")
    try:
        return Response(media.image_preview(data), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except (OSError, ValueError) as exc:
        raise HTTPException(415, "This photo could not be opened. Try a JPEG, PNG, WebP or HEIC image.") from exc


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(HERE, "static", "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/api/mobile")
def mobile_config():
    return {"https_url": _s().get("mobile", {}).get("https_url", "")}


@app.get("/local-ca.crt")
def local_certificate():
    path = os.path.join(HERE, "static", "reelwright-local-ca.crt")
    if not os.path.isfile(path):
        _404("local HTTPS certificate")
    return FileResponse(path, media_type="application/x-x509-ca-cert", filename="Reelwright-Local-CA.crt")
