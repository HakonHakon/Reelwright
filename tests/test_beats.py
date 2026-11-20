"""Beat detection against synthetic tracks with known beat times, plus the timeline endpoints that use it."""
import pathlib
import shutil
import wave

import numpy as np
import pytest

from studio import beats, store
from test_lipsync import film  # noqa: F401  (fixture)

SR = beats.SR


def _kick(length=.12):
    t = np.arange(int(length * SR)) / SR
    return (np.sin(2 * np.pi * (60 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 25)).astype(np.float32)


def _burst(rng, seconds, decay, gain):
    n = int(seconds * SR)
    return (gain * rng.standard_normal(n) * np.exp(-np.arange(n) / (decay * SR))).astype(np.float32)


def _place(y, start, sound):
    start = int(start * SR)
    y[start:start + len(sound)] += sound[:max(0, len(y) - start)]


def offbeat_hats(bpm, seconds=30, offset=.35, noise=0.0, seed=1):
    """Kick on every beat (accented on the bar), hi-hat between beats: the hardest pattern for flux detectors."""
    rng = np.random.default_rng(seed)
    y = np.zeros(int(seconds * SR), dtype=np.float32)
    period, truth = 60 / bpm, []
    for k, t in enumerate(np.arange(offset, seconds - 1.0, period)):
        _place(y, t, (1.0 if k % 4 == 0 else .6) * _kick())
        _place(y, t + period / 2, _burst(rng, .03, .008, .2))
        truth.append(t)
    if noise:
        y += (noise * rng.standard_normal(len(y))).astype(np.float32)
    return y, np.array(truth)


def kick_snare_hats(bpm, seconds=30, offset=.35, seed=2):
    rng = np.random.default_rng(seed)
    y = np.zeros(int(seconds * SR), dtype=np.float32)
    period, truth = 60 / bpm, []
    for k, t in enumerate(np.arange(offset, seconds - 1.0, period)):
        _place(y, t, _kick() if k % 2 == 0 else _burst(rng, .1, .03, .5))
        for half in (0, .5):
            _place(y, t + half * period, _burst(rng, .025, .006, .12))
        truth.append(t)
    return y, np.array(truth)


def _errors(result, truth):
    found = np.array(result['beats'])
    errors = np.array([b - truth[np.argmin(abs(truth - b))] for b in found])
    coverage = np.mean([np.min(abs(found - t)) < .07 for t in truth])
    return errors, coverage


@pytest.mark.parametrize('bpm', [90, 100, 120, 128, 140])
@pytest.mark.parametrize('make', [offbeat_hats, lambda bpm: offbeat_hats(bpm, noise=.05), kick_snare_hats],
                         ids=['offbeat-hats', 'noisy', 'kick-snare'])
def test_finds_tempo_and_beat_times(bpm, make):
    signal, truth = make(bpm)
    result = beats.analyze_samples(signal)
    assert result['tempo'] == pytest.approx(bpm, rel=.02)
    errors, coverage = _errors(result, truth)
    assert coverage >= .95, 'every true beat should have a detected beat within 70 ms'
    assert abs(np.median(errors)) < .015, 'timing should be calibrated to within a frame'
    assert len(result['beats']) == len(result['strength'])
    assert np.all(np.diff(result['beats']) > 0)


@pytest.mark.parametrize('bpm', [100, 120, 140])
def test_bar_lines_follow_the_accent(bpm):
    signal, truth = offbeat_hats(bpm, noise=.05)
    result = beats.analyze_samples(signal)
    assert result['bar_confidence'] > 1.1
    found = np.array(result['beats'])
    downbeats = [round((truth[np.argmin(abs(truth - found[i]))] - truth[0]) / (60 / bpm)) % 4 for i in result['bars']]
    assert np.mean([d == 0 for d in downbeats]) >= .8


def test_slow_music_is_not_reported_at_double_speed():
    signal, truth = offbeat_hats(90)
    result = beats.analyze_samples(signal)
    assert result['tempo'] == pytest.approx(90, rel=.03)
    assert len(result['beats']) == pytest.approx(len(truth), abs=2)


@pytest.mark.parametrize('bpm,folded', [(178, 89), (60, 120), (120, 120), (165, 165), (166, 83), (250, 125), (35, 70), (30, 120)])
def test_tempo_folding(bpm, folded):
    assert beats.fold_tempo(bpm) == pytest.approx(folded)


def test_silence_noise_and_short_audio_do_not_crash():
    silent = beats.analyze_samples(np.zeros(SR * 10, dtype=np.float32))
    assert silent['tempo'] is None and silent['beats'] == []
    short = beats.analyze_samples(np.random.default_rng(0).standard_normal(SR).astype(np.float32))
    assert short['beats'] == [] and short['duration'] == pytest.approx(1, abs=.01)
    noise = beats.analyze_samples(np.random.default_rng(0).standard_normal(SR * 20).astype(np.float32) * .3)
    assert np.all(np.diff(noise['beats']) > 0) and all(0 <= b <= 20 for b in noise['beats'])


def _write_wav(path, signal):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(SR)
        out.writeframes((np.clip(signal, -1, 1) * 32767).astype('<i2').tobytes())


@pytest.fixture
def ffmpeg():
    path = shutil.which('ffmpeg')
    if not path:
        pytest.skip('requires ffmpeg')
    return path


def test_analyze_file_caches_by_file_identity(tmp_path, ffmpeg, monkeypatch):
    song = tmp_path / 'song.wav'
    _write_wav(song, offbeat_hats(120, seconds=12, noise=.05)[0])
    first = beats.analyze_file(ffmpeg, str(song), tmp_path / 'cache')
    assert first['tempo'] == pytest.approx(120, rel=.02) and first['truncated'] is False
    assert len(list((tmp_path / 'cache').glob('beats_*.json'))) == 1
    monkeypatch.setattr(beats, 'decode', lambda *a, **k: (_ for _ in ()).throw(AssertionError('should use the cache')))
    assert beats.analyze_file(ffmpeg, str(song), tmp_path / 'cache') == first
    song.write_bytes(song.read_bytes() + b'\0\0')  # a changed file is a new cache key
    monkeypatch.undo()
    assert beats.analyze_file(ffmpeg, str(song), tmp_path / 'cache')['tempo'] == pytest.approx(120, rel=.02)
    assert len(list((tmp_path / 'cache').glob('beats_*.json'))) == 2


def test_beats_endpoint_validates_the_file(film):  # noqa: F811
    client, sid, eid, shots, music = film
    base = f'/api/s/{sid}/e/{eid}/timeline/beats'
    ok = client.get(base, params={'file': store.rel(str(music))})
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) >= {'tempo', 'beats', 'strength', 'bars', 'duration', 'meter', 'bar_confidence'}
    assert body['duration'] == pytest.approx(2, abs=.05)
    for bad in ['../../etc/passwd', 'not/in/episode.wav', shots[0]['takes']['items'][0]['file']]:
        assert client.get(base, params={'file': bad}).status_code == 400


def test_thumbnail_endpoint_serves_cached_jpegs_and_rejects_bad_requests(film):  # noqa: F811
    client, sid, eid, shots, music = film
    take = shots[0]['takes']['items'][0]['file']
    url = f'/api/s/{sid}/e/{eid}/timeline/thumb'
    first = client.get(url, params={'file': take, 't': .52, 'h': 64})
    assert first.status_code == 200 and first.headers['content-type'] == 'image/jpeg'
    assert first.content[:2] == b'\xff\xd8' and 'max-age' in first.headers['cache-control']
    assert client.get(url, params={'file': take, 't': .52, 'h': 64}).content == first.content
    assert client.get(url, params={'file': take, 't': .53, 'h': 64}).content == first.content  # quantised to 40 ms
    thumbs = list((pathlib.Path(store.ep_dir(sid, eid)) / 'out' / 'thumbs').glob('*.jpg'))
    assert len(thumbs) == 1
    for params in [{'file': 'nope.mp4'}, {'file': store.rel(str(music))}, {'file': take, 't': -1},
                   {'file': take, 't': 'nan'}, {'file': take, 't': 99}]:
        assert client.get(url, params=params).status_code == 400, params
    assert client.get(url, params={'file': take, 't': 0, 'h': 5000}).status_code == 200  # height is clamped
