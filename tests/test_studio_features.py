"""Regression coverage for recoverable deletion, model routing and take restoration."""
import copy
import os

import pytest
from fastapi.testclient import TestClient

from studio import jobs, planner, providers, settings, store
from studio.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    return TestClient(app)


def project(client):
    sid = client.post("/api/series", json={"name": "Film"}).json()["id"]
    eid = client.post(f"/api/s/{sid}/episodes", json={"title": "Pilot"}).json()["id"]
    sh = planner.new_shot(id="S01_SH01", order=0)
    sh["frame"]["candidates"] = [{"file": "first.png", "source": "upload"}]
    sh["frame"]["selected"] = 0
    sh["takes"]["items"] = [{"file": f"take{i}.mp4", "engine": "ltx"} for i in range(4)]
    sh["takes"]["selected"] = 2
    sh["takes"]["locked"] = True
    store.update_episode(sid, eid, lambda e: e["shots"].append(sh))
    return sid, eid, sh


def test_project_and_episode_deletion_is_recoverable(client):
    sid, eid, _ = project(client)
    base = f"/api/s/{sid}"
    assert client.delete(f"{base}/e/{eid}").status_code == 200
    assert client.get(base).json()["episodes"] == []
    assert any(x.startswith(".deleted_") for x in os.listdir(os.path.join(store.series_dir(sid), "episodes")))
    assert client.delete(base).status_code == 200
    assert client.get("/api/series").json() == []
    assert client.get(base).status_code == 404
    assert any(x.startswith(".deleted_") for x in os.listdir(store.root()))


def test_deleted_bible_entries_do_not_reappear(client):
    sid, _, _ = project(client)
    for collection, name in (("characters", "Sam"), ("locations", "Kitchen")):
        entry = client.post(f"/api/s/{sid}/{collection}", json={"name": name}).json()
        assert client.delete(f"/api/s/{sid}/{collection}/{entry['id']}").status_code == 200
        assert client.get(f"/api/s/{sid}").json()["bible"][collection] == {}


def test_bulk_delete_keeps_selection_and_invalidates_reviews(client):
    sid, eid, sh = project(client)
    base = f"/api/s/{sid}/e/{eid}"
    def approve(e):
        e["gates"]["takes"] = True
        e["outputs"] = {"final": "old.mp4"}
    store.update_episode(sid, eid, approve)
    response = client.post(f"{base}/delete_assets", json={"kind": "take", "items": {sh["id"]: [0, 3]}})
    assert response.status_code == 200
    episode = response.json()
    takes = episode["shots"][0]["takes"]
    assert [x["file"] for x in takes["items"]] == ["take1.mp4", "take2.mp4"]
    assert takes["selected"] == 1 and not takes["locked"]
    assert not episode["gates"]["takes"] and not episode["outputs"]
    before = copy.deepcopy(store.get_episode(sid, eid))
    assert client.post(f"{base}/delete_assets", json={"kind": "take", "items": {sh["id"]: [9]}}).status_code == 400
    assert store.get_episode(sid, eid) == before
    client.post(f"{base}/delete_assets", json={"kind": "take", "items": {sh["id"]: [1]}})
    assert store.get_episode(sid, eid)["shots"][0]["takes"]["selected"] is None


def test_deletion_rejects_active_job(client, monkeypatch):
    sid, eid, _ = project(client)
    monkeypatch.setattr(jobs.QUEUE, "list", lambda: [{"status": "running", "params": {"sid": sid, "eid": eid}}])
    assert client.delete(f"/api/s/{sid}").status_code == 409
    assert client.delete(f"/api/s/{sid}/e/{eid}").status_code == 409
    assert store.get_series(sid)


def test_model_validation_and_override_submission(client, monkeypatch):
    sid, eid, _ = project(client)
    submitted = []
    monkeypatch.setattr("studio.app._submit", lambda *a, **kw: submitted.append(kw) or {"id": "test"})
    url = f"/api/s/{sid}/e/{eid}/takes"
    assert client.post(url, json={"engine": "unknown"}).status_code == 400
    assert client.post(url, json={"engine": "wan", "model": "MiniMax-Hailuo-2.3"}).status_code == 400
    assert client.post(url, json={"takes": 9}).status_code == 400
    assert client.post(url, json={"engine": "minimax", "model": "MiniMax-Hailuo-2.3-Fast"}).status_code == 200
    assert submitted[-1]["engine_override"] == "minimax"
    assert submitted[-1]["model_override"] == "MiniMax-Hailuo-2.3-Fast"
    assert client.put(f"/api/s/{sid}/style", json={"default_engine": "unknown"}).status_code == 400


def test_minimax_routes_without_comfy(client, monkeypatch):
    sid, eid, sh = project(client)
    monkeypatch.setattr(jobs, "_gpu_prepare", lambda *a: pytest.fail("Cloud rendering must not require ComfyUI"))
    monkeypatch.setattr(jobs, "_first_frame_for", lambda *a: "/tmp/first.png")
    calls = []
    monkeypatch.setattr(providers, "minimax_generate", lambda *a: calls.append(a[-1]) or (os.path.join(settings.DATA_ROOT, "cloud.mp4"), 2, a[-1]))
    monkeypatch.setattr(jobs.media, "probe", lambda *a: {"duration": 6, "fps": 24})
    monkeypatch.setattr(jobs.media, "qc_video", lambda *a: [])
    job = jobs.Job("takes", "test", None, {})
    jobs.job_takes(job, sid, eid, force=True, takes=1, engine_override="minimax", model_override="MiniMax-Hailuo-2.3-Fast")
    assert calls == ["MiniMax-Hailuo-2.3-Fast"]
    item = store.get_episode(sid, eid)["shots"][0]["takes"]["items"][-1]
    assert item["model"] == "MiniMax-Hailuo-2.3-Fast"
    assert item["engine"] == "minimax"


def test_face_correction_adds_take_without_replacing_original(client, monkeypatch):
    from studio import face_correction
    sid, eid, sh = project(client)
    monkeypatch.setattr(jobs.comfy_mod.Comfy, "alive", lambda *a: False)
    monkeypatch.setattr(jobs.llm, "unload", lambda *a: None)
    restored = []
    monkeypatch.setattr(face_correction, "restore", lambda *a: restored.append(a))
    job = jobs.Job("face_correction", "test", None, {})
    jobs.job_face_correction(job, sid, eid, sh["id"], "take2.mp4", "keep")
    takes = store.get_episode(sid, eid)["shots"][0]["takes"]
    assert len(takes["items"]) == 5 and takes["selected"] == 2
    assert takes["items"][2]["file"] == "take2.mp4"
    assert takes["items"][-1]["source_take"] == "take2.mp4"
    assert takes["items"][-1]["face_correction"] == "keep"
    assert len(restored) == 1


def test_minimax_async_download(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-secret")
    image = tmp_path / "first.png"
    image.write_bytes(b"image")
    calls = []
    responses = iter([{"task_id": "123"}, {"status": "Success", "file_id": "456"}, {"file": {"download_url": "https://cdn.example/video.mp4"}}])
    class Response:
        ok = True
        def json(self): return next(responses)
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()
    monkeypatch.setattr(providers.requests, "request", request)
    class Download:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_content(self, *a): return iter([b"video"])
    monkeypatch.setattr(providers.requests, "get", lambda *a, **kw: Download())
    job = jobs.Job("takes", "test", None, {})
    path, _, model = providers.minimax_generate(job, settings.load(), str(image), "move", 9, str(tmp_path))
    assert open(path, "rb").read() == b"video"
    assert calls[0][2]["json"]["duration"] == 10
    assert calls[0][2]["json"]["first_frame_image"].startswith("data:image/png;base64,")
    assert calls[1][2]["params"] == {"task_id": "123"}
    assert calls[2][2]["params"] == {"file_id": "456"}
    assert model == "MiniMax-Hailuo-2.3"
    assert "test-secret" not in "".join(job.log)


@pytest.mark.parametrize("model", ["keep", "codeformer"])
def test_restorer_preserves_audio_and_rate(client, tmp_path, model):
    import shutil
    import subprocess
    import sys
    from studio import face_correction, media
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("requires ffmpeg and ffprobe")
    source = tmp_path / "original.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:r=24:d=0.5", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.5", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    # A fake upstream runner produces silent video, as KEEP does. The adapter must restore audio.
    script = tmp_path / ("inference_keep.py" if model == "keep" else "inference_codeformer.py")
    script.write_text('''import sys, pathlib, subprocess
args = sys.argv
source = args[args.index("-i")+1]
out = pathlib.Path(args[args.index("-o")+1])
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", source, "-an", "-c:v", "copy", str(out/"restored.mp4")], check=True)
''')
    cfg = settings.load()
    cfg["face_correction"].update({f"{model}_dir": str(tmp_path), "python": sys.executable})
    destination = str(tmp_path / "corrected.mp4")
    face_correction.restore(jobs.Job("face", "test", None, {}), cfg, str(source), destination, model)
    before, after = media.probe("ffprobe", str(source)), media.probe("ffprobe", destination)
    assert after["has_audio"]
    assert before["fps"] == after["fps"] == 24
    assert abs(before["duration"] - after["duration"]) < 0.1


def test_invalid_individual_removal_returns_client_error(client):
    sid, eid, sh = project(client)
    for kind in ("frame", "take"):
        response = client.post(f"/api/s/{sid}/e/{eid}/shots/{sh['id']}/{kind}", json={"remove": 99})
        assert response.status_code == 400


def test_minimax_missing_key_never_submits(client, monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(providers.requests, "request", lambda *a, **kw: pytest.fail("Must not submit without credentials"))
    with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
        providers.minimax_generate(jobs.Job("takes", "test", None, {}), settings.load(), "unused", "move", 6, settings.DATA_ROOT)


def test_minimax_failed_task_does_not_download(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-secret")
    image = tmp_path / "first.png"
    image.write_bytes(b"image")
    responses = iter([{"task_id": "123"}, {"status": "Fail"}])
    class Response:
        ok = True
        def json(self): return next(responses)
    monkeypatch.setattr(providers.requests, "request", lambda *a, **kw: Response())
    monkeypatch.setattr(providers.requests, "get", lambda *a, **kw: pytest.fail("Must not download a failed task"))
    with pytest.raises(RuntimeError, match="generation failed"):
        providers.minimax_generate(jobs.Job("takes", "test", None, {}), settings.load(), str(image), "move", 6, str(tmp_path))
