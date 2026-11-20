"""Generate a film's dialogue with VoiceStudio and place each line on the Cut timeline.

* Every speaking character gets one reference voice (an uploaded voice sample, else designed from the
  character's voice description), so all its lines sound like the same person.
* Every line is acted with its direction (dialogue item "emotion"), checked with speech recognition and
  retried with a new seed when the words are off; the best take is kept.
* Takes are cached by (character voice, text, direction, engine) so unchanged lines are never re-voiced.
* Each line becomes its own audio-lane clip (one lane per character) inside its shot, marked as the
  lip-sync guide. Earlier generated dialogue clips (id "dlg_...") are replaced; other lane clips are kept.
"""
import hashlib
import json
import os
import subprocess

from . import comfy as comfy_mod
from . import llm, media, settings as settings_mod, store, timeline, voices

LEAD_IN, GAP = 0.35, 0.25          # seconds: first line after the cut, pause between lines in one shot


def _ctx():
    return settings_mod.load()


def _free_gpu(job, s):
    """Make room for the voice engines: ComfyUI and Ollama models off the GPU."""
    job.say("Freeing GPU memory (ComfyUI, Ollama) for the voice engine...")
    comfy_mod.Comfy(s["comfy_host"]).free()
    llm.unload(s["ollama_host"], [s["planner_model"], s["vision_model"]])


def voice_reference(job, s, sid, cid, force=False):
    """(reference wav path, its transcript) for a character, creating it if needed."""
    c = store.get_character(sid, cid)
    if c is None:
        raise ValueError(f"No character '{cid}'")
    v = c.setdefault("voice", {})
    if v.get("file") and not force and os.path.exists(store.abs_media(v["file"])):
        path = store.abs_media(v["file"])
        if not v.get("ref_text"):
            job.say(f"{c['name']}: transcribing the voice sample...")
            v["ref_text"] = voices.transcribe(s, path)
            store.save_character(sid, c, bump=False)
        return path, v["ref_text"]
    description = (v.get("description") or "").strip()
    if not description:
        raise ValueError(f"{c['name']} has no voice yet: upload a voice sample or write a voice description "
                         "(e.g. 'a cheerful four-year-old boy, high-pitched child voice') on the character page")
    job.say(f"{c['name']}: designing a voice from '{description}'...")
    wav, text = voices.design_reference(s, description, language="en")    # the reference line is English
    path = os.path.join(store.char_dir(sid, cid), "voice_reference.wav")
    with open(path, "wb") as f:
        f.write(wav)
    v.update(file=store.rel(path), ref_text=text, designed=True)
    store.save_character(sid, c, bump=False)           # not a look change: frames stay valid; takes are keyed on the audio
    return path, text


def job_design_voice(job, sid, cid, description=""):
    s = _ctx()
    voices.require(s)
    c = store.get_character(sid, cid)
    if description:
        c.setdefault("voice", {})["description"] = description
    c.setdefault("voice", {}).pop("file", None)
    c["voice"].pop("ref_text", None)
    c["voice"].pop("profile", None)
    store.save_character(sid, c, bump=False)
    _free_gpu(job, s)
    path, _ = voice_reference(job, sid=sid, s=s, cid=cid, force=True)
    job.say(f"Voice ready: {store.rel(path)}")


def _key(ref_path, text, emotion, s):
    h = hashlib.sha256()
    with open(ref_path, "rb") as f:
        h.update(f.read())
    c = voices.cfg(s)
    h.update(json.dumps([text, emotion, c["engine"], c["language"], c["emo_alpha"], c["emotion"]]).encode())
    return h.hexdigest()[:16]


def _duration(s, path):
    return media.probe(s["ffprobe"], path)["duration"]


def _clean(s, src, dst):
    """Trim leading/trailing silence and level the line (EBU R128, -16 LUFS)."""
    af = ("silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_threshold=-45dB:"
          "stop_duration=0.35,loudnorm=I=-16:TP=-1.5:LRA=7,aresample=48000")
    subprocess.run([s["ffmpeg"], "-y", "-v", "error", "-i", src, "-af", af, "-ac", "1", dst], check=True)


def voice_line(job, s, sid, eid, ref, ref_text, text, emotion, cache):
    """Path (episode-relative) of the best take for one line, using the cache when possible."""
    key = _key(ref, text, emotion, s)
    hit = cache.get(key)
    if hit and os.path.exists(store.abs_media(hit["file"])):
        return hit, True
    out_dir = os.path.join(store.ep_dir(sid, eid), "dialogue")
    os.makedirs(out_dir, exist_ok=True)
    best = None
    for attempt, seed in enumerate((7, 11, 23, 42, 101)[: int(voices.cfg(s)["tries"])], 1):
        job.check()
        raw = os.path.join(out_dir, f"{key}_{seed}_raw.wav")
        with open(raw, "wb") as f:
            f.write(voices.speak(s, text, ref, ref_text, emotion, seed=seed))
        heard = voices.transcribe(s, raw)
        score = voices.match(text, heard)
        if best is None or score > best[0]:
            best = (score, raw, heard, seed)
        if score >= voices.cfg(s)["min_match"]:
            break
        job.say(f"   take {attempt}: heard '{heard}' ({score:.2f}), trying again")
    score, raw, heard, seed = best
    final = os.path.join(out_dir, f"{key}.wav")
    _clean(s, raw, final)
    for f in os.listdir(out_dir):
        if f.startswith(key + "_") and f.endswith("_raw.wav"):
            os.remove(os.path.join(out_dir, f))
    hit = {"file": store.rel(final), "text": text, "emotion": emotion, "heard": heard, "match": round(score, 3),
           "seed": seed, "duration": round(_duration(s, final), 3)}
    cache[key] = hit
    return hit, False


def plan_placement(edit, fps, lines):
    """Lane clips for the lines: one lane per speaker, each line inside its shot (lines in a shot follow
    each other). lines: [(shot_id, speaker_id, take)]. Returns (clips, warnings)."""
    starts, t = {}, 0
    for c in edit["clips"]:
        starts.setdefault(c["shot_id"], (t, c["frames"]))
        t += c["frames"]
    lanes, clips, warnings, cursor = {}, [], [], {}
    for n, (shot_id, speaker, take) in enumerate(lines):
        if shot_id not in starts:
            warnings.append(f"{shot_id} is not on the timeline; its line was left out")
            continue
        lane = lanes.setdefault(speaker, len(lanes))
        start, length = starts[shot_id]
        at = cursor.get(shot_id, start / fps + LEAD_IN)
        frames = max(1, int(take["duration"] * fps))
        clips.append({"id": f"dlg_{n:03d}_{shot_id}", "file": take["file"], "lane": lane, "source_in": 0,
                      "start_frame": round(at * fps), "frames": frames, "loop": False, "volume": 1, "muted": False,
                      "fade_in": 0, "fade_out": 0, "guide": True})
        cursor[shot_id] = at + take["duration"] + GAP
        over = at + take["duration"] - (start + length) / fps
        if over > 0.3:
            warnings.append(f"{shot_id}: the dialogue runs {over:.1f} s past the shot")
    return clips, warnings


def job_dialogue(job, sid, eid, shot_ids=None):
    s = settings_mod.for_episode(_ctx(), store.get_episode(sid, eid))
    voices.require(s)
    e = store.get_episode(sid, eid)
    b = store.bible(sid)
    shots = sorted(e["shots"], key=lambda x: x["order"])
    if shot_ids:
        shots = [sh for sh in shots if sh["id"] in shot_ids]
    todo = []
    for sh in shots:
        for d in sh.get("dialogue") or []:
            if not (d.get("line") or "").strip():
                continue
            cid = store.resolve_character_id(b["characters"], d.get("speaker"))
            if not cid:
                job.say(f"{sh['id']}: '{d.get('speaker')}' is not a character in the bible; line skipped")
                continue
            todo.append((sh["id"], cid, d["line"].strip(), (d.get("emotion") or "").strip()))
    if not todo:
        job.say("No dialogue to voice.")
        return
    _free_gpu(job, s)
    refs = {cid: voice_reference(job, s, sid, cid) for cid in dict.fromkeys(c for _, c, _, _ in todo)}
    cache = dict(e.get("dialogue_takes") or {})
    job.progress = [0, len(todo)]
    lines = []
    for i, (shot_id, cid, text, emotion) in enumerate(todo, 1):
        job.check()
        take, cached = voice_line(job, s, sid, eid, *refs[cid], text, emotion, cache)
        mark = "" if take["match"] >= voices.cfg(s)["min_match"] else "  ⚠ words may be off"
        job.say(f"{shot_id} {b['characters'][cid]['name']}: \"{text}\"" + (f" [{emotion}]" if emotion else "")
                + (" (unchanged)" if cached else f" -> heard \"{take['heard']}\"{mark}"))
        lines.append((shot_id, cid, take))
        job.progress = [i, len(todo)]
    voices.unload(s)

    def apply(ep):
        ep["dialogue_takes"] = cache
        assets = ep.setdefault("audio_assets", [])
        for _, _, take in lines:
            if take["file"] not in assets:
                assets.append(take["file"])
    store.update_episode(sid, eid, apply)

    ep = store.get_episode(sid, eid)
    edit = ep.get("timeline") or timeline.default_edit(ep, s)
    clips, warnings = plan_placement(edit, edit["fps"], lines)
    keep = [t for t in edit.get("audio") or [] if not str(t.get("id", "")).startswith("dlg_")]
    lanes_kept = sorted({t["lane"] for t in keep})
    shift = (max(lanes_kept) + 1) if lanes_kept else 0          # dialogue lanes go below the kept lanes
    for c in clips:
        c["lane"] += shift
    body = {**edit, "audio": keep + clips, "revision": ep.get("timeline", {}).get("revision", 0)}
    timeline.save(sid, eid, body, s)
    for w in warnings:
        job.say("⚠ " + w)
    speakers = len({c for _, c, _ in lines})
    job.say(f"Done: {len(clips)} lines on {speakers} dialogue lane(s) in Cut, each a lip-sync guide. "
            "Tick lip-sync on the shots where the speaker's face is clear, then export.")
