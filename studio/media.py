"""ffmpeg / image helpers: fitting uploads, last frames, QC, animatic, final cut, SRT, EDL."""

import json
import os
import subprocess
import tempfile

from PIL import Image


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str]) -> str:
    kwargs = {"capture_output": True, "text": True}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, **kwargs)
    if p.returncode != 0:
        raise MediaError(f"{os.path.basename(cmd[0])} failed: {p.stderr[-1500:]}")
    return p.stdout


def probe(ffprobe: str, path: str) -> dict:
    out = _run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path])
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = 0.0
    if v.get("r_frame_rate") and v["r_frame_rate"] != "0/0":
        n, d = v["r_frame_rate"].split("/")
        fps = float(n) / float(d or 1)
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
        "width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "fps": fps,
    }


# ── Images ──────────────────────────────────────────────────────────────

def fit_image(src: str, dst: str, width: int, height: int, crop: dict | None = None) -> str:
    """Crop to the target aspect (centre, or a crop box in 0-1 fractions) and resize."""
    im = Image.open(src).convert("RGB")
    W, H = im.size
    target = width / height
    if crop and all(k in crop for k in ("x", "y", "w", "h")):
        box = (int(crop["x"] * W), int(crop["y"] * H), int((crop["x"] + crop["w"]) * W), int((crop["y"] + crop["h"]) * H))
        im = im.crop(box)
        W, H = im.size
    if W / H > target:
        nw = int(H * target)
        im = im.crop(((W - nw) // 2, 0, (W - nw) // 2 + nw, H))
    else:
        nh = int(W / target)
        im = im.crop((0, (H - nh) // 2, W, (H - nh) // 2 + nh))
    im = im.resize((width, height), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst)
    return dst


POSITION_X = {"left": 0.32, "center": 0.5, "right": 0.68, "background": 0.5, "foreground": 0.5}


def punch_crop(src: str, dst: str, width: int, height: int, fraction: float, position: str = "center",
               y_center: float = 0.42) -> str:
    """Crop a closer framing out of a master frame (same aspect), centred on the character's screen side."""
    im = Image.open(src).convert("RGB")
    W, H = im.size
    cw = max(16, int(W * fraction))
    ch = int(cw * height / width)
    if ch > H:
        ch = H
        cw = int(ch * width / height)
    cx = POSITION_X.get((position or "center").lower(), 0.5) * W
    cy = y_center * H
    x0 = int(min(max(0, cx - cw / 2), W - cw))
    y0 = int(min(max(0, cy - ch / 2), H - ch))
    im = im.crop((x0, y0, x0 + cw, y0 + ch)).resize((width, height), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst)
    return dst


def last_frame(ffmpeg: str, video: str, dst: str) -> str:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    _run([ffmpeg, "-y", "-sseof", "-0.25", "-i", video, "-frames:v", "1", "-update", "1", dst])
    if not os.path.exists(dst):
        raise MediaError(f"could not extract last frame of {video}")
    return dst


def middle_frame(ffmpeg: str, video: str, dst: str, seconds: float) -> str:
    _run([ffmpeg, "-y", "-ss", f"{max(0.0, seconds / 2):.2f}", "-i", video, "-frames:v", "1", dst])
    return dst


# ── QC ──────────────────────────────────────────────────────────────────

def qc_video(path: str, expected_seconds: float, actual_seconds: float) -> list[str]:
    """Cheap automatic flags: black, frozen, wrong length."""
    flags = []
    if expected_seconds and abs(actual_seconds - expected_seconds) > 0.6:
        flags.append(f"length {actual_seconds:.1f}s, expected {expected_seconds:.1f}s")
    try:
        import cv2
        import numpy as np
    except ImportError:
        return flags
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if n <= 0:
        cap.release()
        return flags + ["unreadable video"]
    idxs = sorted({int(i * (n - 1) / 11) for i in range(12)})
    frames = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if ok:
            frames.append(cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (160, 90)).astype("float32"))
    cap.release()
    if not frames:
        return flags + ["unreadable video"]
    if np.mean([f.mean() for f in frames]) < 10:
        flags.append("mostly black")
    diffs = [float(np.abs(a - b).mean()) for a, b in zip(frames, frames[1:])]
    if diffs and max(diffs) < 1.5:
        flags.append("frozen / almost no motion")
    # A take that jumps to another picture (LTX sometimes cuts mid-shot) breaks continuity.
    if diffs:
        base = sorted(diffs)[len(diffs) // 2] or 1.0
        if max(diffs) > 35 and max(diffs) > 4 * base:
            flags.append("cut / jump inside the take")
    if len(frames) > 2 and float(np.abs(frames[0] - frames[-1]).mean()) > 45:
        flags.append("ends far from the first frame (drift)")
    return flags


# ── Cut ─────────────────────────────────────────────────────────────────

def normalize_clip(ffmpeg: str, ffprobe: str, src: str, dst: str, trim_in: float, trim_out: float,
                   width: int, height: int, fps: int, lufs: float) -> float:
    """Re-encode one take to the common format (size, fps, stereo 48 kHz, loudness). Returns duration."""
    info = probe(ffprobe, src)
    dur = max(0.2, info["duration"] - float(trim_in or 0) - float(trim_out or 0))
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p")
    cmd = [ffmpeg, "-y", "-ss", f"{float(trim_in or 0):.3f}", "-t", f"{dur:.3f}", "-i", src]
    if info["has_audio"]:
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-af",
                f"loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", dst]
    _run(cmd)
    return probe(ffprobe, dst)["duration"]


def concat(ffmpeg: str, clips: list[str], dst: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for c in clips:
            f.write("file '" + os.path.abspath(c).replace("\\", "/").replace("'", "'\\''") + "'\n")
        listfile = f.name
    try:
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy",
              "-movflags", "+faststart", dst])
    finally:
        os.unlink(listfile)
    return dst


def add_music(ffmpeg: str, video: str, music: str, dst: str, volume: float, duck: bool) -> str:
    if duck:
        fc = (f"[0:a]asplit=2[dlg][sc];[1:a]volume={volume}[m];"
              f"[m][sc]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[md];"
              f"[dlg][md]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc = f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]"
    _run([ffmpeg, "-y", "-i", video, "-stream_loop", "-1", "-i", music, "-filter_complex", fc,
          "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", dst])
    return dst


def animatic(ffmpeg: str, items: list[tuple[str, float]], dst: str, width: int, height: int, fps: int) -> str:
    """Still frames held for each shot's duration (no audio)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for path, dur in items:
            f.write("file '" + os.path.abspath(path).replace("\\", "/") + "'\n")
            f.write(f"duration {max(0.1, dur):.3f}\n")
        if items:
            f.write("file '" + os.path.abspath(items[-1][0]).replace("\\", "/") + "'\n")
        listfile = f.name
    try:
        _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
              "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p",
              "-c:v", "libx264", "-crf", "20", dst])
    finally:
        os.unlink(listfile)
    return dst


# ── Text exports ────────────────────────────────────────────────────────

def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(entries: list[tuple[float, float, str]]) -> str:
    """entries: (start, end, text)."""
    out = []
    for i, (a, b, text) in enumerate(entries, 1):
        out.append(f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{text}\n")
    return "\n".join(out)


def _tc(seconds: float, fps: int) -> str:
    f = int(round(seconds * fps))
    h, f = divmod(f, 3600 * fps)
    m, f = divmod(f, 60 * fps)
    s, f = divmod(f, fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def edl(title: str, events: list[dict], fps: int) -> str:
    """CMX3600. events: {clip, src_in, duration}. Resolve relinks by clip name."""
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    rec = 0.0
    for i, ev in enumerate(events, 1):
        sin, dur = float(ev["src_in"]), float(ev["duration"])
        lines.append(f"{i:03d}  AX       B     C        {_tc(sin, fps)} {_tc(sin + dur, fps)} "
                     f"{_tc(rec, fps)} {_tc(rec + dur, fps)}")
        lines.append(f"* FROM CLIP NAME: {ev['clip']}")
        lines.append("")
        rec += dur
    return "\n".join(lines)
