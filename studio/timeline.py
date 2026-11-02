"""Frame-based editing and rendering of existing Studio media."""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import threading
import uuid

import re

from . import media, settings, store, titles as titles_mod, workflows


class EditConflict(ValueError):
    pass


MARKER_COLORS = ('red', 'orange', 'yellow', 'green', 'blue', 'purple')
MAX_MARKERS = 500
MAX_TITLES = 200
MAX_FRAME = 10_000_000


def assets(episode):
    files = {t['file'] for sh in episode['shots'] for t in sh['takes'].get('items', [])}
    outputs = episode.get('outputs', {})
    files.update(v['file'] for v in outputs.get('lipsync_versions', []))
    files.update(v['file'] for v in outputs.get('timeline_versions', []))
    files.update(outputs[k] for k in ('final', 'before_lipsync') if outputs.get(k))
    files.update(c['source'] for c in episode.get('timeline', {}).get('clips', []))
    files.update(o['source'] for o in episode.get('timeline', {}).get('overlays') or [])
    return files


def source_info(episode, file, cfg, audio=False):
    allowed = assets(episode)
    if audio:
        allowed = {episode.get('music', {}).get('file'), (episode.get('timeline', {}).get('music') or {}).get('file'),
                   *episode.get('audio_assets', []), *(t.get('file') for t in episode.get('timeline', {}).get('audio') or [])}
    if not isinstance(file, str) or file not in allowed:
        raise ValueError('Choose media from this episode')
    path = Path(store.abs_media(file))
    if not path.is_file():
        raise ValueError('Source media is missing; replace or remove this clip')
    info = media.probe(cfg['ffprobe'], str(path))
    if audio and not info['has_audio']:
        raise ValueError('Choose a playable audio file')
    if not audio and (info['width'] <= 0 or info['height'] <= 0):
        raise ValueError('Choose a playable video take')
    if not math.isfinite(info['duration']) or info['duration'] <= 0:
        raise ValueError('The source has no playable duration')
    return info


_THUMBS = threading.BoundedSemaphore(3)


def thumbnail(sid, eid, episode, file, seconds, height, cfg):
    """One cached JPEG frame of a timeline source, for the clips' filmstrips."""
    if not isinstance(file, str) or file not in assets(episode):
        raise ValueError('Choose media from this episode')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Choose a time inside the video')
    path = Path(store.abs_media(file))
    if not path.is_file():
        raise ValueError('Source media is missing; replace or remove this clip')
    height = max(32, min(256, int(height)))
    seconds = round(seconds * 25) / 25
    stat = path.stat()
    key = hashlib.sha1(f'{file}|{stat.st_mtime_ns}|{stat.st_size}|{seconds:.2f}|{height}'.encode()).hexdigest()[:24]
    folder = Path(store.ep_dir(sid, eid)) / 'out' / 'thumbs'
    target = folder / f'{key}.jpg'
    if not target.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        with _THUMBS:  # keep a burst of tiles from starting dozens of ffmpeg processes
            if not target.is_file():
                partial = folder / f'{key}.part.jpg'
                media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-ss', f'{seconds:.3f}', '-i', str(path),
                            '-frames:v', '1', '-vf', f'scale=-2:{height}', '-q:v', '5', str(partial)])
                if not partial.is_file() or not partial.stat().st_size:
                    partial.unlink(missing_ok=True)
                    raise media.MediaError('There is no frame at that time')
                partial.replace(target)
    return target


def default_edit(episode, cfg, selected_takes=False):
    fps = settings.for_episode(cfg, episode)['video']['fps']
    clips = []
    selected_version = next((v for v in episode.get('outputs', {}).get('lipsync_versions', [])
                             if v['file'] == episode.get('outputs', {}).get('final') and v.get('timeline')), None)
    if selected_version and not selected_takes:
        for i, c in enumerate(selected_version['timeline']):
            clips.append({'id': f'clip_{i}', 'shot_id': c['shot_id'], 'source': selected_version['file'],
                          'source_in': c['start'], 'frames': round(c['duration'] * fps),
                          'muted': True, 'volume': 1, 'lipsync': False, 'fade_in': 0, 'fade_out': 0, 'speed': 1, 'transition': 0, 'reframe': dict(DEFAULT_REFRAME)})
    else:
        for sh in sorted(episode['shots'], key=lambda x: x['order']):
            items = sh['takes'].get('items', [])
            index = sh['takes'].get('selected')
            index = 0 if index is None else index
            if not items or not 0 <= index < len(items):
                continue
            take = items[index]
            try:
                duration = source_info(episode, take['file'], cfg)['video_duration']
            except (ValueError, media.MediaError):
                duration = take.get('seconds') or sh['duration_s']
            tin = float(sh['takes'].get('trim_in') or 0)
            duration -= tin + float(sh['takes'].get('trim_out') or 0)
            clips.append({'id': 'clip_' + sh['id'], 'shot_id': sh['id'], 'source': take['file'],
                          'source_in': tin, 'frames': max(1, round(duration * fps)),
                          'muted': False, 'volume': 1, 'lipsync': False, 'fade_in': 0, 'fade_out': 0, 'speed': 1, 'transition': 0, 'reframe': dict(DEFAULT_REFRAME)})
    music = None
    m = episode.get('music') or {}
    if m.get('file'):
        try:
            duration = source_info(episode, m['file'], cfg, audio=True)['duration']
            music = {'file': m['file'], 'source_in': 0, 'start_frame': 0,
                     'frames': max(1, round(duration * fps)), 'volume': m.get('volume', .25),
                     'muted': False, 'loop': False, 'isolate_vocals': True}
        except (ValueError, media.MediaError):
            pass
    return {'schema_version': 1, 'revision': episode.get('timeline', {}).get('revision', 0),
            'fps': fps, 'clips': clips, 'music': music, 'markers': [], 'range': None, 'titles': [], 'audio': [], 'overlays': []}


def view(episode, cfg):
    edit = copy.deepcopy(episode.get('timeline') or default_edit(episode, cfg))
    edit = retime(edit, settings.for_episode(cfg, episode)['video']['fps'])
    for clip in edit['clips']:  # timelines saved before fades existed
        clip.setdefault('fade_in', 0); clip.setdefault('fade_out', 0); clip.setdefault('speed', 1); clip.setdefault('transition', 0)
        clip.setdefault('reframe', dict(DEFAULT_REFRAME))
    edit.setdefault('markers', [])  # timelines saved before markers and ranges existed
    edit.setdefault('range', None)
    edit.setdefault('titles', [])
    edit.setdefault('audio', [])  # timelines saved before audio lanes existed
    edit.setdefault('overlays', [])  # ... and before the overlay lane
    sources = {}
    for c in edit['clips'] + edit['overlays']:
        if c['source'] in sources:
            continue
        try:
            sources[c['source']] = source_info(episode, c['source'], cfg)
        except (ValueError, media.MediaError):
            sources[c['source']] = {'missing': True}
    for track in [edit['music']] * bool(edit.get('music')) + edit['audio']:
        try:
            sources[track['file']] = source_info(episode, track['file'], cfg, audio=True)
        except (ValueError, media.MediaError):
            sources[track['file']] = {'missing': True}
    return {'edit': edit, 'sources': sources}


def retime(edit, fps):
    old = number(edit.get('fps', fps), 'Timeline frame rate', 1)
    if old != fps:
        edit = copy.deepcopy(edit)
        for clip in edit['clips']:
            clip['frames'] = max(1, round(number(clip['frames'], 'Clip length', 1) * fps / old))
            fade_in = min(clip['frames'], round(number(clip.get('fade_in', 0), 'Fade in') * fps / old))
            clip['fade_in'] = fade_in
            clip['transition'] = round(number(clip.get('transition', 0), 'Crossfade') * fps / old)
            clip['fade_out'] = min(clip['frames'] - fade_in, round(number(clip.get('fade_out', 0), 'Fade out') * fps / old))
        if edit.get('music'):
            edit['music']['start_frame'] = round(number(edit['music']['start_frame'], 'Music position') * fps / old)
            edit['music']['frames'] = max(1, round(number(edit['music']['frames'], 'Music length', 1) * fps / old))
        for o in edit.get('overlays') or []:
            o['start_frame'] = round(number(o['start_frame'], 'Overlay position') * fps / old)
            o['frames'] = max(1, round(number(o['frames'], 'Overlay length', 1) * fps / old))
            fade_in = min(o['frames'], round(number(o.get('fade_in', 0), 'Overlay fade in') * fps / old))
            o['fade_in'] = fade_in
            o['fade_out'] = min(o['frames'] - fade_in, round(number(o.get('fade_out', 0), 'Overlay fade out') * fps / old))
        for track in edit.get('audio') or []:
            track['start_frame'] = round(number(track['start_frame'], 'Audio position') * fps / old)
            track['frames'] = max(1, round(number(track['frames'], 'Audio length', 1) * fps / old))
            fade_in = min(track['frames'], round(number(track.get('fade_in', 0), 'Audio fade in') * fps / old))
            track['fade_in'] = fade_in
            track['fade_out'] = min(track['frames'] - fade_in, round(number(track.get('fade_out', 0), 'Audio fade out') * fps / old))
        for marker in edit.get('markers') or []:
            marker['frame'] = round(number(marker['frame'], 'Marker position') * fps / old)
        for title in edit.get('titles') or []:
            title['start_frame'] = round(number(title['start_frame'], 'Title start') * fps / old)
            title['frames'] = max(1, round(number(title['frames'], 'Title length', 1) * fps / old))
        if edit.get('range'):
            start = round(number(edit['range']['start'], 'Range start') * fps / old)
            edit['range'] = {'start': start, 'end': max(start + 1, round(number(edit['range']['end'], 'Range end') * fps / old))}
    edit['fps'] = fps
    return edit


MIN_SPEED, MAX_SPEED = 0.25, 4
MAX_ZOOM = 4
DEFAULT_REFRAME = {'fit': 'contain', 'zoom': 1, 'x': .5, 'y': .5}


def parse_reframe(value):
    """How a clip's picture sits in the project frame: `fit` 'contain' (whole picture, bars) or 'cover' (fills the
    frame, cropping), a further `zoom` of 1..4, and the picture point (x, y in 0..1) kept in view when it is cropped."""
    if value is None:
        return dict(DEFAULT_REFRAME)
    if not isinstance(value, dict) or value.get('fit', 'contain') not in ('contain', 'cover'):
        raise ValueError('Reframe needs a fit of contain or cover')
    return {'fit': value.get('fit', 'contain'), 'zoom': number(value.get('zoom', 1), 'Zoom', 1, MAX_ZOOM),
            'x': number(value.get('x', .5), 'Reframe x', 0, 1), 'y': number(value.get('y', .5), 'Reframe y', 0, 1)}


def reframe_geometry(sw, sh, width, height, reframe):
    """Where the source picture lands in a width x height frame (all in source pixels unless noted).

    Returns the crop window (x0, y0, vx, vy), the scale k (frame px per source px) and the size the cropped part
    is drawn at (ow, oh), which is smaller than the frame on an axis that is not cropped (then it is centred).
    The editor monitor has the same maths (reframeGeometry in timeline.js)."""
    fit_k = max(width / sw, height / sh) if reframe['fit'] == 'cover' else min(width / sw, height / sh)
    k = fit_k * reframe['zoom']
    vx, vy = min(sw, width / k), min(sh, height / k)
    x0 = min(max(reframe['x'] * sw - vx / 2, 0), sw - vx)
    y0 = min(max(reframe['y'] * sh - vy / 2, 0), sh - vy)
    return {'k': k, 'x0': x0, 'y0': y0, 'vx': vx, 'vy': vy, 'ow': vx * k, 'oh': vy * k}


def reframe_filter(sw, sh, width, height, reframe):
    """The ffmpeg filters that put the source picture into the frame (replaces plain scale + pad)."""
    if reframe == DEFAULT_REFRAME:
        return (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2')
    g = reframe_geometry(sw, sh, width, height, reframe)
    even = lambda v: max(2, round(v / 2) * 2)
    ow, oh = min(width, even(g['ow'])), min(height, even(g['oh']))
    return (f"crop={max(2, round(g['vx']))}:{max(2, round(g['vy']))}:{round(g['x0'])}:{round(g['y0'])},"
            f'scale={ow}:{oh},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2')


def clip_starts(clips):
    """Timeline start frame of every clip, and the timeline length.

    A clip's `transition` is a crossfade with the clip before it: the two overlap by that many frames, so the
    timeline is shorter than the sum of the clip lengths. Everything that places something on the timeline
    (render, lip-sync positions, range previews, the editor) must use this, never a plain sum."""
    starts, end = [], 0
    for i, c in enumerate(clips):
        begin = end - (c.get('transition', 0) if i else 0)
        starts.append(begin)
        end = begin + c['frames']
    return starts, end


def atempo(speed):
    """An ffmpeg audio filter chain that plays audio `speed` times faster (each atempo factor must be 0.5..2)."""
    chain = []
    while speed > 2:
        chain.append('atempo=2'); speed /= 2
    while speed < 0.5:
        chain.append('atempo=0.5'); speed /= 0.5
    chain.append(f'atempo={speed:.8f}')
    return ','.join(chain)


def number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{name} is outside its allowed range')
    return value


def flag(value, name):
    if type(value) is not bool:
        raise ValueError(f'{name} must be on or off')
    return value


def parse_markers(value):
    """Named, coloured points on the timeline. They never move or change the picture."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_MARKERS:
        raise ValueError(f'Markers must be a list of at most {MAX_MARKERS}')
    out, seen = [], set()
    for m in value:
        if not isinstance(m, dict) or not isinstance(m.get('id'), str) or not 0 < len(m['id']) <= 64 or m['id'] in seen:
            raise ValueError('Each marker needs a unique id')
        seen.add(m['id'])
        frame = number(m.get('frame'), 'Marker position', 0, MAX_FRAME)
        label, color = m.get('label', ''), m.get('color', 'blue')
        if type(frame) is not int:
            raise ValueError('A marker must sit on a whole frame')
        if not isinstance(label, str) or len(label) > 80:
            raise ValueError('Marker names are at most 80 characters')
        if color not in MARKER_COLORS:
            raise ValueError('Choose a marker colour from the list')
        out.append({'id': m['id'], 'frame': frame, 'label': label.strip(), 'color': color})
    return sorted(out, key=lambda m: (m['frame'], m['id']))


def parse_titles(value):
    """Text drawn over the picture for a span of the timeline (position and size relative to the frame)."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TITLES:
        raise ValueError(f'Titles must be a list of at most {MAX_TITLES}')
    out, seen = [], set()
    for t in value:
        if not isinstance(t, dict) or not isinstance(t.get('id'), str) or not 0 < len(t['id']) <= 64 or t['id'] in seen:
            raise ValueError('Each title needs a unique id')
        seen.add(t['id'])
        text = t.get('text')
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 200 or any(ord(ch) < 32 and ch != '\n' for ch in text):
            raise ValueError('A title needs 1 to 200 characters of text')
        start, frames = number(t.get('start_frame'), 'Title start', 0, MAX_FRAME), number(t.get('frames'), 'Title length', 1, MAX_FRAME)
        if type(start) is not int or type(frames) is not int:
            raise ValueError('Title timing must be whole frames')
        color = t.get('color', '#ffffff')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Choose a title colour like #ffffff')
        out.append({'id': t['id'], 'text': text.strip(), 'start_frame': start, 'frames': frames,
                    'x': float(number(t.get('x', .5), 'Title position', 0, 1)), 'y': float(number(t.get('y', .85), 'Title position', 0, 1)),
                    'size': float(number(t.get('size', .06), 'Title size', .02, .3)), 'color': color.lower(),
                    'box': flag(t.get('box', True), 'Title box')})
    return sorted(out, key=lambda x: (x['start_frame'], x['id']))


MAX_AUDIO = 8
MAX_OVERLAYS = 8


def parse_overlays(episode, value, fps, cfg, taken):
    """Picture-in-picture / B-roll clips drawn over the main track. They sit at a free start frame (nothing ripples)
    and never change the timeline length: whatever sticks out past the end is cut off."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_OVERLAYS:
        raise ValueError(f'Use at most {MAX_OVERLAYS} overlay clips')
    out, probes = [], {}
    for o in value:
        if not isinstance(o, dict) or not isinstance(o.get('id'), str) or not o['id'] or o['id'] in taken:
            raise ValueError('Each overlay clip needs a unique id')
        taken.add(o['id'])
        file = o.get('source')
        if file not in probes:
            probes[file] = source_info(episode, file, cfg)
        start = round(number(o.get('source_in', 0), 'Overlay source start') * fps)
        position, frames = number(o.get('start_frame', 0), 'Overlay position', 0, MAX_FRAME), number(o.get('frames'), 'Overlay length', 1, MAX_FRAME)
        if type(position) is not int or type(frames) is not int or start + frames > round(probes[file]['video_duration'] * fps):
            raise ValueError('Overlay trim extends beyond its source video')
        fade_in, fade_out = number(o.get('fade_in', 0), 'Overlay fade in'), number(o.get('fade_out', 0), 'Overlay fade out')
        if type(fade_in) is not int or type(fade_out) is not int or fade_in + fade_out > frames:
            raise ValueError('Overlay fades must be whole frames and fit inside the clip')
        out.append({'id': o['id'], 'source': file, 'source_in': start / fps, 'start_frame': position, 'frames': frames,
                    'x': number(o.get('x', .75), 'Overlay x', 0, 1), 'y': number(o.get('y', .25), 'Overlay y', 0, 1),
                    'scale': number(o.get('scale', .4), 'Overlay size', .05, 1), 'opacity': number(o.get('opacity', 1), 'Overlay opacity', 0, 1),
                    'volume': number(o.get('volume', 1), 'Overlay volume', 0, 2), 'muted': flag(o.get('muted', True), 'Overlay mute'),
                    'fade_in': fade_in, 'fade_out': fade_out})
    return out


def slice_overlays(overlays, first, last, fps):
    """Overlay clips as they appear inside a range preview, moved so the range starts at frame 0."""
    out = []
    for o in overlays:
        start, end = o['start_frame'], o['start_frame'] + o['frames']
        lo, hi = max(start, first), min(end, last)
        if lo < hi:
            out.append({**o, 'source_in': o['source_in'] + (lo - start) / fps, 'start_frame': lo - first, 'frames': hi - lo,
                        'fade_in': o.get('fade_in', 0) if lo == start else 0, 'fade_out': o.get('fade_out', 0) if hi == end else 0})
    return out


def parse_audio(episode, value, fps, cfg):
    """Extra audio lanes (voice-over, effects) mixed under the picture. Music stays the lip-sync guide."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_AUDIO:
        raise ValueError(f'Use at most {MAX_AUDIO} audio lanes')
    out, seen = [], set()
    for t in value:
        if not isinstance(t, dict) or not isinstance(t.get('id'), str) or not t['id'] or t['id'] in seen:
            raise ValueError('Each audio lane clip needs a unique id')
        seen.add(t['id'])
        info = source_info(episode, t.get('file'), cfg, audio=True)
        start = round(number(t.get('source_in', 0), 'Audio source start') * fps)
        position, frames = number(t.get('start_frame', 0), 'Audio position', 0, MAX_FRAME), number(t.get('frames'), 'Audio length', 1, MAX_FRAME)
        loop = flag(t.get('loop', False), 'Audio loop')
        available = round(info['duration'] * fps) - start
        if type(position) is not int or type(frames) is not int or available < 1 or not loop and frames > available:
            raise ValueError('Audio trim extends beyond the recording; enable looping or shorten it')
        fade_in, fade_out = number(t.get('fade_in', 0), 'Audio fade in'), number(t.get('fade_out', 0), 'Audio fade out')
        if type(fade_in) is not int or type(fade_out) is not int or fade_in + fade_out > frames:
            raise ValueError('Audio fades must be whole frames and fit inside the clip')
        lane = number(t.get('lane', len(out)), 'Audio lane', 0, MAX_AUDIO - 1)
        if type(lane) is not int:
            raise ValueError('Audio lane must be a whole number')
        out.append({'id': t['id'], 'file': t['file'], 'lane': lane, 'source_in': start / fps, 'start_frame': position,
                    'frames': frames, 'loop': loop, 'volume': number(t.get('volume', 1), 'Audio volume', 0, 2),
                    'muted': flag(t.get('muted', False), 'Audio mute'), 'fade_in': fade_in, 'fade_out': fade_out})
    return out


def parse_range(value):
    """The In/Out span used by "Render range". Clamped to the timeline when rendering."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('The render range needs a start and an end')
    start = number(value.get('start'), 'Range start', 0, MAX_FRAME)
    end = number(value.get('end'), 'Range end', 1, MAX_FRAME)
    if type(start) is not int or type(end) is not int or end <= start:
        raise ValueError('The render range must end after it starts')
    return {'start': start, 'end': end}


def validate(episode, body, cfg):
    if not isinstance(body, dict) or not isinstance(body.get('clips'), list):
        raise ValueError('Provide a timeline with video clips')
    fps = settings.for_episode(cfg, episode)['video']['fps']
    body = retime(body, fps)
    shots = {sh['id']: sh for sh in episode['shots']}
    clips, seen, probes = [], set(), {}
    for c in body['clips']:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id'] or c['id'] in seen:
            raise ValueError('Each timeline clip needs a unique id')
        seen.add(c['id'])
        file = c.get('source')
        if file not in probes:
            probes[file] = source_info(episode, file, cfg)
        start = round(number(c.get('source_in', 0), 'Clip start') * fps)
        frames = number(c.get('frames'), 'Clip length', 1)
        speed = number(c.get('speed', 1), 'Clip speed', MIN_SPEED, MAX_SPEED)
        # a clip at speed s uses frames * s frames of its source
        if type(frames) is not int or start + round(frames * speed) > round(probes[file]['video_duration'] * fps):
            raise ValueError('Clip trim extends beyond its source video')
        shot_id = c.get('shot_id') if c.get('shot_id') in shots else None
        sync = flag(c.get('lipsync', False), 'Lip-sync')
        if sync and (not shot_id or len(shots[shot_id].get('characters', [])) != 1):
            raise ValueError('Lip-sync clips must belong to a single-character shot')
        if sync and speed != 1:
            raise ValueError('Lip-sync clips must play at normal speed')
        fade_in, fade_out = number(c.get('fade_in', 0), 'Fade in'), number(c.get('fade_out', 0), 'Fade out')
        if type(fade_in) is not int or type(fade_out) is not int or fade_in + fade_out > frames:
            raise ValueError('Fades must be whole frames and fit inside the clip')
        clips.append({'id': c['id'], 'shot_id': shot_id, 'source': file, 'source_in': start / fps,
                      'frames': frames, 'muted': flag(c.get('muted', False), 'Clip mute'),
                      'volume': number(c.get('volume', 1), 'Clip volume', 0, 2), 'lipsync': sync,
                      'fade_in': fade_in, 'fade_out': fade_out, 'speed': speed,
                      'transition': number(c.get('transition', 0), 'Crossfade'), 'reframe': parse_reframe(c.get('reframe'))})
    for i, c in enumerate(clips):
        t = c['transition']
        if type(t) is not int:
            raise ValueError('A crossfade must be a whole number of frames')
        if t and (i == 0 or t * 2 > min(c['frames'], clips[i - 1]['frames'])):
            raise ValueError('A crossfade needs a clip before it and can be at most half of the shorter of the two clips')
        if t and (c['lipsync'] or clips[i - 1]['lipsync']):
            raise ValueError('Lip-sync clips cannot be crossfaded; turn lip-sync off or the crossfade off')
    music = body.get('music')
    if music is not None:
        if not isinstance(music, dict):
            raise ValueError('Provide music settings or remove the music track')
        info = source_info(episode, music.get('file'), cfg, audio=True)
        start = round(number(music.get('source_in', 0), 'Music source start') * fps)
        position = number(music.get('start_frame', 0), 'Music position')
        frames = number(music.get('frames'), 'Music length', 1)
        loop = flag(music.get('loop', False), 'Music loop')
        available = round(info['duration'] * fps) - start
        if type(position) is not int or type(frames) is not int or available < 1 or not loop and frames > available:
            raise ValueError('Music trim extends beyond the recording; enable looping or shorten it')
        music = {'file': music['file'], 'source_in': start / fps, 'start_frame': position, 'frames': frames,
                 'loop': loop, 'volume': number(music.get('volume', 1), 'Music volume', 0, 2),
                 'muted': flag(music.get('muted', False), 'Music mute'),
                 'isolate_vocals': flag(music.get('isolate_vocals', True), 'Vocal isolation')}
    if any(c['lipsync'] for c in clips) and not music:
        raise ValueError('Add a music or voice track before enabling lip-sync')
    return {'schema_version': 1, 'revision': episode.get('timeline', {}).get('revision', 0),
            'fps': fps, 'clips': clips, 'music': music,
            'markers': parse_markers(body.get('markers')), 'range': parse_range(body.get('range')),
            'titles': parse_titles(body.get('titles')), 'audio': parse_audio(episode, body.get('audio'), fps, cfg),
            'overlays': parse_overlays(episode, body.get('overlays'), fps, cfg, set(seen))}


def save(sid, eid, body, cfg):
    def apply(ep):
        revision = ep.get('timeline', {}).get('revision', 0)
        if type(body.get('revision')) is not int or body['revision'] != revision:
            raise EditConflict('The timeline changed in another window. Reload before saving.')
        edit = validate(ep, body, cfg)
        edit['revision'] = revision + 1
        ep['timeline'] = edit
    return store.update_episode(sid, eid, apply)


def run(job, command):
    with tempfile.TemporaryFile(mode='w+') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            while True:
                job.check()
                try:
                    code = process.wait(timeout=.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
        if code:
            log.seek(0)
            raise media.MediaError('Timeline processing failed: ' + log.read()[-2000:])


def prepare_music(job, cfg, music, frames, fps, out, guide=False, name='music'):
    """Place and trim audio on the same frame clock as the picture."""
    destination = out / ('guide.wav' if guide else name + '.wav')
    if not music:
        return None
    trimmed = out / (name + '_source.wav')
    if not trimmed.exists():
        run(job, [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(music['source_in']),
                  '-i', store.abs_media(music['file']), '-vn', '-ar', '48000', '-ac', '2', str(trimmed)])
    loop = ['-stream_loop', '-1'] if music['loop'] else []
    volume = 1 if guide else 0 if music['muted'] else music['volume']
    delay = round(music['start_frame'] / fps * 48000)
    fi, fo = music.get('fade_in', 0), music.get('fade_out', 0)
    fades = (f',afade=t=in:st=0:d={fi / fps:.6f}' if fi else '') + (f',afade=t=out:st={(music["frames"] - fo) / fps:.6f}:d={fo / fps:.6f}' if fo else '')
    run(job, [cfg['ffmpeg'], '-y', '-v', 'error', *loop, '-i', str(trimmed), '-af',
              f'atrim=duration={music["frames"]/fps},asetpts=N/SR/TB{fades},volume={volume},'
              f'adelay={delay}S:all=1,apad,atrim=end_sample={round(frames/fps*48000)}',
              '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(destination)])
    return destination


def slice_clips(clips, first, last, fps):
    """The part of the picture between two timeline frames, as ordinary clips (lip-sync is not applied).

    A crossfade inside the range stays; one that is cut by the range keeps only the part that is inside it."""
    out, last_index, last_hi = [], None, 0
    starts, _ = clip_starts(clips)
    for i, c in enumerate(clips):
        start, end = starts[i], starts[i] + c['frames']
        lo, hi = max(start, first), min(end, last)
        if lo < hi:
            # a fade belongs to its clip edge: keep it only if that edge is inside the range
            overlap = max(0, last_hi - lo) if last_index == i - 1 and out else 0
            out.append({**c, 'source_in': c['source_in'] + (lo - start) / fps * c.get('speed', 1), 'frames': hi - lo, 'lipsync': False,
                        'fade_in': c.get('fade_in', 0) if lo == start else 0, 'fade_out': c.get('fade_out', 0) if hi == end else 0,
                        'transition': overlap})
            last_index, last_hi = i, hi
    return out


def render(job, sid, eid, edit, video, window=None):
    """Render the whole timeline, or with `window=(first, last)` only that range as a quick preview.

    A preview never replaces the current final film, and it skips lip-sync so it never needs the GPU."""
    from . import jobs, comfy, lipsync
    cfg = settings.load()
    episode = store.get_episode(sid, eid)
    cfg['video'] = video
    fps, width, height = [video[k] for k in ('fps', 'width', 'height')]
    _, full = clip_starts(edit['clips'])
    if not full:
        raise ValueError('Add clips to the timeline before rendering')
    first, last = window if window else (0, full)
    if not 0 <= first < last <= full:
        raise ValueError('The render range is outside the timeline')
    clips_in = slice_clips(edit['clips'], first, last, fps) if window else edit['clips']
    total = last - first
    out = Path(store.ep_dir(sid, eid)) / 'out' / (('preview_' if window else 'timeline_') + uuid.uuid4().hex)
    out.mkdir(parents=True)
    (out / 'edit.json').write_text(json.dumps({**edit, 'render_window': [first, last]} if window else edit, indent=2))
    skipped_sync = bool(window) and any(c['lipsync'] for c in edit['clips'])
    sync = not window and any(c['lipsync'] for c in edit['clips'])
    guide = None
    client = None
    if sync:
        client = comfy.Comfy(cfg['comfy_host'])
        jobs._gpu_prepare(job, cfg, client)
        guide = prepare_music(job, cfg, edit['music'], full, fps, out, guide=True)
        if edit['music']['isolate_vocals']:
            job.say('Isolating vocals for the edited timeline')
            uploaded = client.upload(str(guide), out.name + '_guide.wav')
            result = client.run(workflows.separate_vocals(uploaded), cancel=lambda: job.cancel_flag)
            if not result['audios']:
                raise RuntimeError('Vocal isolation produced no audio')
            guide = out / 'vocals.flac'
            client.download(result['audios'][0], str(guide))
    music = prepare_music(job, cfg, edit.get('music'), full, fps, out)
    lanes = [prepare_music(job, cfg, t, full, fps, out, name=f'lane{k}') for k, t in enumerate(edit.get('audio') or []) if not t['muted']]
    all_overlays = edit.get('overlays') or []
    for k, o in enumerate(all_overlays):  # the sound of an overlay joins the audio lanes (prepared on the whole timeline, then seeked)
        if not o['muted'] and media.probe(cfg['ffprobe'], store.abs_media(o['source']))['has_audio']:
            lanes.append(prepare_music(job, cfg, {**o, 'file': o['source'], 'loop': False}, full, fps, out, name=f'ovsound{k}'))
    overlays = slice_overlays(all_overlays, first, last, fps) if window else all_overlays
    overlay_files = []
    for k, o in enumerate(overlays):  # each overlay clip becomes an exact-length file of the size it will be drawn at
        job.check()
        target = out / f'ov{k}.mp4'
        wide = max(2, round(o['scale'] * width / 2) * 2)
        run(job, [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(o['source_in']), '-i', store.abs_media(o['source']), '-an',
                  '-vf', f'scale={wide}:-2,{media.to_frame_rate(fps, media.probe(cfg["ffprobe"], store.abs_media(o["source"]))["fps"])},tpad=stop_mode=clone:stop_duration=1,format=yuv420p',
                  '-frames:v', str(o['frames']), '-c:v', 'libx264', '-crf', '16', str(target)])
        overlay_files.append(target)
    cache = Path(store.ep_dir(sid, eid)) / 'out' / 'timeline_cache'
    cache.mkdir(exist_ok=True)
    clips, events = [], []
    starts, _ = clip_starts(clips_in)
    for i, c in enumerate(clips_in):
        job.check()
        source = store.abs_media(c['source'])
        info = media.probe(cfg['ffprobe'], source)
        duration = c['frames'] / fps
        speed = c.get('speed', 1)
        base = out / f'{i:04d}_source.mp4'
        command = [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(c['source_in']), '-i', source]
        if not info['has_audio']:
            command += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        # start_time=0 keeps the first frame when a trim lands between the source's own frames
        # (e.g. a 24 fps take on a 30 fps timeline); otherwise the clip comes out one frame short.
        command += ['-map', '0:v:0', '-map', '0:a:0' if info['has_audio'] else '1:a:0',
                    '-vf', reframe_filter(info['width'], info['height'], width, height, c.get('reframe') or DEFAULT_REFRAME) + (f',setpts=PTS/{speed:.8f}' if speed != 1 else '') + f',{media.to_frame_rate(fps, info["fps"] * speed)},tpad=stop_mode=clone:stop_duration=1,format=yuv420p',
                    '-af', (atempo(speed) + ',' if speed != 1 else '') + f'aresample=48000,apad,atrim=end_sample={round(duration*48000)}',
                    '-frames:v', str(c['frames']), '-t', str(duration), '-c:v', 'libx264', '-crf', '16',
                    '-c:a', 'aac', '-b:a', '256k', str(base)]
        run(job, command)
        picture = base
        if c['lipsync']:
            voice = out / f'{i:04d}_voice.wav'
            run(job, [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(starts[i]/fps), '-i', str(guide),
                      '-t', str(duration), '-ar', '16000', '-ac', '1', str(voice)])
            fingerprint = [source, Path(source).stat().st_mtime_ns, Path(source).stat().st_size,
                           c['source_in'], c['frames'], video, hashlib.sha256(voice.read_bytes()).hexdigest(),
                           'LatentSync1.6-{steps}-{guidance}-{seed}'.format(**settings.lipsync_params(cfg)), c.get('reframe'), lipsync.RETIME_VERSION]
            key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
            picture = cache / (key + '.mp4')
            if not picture.is_file():
                temporary = out / f'{i:04d}_synced.mp4'
                lipsync.correct_clip(job, cfg, client, str(base), str(voice), str(temporary),
                                     c['frames'], width, height, fps, out.name + f'_{i}')
                temporary.replace(picture)
            else:
                job.say(f'Clip {i+1}: reusing the completed lip-sync')
        # Use original take audio, independently of the lip-sync model's guide audio.
        clip = out / f'{i:04d}_clip.mp4'
        volume = 0 if c['muted'] else c['volume']
        run(job, [cfg['ffmpeg'], '-y', '-v', 'error', '-i', str(picture), '-i', str(base),
                  '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-af', f'volume={volume}',
                  '-c:a', 'aac', '-b:a', '256k', '-t', str(duration), str(clip)])
        clips.append(clip)
        events.append({'clip': Path(source).name, 'src_in': c['source_in'], 'duration': duration})
        job.progress = [i + 1, len(clips_in)]
        job.say(f'Clip {i+1}/{len(clips_in)} ready')
    command = [cfg['ffmpeg'], '-y', '-v', 'error']
    for path in clips:
        command += ['-i', str(path)]
    shown = []
    for title in edit.get('titles') or []:  # titles overlapping the rendered span, in span time
        a, b = max(title['start_frame'], first) - first, min(title['start_frame'] + title['frames'], last) - first
        if a < b:
            shown.append((titles_mod.render_png(title, width, height, out / f'title_{len(shown)}.png'), a, b))
    if music:
        command += (['-ss', f'{first / fps:.6f}', '-t', f'{total / fps:.6f}'] if window else []) + ['-i', str(music)]
    for lane in lanes:
        command += (['-ss', f'{first / fps:.6f}', '-t', f'{total / fps:.6f}'] if window else []) + ['-i', str(lane)]
    for target in overlay_files:
        command += ['-i', str(target)]
    for png, _, _ in shown:
        command += ['-i', str(png)]
    graph, inputs = [], ''
    crossfades = any(c.get('transition') for c in clips_in)
    same_base = ',settb=1/1000000' if crossfades else ''  # xfade needs equal time bases; concat results use microseconds
    for i, c in enumerate(clips_in):
        fi, fo = c.get('fade_in', 0), c.get('fade_out', 0)
        fade_v = (f',fade=t=in:s=0:n={fi}' if fi else '') + (f',fade=t=out:s={c["frames"] - fo}:n={fo}' if fo else '')
        fade_a = (f',afade=t=in:st=0:d={fi / fps:.6f}' if fi else '') + (f',afade=t=out:st={(c["frames"] - fo) / fps:.6f}:d={fo / fps:.6f}' if fo else '')
        graph += [f'[{i}:v]trim=end_frame={c["frames"]},setpts=N/({fps}*TB){fade_v}{same_base}[v{i}]',
                  f'[{i}:a]aresample=48000,atrim=end_sample={round(c["frames"]/fps*48000)},asetpts=N/SR/TB{fade_a}[a{i}]']
        inputs += f'[v{i}][a{i}]'
    first_input = len(clips) + (1 if music else 0) + len(lanes) + len(overlay_files)
    if not crossfades:
        graph += [f'{inputs}concat=n={len(clips)}:v=1:a=1[v][a]']
    else:  # chain the clips; a crossfade overlaps the incoming clip with the running result
        video, sound, length = 'v0', 'a0', clips_in[0]['frames']
        for k in range(1, len(clips_in)):
            t, last_step = clips_in[k]['transition'], k == len(clips_in) - 1
            out_v, out_a = ('v', 'a') if last_step else (f'cv{k}', f'ca{k}')
            if t:
                graph += [f'[{video}][v{k}]xfade=transition=fade:duration={t / fps:.6f}:offset={(length - t) / fps:.6f}[{out_v}]',
                          f'[{sound}][a{k}]acrossfade=d={t / fps:.6f}:c1=tri:c2=tri[{out_a}]']
            else:
                graph += [f'[{video}][{sound}][v{k}][a{k}]concat=n=2:v=1:a=1[{out_v}][{out_a}]']
            video, sound, length = out_v, out_a, length + clips_in[k]['frames'] - t
    # overlay and picture are paired by time stamp: give both an exact frame-number clock (time base 1/fps) so a start
    # time that is rounded to microseconds can never put an overlay frame on the wrong side of the main frame it belongs to
    frame_clock = f',settb=expr=1/{fps}' if overlays else ''
    graph += [f'[v]setpts=N/({fps}*TB){frame_clock}[main0]']
    ov_base = len(clips) + (1 if music else 0) + len(lanes)
    for k, o in enumerate(overlays):
        fi, fo = o.get('fade_in', 0), o.get('fade_out', 0)
        shape = (f',fade=t=in:s=0:n={fi}:alpha=1' if fi else '') + (f',fade=t=out:s={o["frames"] - fo}:n={fo}:alpha=1' if fo else '')
        shape += f',colorchannelmixer=aa={o["opacity"]}' if o['opacity'] != 1 else ''
        graph += [f'[{ov_base + k}:v]format=yuva420p{shape},setpts=PTS-STARTPTS+{o["start_frame"]}/({fps}*TB),settb=expr=1/{fps}[ov{k}]',
                  f"[main{k}][ov{k}]overlay=x='main_w*{o['x']}-overlay_w/2':y='main_h*{o['y']}-overlay_h/2':eof_action=repeat:enable='between(n,{o['start_frame']},{o['start_frame'] + o['frames'] - 1})'[main{k + 1}]"]
    graph += [f'[main{len(overlays)}]null[pic0]']
    for j, (_, a, b) in enumerate(shown):
        graph.append(f"[pic{j}][{first_input + j}:v]overlay=0:0:enable='between(n,{a},{b - 1})'[pic{j + 1}]")
    graph.append(f'[pic{len(shown)}]null[picture]')
    extra = ([len(clips)] if music else []) + [len(clips) + (1 if music else 0) + k for k in range(len(lanes))]
    graph += [f'[a]' + ''.join(f'[{i}:a]' for i in extra) + f'amix=inputs={len(extra) + 1}:duration=first:normalize=0[mix]'] if extra else ['[a]anull[mix]']
    graph += ['[mix]alimiter=limit=0.95:level=false:latency=true[sound]']
    final = out / ('preview.mp4' if window else 'timeline.mp4')
    job.check()
    run(job, command + ['-filter_complex_threads', '1', '-filter_complex', ';'.join(graph),
                       '-map', '[picture]', '-map', '[sound]', '-r', str(fps), '-frames:v', str(total),
                       '-t', str(total/fps), '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p',
                       '-c:a', 'aac', '-b:a', '256k', '-movflags', '+faststart', str(final)])
    probe = media.probe(cfg['ffprobe'], str(final))
    count = json.loads(media._run([cfg['ffprobe'], '-v', 'error', '-count_frames', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(final)]))['streams'][0]['nb_read_frames']
    if int(count) != total or abs(probe['video_duration'] - total/fps) > 1/fps:
        raise RuntimeError('Timeline export failed its frame-count check; previous final retained')
    job.check()
    (out / 'timeline.edl').write_text(media.edl(episode.get('title', eid), events, fps))
    version = {'file': store.rel(str(final)), 'created': store.now(), 'runtime': total/fps,
               'revision': edit['revision'], 'edit': edit, 'video': video, 'frames': total, 'lipsync': sync}
    if window:
        version.update(window=[first, last], lipsync_skipped=skipped_sync)
    (out / 'verification.json').write_text(json.dumps(version, indent=2))
    def publish(ep):
        outputs = ep.setdefault('outputs', {})
        if window:  # a preview is kept beside the film, never as the film
            outputs.setdefault('timeline_previews', []).append(version)
            return
        previous = outputs.get('final')
        outputs.setdefault('timeline_versions', []).append(version)
        outputs.update(final=version['file'], runtime=version['runtime'], previous_final=previous,
                       edl=store.rel(str(out/'timeline.edl')), timeline_revision=edit['revision'])
        outputs.pop('srt', None)
    store.update_episode(sid, eid, publish)
    job.say('Range preview ready in Takes.' if window else 'Timeline export ready in Cut. Source takes retained.')
