"""Saved edits, safe media references and actual frame/audio timeline exports."""
import copy
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studio import jobs, lipsync, media, settings, store, timeline
from test_lipsync import film


def test_saved_timeline_split_delete_and_conflict_do_not_change_takes(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    before = copy.deepcopy(store.get_episode(sid, eid))
    edit = client.get(url).json()['edit']
    assert edit['revision'] == 0 and len(edit['clips']) == 2
    assert 'timeline' not in store.get_episode(sid, eid)
    left = edit['clips'][0]; left['frames'] = 15
    right = {**left, 'id': 'split', 'source_in': .5, 'muted': True}
    edit['clips'] = [edit['clips'][1], right, left]
    response = client.put(url, json=edit)
    assert response.status_code == 200, response.text
    saved = response.json()['edit']
    assert saved['revision'] == 1 and saved['clips'][1]['source_in'] == .5
    assert client.put(url, json=edit).status_code == 409
    saved['clips'].pop(1)
    assert client.put(url, json=saved).status_code == 200
    after = store.get_episode(sid, eid)
    assert after['shots'] == before['shots']
    assert after['outputs'] == before['outputs']
    assert all(Path(store.abs_media(c['source'])).exists() for c in edit['clips'])


@pytest.mark.parametrize('change', ['escape', 'overtrim', 'duplicate', 'badvolume', 'badmute'])
def test_timeline_rejects_invalid_media_or_edits_atomically(film, change):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    if change == 'escape': edit['clips'][0]['source'] = '../private.mp4'
    if change == 'overtrim': edit['clips'][0]['frames'] = 99999
    if change == 'duplicate': edit['clips'][1]['id'] = edit['clips'][0]['id']
    if change == 'badvolume': edit['clips'][0]['volume'] = -1
    if change == 'badmute': edit['clips'][0]['muted'] = 'false'
    assert client.put(url, json=edit).status_code == 400
    assert 'timeline' not in store.get_episode(sid, eid)


def test_timeline_export_and_cut_snapshot_saved_edits(film, monkeypatch):
    client, sid, eid, shots, music = film
    base = f'/api/s/{sid}/e/{eid}'
    edit = client.get(base+'/timeline').json()['edit']
    edit['clips'] = [edit['clips'][1]]
    assert client.put(base+'/timeline', json=edit).status_code == 200
    captured = []
    monkeypatch.setattr('studio.app._submit', lambda *a, **kw: captured.append(kw) or {'id':'test'})
    assert client.post(base+'/timeline/export').status_code == 200
    assert client.post(base+'/cut').status_code == 200
    assert captured[0]['edit']['clips'] == captured[1]['edit']['clips']
    assert captured[0]['video'] == {'width':64, 'height':96, 'fps':30}
    store.update_episode(sid, eid, lambda e: e['timeline']['clips'].clear())
    assert len(captured[0]['edit']['clips']) == 1
    assert client.post(base+'/timeline/export').status_code == 400


def test_existing_duet_final_imports_its_cuts_without_replacing_sources(film):
    client, sid, eid, shots, music = film
    def prepare(ep):
        ep['outputs']['final'] = shots[0]['takes']['items'][0]['file']
        ep['outputs']['lipsync_versions'] = [{'file':ep['outputs']['final'], 'timeline':[
            {'shot_id':shots[0]['id'], 'start':0, 'duration':.4},
            {'shot_id':shots[1]['id'], 'start':.4, 'duration':.6}]}]
    store.update_episode(sid, eid, prepare)
    edit = client.get(f'/api/s/{sid}/e/{eid}/timeline').json()['edit']
    assert [c['frames'] for c in edit['clips']] == [12,18]
    assert all(c['muted'] for c in edit['clips'])
    assert edit['clips'][1]['source_in'] == .4
    assert sum(c['frames'] for c in edit['clips']) == 30


def test_audio_waveform_and_undo_to_previous_uploaded_music(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    old = copy.deepcopy(edit['music'])
    assert client.put(url, json=edit).status_code == 200
    upload = client.post(f'/api/s/{sid}/e/{eid}/music', files={'file':('new.wav', music.read_bytes(), 'audio/wav')})
    assert upload.status_code == 200
    edit = client.get(url).json()['edit']; edit['music']['file'] = upload.json()['file']
    saved = client.put(url, json=edit).json()['edit']; saved['music'] = old
    assert client.put(url, json=saved).status_code == 200
    waveform = client.get(url+'/waveform', params={'file':old['file']}).json()
    assert len(waveform['peaks']) == 512 and max(waveform['peaks']) == 1
    assert client.get(url+'/source', params={'file':'../secret.mp4'}).status_code == 400


def make_audio_video(path, color, hz):
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=c={color}:s=64x96:r=30:d=1', '-f', 'lavfi', '-i',
                    f'sine=frequency={hz}:duration=1', '-c:v', 'libx264', '-c:a', 'aac', str(path)], check=True)


def test_actual_export_obeys_reorder_trims_mute_and_music_position(film):
    client, sid, eid, shots, music = film
    for sh, color, hz in zip(shots, ['red','blue'], [990,660]):
        make_audio_video(store.abs_media(sh['takes']['items'][0]['file']), color, hz)
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    blue, red = edit['clips'][1], edit['clips'][0]
    blue.update(source_in=.2, frames=15)
    red.update(frames=15, muted=True)
    edit['clips'] = [blue,red]
    edit['music'].update(start_frame=15, frames=15, volume=1)
    edit = timeline.validate(store.get_episode(sid, eid), edit, settings.load())
    before = copy.deepcopy(store.get_episode(sid, eid)['shots'])
    timeline.render(jobs.Job('timeline','test',None,{}), sid, eid, edit, settings.load()['video'])
    e = store.get_episode(sid, eid); path = store.abs_media(e['outputs']['final'])
    assert e['shots'] == before
    assert e['outputs']['runtime'] == 1 and e['outputs']['timeline_versions'][-1]['frames'] == 30
    info = media.probe('ffprobe', path)
    assert info['video_duration'] == 1 and info['fps'] == 30
    import cv2
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES,5); ok, a = cap.read(); assert ok
    cap.set(cv2.CAP_PROP_POS_FRAMES,20); ok,b = cap.read(); assert ok; cap.release()
    assert a[48,32,0]>200 and b[48,32,2]>200
    raw = subprocess.check_output(['ffmpeg','-v','error','-i',path,'-map','0:a:0','-f','f32le','-ac','1','-ar','8000','-'])
    samples = np.frombuffer(raw,dtype='<f4')
    def peak(start,end):
        data = samples[int(start*8000):int(end*8000)]
        return np.argmax(np.abs(np.fft.rfft(data))) * 8000 / len(data)
    assert abs(peak(.1,.4)-660)<5
    assert abs(peak(.6,.9)-330)<5


def test_lipsync_uses_edit_positions_and_reuses_completed_clips(film, monkeypatch):
    client, sid, eid, shots, music = film
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    edit['clips'][1]['lipsync'] = True
    edit['music']['isolate_vocals'] = False
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a:None)
    calls = []
    def correct(job,cfg,client,source,voice,dest,*args):
        import shutil
        calls.append({'voice':Path(voice).read_bytes(),'frames':args[0]})
        shutil.copyfile(source,dest)
    monkeypatch.setattr(lipsync,'correct_clip',correct)
    timeline.render(jobs.Job('timeline','test',None,{}), sid,eid,edit,settings.load()['video'])
    assert len(calls)==1 and calls[0]['frames']==30
    edit['clips'][1]['muted'] = True
    timeline.render(jobs.Job('timeline','test',None,{}), sid,eid,edit,settings.load()['video'])
    assert len(calls)==1


def test_cancelled_timeline_does_not_publish_partial_export(film):
    client,sid,eid,shots,music = film
    before = copy.deepcopy(store.get_episode(sid,eid))
    edit = timeline.default_edit(before,settings.load())
    job = jobs.Job('timeline','test',None,{}); job.cancel_flag=True
    with pytest.raises(jobs.Cancelled):timeline.render(job,sid,eid,edit,settings.load()['video'])
    assert store.get_episode(sid,eid)==before


def test_changing_project_fps_preserves_edit_durations(film):
    client,sid,eid,shots,music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']; assert client.put(url,json=edit).status_code==200
    store.update_episode(sid,eid,lambda e:e.update(video={'fps':24,'width':64,'height':96}))
    changed = client.get(url).json()['edit']
    assert changed['fps']==24 and [c['frames'] for c in changed['clips']]==[24,24]
    assert changed['music']['frames']==48
    assert client.put(url,json=changed).status_code==200


def test_real_source_frame_numbers_survive_split_and_trim(film):
    client,sid,eid,shots,music = film
    source = store.abs_media(shots[0]['takes']['items'][0]['file'])
    frames=np.zeros((60,96,64,3),dtype=np.uint8)
    for n in range(60):frames[n,:,:,0]=n*4
    subprocess.run(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','64x96','-r','30',
                    '-i','pipe:0','-c:v','libx264','-crf','0',source],input=frames.tobytes(),check=True)
    edit=timeline.default_edit(store.get_episode(sid,eid),settings.load())
    c=edit['clips'][0]; c.update(source_in=35/30,frames=10,muted=True)
    edit['clips']=[c,{**c,'id':'right','source_in':45/30,'frames':6}];edit['music']=None
    timeline.render(jobs.Job('timeline','test',None,{}),sid,eid,edit,settings.load()['video'])
    path=store.abs_media(store.get_episode(sid,eid)['outputs']['final'])
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',path,'-f','rawvideo','-pix_fmt','rgb24','-'])
    actual=np.frombuffer(raw,dtype=np.uint8).reshape(-1,96,64,3)
    assert len(actual)==16
    expected=np.arange(35,51)*4
    assert np.max(np.abs(actual[:,48,32,0].astype(float)-expected))<=3


@pytest.mark.parametrize('offset', [0, 1, 3, 5, 8, 13, 22])
def test_export_is_frame_exact_when_a_trim_falls_between_source_frames(film, offset):
    """A 24 fps take on a 30 fps timeline: any trim point not on the take's own frame grid used to
    lose the first output frame, which the export's frame-count check then (rightly) rejected."""
    client, sid, eid, shots, music = film
    source = store.abs_media(shots[0]['takes']['items'][0]['file'])
    frames = np.zeros((48, 96, 64, 3), dtype=np.uint8)
    for n in range(48):
        frames[n, :, :, 0] = n * 5
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '64x96', '-r', '24',
                    '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '0', source], input=frames.tobytes(), check=True)
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    clip = edit['clips'][0]; clip.update(source_in=offset / 30, frames=30, muted=True)
    edit['clips'] = [clip]; edit['music'] = None
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'])
    path = store.abs_media(store.get_episode(sid, eid)['outputs']['final'])
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', path, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    actual = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 96, 64, 3)
    assert len(actual) == 30
    expected = np.array([min(47, round((offset + k) / 30 * 24)) * 5 for k in range(30)])
    assert np.max(np.abs(actual[:, 48, 32, 0].astype(float) - expected)) <= 8, 'wrong footage or a shifted start'


# ── markers, render range and range previews ─────────────────────────────

def test_markers_and_range_are_stored_sorted_validated_and_default_for_old_timelines(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    assert edit['markers'] == [] and edit['range'] is None
    edit['markers'] = [{'id': 'b', 'frame': 40, 'label': '  Chorus  ', 'color': 'red'},
                       {'id': 'a', 'frame': 10, 'label': 'Verse'}]
    edit['range'] = {'start': 5, 'end': 25}
    saved = client.put(url, json=edit)
    assert saved.status_code == 200, saved.text
    body = saved.json()['edit']
    assert body['markers'] == [{'id': 'a', 'frame': 10, 'label': 'Verse', 'color': 'blue'},
                               {'id': 'b', 'frame': 40, 'label': 'Chorus', 'color': 'red'}]
    assert body['range'] == {'start': 5, 'end': 25}
    # a timeline written before these fields existed still loads, with empty defaults
    old = {k: v for k, v in store.get_episode(sid, eid)['timeline'].items() if k not in ('markers', 'range')}
    store.update_episode(sid, eid, lambda e: e.update(timeline=old))
    loaded = client.get(url).json()['edit']
    assert loaded['markers'] == [] and loaded['range'] is None


@pytest.mark.parametrize('field,value', [
    ('markers', 'nope'), ('markers', [{'id': 'a', 'frame': 1.5}]), ('markers', [{'id': 'a', 'frame': -1}]),
    ('markers', [{'id': 'a', 'frame': 1}, {'id': 'a', 'frame': 2}]), ('markers', [{'id': '', 'frame': 1}]),
    ('markers', [{'id': 'a', 'frame': 1, 'label': 'x' * 81}]), ('markers', [{'id': 'a', 'frame': 1, 'label': 7}]),
    ('markers', [{'id': 'a', 'frame': 1, 'color': 'chartreuse'}]), ('markers', [{'id': 'a', 'frame': True}]),
    ('markers', [{'id': str(i), 'frame': i} for i in range(501)]),
    ('range', {'start': 10, 'end': 10}), ('range', {'start': 10, 'end': 5}), ('range', {'start': 1.5, 'end': 9}),
    ('range', {'start': -1, 'end': 9}), ('range', [1, 9]), ('range', {'start': 1}),
])
def test_invalid_markers_or_range_are_rejected_without_saving(film, field, value):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']; edit[field] = value
    assert client.put(url, json=edit).status_code == 400
    assert 'timeline' not in store.get_episode(sid, eid)


def test_changing_project_fps_keeps_markers_and_range_on_the_same_moments(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    edit['markers'] = [{'id': 'm', 'frame': 15, 'label': 'Half', 'color': 'green'}]; edit['range'] = {'start': 15, 'end': 45}
    assert client.put(url, json=edit).status_code == 200
    store.update_episode(sid, eid, lambda e: e.update(video={'fps': 24, 'width': 64, 'height': 96}))
    changed = client.get(url).json()['edit']
    assert changed['fps'] == 24 and changed['markers'][0]['frame'] == 12 and changed['range'] == {'start': 12, 'end': 36}


def _two_tone_film(film):
    client, sid, eid, shots, music = film
    for sh, color, hz in zip(shots, ['red', 'blue'], [990, 660]):
        make_audio_video(store.abs_media(sh['takes']['items'][0]['file']), color, hz)
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    for clip in edit['clips']:
        clip['muted'] = True
    edit['music'].update(start_frame=15, frames=30, volume=1)
    return sid, eid, timeline.validate(store.get_episode(sid, eid), edit, settings.load())


def _frames_and_audio(path):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', path, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    video = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 96, 64, 3)
    audio = np.frombuffer(subprocess.check_output(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 'f32le',
                                                   '-ac', '1', '-ar', '8000', '-']), dtype='<f4')
    return video, audio


def _dominant(audio, start, end):
    data = audio[int(start * 8000):int(end * 8000)]
    return np.argmax(np.abs(np.fft.rfft(data))) * 8000 / len(data), float(np.sqrt(np.mean(data ** 2)))


def test_range_preview_renders_only_that_span_with_the_right_picture_and_music(film):
    sid, eid, edit = _two_tone_film(film)
    before = copy.deepcopy(store.get_episode(sid, eid))
    # frames 15..45 straddle the cut: the second half of the red clip, the first half of the blue one
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=(15, 45))
    after = store.get_episode(sid, eid)
    assert after['outputs']['final'] == before['outputs']['final'] and 'timeline_versions' not in after['outputs']
    preview, = after['outputs']['timeline_previews']
    assert preview['frames'] == 30 and preview['window'] == [15, 45] and preview['lipsync'] is False
    video, audio = _frames_and_audio(store.abs_media(preview['file']))
    assert len(video) == 30
    assert video[5, 48, 32, 0] > 200 and video[25, 48, 32, 2] > 200   # red first, then blue
    assert abs(_dominant(audio, .1, .4)[0] - 330) < 6 and abs(_dominant(audio, .6, .9)[0] - 330) < 6, 'music spans the whole range'
    # the same edit, a different window: music only starts halfway through
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=(0, 30))
    video, audio = _frames_and_audio(store.abs_media(store.get_episode(sid, eid)['outputs']['timeline_previews'][-1]['file']))
    assert len(video) == 30 and video[10, 48, 32, 0] > 200
    assert _dominant(audio, .05, .45)[1] < .02, 'silent before the music starts'
    assert abs(_dominant(audio, .55, .95)[0] - 330) < 6 and _dominant(audio, .55, .95)[1] > .05


def test_range_preview_never_runs_lipsync_or_needs_the_gpu(film, monkeypatch):
    sid, eid, edit = _two_tone_film(film)
    edit['clips'][1]['lipsync'] = True; edit['music']['isolate_vocals'] = False
    def forbidden(*a, **k):
        raise AssertionError('previews must not run lip-sync')
    monkeypatch.setattr(jobs, '_gpu_prepare', forbidden); monkeypatch.setattr(lipsync, 'correct_clip', forbidden)
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=(20, 50))
    preview, = store.get_episode(sid, eid)['outputs']['timeline_previews']
    assert preview['lipsync'] is False and preview['lipsync_skipped'] is True


@pytest.mark.parametrize('window', [(40, 20), (-1, 10), (0, 9999), (60, 61), (10, 10)])
def test_render_rejects_a_range_outside_the_timeline(film, window):
    sid, eid, edit = _two_tone_film(film)
    with pytest.raises(ValueError):
        timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=window)
    assert 'timeline_previews' not in store.get_episode(sid, eid)['outputs']


def test_export_endpoint_accepts_and_clamps_a_range_and_rejects_nonsense(film, monkeypatch):
    client, sid, eid, shots, music = film
    sent = []
    monkeypatch.setattr('studio.app._submit', lambda kind, title, fn, **kw: sent.append((title, kw)) or {'id': 'x', 'title': title})
    url = f'/api/s/{sid}/e/{eid}/timeline/export'
    assert client.post(url).status_code == 200 and sent[-1][1]['window'] is None and 'export' in sent[-1][0]
    assert client.post(url, json={}).status_code == 200 and sent[-1][1]['window'] is None
    assert client.post(url, json={'range': [10, 20]}).status_code == 200
    assert sent[-1][1]['window'] == (10, 20) and 'preview' in sent[-1][0]
    assert client.post(url, json={'range': [10, 9999]}).status_code == 200 and sent[-1][1]['window'] == (10, 60)
    count = len(sent)
    for bad in ([20, 10], [10, 10], [-1, 5], [1.5, 9], 'x', [1], [1, 2, 3], [True, 9], [60, 70], [999, 1000]):
        assert client.post(url, json={'range': bad}).status_code == 400, bad
    assert len(sent) == count


# ── fades ────────────────────────────────────────────────────────────────

def test_fades_default_to_zero_validate_and_scale_with_fps(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    assert all(c['fade_in'] == 0 and c['fade_out'] == 0 for c in edit['clips'])
    edit['clips'][0].update(fade_in=10, fade_out=20)
    saved = client.put(url, json=edit)
    assert saved.status_code == 200, saved.text
    assert saved.json()['edit']['clips'][0]['fade_in'] == 10 and saved.json()['edit']['clips'][0]['fade_out'] == 20
    # old saved clips without the fields load with zeros
    old = copy.deepcopy(store.get_episode(sid, eid)['timeline'])
    for c in old['clips']:
        c.pop('fade_in'); c.pop('fade_out')
    store.update_episode(sid, eid, lambda e: e.update(timeline=old))
    assert all(c['fade_in'] == 0 and c['fade_out'] == 0 for c in client.get(url).json()['edit']['clips'])
    # fps change keeps the same moments, and never lets the fades exceed the clip
    store.update_episode(sid, eid, lambda e: e['timeline']['clips'][0].update(fade_in=15, fade_out=15))
    store.update_episode(sid, eid, lambda e: e.update(video={'fps': 24, 'width': 64, 'height': 96}))
    changed = client.get(url).json()['edit']['clips'][0]
    assert (changed['frames'], changed['fade_in'], changed['fade_out']) == (24, 12, 12)


@pytest.mark.parametrize('fades', [{'fade_in': -1}, {'fade_out': 1.5}, {'fade_in': 20, 'fade_out': 11}, {'fade_in': 31},
                                   {'fade_in': True}, {'fade_out': '5'}])
def test_invalid_fades_are_rejected_without_saving(film, fades):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']; edit['clips'][0].update(fades)
    assert client.put(url, json=edit).status_code == 400
    assert 'timeline' not in store.get_episode(sid, eid)


def test_a_preview_keeps_a_fade_only_where_its_clip_edge_is_in_the_range():
    clips = [{'id': 'a', 'frames': 30, 'source_in': 0.0, 'lipsync': True, 'fade_in': 6, 'fade_out': 9}]
    whole, = timeline.slice_clips(clips, 0, 30, 30)
    assert (whole['fade_in'], whole['fade_out']) == (6, 9)
    middle, = timeline.slice_clips(clips, 10, 20, 30)
    assert (middle['fade_in'], middle['fade_out'], middle['frames'], middle['lipsync']) == (0, 0, 10, False)
    tail, = timeline.slice_clips(clips, 12, 30, 30)
    assert (tail['fade_in'], tail['fade_out']) == (0, 9)
    head, = timeline.slice_clips(clips, 0, 20, 30)
    assert (head['fade_in'], head['fade_out']) == (6, 0)


def test_export_fades_picture_and_sound_without_changing_the_length(film):
    client, sid, eid, shots, music = film
    make_audio_video(store.abs_media(shots[0]['takes']['items'][0]['file']), 'red', 440)
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    clip = edit['clips'][0]; clip.update(fade_in=10, fade_out=12, muted=False)
    edit['clips'] = [clip]; edit['music'] = None
    edit = timeline.validate(store.get_episode(sid, eid), edit, settings.load())
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'])
    path = store.abs_media(store.get_episode(sid, eid)['outputs']['final'])
    video, audio = _frames_and_audio(path)
    assert len(video) == 30
    red = video[:, 48, 32, 0].astype(int)
    assert red[0] < 40 and red[29] < 40, 'dark at both ends'
    assert red[14] > 200 and red[18] > 200, 'full picture in between'
    assert red[3] < red[6] < red[9] and red[20] > red[24] > red[28], 'fades are gradual'
    rms = lambda a, b: float(np.sqrt(np.mean(audio[int(a * 8000):int(b * 8000)] ** 2)))
    assert rms(0, .06) < rms(.45, .6) * .5 and rms(.94, 1.0) < rms(.45, .6) * .5


def test_a_range_preview_inside_a_faded_clip_has_hard_cut_ends(film):
    client, sid, eid, shots, music = film
    make_audio_video(store.abs_media(shots[0]['takes']['items'][0]['file']), 'red', 440)
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    clip = edit['clips'][0]; clip.update(fade_in=10, fade_out=10, muted=True)
    edit['clips'] = [clip]; edit['music'] = None
    edit = timeline.validate(store.get_episode(sid, eid), edit, settings.load())
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=(2, 28))
    video, _ = _frames_and_audio(store.abs_media(store.get_episode(sid, eid)['outputs']['timeline_previews'][-1]['file']))
    assert len(video) == 26 and video[0, 48, 32, 0] > 200 and video[-1, 48, 32, 0] > 200


# ── titles ───────────────────────────────────────────────────────────────
TITLE = {'id': 't1', 'text': 'HELLO', 'start_frame': 10, 'frames': 10, 'x': .5, 'y': .5, 'size': .25, 'color': '#ffffff', 'box': False}


def test_titles_are_stored_sorted_validated_scaled_and_default_for_old_timelines(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    assert edit['titles'] == []
    edit['titles'] = [{**TITLE, 'id': 'b', 'start_frame': 40, 'color': '#FFAA00'}, {'id': 'a', 'text': ' Hi \n there ', 'start_frame': 5, 'frames': 8}]
    saved = client.put(url, json=edit)
    assert saved.status_code == 200, saved.text
    got = saved.json()['edit']['titles']
    assert [t['id'] for t in got] == ['a', 'b'] and got[0]['text'] == 'Hi \n there' and got[0]['box'] is True and got[0]['size'] == .06
    assert got[1]['color'] == '#ffaa00'
    store.update_episode(sid, eid, lambda e: e.update(video={'fps': 24, 'width': 64, 'height': 96}))
    scaled = client.get(url).json()['edit']['titles']
    assert (scaled[1]['start_frame'], scaled[1]['frames']) == (32, 8)
    old = {k: v for k, v in store.get_episode(sid, eid)['timeline'].items() if k != 'titles'}
    store.update_episode(sid, eid, lambda e: e.update(timeline=old))
    assert client.get(url).json()['edit']['titles'] == []


@pytest.mark.parametrize('change', [{'text': ''}, {'text': '   '}, {'text': 'x' * 201}, {'text': 5}, {'text': 'bad\x07'}, {'frames': 0},
                                    {'start_frame': -1}, {'start_frame': 1.5}, {'x': 1.5}, {'y': -.1}, {'size': .01}, {'size': .5},
                                    {'color': 'red'}, {'color': '#fff'}, {'box': 'yes'}, {'id': ''}])
def test_invalid_titles_are_rejected_without_saving(film, change):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']; edit['titles'] = [{**TITLE, **change}]
    assert client.put(url, json=edit).status_code == 400
    assert 'timeline' not in store.get_episode(sid, eid)
    edit['titles'] = [TITLE, TITLE]
    assert client.put(url, json=edit).status_code == 400, 'duplicate ids'
    edit['titles'] = [{**TITLE, 'id': str(i)} for i in range(201)]
    assert client.put(url, json=edit).status_code == 400


def test_title_picture_is_transparent_positioned_wrapped_and_kept_inside_the_frame(tmp_path):
    from PIL import Image
    from studio import titles
    path = titles.render_png({**TITLE, 'x': .5, 'y': .5}, 200, 300, tmp_path / 'a.png')
    image = Image.open(path); assert image.size == (200, 300) and image.mode == 'RGBA'
    alpha = np.array(image)[:, :, 3]
    assert alpha[0, 0] == 0 and alpha[150].max() == 255
    ys, xs = np.nonzero(alpha)
    assert abs((xs.min() + xs.max()) / 2 - 100) < 6 and abs((ys.min() + ys.max()) / 2 - 150) < 12, 'centred where asked'
    long = titles.render_png({**TITLE, 'text': 'one two three four five six seven eight nine ten', 'size': .08}, 200, 300, tmp_path / 'b.png')
    rows = np.nonzero(np.array(Image.open(long))[:, :, 3].max(axis=1))[0]
    assert rows.max() - rows.min() > 40, 'long text wraps onto several lines'
    corner = np.array(Image.open(titles.render_png({**TITLE, 'x': 0, 'y': 1, 'box': True}, 200, 300, tmp_path / 'c.png')))[:, :, 3]
    ys, xs = np.nonzero(corner)
    assert xs.min() >= 0 and ys.max() <= 299 and xs.max() < 200, 'pushed back inside the frame'


def _title_film(film, **title):
    client, sid, eid, shots, music = film
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    edit['clips'] = [edit['clips'][0]]; edit['music'] = None; edit['clips'][0]['muted'] = True
    edit['titles'] = [{**TITLE, **title}]
    return sid, eid, timeline.validate(store.get_episode(sid, eid), edit, settings.load())


def _white_per_frame(video):
    # the film is solid red: any bright green means title text is on screen
    return video[:, :, :, 1].max(axis=(1, 2)) > 200


def test_export_shows_the_title_only_during_its_frames(film):
    sid, eid, edit = _title_film(film)
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'])
    video, _ = _frames_and_audio(store.abs_media(store.get_episode(sid, eid)['outputs']['final']))
    assert len(video) == 30
    assert list(np.nonzero(_white_per_frame(video))[0]) == list(range(10, 20))
    # the text sits in the middle of the frame
    ys, xs = np.nonzero(video[15, :, :, 1] > 200)
    assert abs(xs.mean() - 32) < 8 and abs(ys.mean() - 48) < 10


def test_a_range_preview_shifts_titles_into_the_rendered_span(film):
    sid, eid, edit = _title_film(film)
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=(5, 25))
    video, _ = _frames_and_audio(store.abs_media(store.get_episode(sid, eid)['outputs']['timeline_previews'][-1]['file']))
    assert len(video) == 20 and list(np.nonzero(_white_per_frame(video))[0]) == list(range(5, 15))
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=(0, 8))
    video, _ = _frames_and_audio(store.abs_media(store.get_episode(sid, eid)['outputs']['timeline_previews'][-1]['file']))
    assert not _white_per_frame(video).any(), 'a title outside the span is not drawn'


# ── lip-sync settings ────────────────────────────────────────────────────

def test_lipsync_settings_default_to_the_tested_values_and_are_clamped_to_what_the_node_accepts():
    assert settings.lipsync_params(settings.DEFAULTS) == {'steps': 20, 'guidance': 2.0, 'seed': 1247}
    assert settings.lipsync_params({}) == settings.lipsync_params(None) == {'steps': 20, 'guidance': 2.0, 'seed': 1247}
    assert settings.lipsync_params({'lipsync': {'steps': 500, 'guidance': 9, 'seed': -5}}) == {'steps': 100, 'guidance': 3.0, 'seed': 0}
    assert settings.lipsync_params({'lipsync': {'steps': 0, 'guidance': 0.2}}) == {'steps': 1, 'guidance': 1.0, 'seed': 1247}
    assert settings.lipsync_params({'lipsync': {'steps': '35', 'guidance': '2.26'}}) == {'steps': 35, 'guidance': 2.3, 'seed': 1247}
    for junk in ('x', None, float('nan'), float('inf'), True, [], {}):
        assert settings.lipsync_params({'lipsync': {'steps': junk, 'guidance': junk, 'seed': junk}}) == {'steps': 20, 'guidance': 2.0, 'seed': 1247}, junk
    assert settings.lipsync_params({'lipsync': 'broken'}) == {'steps': 20, 'guidance': 2.0, 'seed': 1247}


def test_the_configured_lipsync_values_reach_the_comfyui_workflow(film, tmp_path):
    class Recorder:
        sent = None
        def upload(self, path, name): return name
        def run(self, workflow, cancel=None):
            self.sent = workflow; return {'videos': []}
    def sent_by(user):
        settings.save(user); client = Recorder()
        (tmp_path / 'a.mp4').write_bytes(b''); (tmp_path / 'v.wav').write_bytes(b'')
        with pytest.raises(RuntimeError, match='no video'):
            lipsync.correct_clip(jobs.Job('t', 't', None, {}), settings.load(), client, str(tmp_path / 'a.mp4'), str(tmp_path / 'v.wav'),
                                 str(tmp_path / 'o.mp4'), 30, 64, 96, 30, 'p')
        return client.sent['3']['inputs']
    user = settings.raw_user()
    node = sent_by(user)
    assert (node['steps'], node['guidance'], node['seed']) == (20, 2.0, 1247), 'the new default'
    node = sent_by({**user, 'lipsync': {'steps': 33, 'guidance': 2.5, 'seed': 7}})
    assert (node['steps'], node['guidance'], node['seed']) == (33, 2.5, 7)


def test_changing_lipsync_settings_invalidates_cached_synced_clips(film, monkeypatch):
    client, sid, eid, shots, music = film
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    edit['clips'][1]['lipsync'] = True; edit['music']['isolate_vocals'] = False
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: None)
    calls = []
    def correct(job, cfg, client, source, voice, dest, *args):
        import shutil
        calls.append(settings.lipsync_params(cfg)); shutil.copyfile(source, dest)
    monkeypatch.setattr(lipsync, 'correct_clip', correct)
    render = lambda: timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'])
    render(); render()
    assert len(calls) == 1, 'unchanged settings reuse the finished clip'
    user = settings.raw_user(); settings.save({**user, 'lipsync': {'guidance': 1.5}})
    render()
    assert len(calls) == 2 and calls[-1]['guidance'] == 1.5, 'a different guidance must not reuse the old clip'
    render()
    assert len(calls) == 2
    settings.save({**user, 'lipsync': {'steps': 30}})
    render()
    assert len(calls) == 3 and calls[-1]['steps'] == 30
    settings.save(user)
    render()
    assert len(calls) == 3, 'going back to earlier settings finds the earlier clip again'


# ── engines that cannot run on this install ──────────────────────────────

def test_an_engine_without_its_workflow_file_is_flagged_refused_early_and_never_unloads_models(film, monkeypatch, tmp_path):
    from studio import providers
    client, sid, eid, shots, music = film
    user = settings.raw_user()
    settings.save({**user, 'ltx': {'template': str(tmp_path / 'missing.json')}})
    models = {m['id']: m for m in client.get('/api/models').json()}
    assert models['ltx']['available'] is False and 'workflow file is missing' in models['ltx']['note'] and 'Wan 2.2' in models['ltx']['note']
    assert all(models[k]['available'] and models[k]['note'] == '' for k in ('wan', 'MiniMax-H3', 'MiniMax-Hailuo-2.3'))
    sent = []
    monkeypatch.setattr('studio.app._submit', lambda kind, title, fn, **kw: sent.append(kw) or {'id': 'x', 'title': title})
    refused = client.post(f'/api/s/{sid}/e/{eid}/takes', json={'engine': 'ltx', 'model': 'ltx'})
    assert refused.status_code == 400 and 'not set up' in refused.json()['detail'] and not sent
    assert client.post(f'/api/s/{sid}/e/{eid}/takes', json={'engine': 'wan', 'model': 'wan'}).status_code == 200 and len(sent) == 1
    # a job that resolves to LTX on its own (shot or project default) says so clearly, before unloading anything
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: (_ for _ in ()).throw(AssertionError('must not touch the GPU')))
    with pytest.raises(ValueError, match='not set up'):
        jobs.job_takes(jobs.Job('takes', 't', None, {}), sid, eid, force=True, engine_override='ltx', model_override='ltx')
    # once the file exists the engine is offered again
    template = tmp_path / 'i2v.json'; template.write_text('{}')
    settings.save({**user, 'ltx': {'template': str(template)}})
    assert {m['id']: m for m in client.get('/api/models').json()}['ltx']['available'] is True
    assert providers.engine_problem(settings.load(), 'ltx') == '' and providers.engine_problem(settings.load(), 'wan') == ''


# ── clip speed ───────────────────────────────────────────────────────────

def _numbered_source(film, count=90, fps=30):
    """Replace the first take with footage whose red channel is 2 * its own frame number."""
    client, sid, eid, shots, music = film
    source = store.abs_media(shots[0]['takes']['items'][0]['file'])
    frames = np.zeros((count, 96, 64, 3), dtype=np.uint8)
    for n in range(count):
        frames[n, :, :, 0] = n * 2
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '64x96', '-r', str(fps),
                    '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '0', source], input=frames.tobytes(), check=True)
    return source


def _render_speed(film, source_in, frames, speed, window=None, music=False):
    client, sid, eid, shots, _ = film
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    clip = edit['clips'][0]; clip.update(source_in=source_in, frames=frames, speed=speed, muted=False)
    edit['clips'] = [clip]
    if not music:
        edit['music'] = None
    edit = timeline.validate(store.get_episode(sid, eid), edit, settings.load())
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'], window=window)
    ep = store.get_episode(sid, eid)
    path = store.abs_media(ep['outputs']['timeline_previews'][-1]['file'] if window else ep['outputs']['final'])
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', path, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 96, 64, 3)[:, 48, 32, 0].astype(float) / 2


def test_speed_defaults_to_one_and_old_timelines_load(film):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']
    assert [c['speed'] for c in edit['clips']] == [1, 1]
    assert client.put(url, json=edit).status_code == 200
    old = copy.deepcopy(store.get_episode(sid, eid)['timeline'])
    for c in old['clips']:
        del c['speed']
    store.update_episode(sid, eid, lambda e: e.update(timeline=old))
    assert [c['speed'] for c in client.get(url).json()['edit']['clips']] == [1, 1]


@pytest.mark.parametrize('speed', [0, .2, 4.5, -1, 'fast', True, None])
def test_invalid_speeds_are_rejected_without_saving(film, speed):
    client, sid, eid, shots, music = film
    url = f'/api/s/{sid}/e/{eid}/timeline'
    edit = client.get(url).json()['edit']; edit['clips'][0]['speed'] = speed
    assert client.put(url, json=edit).status_code == 400
    assert 'timeline' not in store.get_episode(sid, eid)


def test_speed_counts_source_frames_and_is_not_allowed_with_lipsync(film):
    client, sid, eid, shots, music = film
    _numbered_source(film)                      # 90 source frames
    episode, cfg = store.get_episode(sid, eid), settings.load()
    edit = timeline.default_edit(episode, cfg)
    c = edit['clips'][0]

    def accepted(**fields):
        c.update({'source_in': 0, 'speed': 1, 'lipsync': False, **fields})
        try:
            timeline.validate(episode, copy.deepcopy(edit), cfg)
            return True
        except ValueError:
            return False
    assert accepted(frames=90)
    assert not accepted(frames=90, speed=2)     # 90 timeline frames would need 180 source frames
    assert accepted(frames=45, speed=2)         # 45 * 2 = 90: exactly the whole source
    assert not accepted(frames=45, speed=2, source_in=1 / 30)  # one frame later does not fit
    assert accepted(frames=180, speed=.5) and not accepted(frames=182, speed=.5)
    assert not accepted(frames=10, speed=1.5, lipsync=True), 'lip-sync clips must play at normal speed'
    assert accepted(frames=10, lipsync=True)


def test_atempo_chain_stays_inside_the_filters_range_and_multiplies_to_the_speed():
    for speed in (.25, .3, .5, 1, 1.5, 2, 3.7, 4):
        factors = [float(part.split('=')[1]) for part in timeline.atempo(speed).split(',')]
        assert all(.5 <= f <= 2 for f in factors)
        assert abs(np.prod(factors) - speed) < 1e-6


def test_a_range_preview_of_a_sped_up_clip_starts_at_the_right_source_frame():
    clips = [{'id': 'a', 'frames': 30, 'source_in': 1.0, 'speed': 2, 'lipsync': False}]
    part, = timeline.slice_clips(clips, 10, 20, 30)
    assert part['frames'] == 10 and abs(part['source_in'] - (1.0 + 10 / 30 * 2)) < 1e-9 and part['speed'] == 2


@pytest.mark.parametrize('speed,frames', [(2, 30), (.5, 30), (1.5, 24), (3, 20), (.25, 12)])
def test_export_plays_the_right_source_frames_at_every_speed(film, speed, frames):
    _numbered_source(film)
    shown = _render_speed(film, 10 / 30, frames, speed)
    assert len(shown) == frames
    expected = 10 + np.arange(frames) * speed
    assert np.max(np.abs(shown - expected)) <= 1.6, (shown[:8], expected[:8])
    assert np.all(np.diff(shown) >= 0)


def test_export_speed_works_on_a_24_fps_take_and_with_music(film):
    _numbered_source(film, count=72, fps=24)    # 3 s at 24 fps on a 30 fps timeline
    shown = _render_speed(film, 7 / 30, 30, 2, music=True)
    assert len(shown) == 30
    expected = (7 / 30 + np.arange(30) / 30 * 2) * 24
    assert np.max(np.abs(shown - np.round(expected))) <= 2.1


def test_a_range_preview_at_speed_matches_the_same_frames_of_the_full_export(film):
    _numbered_source(film)
    full = _render_speed(film, 0, 40, 1.5)
    part = _render_speed(film, 0, 40, 1.5, window=(10, 30))
    assert len(part) == 20 and np.max(np.abs(part - full[10:30])) <= 1.6


def test_a_sped_up_clip_keeps_the_audio_length_and_pitch_free_tempo(film):
    client, sid, eid, shots, music = film
    make_audio_video(store.abs_media(shots[0]['takes']['items'][0]['file']), 'red', 440)
    edit = timeline.default_edit(store.get_episode(sid, eid), settings.load())
    clip = edit['clips'][0]; clip.update(source_in=0, frames=15, speed=2, muted=False)
    edit['clips'] = [clip]; edit['music'] = None
    edit = timeline.validate(store.get_episode(sid, eid), edit, settings.load())
    timeline.render(jobs.Job('timeline', 'test', None, {}), sid, eid, edit, settings.load()['video'])
    video, audio = _frames_and_audio(store.abs_media(store.get_episode(sid, eid)['outputs']['final']))
    assert len(video) == 15
    assert abs(len(audio) / 8000 - .5) < .05
    assert np.sqrt(np.mean(audio[:3000] ** 2)) > 0, 'audio from the whole 1 s source is squeezed into 0.5 s'
