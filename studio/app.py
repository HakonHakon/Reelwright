"""Studio web app: JSON API + a single-page UI (static/index.html)."""

import json
import os
import re
import shutil
import time

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import comfy as comfy_mod
from . import jobs, llm, media, planner, prompts, store
from . import settings as settings_mod

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Reelwright")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

EDITABLE_SHOT_FIELDS = {
    "scene_id", "location_id", "time_of_day", "framing", "camera", "characters", "action", "dialogue",
    "sound", "action_seconds", "duration_s", "duration_locked", "start_mode", "frame_source", "engine",
    "image_prompt_extra", "motion_prompt_extra", "image_prompt_override", "motion_prompt_override", "notes",
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
            needed = {("UNETLoader", "unet_name"): [s["flux"]["unet"], s["zimage"]["unet"], s["wan"]["high"], s["wan"]["low"]],
                      ("CLIPLoader", "clip_name"): [s["zimage"]["clip"], s["wan"]["text_encoder"]],
                      ("VAELoader", "vae_name"): [s["flux"]["vae"], s["wan"]["vae"]]}
            for (node, inp), names in needed.items():
                opts = set(c.options(node, inp))
                out["missing"] += [n for n in names if n not in opts]
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
    d = store.char_dir(sid, cid)
    if os.path.isdir(d):
        os.replace(d, os.path.join(os.path.dirname(d), f".deleted_{cid}_{int(time.time())}"))
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
    d = store.loc_dir(sid, lid)
    if os.path.isdir(d):
        os.replace(d, os.path.join(os.path.dirname(d), f".deleted_{lid}_{int(time.time())}"))
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
    s = _s()
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
                      "video": s["video"], "ltx_max": s["ltx"]["max_seconds"], "wan_max": s["wan"]["max_seconds"]}
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
                cap = s["wan"]["max_seconds"] if eng == "wan" else s["ltx"]["max_seconds"]
                base["duration_s"] = planner.compute_duration(base, b, cap)
            new_list.append(base)
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
    store.find_shot(e, shot_id)
    raw = await _save_upload(file, os.path.join(store.ep_dir(sid, eid), "uploads"))
    out_dir = os.path.join(store.ep_dir(sid, eid), "frames", shot_id)
    dst = os.path.join(out_dir, f"{'end' if end else 'u'}_{int(time.time() * 1000)}.png")
    media.fit_image(raw, dst, s["image"]["width"], s["image"]["height"], json.loads(crop) if crop else None)
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
            i = int(body["remove"])
            cands = sh["frame"]["candidates"]
            if 0 <= i < len(cands):
                cands.pop(i)
                sel = sh["frame"].get("selected")
                if sel is not None:
                    sh["frame"]["selected"] = None if sel == i else (sel - 1 if sel > i else sel)
        if "set_end" in body:
            sh["end_frame"] = body["set_end"]
    return _decorate(sid, store.update_episode(sid, eid, apply))


@app.post("/api/s/{sid}/e/{eid}/shots/{shot_id}/refine")
def shot_refine(sid: str, eid: str, shot_id: str, body: dict = Body(...)):
    return _submit("refine", f"Refine {shot_id}", jobs.job_refine, sid=sid, eid=eid, shot_id=shot_id,
                   cand_index=int(body.get("index", 0)), engine=body.get("engine") or "flux_lora",
                   denoise=float(body.get("denoise") or 0.3))


# takes
@app.post("/api/s/{sid}/e/{eid}/takes")
def ep_takes(sid: str, eid: str, body: dict = Body(default={})):
    return _submit("takes", f"Takes {eid}", jobs.job_takes, sid=sid, eid=eid,
                   shot_ids=body.get("shot_ids") or [], takes=body.get("takes"), force=bool(body.get("force")))


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
            i = int(body["remove"])
            if 0 <= i < len(t["items"]):
                t["items"].pop(i)
                sel = t.get("selected")
                if sel is not None:
                    t["selected"] = None if sel == i else (sel - 1 if sel > i else sel)
    return _decorate(sid, store.update_episode(sid, eid, apply))


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
    return _submit("cut", f"Final cut {eid}", jobs.job_cut, sid=sid, eid=eid)


@app.post("/api/s/{sid}/e/{eid}/music")
async def ep_music(sid: str, eid: str, file: UploadFile = File(None), volume: float = Form(0.25),
                   duck: str = Form("true")):
    rel = None
    if file is not None and file.filename:
        p = await _save_upload(file, os.path.join(store.ep_dir(sid, eid), "uploads"))
        rel = store.rel(p)

    def apply(e):
        m = e.setdefault("music", {})
        if rel:
            m["file"] = rel
        m["volume"] = float(volume)
        m["duck"] = duck.lower() in ("1", "true", "yes", "on")
    return store.update_episode(sid, eid, apply)["music"]
