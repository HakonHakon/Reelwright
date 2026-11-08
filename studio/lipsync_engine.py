"""LTX-2.3 lip-sync engine ("ltx_lipsync"): image + the voiced lines of whoever is on screen -> a take whose mouths
follow those lines (ComfyUI's official LTX-2.3 ia2v workflow, exported to API format). Also the sound-effect pass
(MMAudio, video-to-audio) and the cut that lays the lip-sync takes and their lines on the timeline.

Timing comes from the shots: each dialogue item may carry "pause" (seconds before the line, default 0.3) and the
shot's action_seconds is the hold after the last line; duration_s is the shortest the shot may be."""
import copy
import json
import math
import os
import subprocess

from . import media, store
from . import settings as settings_mod

NEGATIVE = ("scene cut, jump cut, camera cut, transition, new scene, different location, different person, face changes, "
            "fade to black, dissolve, cartoon, video game, blurry, deformed hands, text, watermark, subtitles")
SFX_NEGATIVE = "speech, voice, talking, dialogue, singing, music, melody, piano, strings"


def template(s):
    path = s["ltx_lipsync"]["template"]
    return path if os.path.isabs(path) else os.path.join(settings_mod.REPO_ROOT, path)


def square_portrait(s):
    """Portrait projects render lip-sync square on a blurred-pad first frame and the timeline crops back to the
    project shape: at 9:16 the model paints burned-in subtitles under speech (phone-video habit), even with a clean
    prompt; at 1:1 it has not. Turn off with ltx_lipsync.portrait_square = false."""
    v = s["video"]
    return v["width"] < v["height"] and bool(s["ltx_lipsync"].get("portrait_square", True))


def pad_square(cfg, src, dst):
    """The portrait first frame centred on a square canvas, sides filled with a blurred copy (cropped away later)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    media._run([cfg["ffmpeg"], "-y", "-v", "error", "-i", src, "-filter_complex",
                "[0]split[a][b];[a]scale='max(iw,ih)':'max(iw,ih)',boxblur=30:3[bg];[bg][b]overlay=(W-w)/2:(H-h)/2",
                "-frames:v", "1", dst])
    return dst


def size(s):
    """Render size: the project's aspect at about the configured pixel count, in multiples of 64 (two-stage LTX)."""
    v = s["video"]; ratio = 1.0 if square_portrait(s) else v["width"] / v["height"]; pixels = float(s["ltx_lipsync"]["pixels"])
    w = max(64, round(math.sqrt(pixels * ratio) / 64) * 64)
    h = max(64, round(math.sqrt(pixels / ratio) / 64) * 64)
    return w, h


def line_takes(episode):
    return {(v.get("text") or "").strip(): v for v in (episode.get("dialogue_takes") or {}).values() if v.get("text")}


def layout(episode, max_seconds):
    """[{id, start, len, lines: [[speaker, line, at, duration], ...]}] in shot order. Lines without a voiced take are
    skipped (voice the dialogue first)."""
    voiced = line_takes(episode)
    rows, t, covers = [], 0.0, {}
    for sh in sorted(episode["shots"], key=lambda x: x.get("order", 0)):
        if sh.get("cutaway"):                   # b-roll: no time of its own on the scene clock
            length = min(max(float(sh.get("duration_s") or 0), 1.0), float(max_seconds))
            rows.append({"id": sh["id"], "start": round(t, 3), "len": round(length, 3), "lines": []})
            continue
        c, lines = 0.0, []
        for d in sh.get("dialogue") or []:
            take = voiced.get((d.get("line") or "").strip())
            if not take:
                continue
            c += float(d.get("pause", 0.3))
            lines.append([d.get("speaker"), d["line"], round(c, 3), float(take["duration"])])
            c += float(take["duration"])
        need = c + float(sh.get("action_seconds") or 0.8) if lines else 0.0
        length = min(max(float(sh.get("duration_s") or 0), need, 1.0), float(max_seconds))
        group = covers.get(sh.get("cover")) if sh.get("cover") else None
        if group:                               # coverage: another angle on the same span of scene time
            group["len"] = max(group["len"], round(length, 3))
            rows.append({"id": sh["id"], "start": group["start"], "len": group["len"], "lines": lines, "group": group})
            t = group["start"] + group["len"]
            continue
        row = {"id": sh["id"], "start": round(t, 3), "len": round(length, 3), "lines": lines}
        rows.append(row)
        if sh.get("cover"):
            covers[sh["cover"]] = row
        t += length
    for r in rows:
        g = r.pop("group", None)
        if g:
            r["len"] = g["len"]
    return rows


def frames_for(seconds, fps):
    """LTX wants 8n+1 frames."""
    return math.ceil(seconds * fps / 8) * 8 + 1


def prompt(shot, bible):
    chars = [(store.find_character(bible["characters"], ch.get("id")), ch) for ch in shot.get("characters", [])]
    chars = [(c, ch) for c, ch in chars if c]
    first = lambda c: (c.get("name") or c["id"]).split()[0]
    names = " and ".join(first(c) for c, _ in chars)
    talk = ""
    for c, _ in chars:
        mine = [d["line"] for d in shot.get("dialogue") or [] if d.get("speaker") == c["id"]]
        if mine and (shot.get("cover") or shot.get("post_sync")):
            # Coverage singles: the audio drives the mouth; quoting the words makes the model paint them as captions.
            talk += (f"{first(c)} speaks only when their voice is heard, mouth clearly opening and closing in sync with "
                     f"it, and listens silently with lips closed the rest of the time. ")
        elif mine:
            talk += (f"{first(c)} speaks, mouth clearly opening and closing, lips articulating every syllable in sync with "
                     f"the voice: " + " ".join(f'"{x}"' for x in mine) + ". ")
        else:
            talk += f"{first(c)} does not speak: lips stay closed. "
    only = (f"Only {names} {'is' if len(chars) == 1 else 'are'} in the frame the whole time; nobody else enters the frame "
            f"and nobody leaves it. ") if chars else ""
    return ("One continuous unbroken shot that stays in this exact place with these exact people from start to end. " + only
            + f"{shot.get('framing', '')} shot, camera: {shot.get('camera') or 'static'}. {shot.get('action', '')} {talk}"
            "Same location, same lighting, same faces and clothes as the first frame the whole time. "
            "Realistic restrained acting, natural blinking and breathing, subtle eye movement. Photorealistic cinematic film. "

            + (shot.get("motion_prompt_extra") or ""))


def shot_audio(cfg, episode, shot, row, seconds, path):
    """The lines of the characters on screen at their places in the shot, silence elsewhere (off-screen lines are left
    out so nobody on screen mouths them)."""
    on_screen = {ch.get("id") for ch in shot.get("characters", [])}
    voiced = line_takes(episode)
    lines = [x for x in row["lines"] if x[0] in on_screen]
    if not lines:
        media._run([cfg["ffmpeg"], "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", f"{seconds:.3f}", path])
        return path
    inputs, graph = [], []
    for k, (_, line, at, _) in enumerate(lines):
        inputs += ["-i", store.abs_media(voiced[line.strip()]["file"])]
        ms = max(0, round(at * 1000))
        graph.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{k}]")
    mix = "".join(f"[a{k}]" for k in range(len(lines))) + (f"amix=inputs={len(lines)}:normalize=0" if len(lines) > 1 else "anull")
    media._run([cfg["ffmpeg"], "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph + [mix + ",apad[out]"]),
                "-map", "[out]", "-t", f"{seconds:.3f}", "-ar", "48000", path])
    return path


def workflow(s, image_name, audio_name, text, seconds, seed, prefix):
    lt = s["ltx_lipsync"]
    with open(template(s), encoding="utf-8") as f:
        wf = copy.deepcopy(json.load(f))
    w, h = size(s)
    wf["269"]["inputs"]["image"] = image_name
    wf["276"]["inputs"]["audio"] = audio_name
    wf["340:319"]["inputs"]["value"] = text
    wf["340:306"]["inputs"]["text"] = ["340:319", 0]                 # the prompt as written, no Gemma rewrite
    for node in ("340:345", "340:346", "340:347", "340:348", "340:349"):
        wf.pop(node, None)
    wf["340:314"]["inputs"]["text"] = lt.get("negative") or NEGATIVE
    wf["340:325"]["inputs"]["strength"] = float(lt.get("first_frame_strength", 1.0))
    wf["340:323"]["inputs"]["value"] = int(s["video"]["fps"])
    wf["340:330"]["inputs"]["value"] = w
    wf["340:324"]["inputs"]["value"] = h
    wf["340:331"]["inputs"]["value"] = float(seconds)
    wf["340:286"]["inputs"]["noise_seed"] = int(seed)
    wf["341"]["inputs"]["filename_prefix"] = prefix
    return wf


def sfx_workflow(video_name, seconds, text, seed, prefix):
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "MMAudioModelLoader", "inputs": {"mmaudio_model": "mmaudio_large_44k_v2_fp16.safetensors", "base_precision": "fp16"}},
        "4": {"class_type": "MMAudioFeatureUtilsLoader", "inputs": {
            "vae_model": "mmaudio_vae_44k_fp16.safetensors", "synchformer_model": "mmaudio_synchformer_fp16.safetensors",
            "clip_model": "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors", "mode": "44k", "precision": "fp16"}},
        "5": {"class_type": "MMAudioSampler", "inputs": {
            "mmaudio_model": ["3", 0], "feature_utils": ["4", 0], "duration": float(seconds), "steps": 25, "cfg": 4.5,
            "seed": int(seed), "prompt": text, "negative_prompt": SFX_NEGATIVE, "mask_away_clip": False,
            "force_offload": True, "images": ["2", 0]}},
        "6": {"class_type": "SaveAudio", "inputs": {"audio": ["5", 0], "filename_prefix": prefix}},
    }


PHONE = "highpass=f=350,lowpass=f=3400,acompressor=threshold=-18dB:ratio=4,volume=1.2"


def is_phone(line):
    return bool(line.get("phone")) or "phone" in (line.get("emotion") or "").lower()


def build_cut(cfg, episode, sid, eid, fps):
    """Timeline body: lip-sync takes cut to the layout, one dialogue lane per speaker (phone lines band-limited),
    the MMAudio bed, and a fade to black on the last clip. Returns (body, new audio assets)."""
    from . import timeline
    rows = {r["id"]: r for r in layout(episode, cfg["ltx_lipsync"]["max_seconds"])}
    voiced = line_takes(episode)
    F = lambda x: round(x * fps)
    shots = sorted(episode["shots"], key=lambda x: x.get("order", 0))
    clips, audio, assets, lanes = [], [], [], {}
    for i, sh in enumerate(shots):
        items = sh["takes"].get("items") or []
        if not items:
            raise ValueError(f"{sh['id']} has no take yet")
        take = items[sh["takes"].get("selected") or 0]
        r = rows[sh["id"]]
        avail = math.floor(media.probe(cfg["ffprobe"], store.abs_media(take["file"]))["duration"] * fps)
        n = max(1, min(F(r["start"] + r["len"]) - F(r["start"]), avail))
        clips.append({"id": "clip_" + sh["id"], "shot_id": sh["id"], "source": take["file"], "source_in": 0, "frames": n,
                      "muted": True, "volume": 1, "lipsync": False, "fade_in": 0,
                      "fade_out": min(n, round(1.5 * fps)) if i == len(shots) - 1 else 0, "speed": 1, "transition": 0,
                      "reframe": {"fit": "cover", "zoom": 1, "x": 0.5, "y": 0.5}})
    starts = timeline.clip_starts(clips)[0]
    by_line = {(d.get("line") or "").strip(): d for sh in shots for d in sh.get("dialogue") or []}
    for (sh, clip, start) in zip(shots, clips, starts):
        r = rows[sh["id"]]
        for speaker, line, at, _ in r["lines"]:
            f = voiced[line.strip()]["file"]
            if is_phone(by_line.get(line.strip(), {})):
                src = store.abs_media(f); dst = os.path.splitext(src)[0] + "_phone.wav"
                if not os.path.isfile(dst):
                    media._run([cfg["ffmpeg"], "-y", "-v", "error", "-i", src, "-af", PHONE, dst])
                f = store.rel(dst)
            assets.append(f)
            length = max(1, math.floor(media.probe(cfg["ffprobe"], store.abs_media(f))["duration"] * fps))
            audio.append({"id": f"dlg_{len(audio):03d}_{sh['id']}", "file": f, "lane": lanes.setdefault(speaker, len(lanes)),
                          "source_in": 0, "start_frame": start + F(at), "frames": length, "loop": False, "volume": 1,
                          "muted": False, "fade_in": 0, "fade_out": 0, "guide": True})
    bed = len(lanes)
    for sh, clip, start in zip(shots, clips, starts):
        p = os.path.join(store.ep_dir(sid, eid), "sfx", sh["id"] + ".flac")
        if not os.path.isfile(p):
            continue
        f = store.rel(p); assets.append(f)
        n = min(clip["frames"], math.floor(media.probe(cfg["ffprobe"], p)["duration"] * fps))
        if n < 1:
            continue
        audio.append({"id": f"sfx_{sh['id']}", "file": f, "lane": bed, "source_in": 0, "start_frame": start, "frames": n,
                      "loop": False, "volume": 0.45, "muted": False, "fade_in": min(3, n // 2), "fade_out": min(3, n // 2),
                      "guide": False})
    return {"clips": clips, "audio": audio}, assets
