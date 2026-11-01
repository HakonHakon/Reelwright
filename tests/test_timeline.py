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
