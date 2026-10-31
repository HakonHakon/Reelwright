"""Correct character identity or restore damaged faces using local models."""
import math
import os
import subprocess
import sys
import tempfile
import uuid

from . import media


def match_identity(job, settings, source, destination, character):
    """Replace the take's face using the character's actual uploaded portraits."""
    from . import comfy, store, workflows

    refs = [store.abs_media(path) for path in character.get("refs", [])
            if path and os.path.isfile(store.abs_media(path))][:9]
    if not refs:
        raise ValueError("Upload a reference photo for this character in Characters first")
    info = media.probe(settings["ffprobe"], source)
    if not 0 < info["duration"] <= 15:
        raise ValueError("Character reference correction supports takes up to 15 seconds")
    if info["fps"] <= 0:
        raise ValueError("Cannot determine source frame rate")
    scale = min(1.0, math.sqrt(448 * 800 / (info["width"] * info["height"])))
    width = max(32, round(info["width"] * scale / 32) * 32)
    height = max(32, round(info["height"] * scale / 32) * 32)
    frames = workflows.minimax_h3_frames(max(2, info["duration"]))
    tags = ", ".join(f"<Picture {i + 1}>" for i in range(len(refs)))
    prompt = (f"subject_definitions:\n<Subject 1> is {character['name']}, the adult person in {tags}. "
              f"Their appearance: {character.get('appearance', '')}\n"
              "<Video 1> is the source performance to edit.\n\n"
              "summary:\n[video editing + character replacement] Replace the person in <Video 1> "
              "with <Subject 1>, using the portraits for their complete facial identity.\n\n"
              "retention_analysis:\n<Subject 1>: fully_preserved - preserve their unique face shape, "
              "eyes, nose, lips, skin texture, age, hairstyle and distinguishing features from the portraits.\n"
              "<Video 1>: partially_preserved - preserve the scene, framing, body movements, pose, "
              "gaze, expression, foreground objects and lighting. Replace the face and head using the portraits.\n\n"
              "detailed_description:\nFollow the source performance and camera continuously. "
              "The person remains consistently recognizable as the reference character throughout. "
              "Keep the original scene and its foreground, without adding objects, shots or people.\n\n"
              "overall_soundscape:\nThe original source soundtrack will be retained separately.\n"
              "non_diegetic_music:\nNo additional music.")
    client = comfy.Comfy(settings["comfy_host"])
    with tempfile.TemporaryDirectory(prefix="reelwright_identity_") as work:
        prepared = os.path.join(work, "source.mp4")
        media._run([settings["ffmpeg"], "-y", "-loglevel", "error", "-i", source, "-an", "-vf",
                    f"fps=24,scale={width}:{height},tpad=stop_mode=clone:stop_duration=2",
                    "-frames:v", str(frames), "-c:v", "libx264", "-crf", "15", prepared])
        job.check()
        token = uuid.uuid4().hex
        video = client.upload(prepared, f"identity_{token}.mp4")
        images = [client.upload(path, f"identity_{token}_{i}{os.path.splitext(path)[1]}")
                  for i, path in enumerate(refs)]
        wf = workflows.minimax_h3_reference_edit(settings, prompt, images, video, frames,
                                                int(token[:8], 16), width, height)
        job.say(f"Matching {character['name']} from {len(refs)} uploaded portrait(s) with local MiniMax H3")
        result = client.run(wf, cancel=lambda: job.cancel_flag)
        if not result["videos"]:
            raise RuntimeError("Character correction produced no video")
        rendered = client.download(result["videos"][0], os.path.join(work, "rendered.mp4"))
        job.check()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        media._run([settings["ffmpeg"], "-y", "-loglevel", "error", "-i", rendered, "-i", source,
                    "-map", "0:v:0", "-map", "1:a:0?", "-vf",
                    f"scale={info['width']}:{info['height']},fps={info['fps']},setsar=1",
                    "-t", str(info["duration"]), "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", destination])
    return destination


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
