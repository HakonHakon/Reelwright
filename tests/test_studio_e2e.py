"""End-to-end test of Studio with a fake ComfyUI server and a fake Ollama.

    python -m pytest tests/test_studio_e2e.py -q      (needs ffmpeg on PATH)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = tempfile.mkdtemp(prefix="studio_test_")
os.environ["STUDIO_DATA"] = DATA

from studio import llm, settings, workflows  # noqa: E402

LORAS = ["Sam/ohwx_sam_flux1dev_2500.safetensors", "Maryam flux1/ohwx maryam 2750.safetensors",
         "maryam_wan.safetensors", settings.DEFAULTS["wan"]["high_lightning"],
         settings.DEFAULTS["wan"]["low_lightning"], settings.DEFAULTS["ltx"]["id_lora"],
         settings.DEFAULTS["qwen_edit"]["lora"],
         "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors",
         "gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors"]
MODEL_KEYS = ("_name", "clip_name1", "clip_name2", "text_encoder")


def build_object_info():
    s = settings.load()
    graphs = [
        workflows.flux_image(s, "p", 1, 64, 64, 2, [{"name": LORAS[0], "strength": 1}], init_image="x.png"),
        workflows.flux_image(s, "p", 1, 64, 64, 2),
        workflows.zimage_image(s, "p", 1, 64, 64, 2),
        workflows.qwen_edit_image(s, "p", 1, 64, 64, 2, ["a.png", "b.png"]),
        workflows.ltx_i2v(s, "p", "a.png", 9, 1, end_frame="b.png", voice_audio="v.wav"),
        workflows.wan_i2v(s, "p", "a.png", 9, 1, [{"name": "maryam_wan.safetensors", "strength": 1}]),
    ]
    info = {}
    for wf in graphs:
        for node in wf.values():
            req = info.setdefault(node["class_type"], {"input": {"required": {}}})["input"]["required"]
            for k, v in node["inputs"].items():
                if isinstance(v, str):
                    if k.endswith(MODEL_KEYS):
                        opts = req.setdefault(k, [[]])[0]
                        if v not in opts:
                            opts.append(v)
                    else:
                        req.setdefault(k, ["STRING", {}])
    info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"] = [list(LORAS)]
    return info


class FakeComfy(BaseHTTPRequestHandler):
    out_dir = tempfile.mkdtemp(prefix="fake_comfy_out_")
    history = {}
    object_info = None
    prompts = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/system_stats":
            return self._json({"ok": True})
        if u.path == "/object_info":
            return self._json(self.object_info)
        if u.path.startswith("/history/"):
            pid = u.path.split("/")[-1]
            return self._json({pid: self.history[pid]} if pid in self.history else {})
        if u.path == "/view":
            p = os.path.join(self.out_dir, q["subfolder"][0], q["filename"][0])
            with open(p, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
            return
        self._json({}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path == "/prompt":
            wf = json.loads(raw)["prompt"]
            FakeComfy.prompts.append(wf)
            pid = f"p{len(FakeComfy.prompts)}"
            FakeComfy.history[pid] = {"status": {"status_str": "success", "completed": True},
                                      "outputs": self._render(wf, pid)}
            return self._json({"prompt_id": pid})
        if self.path == "/upload/image":
            return self._json({"name": f"up_{len(raw)}.png", "subfolder": "", "type": "input"})
        return self._json({})

    def _render(self, wf, pid):
        sub = "studio"
        os.makedirs(os.path.join(self.out_dir, sub), exist_ok=True)
        classes = {n["class_type"]: n for n in wf.values()}
        if "SaveImage" in classes:
            batch = 1
            for n in wf.values():
                if n["class_type"] == "EmptySD3LatentImage":
                    batch = n["inputs"]["batch_size"]
                if n["class_type"] == "RepeatLatentBatch":
                    batch = n["inputs"]["amount"]
            items = []
            for i in range(batch):
                fn = f"{pid}_{i}.png"
                Image.new("RGB", (96, 54), ((37 * i) % 255, 90, 140)).save(os.path.join(self.out_dir, sub, fn))
                items.append({"filename": fn, "subfolder": sub, "type": "output"})
            return {"20": {"images": items}}
        if "SaveVideo" in classes:
            if "WanImageToVideo" in classes:
                frames, fps, audio = classes["WanImageToVideo"]["inputs"]["length"], 16, False
            else:
                frames, fps, audio = wf["267:225"]["inputs"]["value"], wf["267:260"]["inputs"]["value"], True
            fn = f"{pid}.mp4"
            dur = frames / fps
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=160x90:rate={fps}:duration={dur}"]
            if audio:
                cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-shortest"]
            cmd += ["-pix_fmt", "yuv420p", os.path.join(self.out_dir, sub, fn)]
            subprocess.run(cmd, check=True)
            return {"75": {"images": [{"filename": fn, "subfolder": sub, "type": "output"}], "animated": [True]}}
        return {}


class FakeOllama:
    def __init__(self):
        self.calls = []

    def chat(self, model, messages, format=None, options=None, think=None):
        props = (format or {}).get("properties", {})
        self.calls.append((model, list(props), bool(messages[-1].get("images"))))
        if "characters" in props and "locations" in props:
            out = {"characters": [{"name": "Sam", "appearance": "boy, 10, curly black hair", "outfit": "red hoodie", "voice": "bright"},
                                  {"name": "Maryam", "appearance": "woman, 30, long dark hair", "outfit": "green coat"}],
                   "locations": [{"name": "Rainy street", "description": "neon city street, wet asphalt", "time_of_day": "night"}]}
        elif "scenes" in props:
            out = {"scenes": [{"location_id": "rainy_street", "time_of_day": "night", "summary": "Sam meets Maryam",
                               "characters": [{"id": "sam", "outfit": "default"}, {"id": "maryam", "outfit": "default"}],
                               "beats": ["Sam waits", "Maryam arrives"],
                               "dialogue": [{"speaker": "maryam", "line": "You came back."}]}]}
        elif "shots" in props:
            out = {"shots": [
                {"framing": "wide", "camera": "static", "characters": [{"id": "sam", "position": "left"}],
                 "action": "Sam waits under a streetlight", "dialogue": [], "sound": "rain", "action_seconds": 3,
                 "continuous_with_previous": False},
                {"framing": "two-shot", "camera": "slow push-in",
                 "characters": [{"id": "sam", "position": "left"}, {"id": "maryam", "position": "right"}],
                 "action": "Maryam steps into the light", "dialogue": [{"speaker": "maryam", "line": "You came back."}],
                 "sound": "", "action_seconds": 2, "continuous_with_previous": True},
                {"framing": "close-up", "camera": "static", "characters": [{"id": "sam", "position": "center"}],
                 "action": "Sam smiles", "dialogue": [], "sound": "", "action_seconds": 2, "continuous_with_previous": False}]}
        elif "matches" in props:
            out = {"matches": True, "score": 8, "issues": []}
        else:
            out = {}
        return {"message": {"content": json.dumps(out)}}

    def generate(self, **kw):
        return {}

    def list(self):
        class M:
            def __init__(self, n):
                self.model = n
        return type("R", (), {"models": [M("bonsai-2:latest")]})()


@pytest.fixture(scope="module")
def client():
    FakeComfy.object_info = build_object_info()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "settings.json"), "w") as f:
        json.dump({"comfy_host": f"127.0.0.1:{srv.server_address[1]}", "candidates_per_shot": 2,
                   "takes_per_shot": 1}, f)
    fake = FakeOllama()
    llm._client = lambda host: fake
    from fastapi.testclient import TestClient
    from studio.app import app
    c = TestClient(app)
    c.fake = fake
    yield c
    srv.shutdown()
    shutil.rmtree(DATA, ignore_errors=True)


def wait(c, job):
    from studio import jobs
    j = jobs.QUEUE.jobs[job["id"]]
    t0 = time.time()
    while j.status in ("queued", "running"):
        assert time.time() - t0 < 120, j.log
        time.sleep(0.1)
    assert j.status == "done", "\n".join(j.log)
    return j


def test_full_pipeline(client):
    c = client
    assert c.get("/").status_code == 200
    st = c.get("/api/status").json()
    assert st["comfy"] and not st["missing"], st

    sid = c.post("/api/series", json={"name": "Sam"}).json()["id"]
    eid = c.post(f"/api/s/{sid}/episodes", json={"title": "Pilot"}).json()["id"]
    story = "EXT. RAINY STREET - NIGHT\nSam waits. Maryam arrives.\nMARYAM: You came back."
    c.put(f"/api/s/{sid}/e/{eid}/story", json={"story": story})

    # Bible: draft from story, then set LoRAs, voice and a video LoRA
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/draft_bible").json())
    bible = c.get(f"/api/s/{sid}").json()["bible"]
    assert set(bible["characters"]) == {"sam", "maryam"} and "rainy_street" in bible["locations"]
    c.put(f"/api/s/{sid}/characters/sam", json={"trigger": "ohwx_sam", "image_lora": {"name": LORAS[0], "strength": 0.9}})
    c.put(f"/api/s/{sid}/characters/maryam", json={"trigger": "ohwx maryam", "image_lora": {"name": LORAS[1], "strength": 0.9},
                                                   "video_lora": {"name": "maryam_wan.safetensors", "strength": 0.8}})
    wav = os.path.join(DATA, "voice_src.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=5", wav], check=True)
    with open(wav, "rb") as f:
        r = c.post(f"/api/s/{sid}/characters/maryam/upload", data={"kind": "voice"}, files={"file": ("v.wav", f)})
    assert r.json()["voice"]["file"].endswith("voice.wav")

    # Character sheet + location plates
    wait(c, c.post(f"/api/s/{sid}/characters/sam/sheet", json={"source": "flux_lora"}).json())
    sam = c.get(f"/api/s/{sid}").json()["bible"]["characters"]["sam"]
    assert len(sam["sheet"]) == 4
    c.post(f"/api/s/{sid}/characters/sam/refs", json={"add": sam["sheet"][0]["file"]})
    wait(c, c.post(f"/api/s/{sid}/locations/rainy_street/plates", json={"variant": "night", "source": "zimage"}).json())
    loc = c.get(f"/api/s/{sid}").json()["bible"]["locations"]["rainy_street"]
    c.post(f"/api/s/{sid}/locations/rainy_street/approve", json={"file": loc["candidates"][0]["file"], "variant": "night"})

    # Plan
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/plan").json())
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    assert [s["id"] for s in e["shots"]] == ["S01_SH01", "S01_SH02", "S01_SH03"]
    assert e["shots"][1]["start_mode"] == "chain"
    assert "ohwx_sam" in e["shots"][0]["_image_prompt"] and "You came back" in e["shots"][1]["_motion_prompt"]
    assert e["shots"][0]["_source"] == "flux_lora"
    assert [x["start_mode"] for x in e["shots"]] == ["new", "chain", "punch_in"], "continuity pass"

    # Edit shot list: shot 3 uses Wan, Z-Image, and an extra; add a 4th shot (reuse)
    shots = e["shots"]
    shots[2].update(engine="wan", image_prompt_extra="rain drops on lens")
    shots.append({"id": "", "scene_id": "S01", "location_id": "rainy_street", "framing": "insert",
                  "characters": [], "action": "a puddle ripples", "dialogue": [], "start_mode": "reuse"})
    e = c.put(f"/api/s/{sid}/e/{eid}/shots", json=shots).json()
    assert len(e["shots"]) == 4 and e["shots"][3]["id"] == "S01_SH04"
    assert "rain drops on lens" in e["shots"][2]["_image_prompt"]
    c.post(f"/api/s/{sid}/e/{eid}/gate", json={"gate": "shots", "value": True})

    # Frames (vision check runs after), then select
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/frames", json={}).json())
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    sh1, sh3 = e["shots"][0], e["shots"][2]
    assert len(sh1["frame"]["candidates"]) == 2 and sh1["frame"]["candidates"][0]["check"]["score"] == 8
    assert not sh3["frame"]["candidates"], "punch-in waits for the master frame to be selected"
    flux_wfs = [w for w in FakeComfy.prompts if any(n["class_type"] == "FluxGuidance" for n in w.values())]
    assert any(n["inputs"].get("lora_name") == LORAS[0] for w in flux_wfs for n in w.values())
    on_plate = [w for w in flux_wfs if "40" in w and abs(w["8"]["inputs"]["denoise"] - 0.82) < 1e-6]
    assert on_plate, "wide flux shots should start from the approved location plate"
    assert sh1["frame"]["candidates"][0]["plate"]
    c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH01/frame", json={"select": 1})
    n_before = len(FakeComfy.prompts)
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/frames", json={}).json())
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    sh3 = e["shots"][2]
    assert sh3["frame"]["candidates"][0]["source"] == "punch_in"
    assert sh3["frame"]["candidates"][0]["from"] == e["shots"][0]["frame"]["candidates"][1]["file"]
    punch = [w for w in FakeComfy.prompts[n_before:] if "40" in w and abs(w["8"]["inputs"]["denoise"] - 0.32) < 1e-6]
    assert punch, "punch-in should be img2img from the cropped master"
    c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH03/frame", json={"select": 0})

    # Upload with crop for shot 2's end frame, refine, then reuse for shot 4
    img = os.path.join(DATA, "up.png")
    Image.new("RGB", (1200, 1200), (200, 50, 50)).save(img)
    with open(img, "rb") as f:
        e = c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH02/upload_frame",
                   files={"file": ("my.png", f)}, data={"crop": json.dumps({"x": 0, "y": 0.2, "w": 1, "h": 0.6}), "end": "1"}).json()
    assert e["shots"][1]["end_frame"].endswith(".png")
    with Image.open(os.path.join(DATA, e["shots"][1]["end_frame"])) as im:
        assert im.size == (1344, 768)
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH01/refine", json={"index": 1, "engine": "flux_lora", "denoise": 0.3}).json())
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/frames", json={"shot_ids": ["S01_SH04"], "force": True}).json())
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    assert e["shots"][0]["frame"]["candidates"][-1]["source"] == "refine:flux_lora"
    assert e["shots"][3]["frame"]["candidates"][0]["source"] == "reuse"
    assert e["shots"][3]["frame"]["candidates"][0]["from"] == e["shots"][0]["frame"]["candidates"][1]["file"]
    c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH04/frame", json={"select": 0})

    # Bulk upload by shot id
    with open(img, "rb") as f1, open(img, "rb") as f2:
        r = c.post(f"/api/s/{sid}/e/{eid}/bulk_upload", files=[("files", ("S01_SH03.png", f1)), ("files", ("nope.png", f2))]).json()
    assert r["matched"] == ["S01_SH03"] and r["skipped"] == ["nope.png"]

    # Stale detection after a bible edit
    c.put(f"/api/s/{sid}/characters/sam", json={"appearance": "boy, 10, curly black hair, freckles"})
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    assert e["shots"][0]["_stale"] is True

    # Animatic
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/animatic").json())

    # Takes: LTX (with chain + end frame + voice) and Wan
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/takes", json={}).json())
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    for sh in e["shots"]:
        assert sh["takes"]["items"], sh["id"]
    assert e["shots"][1]["frame"]["candidates"][-1]["source"] == "chain"
    ltx_voice = [w for w in FakeComfy.prompts if "292" in w]
    assert ltx_voice and "284" in ltx_voice[-1], "voice reference + end frame guide expected on shot 2"
    wan = [w for w in FakeComfy.prompts if any(n["class_type"] == "WanImageToVideo" for n in w.values())]
    assert wan and any(n["inputs"].get("lora_name") == "maryam_wan.safetensors" for n in wan[0].values()) is False
    assert e["stats"]["spf"].get("ltx") is not None and e["stats"]["spf"].get("wan") is not None
    c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH01/take", json={"trim_in": 0.2, "trim_out": 0.1})
    r = c.post(f"/api/s/{sid}/e/{eid}/shots/S01_SH03/next_from_last").json()
    assert r["shots"][3]["frame"]["candidates"][-1]["source"] == "chain"

    # Cut with music
    music = os.path.join(DATA, "music.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=330:d=3", music], check=True)
    with open(music, "rb") as f:
        c.post(f"/api/s/{sid}/e/{eid}/music", files={"file": ("m.mp3", f)}, data={"volume": "0.3", "duck": "true"})
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/cut").json())
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    final = os.path.join(DATA, e["outputs"]["final"])
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", final],
                         capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert abs(float(info["format"]["duration"]) - e["outputs"]["runtime"]) < 0.5
    srt = open(os.path.join(DATA, e["outputs"]["srt"]), encoding="utf-8").read()
    assert "Maryam: You came back." in srt
    edl = open(os.path.join(DATA, e["outputs"]["edl"]), encoding="utf-8").read()
    assert "FROM CLIP NAME: S01_SH01_t001.mp4" in edl


def test_validation_error_is_clear(client):
    from studio import comfy, settings as st
    s = st.load()
    wf = workflows.flux_image(s, "p", 1, 64, 64, 1)
    wf["1"]["inputs"]["unet_name"] = "missing_model.safetensors"
    with pytest.raises(comfy.ComfyError) as ei:
        comfy.Comfy(s["comfy_host"]).run(wf)
    assert "missing_model.safetensors" in str(ei.value)


def test_anchor_end_rules():
    from studio import jobs
    s = {"continuity": {"anchor_end": True}}
    assert jobs._anchor_end(s, {"start_mode": "punch_in", "framing": "medium", "camera": ""})
    assert jobs._anchor_end(s, {"start_mode": "new", "framing": "close-up", "camera": "static camera"})
    assert not jobs._anchor_end(s, {"start_mode": "punch_in", "framing": "close-up", "camera": "slow push in"})
    assert not jobs._anchor_end(s, {"start_mode": "new", "framing": "wide", "camera": ""})
    assert not jobs._anchor_end({"continuity": {"anchor_end": False}}, {"start_mode": "punch_in"})


def test_reference_photo_source(client):
    """Characters with a photo but no LoRA render through Qwen Edit ('auto'/'qwen_refs')."""
    c = client
    sid = c.post("/api/series", json={"name": "Photos"}).json()["id"]
    eid = c.post(f"/api/s/{sid}/episodes", json={"title": "Ep"}).json()["id"]
    ids = []
    for name in ("Amin", "Mona"):
        cid = c.post(f"/api/s/{sid}/characters", json={"name": name}).json()["id"]
        img = os.path.join(DATA, f"{cid}.png")
        Image.new("RGB", (400, 600), (90, 60, 40)).save(img)
        with open(img, "rb") as f:
            c.post(f"/api/s/{sid}/characters/{cid}/upload", data={"kind": "ref"}, files={"file": (f"{cid}.png", f)})
        c.put(f"/api/s/{sid}/characters/{cid}", json={"appearance": f"{name}, 30 years old"})
        ids.append(cid)
    shots = [{"id": "", "scene_id": "S01", "framing": "wide", "action": "they cook",
              "characters": [{"id": ids[0], "position": "left"}, {"id": ids[1], "position": "right"}],
              "dialogue": [], "start_mode": "new", "frame_source": "auto"}]
    e = c.put(f"/api/s/{sid}/e/{eid}/shots", json=shots).json()
    assert e["shots"][0]["_source"] == "auto"
    n0 = len(FakeComfy.prompts)
    wait(c, c.post(f"/api/s/{sid}/e/{eid}/frames", json={}).json())
    wfs = FakeComfy.prompts[n0:]
    qwen = [w for w in wfs if any(n["class_type"] == "TextEncodeQwenImageEditPlus" for n in w.values())]
    assert qwen, "no Qwen Edit graph was queued"
    te = next(n for n in qwen[0].values() if n["class_type"] == "TextEncodeQwenImageEditPlus")["inputs"]
    assert "image1" in te and "image2" in te
    assert te["prompt"].startswith("Image 1 is Amin. Image 2 is Mona.")
    e = c.get(f"/api/s/{sid}/e/{eid}").json()
    cand = e["shots"][0]["frame"]["candidates"][0]
    assert cand["source"] == "qwen_refs" and len(cand["refs"]) == 2
