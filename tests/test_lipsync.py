"""Lip-sync timeline, upload consent, review selection and full music export."""
import copy
import os
import shutil
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio import comfy, jobs, lipsync, media, planner, settings, store
from studio.app import app


@pytest.fixture
def film(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('requires ffmpeg and ffprobe')
    monkeypatch.setattr(settings, 'DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(settings, 'SETTINGS_PATH', str(tmp_path / 'settings.json'))
    settings.save({'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
                   'video': {'width': 64, 'height': 96, 'fps': 30}})
    sid = store.create_series('Music')['id']
    eid = store.create_episode(sid, 'Song')['id']
    shots = []
    for i, color in enumerate(['red', 'blue']):
        path = tmp_path / f'{i}.mp4'
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=64x96:r=30:d=1', '-c:v', 'libx264', str(path)], check=True)
        sh = planner.new_shot(id=f'S0{i+1}_SH01', order=i)
        sh['characters'] = [{'id': 'hakon' if i == 0 else 'maryam'}]
        sh['takes'].update(items=[{'file': store.rel(str(path))}], selected=0)
        shots.append(sh)
    music = tmp_path / 'music.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=330:duration=2', str(music)], check=True)
    def save(ep):
        ep['shots'] = shots
        ep['music'] = {'file': store.rel(str(music))}
        ep['outputs'] = {'final': 'original.mp4'}
    store.update_episode(sid, eid, save)
    return TestClient(app), sid, eid, shots, music


def test_lipsync_submission_snapshots_timeline_and_rejects_bad_inputs(film, monkeypatch):
    client, sid, eid, shots, music = film
    monkeypatch.setattr('studio.app._submit', lambda *a, **kw: kw)
    url = f'/api/s/{sid}/e/{eid}/lipsync'
    for body in [{}, {'shot_ids': ['missing']}, {'shot_ids': [shots[0]['id']], 'audio_offset': -1},
                 {'shot_ids': [shots[0]['id']], 'audio_offset': 99},
                 {'shot_ids': [shots[0]['id']], 'isolate_vocals': 'false'}]:
        assert client.post(url, json=body).status_code == 400
    response = client.post(url, json={'shot_ids': [shots[0]['id']], 'audio_offset': .2})
    assert response.status_code == 200
    value = response.json()
    assert value['audio_file'] == store.rel(str(music)) and value['audio_offset'] == .2
    assert [c['sync'] for c in value['timeline']] == [True, False]
    assert [c['start'] for c in value['timeline']] == [0, 1]
    assert [c['frames'] for c in value['timeline']] == [30, 30]
    store.update_episode(sid, eid, lambda e: e['shots'][0].update(characters=[{'id': 'a'}, {'id': 'b'}]))
    assert client.post(url, json={'shot_ids': [shots[0]['id']]}).status_code == 400


def test_invalid_music_does_not_replace_saved_track(film):
    client, sid, eid, shots, music = film
    response = client.post(f'/api/s/{sid}/e/{eid}/music', files={'file': ('bad.mp3', b'not audio', 'audio/mpeg')})
    assert response.status_code == 400
    assert store.get_episode(sid, eid)['music']['file'] == store.rel(str(music))
    assert not os.path.exists(os.path.join(store.ep_dir(sid, eid), 'uploads', 'bad.mp3'))


def test_lipsync_respects_trims_on_frame_timeline(film):
    client, sid, eid, shots, music = film
    episode = store.get_episode(sid, eid)
    episode['shots'][0]['takes'].update(trim_in=.2, trim_out=.1)
    plan = lipsync.plan_episode(episode, [shots[1]['id']], settings.load())
    assert [c['frames'] for c in plan] == [21, 30]
    assert plan[1]['start'] == .7
    assert plan[0]['trim_in'] == .2


def test_lipsync_exports_full_mix_and_only_changes_selected_shots(film, monkeypatch, tmp_path):
    client, sid, eid, shots, music = film
    original = copy.deepcopy(store.get_episode(sid, eid))
    generated = tmp_path / 'model.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=green:s=64x96:r=25:d=1', '-c:v', 'libx264', str(generated)], check=True)
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: None)
    monkeypatch.setattr(comfy.Comfy, 'has_node', lambda *a: True)
    monkeypatch.setattr(comfy.Comfy, 'upload', lambda self, path, name: name)
    graphs = []
    def run(self, graph, cancel):
        assert not cancel()
        graphs.append(graph)
        if 'ReelwrightSeparateVocals' in [n['class_type'] for n in graph.values()]:
            return {'audios': [{'filename': 'guide.flac'}]}
        return {'videos': [{'filename': 'synced.mp4'}]}
    monkeypatch.setattr(comfy.Comfy, 'run', run)
    monkeypatch.setattr(comfy.Comfy, 'download', lambda self, item, dest:
                        shutil.copyfile(music if item['filename'] == 'guide.flac' else generated, dest))
    plan = lipsync.plan_episode(original, [shots[0]['id']], settings.load())
    lipsync.render(jobs.Job('lip', 'test', None, {}), sid, eid, plan, store.rel(str(music)))
    episode = store.get_episode(sid, eid)
    assert episode['shots'] == original['shots']
    assert episode['outputs']['final'] == original['outputs']['final']
    version = episode['outputs']['lipsync_versions'][0]
    result = store.abs_media(version['file'])
    info = media.probe('ffprobe', result)
    assert info['video_duration'] == 2 and info['fps'] == 30
    assert len(graphs) == 2 and graphs[1]['3']['class_type'] == 'ReelwrightLatentSync'
    # The first chosen shot changes to the synthetic model result; the second stays blue.
    import cv2
    cap = cv2.VideoCapture(result)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 15); ok, first = cap.read(); assert ok
    cap.set(cv2.CAP_PROP_POS_FRAMES, 45); ok, second = cap.read(); assert ok
    assert first[48, 32, 1] > first[48, 32, 0] + 50
    assert second[48, 32, 0] > second[48, 32, 1] + 150
    cap.release()
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', result, '-map', '0:a:0',
                                   '-f', 'f32le', '-ac', '1', '-ar', '8000', '-'])
    samples = np.frombuffer(raw, dtype='<f4')
    peak_hz = np.argmax(np.abs(np.fft.rfft(samples))) * 8000 / len(samples)
    assert abs(peak_hz - 330) < 2  # Full uploaded mix, not model-generated audio.
    assert client.post(f'/api/s/{sid}/e/{eid}/lipsync/select', json={'index': 99}).status_code == 400
    assert client.post(f'/api/s/{sid}/e/{eid}/lipsync/select', json={'index': 0}).status_code == 200
    outputs = store.get_episode(sid, eid)['outputs']
    assert outputs['final'] == version['file'] and outputs['before_lipsync'] == 'original.mp4'


def test_generated_audio_is_downloadable_but_reference_audio_is_ignored(monkeypatch):
    client = comfy.Comfy('unused')
    source = {'filename': 'source.wav', 'type': 'input'}
    generated = {'filename': 'vocals.flac', 'type': 'output'}
    class Response:
        status_code = 200
        def __init__(self, value): self.value = value
        def json(self): return self.value
    monkeypatch.setattr(client, 'validate', lambda wf: [])
    monkeypatch.setattr(comfy.requests, 'post', lambda *a, **kw: Response({'prompt_id': 'job'}))
    history = {'job': {'status': {'completed': True}, 'outputs': {
        'input': {'audio': [source]}, 'save': {'audio': [generated]}}}}
    monkeypatch.setattr(comfy.requests, 'get', lambda *a, **kw: Response(history))
    monkeypatch.setattr(comfy.time, 'sleep', lambda seconds: None)
    assert client.run({})['audios'] == [generated]


@pytest.mark.parametrize('cancel', [False, True])
def test_failed_or_cancelled_lipsync_never_selects_partial_output(film, monkeypatch, cancel):
    client, sid, eid, shots, music = film
    before = copy.deepcopy(store.get_episode(sid, eid))
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: None)
    monkeypatch.setattr(comfy.Comfy, 'has_node', lambda *a: True)
    monkeypatch.setattr(comfy.Comfy, 'upload', lambda self, path, name: name)
    job = jobs.Job('lip', 'test', None, {})
    def fail(self, graph, **kw):
        if cancel:
            job.cancel_flag = True
            job.check()
        raise RuntimeError('Face not detected')
    monkeypatch.setattr(comfy.Comfy, 'run', fail)
    plan = lipsync.plan_episode(before, [shots[0]['id']], settings.load())
    with pytest.raises(jobs.Cancelled if cancel else RuntimeError):
        lipsync.render(job, sid, eid, plan, store.rel(str(music)), isolate_vocals=False)
    assert store.get_episode(sid, eid) == before
