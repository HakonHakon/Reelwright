"""Coverage: shoot a dialogue scene the way a crew does, then cut it the way an editor does.

A *coverage group* is a set of shots that cover the SAME span of scene time: one single per character, each
lip-synced to that character's own lines and listening during the others' lines. Because every single in a group
runs on one clock, the editor may cut between them at any frame and mouths stay in sync. Shots marked
``cutaway`` (inserts, an establishing master) take no time of their own; the cut lays them over the dialogue.

Shot fields used here (all optional, ignored by the classic pipeline):
    cover:     group key; shots with the same key share one start time on the scene clock
    cutaway:   "insert" | "master" - b-roll laid over the dialogue instead of taking its own time
    cue_line:  for a cutaway, the dialogue line it belongs to (shown in the pause before it, or during it)
"""
import math
import os
import re

from . import media, store

CUT_MIN = 0.7        # shortest picture piece, seconds
LONG_LINE = 3.2      # lines longer than this get a second angle (punch-in) half way
J_LEAD = 0.15        # the next speaker's voice leads the picture by this much
L_TAIL = 0.55        # a long line's last moment plays over the listener's reaction
MIN_SPEED = 0.5
PUNCH = {"fit": "cover", "zoom": 1.32, "x": 0.5, "y": 0.4}
BASE = {"fit": "cover", "zoom": 1, "x": 0.5, "y": 0.5}


# ── Converting a shot list into coverage ────────────────────────────────

def _lines(shots):
    out = []
    for sh in shots:
        for d in sh.get("dialogue") or []:
            if (d.get("line") or "").strip():
                out.append(dict(d))
    return out


def segments(lines, voiced, max_seconds, hold=0.8):
    """Split a scene's lines into chunks that fit one clip (pause + speech + hold <= max_seconds)."""
    segs, cur, t = [], [], 0.0
    for d in lines:
        take = voiced.get(d["line"].strip())
        dur = float(take["duration"]) if take else len(re.findall(r"\w+", d["line"])) / 2.4
        need = float(d.get("pause", 0.3)) + dur
        if cur and t + need + hold > max_seconds:
            segs.append(cur); cur, t = [], 0.0
        cur.append(d); t += need
    if cur:
        segs.append(cur)
    return segs


def to_coverage(episode, bible, scene_id, max_seconds):
    """New single shots (two per segment for a two-hander) covering every line of a scene. The original shots are
    left alone; callers replace them. Eyelines follow the scene's master positions (left looks right)."""
    from .lipsync_engine import line_takes
    shots = [s for s in sorted(episode["shots"], key=lambda x: x.get("order", 0))
             if s.get("scene_id") == scene_id and not s.get("cutaway")]
    if not shots:
        raise ValueError(f"{scene_id} has no shots")
    cast, side = [], {}
    for sh in shots:
        for ch in sh.get("characters", []):
            if ch["id"] not in cast:
                cast.append(ch["id"])
    # Screen direction holds for the whole episode: take it from this scene's two-shots first, then any other.
    others = [s for s in episode["shots"] if s.get("scene_id") != scene_id]
    for sh in shots + others:
        if len(sh.get("characters", [])) > 1:
            for ch in sh["characters"]:
                if ch["id"] in cast and ch.get("position") in ("left", "right"):
                    side.setdefault(ch["id"], ch["position"])
    for k, cid in enumerate(cast):
        side.setdefault(cid, "left" if k % 2 == 0 else "right")
    base = shots[0]
    out = []
    segs = segments(_lines(shots), line_takes(episode), max_seconds)
    for n, seg in enumerate(segs, 1):
        for cid in cast:
            c = store.find_character(bible["characters"], cid) or {"name": cid}
            mine = next((s for s in shots if [x["id"] for x in s.get("characters", [])] == [cid]), None)
            src = mine or base
            outfit = next((x.get("outfit") for s in shots for x in s.get("characters", []) if x["id"] == cid), "default")
            others = [store.find_character(bible["characters"], o) or {"name": o} for o in cast if o != cid]
            other = " and ".join((o.get("name") or "").split()[0] for o in others) or "the other person"
            look = "right" if side[cid] == "left" else "left"
            first = (c.get("name") or cid).split()[0]
            speaks = any(d.get("speaker") == cid for d in seg)
            out.append({
                "id": f"{scene_id}_CV{n:02d}_{cid}", "scene_id": scene_id, "cover": f"{scene_id}-{n:02d}",
                "location_id": base.get("location_id"), "time_of_day": base.get("time_of_day"),
                "framing": "medium close-up", "camera": "static, eye level, very subtle handheld breathing",
                "characters": [{"id": cid, "position": "center", "outfit": outfit}],
                "dialogue": [dict(d) for d in seg],
                "action": (f"{first} in a vertical single, face in the upper third of the frame, looking off-camera to "
                           f"frame {look} at {other}, never into the lens. "
                           + (f"{first} {'speaks only the lines given to ' + first + ' and otherwise ' if speaks else ''}listens with small natural reactions "
                              f"while {other} speaks off-screen. ")
                           + (src.get("action", "").split(". ")[0] + "." if src.get("action") else "")),
                "sound": base.get("sound", ""), "action_seconds": 0.8, "duration_locked": False,
                "frame_source": src.get("frame_source", ""), "engine": "ltx_lipsync",
                "image_prompt_extra": (src.get("image_prompt_extra") or "")
                + f" EYELINE: {first} looks off-camera to frame {look}, not at the camera.",
            })
    return out


# ── The cut ──────────────────────────────────────────────────────────────

def _groups(episode, rows):
    """{cover: {"start", "len", "singles": {cid: shot}, "lines": [(cid, at, dur, text)], "scene"}} in time order."""
    groups = {}
    for sh in sorted(episode["shots"], key=lambda x: x.get("order", 0)):
        if sh.get("cutaway") or not sh.get("cover"):
            continue
        r = rows[sh["id"]]
        g = groups.setdefault(sh["cover"], {"start": r["start"], "len": r["len"], "singles": {}, "scene": sh.get("scene_id"),
                                            "lines": [(x[0], x[2], x[3], x[1]) for x in r["lines"]]})
        g["len"] = max(g["len"], r["len"])
        for ch in sh.get("characters", []):
            g["singles"].setdefault(ch["id"], sh)
    return sorted(groups.values(), key=lambda g: g["start"])


def plan_pieces(group, cutaways):
    """Picture plan for one group: [(t0, t1, kind, key, reframe)], times relative to the group start.
    kind "single" -> key is a character id; kind "cutaway" -> key is a shot id (source time runs from 0)."""
    L, lines, singles = group["len"], group["lines"], group["singles"]
    if not singles:
        return []
    cast = list(singles)
    first_speaker = lines[0][0] if lines and lines[0][0] in singles else cast[0]
    plan = []                                  # (t0, kind, key, reframe) change points

    def at(t, kind, key, rf=BASE):
        t = max(0.0, min(t, L), plan[-1][0] if plan else 0.0)     # change points never go back in time
        if plan and abs(plan[-1][0] - t) < 1e-6:
            plan[-1] = (t, kind, key, rf)
        elif not plan or plan[-1][1:] != (kind, key, rf):
            plan.append((t, kind, key, rf))

    at(0, "single", first_speaker)
    for i, (spk, a, dur, text) in enumerate(lines):
        if spk not in singles:
            continue
        b = a + dur
        listener = next((c for c in cast if c != spk), None)
        prev_end = (lines[i - 1][1] + lines[i - 1][2]) if i else 0.0
        cue = next((c for c in cutaways if (c.get("cue_line") or "").strip() == text.strip()), None)
        # A cutaway belongs in a long pause before its line, otherwise in the middle of the line (voice over it).
        if cue and a - prev_end >= 1.4:
            at(prev_end + 0.2, "cutaway", cue["id"])
            cue = None
        at(a + (J_LEAD if i else 0), "single", spk)
        if dur > LONG_LINE:
            at(a + dur / 2, "single", spk, PUNCH)
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if cue:                                   # see the speaker first, then the insert under their voice
            c0 = a + min(1.0, max(CUT_MIN, dur * 0.35))
            c1 = min(b + 0.3, c0 + 2.0)
            if L - c1 < CUT_MIN + 0.3:              # leave room for a face before the group ends
                c1 = max(c0 + 1.0, L - 1.0)
            at(c0, "cutaway", cue["id"])
            back = listener if listener and (not nxt or nxt[0] != spk) else spk
            at(c1, "single", back)
            continue
        if listener and not nxt and L - b >= 1.2:
            at(b + 0.2, "single", listener)             # the scene's last word lands on the listener's face
        elif listener and dur > 2.2 and (not nxt or nxt[0] != spk):
            at(b - L_TAIL, "single", listener)          # L-cut: the line ends over the listener's face
        elif listener and nxt and nxt[1] - b > 1.2:
            at(b + 0.3, "single", listener)             # a held pause plays on the listener
    if lines:
        last_end = lines[-1][1] + lines[-1][2]
        tail = next((c for c in cutaways if not c.get("cue_line") and c.get("cutaway") == "insert"), None)
        if tail and L - last_end >= 1.6:
            at(last_end + 0.3, "cutaway", tail["id"])
    pieces = []
    for k, (t0, kind, key, rf) in enumerate(plan):
        t1 = plan[k + 1][0] if k + 1 < len(plan) else L
        if t1 - t0 > 1e-6:
            pieces.append([t0, t1, kind, key, rf])
    # No flash frames: a piece shorter than CUT_MIN joins its neighbour.
    merged = []
    for p in pieces:
        if merged and (p[1] - p[0] < CUT_MIN or merged[-1][2:] == p[2:]):
            merged[-1][1] = p[1]
        else:
            merged.append(p)
    if len(merged) > 1 and merged[0][1] - merged[0][0] < CUT_MIN:
        merged[1][0] = merged[0][0]; merged.pop(0)
    return [tuple(p) for p in merged]


def build_cut(cfg, episode, sid, eid, fps):
    """Timeline body for a coverage episode: picture cut between the singles, inserts and masters of each group,
    one dialogue lane per speaker. Returns (body, audio assets)."""
    from . import lipsync_engine as le
    rows = {r["id"]: r for r in le.layout(episode, cfg["ltx_lipsync"]["max_seconds"])}
    groups = _groups(episode, rows)
    if not groups:
        raise ValueError("No coverage groups yet: convert a scene to coverage first.")
    shots = {s["id"]: s for s in episode["shots"]}
    cut = [s for s in sorted(episode["shots"], key=lambda x: x.get("order", 0)) if s.get("cutaway")]
    voiced = le.line_takes(episode)
    F = lambda x: round(x * fps)

    def take_of(sh):
        items = sh["takes"].get("items") or []
        if not items:
            raise ValueError(f"{sh['id']} has no take yet")
        t = items[sh["takes"].get("selected") or 0]
        return t["file"], math.floor(media.probe(cfg["ffprobe"], store.abs_media(t["file"]))["duration"] * fps)

    clips, audio, assets, lanes, t_frames = [], [], [], {}, 0
    seen_scene = set()
    for g in groups:
        mine = [c for c in cut if c.get("scene_id") == g["scene"]]
        master = next((c for c in mine if c.get("cutaway") == "master"), None)
        if master and g["scene"] not in seen_scene and master["takes"].get("items"):
            f, avail = take_of(master)
            n = max(1, min(F(float(master.get("duration_s") or 3)), avail - 1))
            clips.append(_clip(master, f, 0, n, fps, k=len(clips))); t_frames += n
        seen_scene.add(g["scene"])
        g_start = t_frames
        used, carry = {}, 0
        for t0, t1, kind, key, rf in plan_pieces(g, [c for c in mine if c.get("cutaway") == "insert"]):
            sh = g["singles"][key] if kind == "single" else shots[key]
            f, avail = take_of(sh)
            n = F(t1) - F(t0)
            if n < 1:
                continue
            if kind == "single":
                # A single runs on the group clock, so it can start early and absorb a short insert before it.
                n += carry
                src_in = max(0, min(F(t0) - carry, avail - n))
                n = min(n, avail - src_in)
                carry = 0
            else:                                  # cutaways play from their start, continuing on reuse
                src_in = used.get(key, 0)
                if src_in + n > avail - 1:
                    src_in = max(0, avail - 1 - n)
                short = max(0, n - (avail - 1 - src_in))
                n -= short; carry += short          # the next single makes up the difference
                used[key] = src_in + n
            if n < 1:
                continue
            clips.append(_clip(sh, f, src_in, n, fps, rf, len(clips)))
            t_frames += n
        if carry and clips:                        # an insert ended the group: hold the last single a little longer
            last = clips[-1]
            last["frames"] += carry; t_frames += carry; carry = 0
        for spk, at, _, text in g["lines"]:
            f = voiced[text.strip()]["file"]
            assets.append(f)
            length = max(1, math.floor(media.probe(cfg["ffprobe"], store.abs_media(f))["duration"] * fps))
            audio.append({"id": f"dlg_{len(audio):03d}", "file": f, "lane": lanes.setdefault(spk, len(lanes)),
                          "source_in": 0, "start_frame": g_start + F(at), "frames": length, "loop": False, "volume": 1,
                          "muted": False, "fade_in": 0, "fade_out": 0, "guide": True})
    if clips:
        clips[-1]["fade_out"] = min(clips[-1]["frames"], round(1.0 * fps))
    return {"clips": clips, "audio": audio}, assets


def _clip(sh, file, src_in, n, fps, rf=BASE, k=0):
    return {"id": f"clip_{k:03d}_{sh['id']}", "shot_id": sh["id"], "source": file, "source_in": round(src_in / fps, 4),
            "frames": int(n), "muted": True, "volume": 1, "lipsync": False, "fade_in": 0, "fade_out": 0, "speed": 1,
            "transition": 0, "reframe": dict(rf)}


def stats(clips, fps):
    secs = [c["frames"] / fps for c in clips]
    return {"shots": len(secs), "mean": sum(secs) / len(secs) if secs else 0, "total": sum(secs)}
