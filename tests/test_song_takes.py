"""Music videos: InfiniteTalk takes driven by the song's isolated vocals, one window of the song per shot."""
import math
import os
import shutil
import subprocess

import pytest

from studio import jobs, media, planner, settings, store
from test_lipsync import film  # noqa: F401 - fixture


def test_song_windows_follow_shot_order_and_planned_durations():
    shots = [planner.new_shot(id='B', order=1, duration_s=2.25), planner.new_shot(id='A', order=0, duration_s=1.5),
             planner.new_shot(id='C', order=2, duration_s=0.7), planner.new_shot(id='D', order=3, duration_s=0)]
    assert jobs.song_windows({'shots': shots}, 24) == {'A': (0, 36), 'B': (36, 90), 'C': (90, 107), 'D': (107, 107)}
    # frames are rounded from the running time, so lengths never drift from the song
    assert jobs.song_windows({'shots': shots}, 25)['C'] == (94, 111)


def test_song_shots_default_to_infinitetalk_shots_with_a_face():
    shots = [planner.new_shot(id='A', order=0, engine='infinitetalk', face='maryam'),
             planner.new_shot(id='B', order=1, engine='infinitetalk'),
             planner.new_shot(id='C', order=2, engine='ltx', face='hakon')]
    assert jobs.song_shots({'shots': shots}) == ['A']


def test_slice_song_cuts_the_window_as_16k_mono(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('requires ffmpeg')
    src = tmp_path / 'vocals.wav'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3:sample_rate=44100',
                    '-ac', '2', str(src)], check=True)
    dst = jobs.slice_song({'ffmpeg': shutil.which('ffmpeg')}, str(src), 1.25, 0.5, str(tmp_path / 'cut.wav'))
    info = media.probe(shutil.which('ffprobe'), dst)
    assert abs(info['duration'] - 0.5) < 0.01
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=sample_rate,channels', '-of', 'csv=p=0', dst],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == '16000,1'


class FakeComfy:
    """Vocal isolation returns the uploaded song; InfiniteTalk records its audio input and returns a video."""
    def __init__(self, tmp):
        self.tmp, self.uploads, self.graphs = tmp, {}, []

    def upload(self, path, name):
        self.uploads[name] = path
        return name

    def run(self, wf, cancel=None):
        self.graphs.append(wf)
        if any(n['class_type'] == 'ReelwrightSeparateVocals' for n in wf.values()):
            return {'audios': [{'filename': 'vocals.flac'}], 'seconds': 1}
        return {'videos': [{'filename': 'take.mp4'}], 'seconds': 2}

    def download(self, item, dest):
        song = next(p for n, p in self.uploads.items() if '_song_' in n)
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', song, dest], check=True)
        return dest

    def free(self):
        pass


def setup_song_job(film, monkeypatch, tmp_path):
    client, sid, eid, shots, music = film
    def plan(ep):
        for sh, d in zip(ep['shots'], (1.0, 0.5)):
            sh.update(engine='infinitetalk', face=sh['characters'][0]['id'], duration_s=d)
    store.update_episode(sid, eid, plan)
    long_take = tmp_path / 'long.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=64x96:r=25:d=2',
                    '-c:v', 'libx264', str(long_take)], check=True)
    fake = FakeComfy(tmp_path)
    s = settings.load()
    monkeypatch.setattr(jobs, '_ctx', lambda: (s, fake))
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: None)
    monkeypatch.setattr(jobs, '_first_frame_for', lambda *a: str(tmp_path / 'first.png'))

    def save(c, items, out_dir, stem):
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, f'{stem}{len(os.listdir(out_dir)):03d}.mp4')
        shutil.copy(long_take, dst)
        return [dst]
    monkeypatch.setattr(jobs, '_save_outputs', save)
    return client, sid, eid, shots, fake


def test_song_job_renders_each_window_selects_and_places_muted_clips(film, monkeypatch, tmp_path):
    client, sid, eid, shots, fake = setup_song_job(film, monkeypatch, tmp_path)
    jobs.job_song_takes(jobs.Job('takes', 'test', None, {}), sid, eid)
    e = store.get_episode(sid, eid)
    s = settings.load()
    voices = {n: p for n, p in fake.uploads.items() if n.endswith('_song16k.wav')}
    assert len(voices) == 2
    for sh, (start, end) in zip(e['shots'], ((0.0, 1.0), (1.0, 1.5))):
        take = sh['takes']['items'][sh['takes']['selected']]
        assert sh['takes']['selected'] == 1 and take['song_window'] == [start, end]
        assert take['engine'] == 'infinitetalk' and take['frames'] == math.ceil((end - start) * 25)
        voice = voices[f"studio_{eid}_{sh['id']}_song16k.wav"]
        assert abs(media.probe(s['ffprobe'], voice)['duration'] - (end - start)) < 0.01
    tl = e['timeline']
    assert [c['shot_id'] for c in tl['clips']] == [sh['id'] for sh in e['shots']]
    for c, sh, frames in zip(tl['clips'], e['shots'], (30, 15)):   # timeline at 30 fps
        assert c['source'] == sh['takes']['items'][-1]['file']
        assert c['frames'] == frames and c['muted'] is True and c['lipsync'] is False and c['source_in'] == 0
    assert tl['music']['file'] == e['music']['file'] and not tl['music']['muted']
    cache = os.listdir(jobs._ep_path(sid, eid, 'out', 'song_vocals'))
    assert sum(f.endswith('_vocals16k.wav') for f in cache) == 1

    # a second pass reuses the cached vocals, renders only the asked shot and leaves the other clip alone
    fake.graphs.clear()
    jobs.job_song_takes(jobs.Job('takes', 'test', None, {}), sid, eid, shot_ids=[shots[1]['id']])
    assert not any(n['class_type'] == 'ReelwrightSeparateVocals' for g in fake.graphs for n in g.values())
    e2 = store.get_episode(sid, eid)
    assert len(e2['shots'][1]['takes']['items']) == 3 and e2['timeline']['clips'][0] == tl['clips'][0]
    assert e2['timeline']['clips'][1]['source'] == e2['shots'][1]['takes']['items'][-1]['file']


def test_song_job_and_route_need_a_song(film, monkeypatch):
    client, sid, eid, shots, music = film
    store.update_episode(sid, eid, lambda ep: ep.update(music={'file': ''}))
    with pytest.raises(ValueError, match='no song'):
        jobs.job_song_takes(jobs.Job('takes', 'test', None, {}), sid, eid)
    url = f'/api/s/{sid}/e/{eid}/takes/song'
    response = client.post(url, json={})
    assert response.status_code == 400 and 'no song' in response.json()['detail']


def test_song_route_validates_and_submits(film, monkeypatch):
    client, sid, eid, shots, music = film
    monkeypatch.setattr('studio.app._submit', lambda kind, title, fn, **kw: {'kind': kind, 'fn': fn.__name__, **kw})
    url = f'/api/s/{sid}/e/{eid}/takes/song'
    assert client.post(url, json={}).status_code == 400            # no InfiniteTalk shots yet
    assert client.post(url, json={'shot_ids': ['missing']}).status_code == 400
    assert client.post(url, json={'shot_ids': 'S01_SH01'}).status_code == 400
    value = client.post(url, json={'shot_ids': [shots[0]['id']]}).json()
    assert value == {'kind': 'takes', 'fn': 'job_song_takes', 'sid': sid, 'eid': eid, 'shot_ids': [shots[0]['id']]}
