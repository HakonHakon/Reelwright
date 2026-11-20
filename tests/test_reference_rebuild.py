"""Studio queue ownership, resumability and cancellation of reference renders."""
import copy
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

import pytest

from studio import jobs, reference_rebuild, settings, store
from test_lipsync import film


def prepare_reference(film):
    client, sid, eid, shots, music = film
    def change(ep):
        ep['reference_rebuild'] = {'source': shots[0]['takes']['items'][0]['file'], 'status': 'prepared', 'shots_done': 1}
        for i, sh in enumerate(ep['shots']):
            sh['reference'] = {'start_frame': i*30, 'end_frame': (i+1)*30, 'start_s': i, 'end_s': i+1}
        ep['shots'][1]['takes']['items'] = []
    store.update_episode(sid, eid, change)
    return client, sid, eid


def test_reference_submission_uses_shared_queue_and_deduplicates(film, monkeypatch):
    client, sid, eid = prepare_reference(film)
    queue = SimpleNamespace(cv=threading.Condition(), jobs={})
    monkeypatch.setattr(jobs, 'QUEUE', queue)
    submitted = []
    def submit(kind, title, fn, **params):
        job = jobs.Job(kind, title, fn, params)
        queue.jobs[job.id] = job
        submitted.append(job)
        return job.public()
    monkeypatch.setattr('studio.app._submit', submit)
    url = f'/api/s/{sid}/e/{eid}/reference_render'
    first = client.post(url, json={})
    assert first.status_code == 200
    assert first.json()['status'] == 'queued'
    assert submitted[0].fn is jobs.job_reference_rebuild
    assert client.post(url, json={}).json()['id'] == first.json()['id']
    assert len(submitted) == 1
    assert client.post(url, json={'shot_ids': ['missing']}).status_code == 400
    store.update_episode(sid, eid, lambda ep: ep.pop('reference_rebuild'))
    assert client.post(url, json={}).status_code == 400


def test_resume_registers_completed_take_without_gpu_rerender(film, monkeypatch):
    _, sid, eid = prepare_reference(film)
    ep = store.get_episode(sid, eid)
    sh = ep['shots'][0]
    out = Path(store.ep_dir(sid, eid)) / 'out' / 'source_faithful_regeneration'
    out.mkdir(parents=True)
    cached = out / (sh['id'] + '.mp4')
    shutil.copy2(store.abs_media(sh['takes']['items'][0]['file']), cached)
    original_bytes = cached.read_bytes()
    def forbid_render(*args, **kwargs):
        raise AssertionError('Completed footage must not be generated again')
    client = SimpleNamespace(free=lambda: None, upload=lambda *a: 'uploaded', run=forbid_render)
    monkeypatch.setattr(reference_rebuild.comfy, 'Comfy', lambda host: client)
    monkeypatch.setattr(jobs, '_gpu_prepare', lambda *a: None)
    monkeypatch.setattr(store, 'bible', lambda sid: {'characters': {
        cid: {'refs': ['first.jpg', sh['takes']['items'][0]['file']]} for cid in ('hakon', 'maryam')}})
    job = jobs.Job('reference_render', 'Reference', None, {})
    reference_rebuild.render(job, sid, eid, ids=[sh['id']])
    assert job.progress == [1, 2]
    assert cached.read_bytes() == original_bytes
    updated = store.get_episode(sid, eid)
    takes = updated['shots'][0]['takes']
    assert takes['items'][takes['selected']]['file'] == store.rel(str(cached))
    assert len(takes['items']) == 2  # Earlier library candidates remain available.
    assert any('resume existing' in line for line in job.log)


def test_cancelled_reference_job_keeps_resume_state(film, monkeypatch):
    _, sid, eid = prepare_reference(film)
    before = copy.deepcopy(store.get_episode(sid, eid)['shots'])
    def cancel(*args, **kwargs):
        raise jobs.Cancelled()
    monkeypatch.setattr(reference_rebuild, 'render', cancel)
    job = jobs.Job('reference_render', 'Reference', None, {})
    job.cancel_flag = True
    with pytest.raises(jobs.Cancelled):
        jobs.job_reference_rebuild(job, sid, eid)
    updated = store.get_episode(sid, eid)
    assert updated['reference_rebuild']['status'] == 'cancelled'
    assert updated['shots'] == before
