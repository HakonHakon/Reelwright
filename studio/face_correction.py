"""Run installed upstream face restorers in their own Python environment."""
import os
import subprocess
import sys
import tempfile

from . import media


def restore(job, settings, source, destination, model):
    cfg = settings["face_correction"]
    directory = os.path.abspath(cfg.get(f"{model}_dir") or ".")
    script = os.path.join(directory, "inference_keep.py" if model == "keep" else "inference_codeformer.py")
    if not cfg.get(f"{model}_dir") or not os.path.isfile(script):
        raise ValueError(f"Install {model.upper()} and set face_correction.{model}_dir in Models first")
    info = media.probe(settings["ffprobe"], source)
    if info["fps"] <= 0:
        raise ValueError("Cannot determine source frame rate")
    with tempfile.TemporaryDirectory(prefix="reelwright_face_") as output:
        cmd = [cfg.get("python") or sys.executable, script, "-i", os.path.abspath(source),
               "-o", output, "-s", "1", "--save_video_fps", str(info["fps"])]
        if model == "keep":
            cmd += ["--save_video"]
        else:
            cmd += ["-w", str(max(0, min(1, float(cfg["fidelity"]))))]
        job.say(f"Restoring with {model.upper()}; preserving source frame rate and audio")
        # A file avoids PIPE deadlocks while allowing cancellation during long GPU inference.
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(cmd, cwd=directory, stdout=log, stderr=log)
            try:
                while True:
                    job.check()
                    try:
                        code = process.wait(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if code:
                log.seek(0)
                raise RuntimeError(f"{model.upper()} failed: {log.read()[-2000:]}")
        videos = [os.path.join(root, name) for root, _, names in os.walk(output)
                  for name in names if name.lower().endswith(".mp4")]
        if len(videos) != 1:
            raise RuntimeError(f"{model.upper()} returned no unambiguous restored video")
        job.check()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        # KEEP does not retain audio; remux from the original for either restorer.
        media._run([settings["ffmpeg"], "-y", "-i", videos[0], "-i", source,
                    "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac",
                    "-movflags", "+faststart", destination])
        result = media.probe(settings["ffprobe"], destination)
        if abs(result["duration"] - info["duration"]) > max(0.15, 2 / info["fps"]):
            raise RuntimeError("Face correction changed the clip duration; original take retained")
    return destination
