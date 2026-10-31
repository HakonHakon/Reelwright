"""Single-GPU job queue and the job implementations for every stage."""

import os
import random
import shutil
import threading
import time
import traceback
import uuid
from collections import deque

from . import comfy as comfy_mod
from . import llm, media, planner, prompts, store, workflows, providers
from . import settings as settings_mod


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, title: str, fn, params: dict):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title, self.fn, self.params = kind, title, fn, params
        self.status = "queued"
        self.log: list[str] = []
        self.progress = [0, 0]
        self.created = time.time()
        self.started = self.finished = None
        self.error = ""
        self.cancel_flag = False

    def say(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.log.append(line)
        del self.log[:-400]

    def check(self):
        if self.cancel_flag:
            raise Cancelled()

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "progress": self.progress, "error": self.error, "log": self.log[-60:],
                "created": self.created, "started": self.started, "finished": self.finished,
                "params": {k: v for k, v in self.params.items() if isinstance(v, (str, int, float, bool, list))}}


class Queue:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.order: deque[str] = deque(maxlen=200)
        self.pending: deque[Job] = deque()
        self.cv = threading.Condition()
        self.current: Job | None = None
        threading.Thread(target=self._worker, daemon=True, name="studio-worker").start()

    def submit(self, kind: str, title: str, fn, **params) -> Job:
        job = Job(kind, title, fn, params)
        with self.cv:
            self.jobs[job.id] = job
            self.order.append(job.id)
            self.pending.append(job)
            self.cv.notify()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.cancel_flag = True
        with self.cv:
            if job in self.pending:
                self.pending.remove(job)
                job.status = "cancelled"
        return True

    def list(self) -> list[dict]:
        return [self.jobs[i].public() for i in reversed(self.order) if i in self.jobs]

    def _worker(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                job = self.pending.popleft()
            self.current = job
            job.status, job.started = "running", time.time()
            try:
                job.fn(job, **job.params)
                job.status = "done"
            except Cancelled:
                job.status = "cancelled"
                job.say("Cancelled.")
            except Exception as exc:  # surface everything in the UI
                job.status, job.error = "error", str(exc)
                job.say(f"ERROR: {exc}")
                job.say(traceback.format_exc(limit=4))
            finally:
                job.finished = time.time()
                self.current = None


QUEUE = Queue()


# ── helpers ─────────────────────────────────────────────────────────────

def _ctx():
    s = settings_mod.load()
    return s, comfy_mod.Comfy(s["comfy_host"])


def _gpu_prepare(job: Job, s: dict, c: comfy_mod.Comfy):
    if not c.alive():
        raise RuntimeError(f"ComfyUI is not running at {s['comfy_host']}. Start Comfy Desktop first.")
    job.say("Unloading Ollama models to free VRAM...")
    llm.unload(s["ollama_host"], [s["planner_model"], s["vision_model"]])


def _seed() -> int:
    return random.randint(1, 2**48)


def _save_outputs(c, items, dest_dir, stem):
    paths = []
    os.makedirs(dest_dir, exist_ok=True)
    existing = len([f for f in os.listdir(dest_dir) if f.startswith(stem)])
    for i, item in enumerate(items):
        ext = os.path.splitext(item["filename"])[1] or ".png"
        dest = os.path.join(dest_dir, f"{stem}{existing + i + 1:03d}{ext}")
        c.download(item, dest)
        paths.append(dest)
    return paths


def _character_refs(sh, b) -> list[tuple[dict, str]]:
    """(character, media path) for each character in the shot that has a reference photo on disk.
    Uploaded refs win over generated sheet images."""
    out = []
    for c, _ in prompts.characters_in(sh, b):
        cands = list(c.get("refs") or []) + [x.get("file") for x in (c.get("sheet") or [])]
        for rel in cands:
            if rel and os.path.exists(store.abs_media(rel)):
                out.append((c, rel))
                break
    return out


def _resolve_source(sh, b) -> str:
    """prompts.frame_source plus the 'auto' choice: LoRA characters -> Flux, else reference photos
    -> Qwen Edit, else Z-Image."""
    src = prompts.frame_source(sh, b)
    if src == "auto":
        if prompts.image_loras(sh, b, 1.0):
            return "flux_lora"
        return "qwen_refs" if _character_refs(sh, b) else "zimage"
    return src


def _refs_prompt(base: str, names: list[str], has_plate: bool) -> str:
    """Tell Qwen Edit which input image is which person, then append the shot's normal prompt."""
    parts = [f"Image {i + 1} is {n}." for i, n in enumerate(names)]
    if has_plate:
        parts.append(f"Image {len(names) + 1} is the room; keep this location.")
    parts.append("Create a new picture of the following scene. Each person keeps the exact face, hair, skin tone "
                 "and age of their reference image.")
    return " ".join(parts) + " " + base


def _render_images(job, s, c, source, prompt, seed, n, loras, prefix, init_image=None, denoise=1.0,
                   ref_images=None):
    w, h = s["image"]["width"], s["image"]["height"]
    if source == "qwen_refs":
        wf = workflows.qwen_edit_image(s, prompt, seed, w, h, n, ref_images or [], prefix)
    elif source == "flux_lora":
        wf = workflows.flux_image(s, prompt, seed, w, h, n, loras, prefix, init_image, denoise)
    else:
        wf = workflows.zimage_image(s, prompt, seed, w, h, n, prefix, init_image, denoise)
    res = c.run(wf, cancel=lambda: job.cancel_flag)
    if not res["images"]:
        raise RuntimeError("ComfyUI returned no images")
    return res["images"]


VISION_SCHEMA = {"type": "object", "properties": {
    "matches": {"type": "boolean"}, "score": {"type": "integer", "minimum": 0, "maximum": 10},
    "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["matches", "score", "issues"]}


def _vision_check(s, image_path, shot, bible) -> dict:
    who = []
    for c, ch in prompts.characters_in(shot, bible):
        who.append(f"{c['name']} ({c.get('appearance', '')[:120]}) {ch.get('position', '')}")
    q = (f"This image is the first frame of a film shot. Expected: framing '{shot.get('framing')}'; "
         f"characters: {'; '.join(who) or 'none'}; action: {shot.get('action')}. "
         "Check: are exactly the expected characters visible, roughly matching their descriptions and positions? "
         "Is there any text, watermark or extra limb? Score 0-10 for how well it fits.")
    try:
        return llm.chat_json(s["ollama_host"], s["vision_model"], "You are a strict film continuity checker.",
                             q, VISION_SCHEMA, images=[image_path], temperature=0.1, num_predict=512)
    except Exception as exc:
        return {"matches": None, "score": None, "issues": [f"check failed: {exc}"]}


# ── Bible jobs ──────────────────────────────────────────────────────────

def job_draft_bible(job, sid, story):
    s, _ = _ctx()
    llm.ensure_model(s["ollama_host"], s["planner_model"], job.say)
    job.say("Extracting characters and locations from the story...")
    added = planner.draft_bible(s, sid, story)
    job.say(f"Added {len(added['characters'])} characters, {len(added['locations'])} locations (marked draft).")


def job_character_sheet(job, sid, cid, source="flux_lora"):
    s, c = _ctx()
    _gpu_prepare(job, s, c)
    b = store.bible(sid)
    ch = b["characters"][cid]
    loras = []
    if source == "flux_lora" and (ch.get("image_lora") or {}).get("name"):
        loras = [{"name": ch["image_lora"]["name"], "strength": float(ch["image_lora"].get("strength", 0.9))}]
    views = prompts.character_sheet_prompts(ch, b)
    job.progress = [0, len(views)]
    out_dir = os.path.join(store.char_dir(sid, cid), "sheet")
    new = []
    for i, (view, prompt) in enumerate(views):
        job.check()
        seed = _seed()
        job.say(f"Rendering {view} ({source})...")
        items = _render_images(job, s, c, source, prompt, seed, 1, loras, f"studio/{sid}/{cid}_{view}")
        for p in _save_outputs(c, items, out_dir, f"{view}_"):
            new.append({"file": store.rel(p), "view": view, "seed": seed, "source": source})
        job.progress = [i + 1, len(views)]
    cur = store.get_character(sid, cid)
    cur["sheet"] = (cur.get("sheet") or []) + new
    store.save_character(sid, cur, bump=False)
    job.say(f"Done: {len(new)} sheet images. Approve the good ones as references.")


def job_location_plate(job, sid, lid, variant, source="zimage", count=4):
    s, c = _ctx()
    _gpu_prepare(job, s, c)
    b = store.bible(sid)
    loc = b["locations"][lid]
    prompt = prompts.location_plate_prompt(loc, variant, b)
    seed = _seed()
    job.say(f"Rendering {count} plates for {loc['name']} / {variant} ({source})...")
    items = _render_images(job, s, c, source, prompt, seed, count, [], f"studio/{sid}/{lid}_{variant}")
    paths = _save_outputs(c, items, os.path.join(store.loc_dir(sid, lid), "candidates"), f"{variant}_")
    cur = store.get_location(sid, lid)
    cur["candidates"] = (cur.get("candidates") or []) + [
        {"file": store.rel(p), "variant": variant, "seed": seed, "source": source} for p in paths]
    store.save_location(sid, cur, bump=False)
    job.say("Done.")


# ── Episode planning ────────────────────────────────────────────────────

def job_plan(job, sid, eid):
    s, _ = _ctx()
    e = store.get_episode(sid, eid)
    if not (e.get("story") or "").strip():
        raise ValueError("Paste a story first.")
    llm.ensure_model(s["ollama_host"], s["planner_model"], job.say)
    scenes, shots = planner.plan_episode(s, sid, e["story"], log=job.say)

    def apply(ep):
        ep["scenes"], ep["shots"] = scenes, shots
        store.renumber_shots(ep)
        planner.assign_continuity(ep["shots"])
        ep["gates"] = {"shots": False, "frames": False, "animatic": False, "takes": False}
    store.update_episode(sid, eid, apply)
    job.say(f"Planned {len(shots)} shots, runtime {planner.runtime_seconds(shots)} s.")


def job_replan_scene(job, sid, eid, scene_id):
    s, _ = _ctx()
    e = store.get_episode(sid, eid)
    scene = next(sc for sc in e["scenes"] if sc["id"] == scene_id)
    llm.ensure_model(s["ollama_host"], s["planner_model"], job.say)
    job.say(f"Re-planning shots for {scene_id}...")
    new = planner.replan_scene(s, sid, scene)

    def apply(ep):
        keep = [sh for sh in ep["shots"] if sh.get("scene_id") != scene_id]
        idx = next((i for i, sh in enumerate(ep["shots"]) if sh.get("scene_id") == scene_id), len(keep))
        ep["shots"] = keep[:idx] + new + keep[idx:]
        store.renumber_shots(ep)
        ep["gates"]["shots"] = False
    store.update_episode(sid, eid, apply)
    job.say(f"{len(new)} new shots for {scene_id}.")


# ── First frames ────────────────────────────────────────────────────────

def _ep_path(sid, eid, *parts):
    return os.path.join(store.ep_dir(sid, eid), *parts)


def job_frames(job, sid, eid, shot_ids=None, force=False, source_override=""):
    s, c = _ctx()
    e = store.get_episode(sid, eid)
    b = store.bible(sid)
    shots = [sh for sh in e["shots"] if (not shot_ids or sh["id"] in shot_ids)]
    todo = []
    for sh in shots:
        if sh["frame"].get("locked") and not force:
            continue
        if not force and not shot_ids and sh["frame"].get("candidates"):
            continue
        if sh.get("start_mode") == "chain":
            job.say(f"{sh['id']}: chained to previous shot - its frame comes from the previous take.")
            continue
        todo.append(sh)
    if not todo:
        job.say("Nothing to render.")
        return
    _gpu_prepare(job, s, c)
    # Batch by source to keep one model family loaded.
    order = {"flux_lora": 0, "zimage": 1, "qwen_refs": 2, "reuse": 3, "punch_in": 4}
    plan = []
    for sh in todo:
        src = source_override or _resolve_source(sh, b)
        if sh.get("start_mode") in ("reuse", "punch_in"):
            src = sh["start_mode"]
        if src == "upload":
            job.say(f"{sh['id']}: source is 'upload' - upload an image on the Frames page.")
            continue
        plan.append((order.get(src, 2), sh["order"], sh, src))
    plan.sort(key=lambda x: (x[0], x[1]))
    n = int(s["candidates_per_shot"])
    job.progress = [0, len(plan)]
    last_src = None
    plate_uploads = {}
    for i, (_, _, sh, src) in enumerate(plan):
        job.check()
        if last_src and src != last_src and src != "reuse":
            c.free()
        last_src = src
        sig = store.bible_signature(b, sh)
        out_dir = _ep_path(sid, eid, "frames", sh["id"])
        if src in ("reuse", "punch_in"):
            e = store.get_episode(sid, eid)
            master = _scene_master(e, sh)
            sel = _selected_frame(master) if master else None
            if not sel:
                job.say(f"{sh['id']}: '{src}' needs a selected frame on the scene's master shot "
                        f"({master['id'] if master else 'none'}). Pick it, then render missing frames again.")
                continue
            os.makedirs(out_dir, exist_ok=True)
            if src == "reuse":
                dst = os.path.join(out_dir, f"reuse_{int(time.time())}.png")
                shutil.copy2(store.abs_media(sel["file"]), dst)
                cands = [{"file": store.rel(dst), "source": "reuse", "from": sel["file"], "seed": None,
                          "sig": sig, "check": None}]
            else:
                cont = s["continuity"]
                framing = (sh.get("framing") or "").lower()
                frac = float(cont["crop"].get(framing, 0.5))
                pos = (sh.get("characters") or [{}])[0].get("position") or "center"
                crop = os.path.join(out_dir, f"punch_src_{int(time.time())}.png")
                media.punch_crop(store.abs_media(sel["file"]), crop, s["image"]["width"], s["image"]["height"], frac, pos)
                name = c.upload(crop, f"studio_{eid}_{sh['id']}_punch.png")
                eng = "flux_lora" if prompts.image_loras(sh, b, 1.0) or prompts.frame_source(sh, b) == "flux_lora" else "zimage"
                loras = prompts.image_loras(sh, b, float(s["flux"]["second_lora_scale"])) if eng == "flux_lora" else []
                prompt, seed, dn = prompts.image_prompt(sh, b), _seed(), float(cont["punch_in_denoise"])
                job.say(f"{sh['id']}: punch-in from master {master['id']} ({framing}, crop {frac:.2f}, {pos}, denoise {dn})...")
                items = _render_images(job, s, c, eng, prompt, seed, n, loras, f"studio/{eid}/{sh['id']}",
                                       init_image=name, denoise=dn)
                paths = _save_outputs(c, items, out_dir, "p")
                cands = [{"file": store.rel(p), "source": "punch_in", "from": sel["file"], "seed": seed,
                          "sig": sig, "prompt": prompt, "loras": loras, "check": None} for p in paths]
        else:
            prompt = prompts.image_prompt(sh, b)
            loras = prompts.image_loras(sh, b, float(s["flux"]["second_lora_scale"])) if src == "flux_lora" else []
            seed = _seed()
            plate, init_name, denoise = _location_plate(s, b, sh), None, 1.0
            ref_names, ref_files = [], []
            if src == "qwen_refs":
                max_refs = int(s["qwen_edit"].get("max_refs", 3))
                found = _character_refs(sh, b)[:max_refs]
                if not found:
                    job.say(f"{sh['id']}: no reference photo on its characters - add one on the Characters page "
                            f"or pick another source.")
                    continue
                for ch, rel in found:
                    ref_files.append(rel)
                    ref_names.append(c.upload(store.abs_media(rel), f"studio_ref_{os.path.basename(rel)}"))
                names = [ch.get("name") or ch.get("id") for ch, _ in found]
                use_plate = bool(plate) and len(ref_names) < max_refs
                if use_plate:
                    ref_names.append(c.upload(store.abs_media(plate), f"studio_plate_{os.path.basename(plate)}"))
                    ref_files.append(plate)
                prompt = _refs_prompt(prompt, names, use_plate)
                plate = plate if use_plate else None
            elif plate:
                if plate not in plate_uploads:
                    plate_uploads[plate] = c.upload(store.abs_media(plate), f"studio_plate_{os.path.basename(plate)}")
                init_name, denoise = plate_uploads[plate], float(s["location_ref"]["denoise"])
            job.say(f"{sh['id']}: {n} candidates via {src}{' + ' + str(len(loras)) + ' LoRA' if loras else ''}"
                    f"{f' from {len(ref_files)} reference image(s)' if ref_files else ''}"
                    f"{f' on location plate (denoise {denoise})' if plate else ''}...")
            t0 = time.time()
            items = _render_images(job, s, c, src, prompt, seed, n, loras, f"studio/{eid}/{sh['id']}",
                                   init_image=init_name, denoise=denoise, ref_images=ref_names)
            paths = _save_outputs(c, items, out_dir, "c")
            job.say(f"  {len(paths)} images in {time.time() - t0:.0f}s")
            cands = [{"file": store.rel(p), "source": src, "seed": seed, "batch_index": k, "sig": sig,
                      "prompt": prompt, "loras": loras, "plate": plate, "refs": ref_files, "check": None}
                     for k, p in enumerate(paths)]
        _add_candidates(sid, eid, sh["id"], cands)
        job.progress = [i + 1, len(plan)]
    if s.get("vision_check"):
        job_vision(job, sid, eid, [p[2]["id"] for p in plan], comfy=c)


def _scene_master(e, sh):
    """First shot of the shot's scene (the master angle the continuity pass assigned)."""
    same = [x for x in sorted(e["shots"], key=lambda x: x["order"]) if x.get("scene_id") == sh.get("scene_id")]
    for x in same:
        if x["id"] == sh["id"]:
            return None
        if x.get("start_mode") == "new" and x.get("characters"):
            return x
    return same[0] if same and same[0]["id"] != sh["id"] else None


def _location_plate(s, b, sh) -> str | None:
    """Approved establishing plate for the shot's location/time, used as img2img base for wider shots."""
    cfg = s.get("location_ref") or {}
    if not cfg.get("enabled") or sh.get("no_location_ref"):
        return None
    if (sh.get("framing") or "") not in cfg.get("framings", []):
        return None
    loc = b["locations"].get(sh.get("location_id") or "")
    plates = (loc or {}).get("plates") or {}
    rel_path = plates.get(sh.get("time_of_day") or "") or next(iter(plates.values()), None)
    return rel_path if rel_path and os.path.exists(store.abs_media(rel_path)) else None


def _add_candidates(sid, eid, shot_id, cands):
    def apply(ep):
        sh = store.find_shot(ep, shot_id)
        sh["frame"]["candidates"].extend(cands)
        sh["frame"]["bible_sig"] = cands[-1].get("sig", "") if cands else sh["frame"].get("bible_sig", "")
    store.update_episode(sid, eid, apply)


def job_vision(job, sid, eid, shot_ids, comfy=None):
    s, c = _ctx()
    (comfy or c).free()
    b = store.bible(sid)
    e = store.get_episode(sid, eid)
    job.say("Vision check of new candidates...")
    for sh in e["shots"]:
        if sh["id"] not in shot_ids:
            continue
        for k, cand in enumerate(sh["frame"]["candidates"]):
            job.check()
            if cand.get("check") is not None or cand.get("source") in ("upload", "reuse", "chain"):
                continue
            res = _vision_check(s, store.abs_media(cand["file"]), sh, b)

            def apply(ep, sid_=sh["id"], k_=k, res_=res):
                store.find_shot(ep, sid_)["frame"]["candidates"][k_]["check"] = res_
            store.update_episode(sid, eid, apply)
    llm.unload(s["ollama_host"], [s["vision_model"]])
    job.say("Vision check done.")


def job_refine(job, sid, eid, shot_id, cand_index, engine="flux_lora", denoise=0.3):
    s, c = _ctx()
    _gpu_prepare(job, s, c)
    b = store.bible(sid)
    e = store.get_episode(sid, eid)
    sh = store.find_shot(e, shot_id)
    cand = sh["frame"]["candidates"][int(cand_index)]
    name = c.upload(store.abs_media(cand["file"]), f"studio_{eid}_{shot_id}_refine_{int(time.time())}.png")
    loras = prompts.image_loras(sh, b, float(s["flux"]["second_lora_scale"])) if engine == "flux_lora" else []
    seed = _seed()
    job.say(f"Refining {shot_id} candidate {cand_index} with {engine} (denoise {denoise})...")
    items = _render_images(job, s, c, engine, prompts.image_prompt(sh, b), seed, 2, loras,
                           f"studio/{eid}/{shot_id}_refine", init_image=name, denoise=float(denoise))
    paths = _save_outputs(c, items, _ep_path(sid, eid, "frames", shot_id), "r")
    sig = store.bible_signature(b, sh)
    _add_candidates(sid, eid, shot_id, [{"file": store.rel(p), "source": f"refine:{engine}", "seed": seed,
                                         "sig": sig, "from": cand["file"], "check": None} for p in paths])
    job.say("Done.")


# ── Takes ───────────────────────────────────────────────────────────────

def _prev_shot(e, sh):
    shots = sorted(e["shots"], key=lambda x: x["order"])
    i = next(k for k, x in enumerate(shots) if x["id"] == sh["id"])
    return shots[i - 1] if i > 0 else None


def _selected_frame(sh):
    sel = sh["frame"].get("selected")
    if sel is None:
        return None
    cands = sh["frame"].get("candidates") or []
    return cands[sel] if 0 <= sel < len(cands) else None


def _selected_take(sh):
    items = sh["takes"].get("items") or []
    sel = sh["takes"].get("selected")
    if sel is not None and 0 <= sel < len(items):
        return items[sel]
    return items[0] if items else None


MOVING_CAMERA = ("push", "pull", "pan", "tilt", "track", "dolly", "handheld", "follow", "zoom", "orbit", "crane")


def _anchor_end(s, sh) -> bool:
    """Static close / punch-in shots: use the first frame as the end guide so LTX can't cut away."""
    if not (s.get("continuity") or {}).get("anchor_end", True):
        return False
    if any(w in (sh.get("camera") or "").lower() for w in MOVING_CAMERA):
        return False
    return sh.get("start_mode") == "punch_in" or (sh.get("framing") or "").lower() in prompts.CLOSE_FRAMINGS


def job_takes(job, sid, eid, shot_ids=None, takes=None, force=False, engine_override="", model_override=""):
    s, c = _ctx()
    b = store.bible(sid)
    e = store.get_episode(sid, eid)
    shots = sorted(e["shots"], key=lambda x: x["order"])
    todo = [sh for sh in shots if (not shot_ids or sh["id"] in shot_ids)
            and not (sh["takes"].get("locked") and not force)
            and (force or shot_ids or not sh["takes"].get("items"))]
    if not todo:
        job.say("Nothing to render.")
        return
    engines = {engine_override or sh.get("engine") or b["style"].get("default_engine") or "ltx" for sh in todo}
    for engine in engines:
        providers.validate_model(engine, model_override if engine_override else "")
    if engines != {"minimax"}:
        _gpu_prepare(job, s, c)
    n_takes = int(takes or s["takes_per_shot"])
    job.progress = [0, len(todo) * n_takes]
    done = 0
    last_engine = None
    for sh in todo:
        job.check()
        e = store.get_episode(sid, eid)  # refresh: chained shots need the latest previous take
        sh = store.find_shot(e, sh["id"])
        engine = engine_override or sh.get("engine") or b["style"].get("default_engine") or "ltx"
        if last_engine and engine != last_engine and last_engine != "minimax":
            c.free()
        last_engine = engine

        first = _first_frame_for(job, s, sid, eid, e, sh)
        if not first:
            done += n_takes
            continue
        first_name = None if engine == "minimax" else c.upload(first, f"studio_{eid}_{sh['id']}_first.png")
        end_name = None
        if engine != "minimax" and sh.get("end_frame"):
            end_name = c.upload(store.abs_media(sh["end_frame"]), f"studio_{eid}_{sh['id']}_end.png")
        elif engine == "ltx" and _anchor_end(s, sh):
            end_name = first_name
            job.say(f"{sh['id']}: end anchored to the first frame (hold the framing)")
        max_s = float(s[engine]["max_seconds"])
        seconds = min(float(sh.get("duration_s") or 3), max_s)
        out_dir = _ep_path(sid, eid, "takes", sh["id"])
        for t in range(n_takes):
            job.check()
            seed = _seed()
            prefix = f"studio/{eid}/{sh['id']}"
            if engine == "minimax":
                model = model_override or sh.get("video_model") or s["minimax"]["model"]
                path, elapsed, model = providers.minimax_generate(
                    job, s, first, prompts.motion_prompt(sh, b, engine), seconds, out_dir, model)
                info = media.probe(s["ffprobe"], path)
                frames = max(1, round(info["duration"] * info["fps"]))
                item = {"file": store.rel(path), "engine": engine, "model": model,
                        "seed": None, "frames": frames, "seconds": round(info["duration"], 2),
                        "render_s": round(elapsed), "flags": media.qc_video(path, info["duration"], info["duration"]),
                        "first_frame": store.rel(first), "sig": store.bible_signature(b, sh), "created": store.now()}
                store.update_episode(sid, eid, lambda ep: store.find_shot(ep, sh["id"])["takes"]["items"].append(item))
                done += 1
                job.progress = [done, len(todo) * n_takes]
                continue
            if engine == "minimax_local":
                seconds = max(4.0, seconds)
                frames = workflows.minimax_h3_frames(seconds)
                wf = workflows.minimax_h3_i2v(s, prompts.motion_prompt(sh, b, engine), first_name,
                                             frames, seed, end_name, prefix)
            elif engine == "wan":
                frames = workflows.wan_frames(seconds, int(s["wan"]["fps"]))
                wf = workflows.wan_i2v(s, prompts.motion_prompt(sh, b, "wan"), first_name, frames, seed,
                                       prompts.video_loras(sh, b), prefix)
            else:
                frames = workflows.ltx_frames(seconds, int(s["video"]["fps"]))
                voice_name = None
                vf = prompts.speaker_voice(sh, b)
                if vf:
                    vpath = store.abs_media(vf)
                    if os.path.exists(vpath):
                        voice_name = c.upload(vpath, f"studio_voice_{os.path.basename(vpath)}")
                wf = workflows.ltx_i2v(s, prompts.motion_prompt(sh, b, "ltx"), first_name, frames, seed,
                                       end_name, voice_name, prefix)
            job.say(f"{sh['id']}: take {t + 1}/{n_takes} ({engine}, {frames} frames, {seconds:.1f}s)...")
            res = c.run(wf, cancel=lambda: job.cancel_flag)
            if not res["videos"]:
                raise RuntimeError("ComfyUI returned no video")
            path = _save_outputs(c, res["videos"][:1], out_dir, f"{sh['id']}_t")[0]
            info = media.probe(s["ffprobe"], path)
            flags = media.qc_video(path, seconds, info["duration"])
            spf = res["seconds"] / max(1, frames)
            job.say(f"  done in {res['seconds']:.0f}s ({spf:.2f} s/frame){' - flags: ' + ', '.join(flags) if flags else ''}")
            item = {"file": store.rel(path), "engine": engine, "model": "MiniMax-H3" if engine == "minimax_local" else engine, "seed": seed, "frames": frames,
                    "seconds": round(info["duration"], 2), "render_s": round(res["seconds"]),
                    "flags": flags, "first_frame": store.rel(first),
                    "sig": store.bible_signature(b, sh), "created": store.now()}

            def apply(ep, sid_=sh["id"], item_=item, eng=engine, spf_=spf):
                shot = store.find_shot(ep, sid_)
                shot["takes"]["items"].append(item_)
                old = ep["stats"]["spf"].get(eng)
                ep["stats"]["spf"][eng] = round(spf_ if not old else old * 0.7 + spf_ * 0.3, 3)
            store.update_episode(sid, eid, apply)
            done += 1
            job.progress = [done, len(todo) * n_takes]


def _first_frame_for(job, s, sid, eid, e, sh):
    if sh.get("start_mode") == "chain":
        prev = _prev_shot(e, sh)
        take = _selected_take(prev) if prev else None
        if not take:
            job.say(f"{sh['id']}: chained, but the previous shot has no take yet; skipped.")
            return None
        dst = _ep_path(sid, eid, "frames", sh["id"], f"chain_{int(time.time())}.png")
        media.last_frame(s["ffmpeg"], store.abs_media(take["file"]), dst)

        def apply(ep):
            shot = store.find_shot(ep, sh["id"])
            shot["frame"]["candidates"].append({"file": store.rel(dst), "source": "chain", "seed": None,
                                                "from": take["file"], "check": None})
            shot["frame"]["selected"] = len(shot["frame"]["candidates"]) - 1
        store.update_episode(sid, eid, apply)
        return dst
    sel = _selected_frame(sh)
    if not sel:
        job.say(f"{sh['id']}: no approved first frame; skipped.")
        return None
    return store.abs_media(sel["file"])


# ── Animatic and cut ────────────────────────────────────────────────────

def job_animatic(job, sid, eid):
    s, _ = _ctx()
    e = store.get_episode(sid, eid)
    items = []
    for sh in sorted(e["shots"], key=lambda x: x["order"]):
        sel = _selected_frame(sh) or (sh["frame"]["candidates"][0] if sh["frame"]["candidates"] else None)
        if sel:
            items.append((store.abs_media(sel["file"]), float(sh.get("duration_s") or 2)))
    if not items:
        raise ValueError("No frames yet.")
    dst = _ep_path(sid, eid, "out", "animatic.mp4")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    v = s["video"]
    media.animatic(s["ffmpeg"], items, dst, v["width"], v["height"], v["fps"])
    store.update_episode(sid, eid, lambda ep: ep["outputs"].update(animatic=store.rel(dst)))
    job.say("Animatic exported.")


def job_cut(job, sid, eid):
    s, _ = _ctx()
    e = store.get_episode(sid, eid)
    b = store.bible(sid)
    v = s["video"]
    work = _ep_path(sid, eid, "out", "work")
    os.makedirs(work, exist_ok=True)
    clips, subs, events = [], [], []
    t = 0.0
    shots = sorted(e["shots"], key=lambda x: x["order"])
    job.progress = [0, len(shots)]
    for i, sh in enumerate(shots):
        job.check()
        take = _selected_take(sh)
        if not take:
            job.say(f"{sh['id']}: no take, left out of the cut.")
            continue
        src = store.abs_media(take["file"])
        out = os.path.join(work, f"{i:03d}_{sh['id']}.mp4")
        tin, tout = float(sh["takes"].get("trim_in") or 0), float(sh["takes"].get("trim_out") or 0)
        dur = media.normalize_clip(s["ffmpeg"], s["ffprobe"], src, out, tin, tout,
                                   v["width"], v["height"], v["fps"], float(s["loudness_lufs"]))
        clips.append(out)
        lines = [f"{(b['characters'].get(d.get('speaker')) or {}).get('name', d.get('speaker'))}: {d.get('line', '')}"
                 for d in sh.get("dialogue", []) or [] if d.get("line")]
        if lines:
            subs.append((t + 0.1, t + max(0.5, dur - 0.1), "\n".join(lines)))
        events.append({"clip": os.path.basename(src), "src_in": tin, "duration": dur})
        t += dur
        job.progress = [i + 1, len(shots)]
        job.say(f"{sh['id']}: {dur:.2f}s")
    if not clips:
        raise ValueError("No takes selected.")
    out_dir = _ep_path(sid, eid, "out")
    film = os.path.join(out_dir, "film_nomusic.mp4")
    media.concat(s["ffmpeg"], clips, film)
    final = os.path.join(out_dir, "final.mp4")
    music = (e.get("music") or {}).get("file")
    if music and os.path.exists(store.abs_media(music)):
        job.say("Adding music bed...")
        media.add_music(s["ffmpeg"], film, store.abs_media(music), final,
                        float(e["music"].get("volume", 0.25)), bool(e["music"].get("duck", True)))
    else:
        shutil.copy2(film, final)
    with open(os.path.join(out_dir, "final.srt"), "w", encoding="utf-8") as f:
        f.write(media.srt(subs))
    with open(os.path.join(out_dir, "final.edl"), "w", encoding="utf-8") as f:
        f.write(media.edl(e.get("title", eid), events, v["fps"]))
    store.update_episode(sid, eid, lambda ep: ep["outputs"].update(
        final=store.rel(final), srt=store.rel(os.path.join(out_dir, "final.srt")),
        edl=store.rel(os.path.join(out_dir, "final.edl")), runtime=round(t, 2)))
    job.say(f"Final cut: {t:.1f}s -> {final}")


def job_face_correction(job, sid, eid, shot_id, source_file, model="keep"):
    from . import face_correction
    s, c = _ctx()
    sh = store.find_shot(store.get_episode(sid, eid), shot_id)
    original = next((x for x in sh["takes"]["items"] if x["file"] == source_file), None)
    if original is None:
        raise ValueError("Source take was removed")
    if c.alive():
        c.free()
    llm.unload(s["ollama_host"], [s["planner_model"], s["vision_model"]])
    destination = os.path.join(_ep_path(sid, eid, "takes", shot_id), f"face_{uuid.uuid4().hex}.mp4")
    started = time.monotonic()
    face_correction.restore(job, s, store.abs_media(source_file), destination, model)
    job.check()
    item = {**original, "file": store.rel(destination), "source_take": source_file,
            "face_correction": model, "created": store.now(), "render_s": round(time.monotonic() - started)}
    def apply(ep):
        take = store.find_shot(ep, shot_id)["takes"]
        take["items"].append(item)
        # Keep the user's original pick until they review the restored version.
        ep["gates"]["takes"] = False
    store.update_episode(sid, eid, apply)
    job.say("Corrected take added for review. Original retained.")
