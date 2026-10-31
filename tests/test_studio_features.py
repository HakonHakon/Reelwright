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


def mock_installed_render_models(monkeypatch, missing=()):
    from studio import comfy, llm
    s = settings.load()
    installed = {group[key] for group in (s['flux'], s['zimage'], s['wan'], s['qwen_edit'])
                 for key in group if isinstance(group[key], str) and group[key].endswith('.safetensors')}
    installed.difference_update(missing)
    monkeypatch.setattr(comfy.Comfy, 'alive', lambda self: True)
    monkeypatch.setattr(comfy.Comfy, 'options', lambda *args: list(installed))
    monkeypatch.setattr(llm, 'installed', lambda *args: [s['planner_model'], s['vision_model']])


def test_status_does_not_require_unused_flux(client, monkeypatch):
    settings.save({'style_defaults': {'default_engine': 'wan', 'default_frame_source': 'zimage'}})
    mock_installed_render_models(monkeypatch, missing=[settings.DEFAULTS['flux']['unet']])
    sid = client.post('/api/series', json={'name': 'Z-Image project'}).json()['id']
    assert client.get('/api/status').json()['missing'] == []
    client.put(f'/api/s/{sid}/style', json={'default_frame_source': 'flux_lora'})
    assert settings.DEFAULTS['flux']['unet'] in client.get('/api/status').json()['missing']


def test_status_checks_shot_overrides_and_selected_missing_models(client, monkeypatch):
    settings.save({'style_defaults': {'default_engine': 'wan', 'default_frame_source': 'zimage'}})
    sid, eid, sh = project(client)
    missing = [settings.DEFAULTS['flux']['unet'], settings.DEFAULTS['wan']['high_lightning']]
    mock_installed_render_models(monkeypatch, missing=missing)
    assert client.get('/api/status').json()['missing'] == [settings.DEFAULTS['wan']['high_lightning']]
    store.update_episode(sid, eid, lambda e: e['shots'][0].update(frame_source='flux_lora'))
    assert set(client.get('/api/status').json()['missing']) == set(missing)


def test_local_h3_workflow_has_native_audio_and_end_frame(client):
    from studio import workflows
    s = settings.load()
    frames = workflows.minimax_h3_frames(4)
    assert (frames - 5) % 17 == 0 and frames >= 96
    wf = workflows.minimax_h3_i2v(s, 'A character waves and says hello', 'first.png', frames, 42, 'last.png')
    assert wf['6']['class_type'] == 'MiniMaxH3ImageToVideo'
    assert wf['6']['inputs']['last_frame'] == ['17', 0]
    assert wf['10']['inputs']['model'] == ['16', 0]
    assert wf['14']['inputs']['audio'] == ['13', 0]
    assert wf['13']['class_type'] == 'VAEDecodeAudio'
    assert not any('Cloud' in n['class_type'] for n in wf.values())
    providers.validate_model('minimax_local', 'MiniMax-H3')


def test_local_h3_job_does_not_call_paid_api(client, monkeypatch):
    sid, eid, sh = project(client)
    graphs = []
    class LocalComfy:
        def upload(self, *a): return 'first.png'
        def run(self, wf, **kw):
            graphs.append(wf)
            return {'videos': [{'filename': 'local.mp4'}], 'seconds': 1}
        def free(self): pass
    monkeypatch.setattr(jobs, '_ctx', lambda: (settings.load(), LocalComfy()))
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: None)
    monkeypatch.setattr(jobs, '_first_frame_for', lambda *a: '/tmp/first.png')
    monkeypatch.setattr(jobs, '_save_outputs', lambda *a: [os.path.join(settings.DATA_ROOT, 'local.mp4')])
    monkeypatch.setattr(jobs.media, 'probe', lambda *a: {'duration': 107/24, 'fps': 24, 'has_audio': True})
    monkeypatch.setattr(jobs.media, 'qc_video', lambda *a: [])
    monkeypatch.setattr(providers, 'minimax_generate', lambda *a: pytest.fail('Local H3 must not use the paid API'))
    jobs.job_takes(jobs.Job('takes', 'test', None, {}), sid, eid, takes=1, force=True,
                   engine_override='minimax_local', model_override='MiniMax-H3')
    assert graphs[0]['6']['class_type'] == 'MiniMaxH3ImageToVideo'
    take = store.get_episode(sid, eid)['shots'][0]['takes']['items'][-1]
    assert take['engine'] == 'minimax_local' and take['model'] == 'MiniMax-H3'


def test_status_checks_local_h3_models(client, monkeypatch):
    settings.save({'style_defaults': {'default_engine': 'minimax_local', 'default_frame_source': 'zimage'}})
    from studio import comfy, llm
    from studio.app import _required_model_options
    s = settings.load()
    required = _required_model_options(s)
    installed = {name for names in required.values() for name in names}
    missing = s['minimax_local']['unet']
    installed.remove(missing)
    monkeypatch.setattr(comfy.Comfy, 'alive', lambda self: True)
    monkeypatch.setattr(comfy.Comfy, 'options', lambda *a: sorted(installed))
    monkeypatch.setattr(llm, 'installed', lambda *a: [s['planner_model'], s['vision_model']])
    assert client.get('/api/status').json()['missing'] == [missing]
    installed.add(missing)
    assert client.get('/api/status').json()['missing'] == []


def test_phone_photo_orientation_and_crop(client, tmp_path):
    import io
    from PIL import Image
    from studio import media
    image = Image.new('RGB', (120, 80), 'red')
    image.paste(Image.new('RGB', (60, 80), 'lime'), (60, 0))
    exif = image.getexif(); exif[274] = 6
    original = tmp_path / 'phone.jpg'; image.save(original, exif=exif)
    preview = client.post('/api/image_preview', files={'file': ('phone.jpg', original.read_bytes(), 'image/jpeg')})
    assert preview.status_code == 200 and preview.headers['cache-control'] == 'no-store'
    assert Image.open(io.BytesIO(preview.content)).size == (80, 120)
    output = tmp_path / 'crop.png'
    media.fit_image(str(original), str(output), 80, 60, {'x': 0, 'y': .5, 'w': 1, 'h': .5})
    pixel = Image.open(output).getpixel((40, 30))
    assert pixel[1] > 220 and pixel[0] < 40


def test_heic_preview_and_upload(client, tmp_path):
    import io,json
    from PIL import Image
    from pillow_heif import from_pillow
    photo = tmp_path / 'phone.heic'
    from_pillow(Image.new('RGB', (120, 80), 'blue')).save(photo)
    preview = client.post('/api/image_preview', files={'file': ('phone.heic', photo.read_bytes(), 'image/heic')})
    assert preview.status_code == 200
    assert Image.open(io.BytesIO(preview.content)).size == (120, 80)
    sid,eid,sh = project(client)
    response = client.post(f'/api/s/{sid}/e/{eid}/shots/{sh["id"]}/upload_frame',
                           files={'file': ('phone.heic', photo.read_bytes(), 'image/heic')},
                           data={'crop': json.dumps({'x': 0, 'y': 0, 'w': 1, 'h': 1})})
    assert response.status_code == 200
    candidate = response.json()['shots'][0]['frame']['candidates'][-1]
    assert Image.open(store.abs_media(candidate['file'])).getpixel((100,100))[2] > 220


def test_invalid_mobile_photo_and_crop_return_clear_errors(client, tmp_path):
    import io,json
    from PIL import Image
    assert client.post('/api/image_preview', files={'file': ('broken.jpg', b'bad', 'image/jpeg')}).status_code == 415
    sid,eid,sh=project(client)
    data=io.BytesIO();Image.new('RGB',(64,64)).save(data,format='PNG')
    response=client.post(f'/api/s/{sid}/e/{eid}/shots/{sh["id"]}/upload_frame',
                         files={'file': ('photo.png',data.getvalue(),'image/png')},
                         data={'crop':json.dumps({'x':0,'y':0,'w':-1,'h':1})})
    assert response.status_code==400
    assert len(store.get_episode(sid,eid)['shots'][0]['frame']['candidates'])==1


def test_app_manifest_and_worker(client):
    manifest=client.get('/static/manifest.webmanifest').json()
    assert manifest['display']=='standalone' and manifest['scope']=='/'
    assert {icon['sizes'] for icon in manifest['icons']}=={'192x192','512x512'}
    worker=client.get('/sw.js')
    assert worker.status_code==200
    assert worker.headers['service-worker-allowed']=='/'
    assert worker.headers['cache-control']=='no-cache'


def test_portrait_episode_upload_keeps_its_own_dimensions(client):
    import io
    from PIL import Image
    sid,eid,sh=project(client)
    def portrait(e):
        e['video']={'width':512,'height':910,'fps':30}
        e['image']={'width':512,'height':910}
    store.update_episode(sid,eid,portrait)
    before=settings.load()
    context=settings.for_episode(before,store.get_episode(sid,eid))
    assert context['minimax_local']['height'] > context['minimax_local']['width']
    assert before['video']['width']==1024 and before['video']['height']==576
    response=client.get(f'/api/s/{sid}/e/{eid}').json()
    assert response['_settings']['video']=={'width':512,'height':910,'fps':30}
    photo=io.BytesIO();Image.new('RGB',(256,455),'blue').save(photo,format='PNG')
    response=client.post(f'/api/s/{sid}/e/{eid}/shots/{sh["id"]}/upload_frame',files={'file':('portrait.png',photo.getvalue(),'image/png')})
    assert response.status_code==200
    file=response.json()['shots'][0]['frame']['candidates'][-1]['file']
    assert Image.open(store.abs_media(file)).size==(512,910)


@pytest.mark.parametrize('model', ['minimax_identity', 'qwen_identity'])
def test_character_identity_correction_uses_uploaded_portrait(client, monkeypatch, tmp_path, model):
    from studio import app as app_module, face_correction
    sid, eid, sh = project(client)
    url = f'/api/s/{sid}/e/{eid}/shots/{sh["id"]}/face_correction'
    assert client.post(url, json={'model': model}).status_code == 400
    photo = tmp_path / 'hakon.png'
    photo.write_bytes(b'portrait')
    character = store.new_character('Hakon', refs=[store.rel(str(photo))])
    store.save_character(sid, character)
    store.update_episode(sid, eid, lambda e: e['shots'][0].update(characters=[{'id': character['id']}]))
    monkeypatch.setattr(app_module, '_submit', lambda *args, **kw: kw)
    response = client.post(url, json={'model': model, 'index': 2})
    assert response.status_code == 200
    assert response.json()['source_file'] == 'take2.mp4'
    calls = []
    method = 'qwen_correct_take' if model == 'qwen_identity' else 'match_identity'
    monkeypatch.setattr(face_correction, method, lambda *args: calls.append(args))
    monkeypatch.setattr(jobs.comfy_mod.Comfy, 'alive', lambda *args: False)
    monkeypatch.setattr(jobs.llm, 'unload', lambda *args: None)
    jobs.job_face_correction(jobs.Job('face', 'test', None, {}), **response.json())
    assert calls[0][-1]['refs'] == character['refs']
    takes = store.get_episode(sid, eid)['shots'][0]['takes']
    assert takes['selected'] == 2 and takes['items'][2]['file'] == 'take2.mp4'
    assert takes['items'][-1]['character_refs'] == character['refs']
    assert takes['items'][-1]['face_correction'] == model


def test_qwen_frame_correction_keeps_original_and_routes_identity(client, monkeypatch, tmp_path):
    from studio import app as app_module, face_correction
    sid, eid, sh = project(client)
    url = f'/api/s/{sid}/e/{eid}/shots/{sh["id"]}/frame_correction'
    assert client.post(url, json={'index': -1}).status_code == 400
    assert client.post(url, json={'index': 0}).status_code == 400
    photo = tmp_path / 'maryam.png'; photo.write_bytes(b'portrait')
    character = store.new_character('Maryam', refs=[store.rel(str(photo))])
    store.save_character(sid, character)
    store.update_episode(sid, eid, lambda e: e['shots'][0].update(characters=[{'id': character['id']}]))
    monkeypatch.setattr(app_module, '_submit', lambda *args, **kw: kw)
    params = client.post(url, json={'index': 0}).json()
    calls = []
    monkeypatch.setattr(face_correction, 'qwen_correct_image', lambda *args, **kw: calls.append(args))
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *args: None)
    jobs.job_frame_correction(jobs.Job('frame', 'test', None, {}), **params)
    frame = store.get_episode(sid, eid)['shots'][0]['frame']
    assert frame['selected'] == 0 and frame['candidates'][0]['file'] == 'first.png'
    assert frame['candidates'][-1]['source_frame'] == 'first.png'
    assert calls[0][-1]['refs'] == character['refs']


def test_qwen_graph_distinguishes_performance_and_identity():
    from studio import workflows
    graph = workflows.qwen_correct_face(settings.load(), 'performance.png', ['portrait.png'],
                                        {'name': 'Maryam'}, 512, 912, 224147)
    assert graph['50']['inputs']['image'] == 'performance.png'
    assert graph['51']['inputs']['image'] == 'portrait.png'
    assert graph['4']['inputs']['image1'] == ['50', 0]
    assert graph['4']['inputs']['image2'] == ['51', 0]
    assert 'Image 1 is the performance frame' in graph['4']['inputs']['prompt']


def test_uploaded_mask_is_not_rejected_by_cached_file_list():
    from studio.comfy import Comfy
    client = Comfy('unused')
    client._object_info = {'LoadImageMask': {'input': {'required': {
        'image': [['old.png']], 'channel': [['red', 'green', 'blue', 'alpha']]}}}}
    graph = {'1': {'class_type': 'LoadImageMask',
                   'inputs': {'image': 'newly_uploaded.png', 'channel': 'red'}}}
    assert client.validate(graph) == []
    graph['1']['inputs']['channel'] = 'invalid'
    assert len(client.validate(graph)) == 1


def test_qwen_take_edits_every_frame_and_copies_audio(client, monkeypatch, tmp_path):
    import shutil
    import subprocess
    from studio import face_correction, comfy, media
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('requires ffmpeg and ffprobe')
    source, destination = tmp_path / 'source.mp4', tmp_path / 'result.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=64x96:r=30:d=0.5', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.5', '-c:v', 'libx264', '-c:a', 'aac', str(source)], check=True)
    monkeypatch.setattr(face_correction, 'qwen_references', lambda *args: ['portrait.png'])
    frames = []
    def correct(job, cfg, src, dest, character, **kw):
        frames.append(src)
        assert kw['references'] == ['portrait.png']
        shutil.copyfile(src, dest)
    monkeypatch.setattr(face_correction, 'qwen_correct_image', correct)
    cfg = settings.load(); cfg.update(ffmpeg=shutil.which('ffmpeg'), ffprobe=shutil.which('ffprobe'))
    job = jobs.Job('face', 'test', None, {})
    face_correction.qwen_correct_take(job, cfg, str(source), str(destination), {'name': 'Maryam'})
    assert len(frames) == 15 and job.progress == [15, 15]
    info = media.probe('ffprobe', str(destination))
    assert (info['width'], info['height'], info['fps']) == (64, 96, 30)
    assert abs(info['duration'] - media.probe('ffprobe', str(source))['duration']) < .04
    def audio_hash(path):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                       '-c:a', 'copy', '-f', 'hash', '-hash', 'sha256', '-'])
    assert audio_hash(source) == audio_hash(destination)


def test_reference_identity_edit_preserves_source_soundtrack(client, monkeypatch, tmp_path):
    import shutil
    import subprocess
    from studio import face_correction, comfy, media
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('requires ffmpeg and ffprobe')
    source, generated, destination = [tmp_path / name for name in ('source.mp4', 'generated.mp4', 'result.mp4')]
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=64x96:r=30:d=0.5', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.5', '-c:v', 'libx264', '-c:a', 'aac', str(source)], check=True)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=64x96:r=24:d=2.5', '-c:v', 'libx264', str(generated)], check=True)
    portrait = tmp_path / 'portrait.png'; portrait.write_bytes(b'portrait')
    uploaded, graphs = [], []
    monkeypatch.setattr(comfy.Comfy, 'upload', lambda self, path, name: uploaded.append(path) or name)
    def run(self, graph, cancel):
        assert not cancel()
        graphs.append(graph)
        return {'videos': [{'filename': 'generated.mp4', 'type': 'output'}]}
    monkeypatch.setattr(comfy.Comfy, 'run', run)
    monkeypatch.setattr(comfy.Comfy, 'download', lambda self, item, dest: shutil.copyfile(generated, dest))
    cfg = settings.load(); cfg.update(ffmpeg=shutil.which('ffmpeg'), ffprobe=shutil.which('ffprobe'))
    face_correction.match_identity(jobs.Job('face', 'test', None, {}), cfg, str(source), str(destination),
                                   {'name': 'Hakon', 'refs': [store.rel(str(portrait))]})
    graph = graphs[0]
    assert str(portrait) in uploaded
    assert graph['6']['inputs']['ref_images.ref_image_0'] == ['5', 0]
    assert graph['6']['inputs']['ref_videos.ref_video_0'] == ['18', 0]
    assert graph['1']['inputs']['unet_name'] == cfg['face_correction']['identity_unet']
    assert graph['19']['inputs']['lora_name'] == cfg['face_correction']['identity_lora']
    before, after = media.probe('ffprobe', str(source)), media.probe('ffprobe', str(destination))
    assert after['width'] == 64 and after['height'] == 96 and after['fps'] == 30
    assert abs(before['duration'] - after['duration']) < .04
    def audio_hash(path):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                       '-c:a', 'copy', '-f', 'hash', '-hash', 'sha256', '-'])
    assert audio_hash(source) == audio_hash(destination)
