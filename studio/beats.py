"""Tempo and beat detection for the timeline editor (numpy only: no scipy or librosa).

The pipeline is the classic one: log-band spectral flux gives an onset-strength envelope, its
autocorrelation gives the tempo, and a dynamic-programming pass (Ellis, 2007) picks the beat
sequence that best balances "lands on strong onsets" against "keeps a steady period".
Bar lines are an estimate: the beat phase (out of four) with the strongest onsets.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

from . import media

SR = 22050
N_FFT = 1024
HOP = 256
FRAME_RATE = SR / HOP
MAX_SECONDS = 900
VERSION = 1
# Spectral flux peaks when a transient first enters the analysis window, which is a little before
# the window's centre. Measured against synthetic click tracks (see tests/test_beats.py).
ONSET_OFFSET = 0.034
# Beats are felt mostly in the low end (kick, bass); weight the bands from BASS_WEIGHT down to 1.
BASS_WEIGHT = 3.0


def decode(ffmpeg, path, max_seconds=MAX_SECONDS):
    command = [ffmpeg, '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', str(SR), '-t', str(max_seconds),
               '-f', 'f32le', '-']
    kwargs = {'capture_output': True}
    if os.name == 'nt':
        kwargs['creationflags'] = 0x08000000
    result = subprocess.run(command, **kwargs)
    if result.returncode:
        raise media.MediaError('Could not read the audio to find the beat')
    return np.frombuffer(result.stdout, dtype='<f4')


def onset_envelope(y):
    """Onset strength per hop, normalised to unit standard deviation (empty for very short audio)."""
    if len(y) < N_FFT * 4:
        return np.zeros(0, dtype=np.float32)
    window = np.hanning(N_FFT).astype(np.float32)
    frames = 1 + (len(y) - N_FFT) // HOP
    edges = np.unique(np.round(np.logspace(np.log10(3), np.log10(N_FFT // 2), 33)).astype(int))
    bands = []
    for first in range(0, frames, 1024):
        count = min(1024, frames - first)
        index = np.arange(N_FFT)[None, :] + HOP * (first + np.arange(count))[:, None]
        magnitude = np.abs(np.fft.rfft(y[index] * window, axis=1))
        bands.append(np.stack([magnitude[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1))
    bands = np.log1p(np.concatenate(bands) * 30)
    weights = np.linspace(BASS_WEIGHT, 1.0, bands.shape[1]) ** 2
    flux = (np.maximum(0, np.diff(bands, axis=0)) * weights).sum(axis=1) / weights.sum()
    flux = np.concatenate([[0], flux])
    # Subtract a slow-moving average so sustained loud sections do not drown out the hits.
    width = max(3, int(FRAME_RATE * 0.5))
    average = np.convolve(flux, np.ones(width) / width, mode='same')
    envelope = np.maximum(0, flux - average)
    deviation = envelope.std()
    return (envelope / deviation).astype(np.float32) if deviation > 1e-9 else np.zeros(len(envelope), dtype=np.float32)


def estimate_tempo(envelope, low=60.0, high=200.0):
    """Tempo in BPM from the envelope's autocorrelation, or None if there is no clear pulse."""
    n = len(envelope)
    if n < FRAME_RATE * 4 or not envelope.any():
        return None
    centred = envelope - envelope.mean()
    spectrum = np.fft.rfft(centred, 2 * n)
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[:n]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    longest = int(min(n // 2 - 1, FRAME_RATE * 60 / low * 1.05))
    lags = np.arange(max(2, int(FRAME_RATE * 60 / high * 0.95)), longest)
    if len(lags) < 3:
        return None
    # A beat period is supported by its multiples as well, which separates it from its subdivisions.
    support = ac[lags] + 0.5 * ac[np.minimum(lags * 2, n - 1)] + 0.25 * ac[np.minimum(lags * 4, n - 1)]
    bpm = 60 * FRAME_RATE / lags
    prior = np.exp(-0.5 * (np.log2(bpm / 120.0) / 1.0) ** 2)
    scores = support * prior
    best = int(np.argmax(scores))
    if scores[best] <= 0:
        return None
    lag = float(lags[best])
    if 0 < best < len(lags) - 1:  # parabolic refinement between lag samples
        a, b, c = scores[best - 1], scores[best], scores[best + 1]
        denominator = a - 2 * b + c
        if denominator < 0:
            lag += 0.5 * (a - c) / denominator
    return float(60 * FRAME_RATE / lag)


def fold_tempo(bpm, low=70.0, high=165.0):
    """Report the tempo in the range most music is felt in; the editor can still double or halve it."""
    while bpm > high:
        bpm /= 2
    while bpm < low:
        bpm *= 2
    return bpm


def track_beats(envelope, bpm, tightness=100.0):
    """Frame indices of the beats: best-scoring path through the envelope with a steady period."""
    period = 60 * FRAME_RATE / bpm
    sigma = max(1.0, period / 32)
    half = int(round(4 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-half, half + 1) / sigma) ** 2)
    local = np.convolve(envelope, kernel, mode='same')
    near, far = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(-far, -near + 1)
    penalty = -tightness * np.log(-offsets / period) ** 2
    n = len(local)
    cumulative = np.concatenate([np.zeros(far), local.astype(np.float64)])
    link = np.full(n, -1, dtype=np.int64)
    strongest = float(local.max()) or 1.0
    started = False
    for i in range(n):
        window = cumulative[i + far + offsets] + penalty
        j = int(np.argmax(window))
        cumulative[i + far] = local[i] + window[j]
        if not started and local[i] < 0.01 * strongest:
            continue
        started = True
        target = i + offsets[j]
        link[i] = target if target >= 0 else -1
    score = cumulative[far:]
    peaks = np.flatnonzero((score[1:-1] > score[:-2]) & (score[1:-1] >= score[2:])) + 1
    if not len(peaks):
        return np.zeros(0, dtype=int)
    threshold = 0.5 * np.median(score[peaks])
    eligible = peaks[score[peaks] >= threshold]
    last = int(eligible[-1]) if len(eligible) else int(peaks[-1])
    beats = [last]
    while link[beats[-1]] >= 0:
        beats.append(int(link[beats[-1]]))
    beats = np.array(beats[::-1], dtype=int)
    strength = local[beats]
    if len(beats) > 2:  # drop weak leading and trailing beats (silence, fades)
        cutoff = 0.5 * float(np.sqrt(np.mean(strength ** 2)))
        keep = np.flatnonzero(strength > cutoff)
        if len(keep):
            beats = beats[keep[0]:keep[-1] + 1]
    return beats


def analyze_samples(y):
    envelope = onset_envelope(y)
    duration = len(y) / SR
    empty = {'version': VERSION, 'duration': round(duration, 3), 'tempo': None, 'beats': [], 'strength': [],
             'bars': [], 'bar_confidence': 0.0, 'meter': 4}
    bpm = estimate_tempo(envelope)
    if bpm is None:
        return empty
    frames = track_beats(envelope, fold_tempo(bpm))
    if len(frames) < 4:
        return empty
    times = frames * HOP / SR + ONSET_OFFSET
    tempo = 60.0 / float(np.median(np.diff(times)))
    strength = envelope[frames]
    scale = float(np.percentile(strength, 95)) or 1.0
    normalised = np.clip(strength / scale, 0, 1)
    meter = 4
    phases = [float(normalised[k::meter].mean()) if len(normalised[k::meter]) else 0.0 for k in range(meter)]
    phase = int(np.argmax(phases))
    others = [p for k, p in enumerate(phases) if k != phase]
    confidence = phases[phase] / (float(np.mean(others)) or 1.0) if others else 0.0
    return {'version': VERSION, 'duration': round(duration, 3), 'tempo': round(tempo, 2),
            'beats': [round(float(t), 3) for t in times], 'strength': [round(float(s), 3) for s in normalised],
            'bars': list(range(phase, len(times), meter)), 'bar_confidence': round(confidence, 2), 'meter': meter}


def analyze_file(ffmpeg, path, cache_dir=None):
    """Analyse an audio file, caching the result next to the episode (keyed on the file's identity)."""
    stat = os.stat(path)
    key = hashlib.sha1(f'{path}|{stat.st_mtime_ns}|{stat.st_size}|{VERSION}'.encode()).hexdigest()[:20]
    cached = Path(cache_dir) / f'beats_{key}.json' if cache_dir else None
    if cached and cached.is_file():
        try:
            return json.loads(cached.read_text())
        except (OSError, ValueError):
            pass
    started = time.time()
    samples = decode(ffmpeg, path)
    result = analyze_samples(samples)
    result['truncated'] = len(samples) >= MAX_SECONDS * SR
    result['analysis_seconds'] = round(time.time() - started, 2)
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix('.tmp')
        temporary.write_text(json.dumps(result))
        temporary.replace(cached)
    return result
