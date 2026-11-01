"""Frame-based editing and rendering of existing Studio media."""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import uuid

from . import media, settings, store, workflows


class EditConflict(ValueError):
    pass


def assets(episode):
    files = {t['file'] for sh in episode['shots'] for t in sh['takes'].get('items', [])}
    outputs = episode.get('outputs', {})
    files.update(v['file'] for v in outputs.get('lipsync_versions', []))
    files.update(v['file'] for v in outputs.get('timeline_versions', []))
    files.update(outputs[k] for k in ('final', 'before_lipsync') if outputs.get(k))
    files.update(c['source'] for c in episode.get('timeline', {}).get('clips', []))
    return files


def source_info(episode, file, cfg, audio=False):
    allowed = assets(episode)
    if audio:
        allowed = {episode.get('music', {}).get('file'), (episode.get('timeline', {}).get('music') or {}).get('file'),
                   *episode.get('audio_assets', [])}
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


def default_edit(episode, cfg, selected_takes=False):
    fps = settings.for_episode(cfg, episode)['video']['fps']
    clips = []
    selected_version = next((v for v in episode.get('outputs', {}).get('lipsync_versions', [])
                             if v['file'] == episode.get('outputs', {}).get('final') and v.get('timeline')), None)
    if selected_version and not selected_takes:
        for i, c in enumerate(selected_version['timeline']):
            clips.append({'id': f'clip_{i}', 'shot_id': c['shot_id'], 'source': selected_version['file'],
                          'source_in': c['start'], 'frames': round(c['duration'] * fps),
                          'muted': True, 'volume': 1, 'lipsync': False})
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
                          'muted': False, 'volume': 1, 'lipsync': False})
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
            'fps': fps, 'clips': clips, 'music': music}


def view(episode, cfg):
    edit = copy.deepcopy(episode.get('timeline') or default_edit(episode, cfg))
    edit = retime(edit, settings.for_episode(cfg, episode)['video']['fps'])
    sources = {}
    for c in edit['clips']:
        if c['source'] in sources:
            continue
        try:
            sources[c['source']] = source_info(episode, c['source'], cfg)
        except (ValueError, media.MediaError):
            sources[c['source']] = {'missing': True}
    if edit.get('music'):
        try:
            sources[edit['music']['file']] = source_info(episode, edit['music']['file'], cfg, audio=True)
        except (ValueError, media.MediaError):
            sources[edit['music']['file']] = {'missing': True}
    return {'edit': edit, 'sources': sources}


def retime(edit, fps):
    old = number(edit.get('fps', fps), 'Timeline frame rate', 1)
    if old != fps:
        edit = copy.deepcopy(edit)
        for clip in edit['clips']:
            clip['frames'] = max(1, round(number(clip['frames'], 'Clip length', 1) * fps / old))
        if edit.get('music'):
            edit['music']['start_frame'] = round(number(edit['music']['start_frame'], 'Music position') * fps / old)
            edit['music']['frames'] = max(1, round(number(edit['music']['frames'], 'Music length', 1) * fps / old))
    edit['fps'] = fps
    return edit


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
        if type(frames) is not int or start + frames > round(probes[file]['video_duration'] * fps):
            raise ValueError('Clip trim extends beyond its source video')
        shot_id = c.get('shot_id') if c.get('shot_id') in shots else None
        sync = flag(c.get('lipsync', False), 'Lip-sync')
        if sync and (not shot_id or len(shots[shot_id].get('characters', [])) != 1):
            raise ValueError('Lip-sync clips must belong to a single-character shot')
        clips.append({'id': c['id'], 'shot_id': shot_id, 'source': file, 'source_in': start / fps,
                      'frames': frames, 'muted': flag(c.get('muted', False), 'Clip mute'),
                      'volume': number(c.get('volume', 1), 'Clip volume', 0, 2), 'lipsync': sync})
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
            'fps': fps, 'clips': clips, 'music': music}


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


def prepare_music(job, cfg, music, frames, fps, out, guide=False):
    """Place and trim audio on the same frame clock as the picture."""
    destination = out / ('guide.wav' if guide else 'music.wav')
    if not music:
        return None
    trimmed = out / 'music_source.wav'
    if not trimmed.exists():
        run(job, [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(music['source_in']),
                  '-i', store.abs_media(music['file']), '-vn', '-ar', '48000', '-ac', '2', str(trimmed)])
    loop = ['-stream_loop', '-1'] if music['loop'] else []
    volume = 1 if guide else 0 if music['muted'] else music['volume']
    delay = round(music['start_frame'] / fps * 48000)
    run(job, [cfg['ffmpeg'], '-y', '-v', 'error', *loop, '-i', str(trimmed), '-af',
              f'atrim=duration={music["frames"]/fps},asetpts=N/SR/TB,volume={volume},'
              f'adelay={delay}S:all=1,apad,atrim=end_sample={round(frames/fps*48000)}',
              '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(destination)])
    return destination


def render(job, sid, eid, edit, video):
    from . import jobs, comfy, lipsync
    cfg = settings.load()
    episode = store.get_episode(sid, eid)
    cfg['video'] = video
    fps, width, height = [video[k] for k in ('fps', 'width', 'height')]
    total = sum(c['frames'] for c in edit['clips'])
    if not total:
        raise ValueError('Add clips to the timeline before rendering')
    out = Path(store.ep_dir(sid, eid)) / 'out' / ('timeline_' + uuid.uuid4().hex)
    out.mkdir(parents=True)
    (out / 'edit.json').write_text(json.dumps(edit, indent=2))
    sync = any(c['lipsync'] for c in edit['clips'])
    guide = None
    client = None
    if sync:
        client = comfy.Comfy(cfg['comfy_host'])
        jobs._gpu_prepare(job, cfg, client)
        guide = prepare_music(job, cfg, edit['music'], total, fps, out, guide=True)
        if edit['music']['isolate_vocals']:
            job.say('Isolating vocals for the edited timeline')
            uploaded = client.upload(str(guide), out.name + '_guide.wav')
            result = client.run(workflows.separate_vocals(uploaded), cancel=lambda: job.cancel_flag)
            if not result['audios']:
                raise RuntimeError('Vocal isolation produced no audio')
            guide = out / 'vocals.flac'
            client.download(result['audios'][0], str(guide))
    music = prepare_music(job, cfg, edit.get('music'), total, fps, out)
    cache = Path(store.ep_dir(sid, eid)) / 'out' / 'timeline_cache'
    cache.mkdir(exist_ok=True)
    clips, events, position = [], [], 0
    for i, c in enumerate(edit['clips']):
        job.check()
        source = store.abs_media(c['source'])
        info = media.probe(cfg['ffprobe'], source)
        duration = c['frames'] / fps
        base = out / f'{i:04d}_source.mp4'
        command = [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(c['source_in']), '-i', source]
        if not info['has_audio']:
            command += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        command += ['-map', '0:v:0', '-map', '0:a:0' if info['has_audio'] else '1:a:0',
                    '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                    f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},tpad=stop_mode=clone:stop_duration=1,format=yuv420p',
                    '-af', f'aresample=48000,apad,atrim=end_sample={round(duration*48000)}',
                    '-frames:v', str(c['frames']), '-t', str(duration), '-c:v', 'libx264', '-crf', '16',
                    '-c:a', 'aac', '-b:a', '256k', str(base)]
        run(job, command)
        picture = base
        if c['lipsync']:
            voice = out / f'{i:04d}_voice.wav'
            run(job, [cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(position/fps), '-i', str(guide),
                      '-t', str(duration), '-ar', '16000', '-ac', '1', str(voice)])
            fingerprint = [source, Path(source).stat().st_mtime_ns, Path(source).stat().st_size,
                           c['source_in'], c['frames'], video, hashlib.sha256(voice.read_bytes()).hexdigest(), 'LatentSync1.6-20-1.5-1247']
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
        position += c['frames']
        job.progress = [i + 1, len(edit['clips'])]
        job.say(f'Clip {i+1}/{len(edit["clips"])} ready')
    command = [cfg['ffmpeg'], '-y', '-v', 'error']
    for path in clips:
        command += ['-i', str(path)]
    if music:
        command += ['-i', str(music)]
    graph, inputs = [], ''
    for i, c in enumerate(edit['clips']):
        graph += [f'[{i}:v]trim=end_frame={c["frames"]},setpts=N/({fps}*TB)[v{i}]',
                  f'[{i}:a]aresample=48000,atrim=end_sample={round(c["frames"]/fps*48000)},asetpts=N/SR/TB[a{i}]']
        inputs += f'[v{i}][a{i}]'
    graph += [f'{inputs}concat=n={len(clips)}:v=1:a=1[v][a]', f'[v]setpts=N/({fps}*TB)[picture]']
    graph += [f'[a][{len(clips)}:a]amix=inputs=2:duration=first:normalize=0[mix]'] if music else ['[a]anull[mix]']
    graph += ['[mix]alimiter=limit=0.95:level=false:latency=true[sound]']
    final = out / 'timeline.mp4'
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
    (out / 'verification.json').write_text(json.dumps(version, indent=2))
    def publish(ep):
        outputs = ep.setdefault('outputs', {})
        previous = outputs.get('final')
        outputs.setdefault('timeline_versions', []).append(version)
        outputs.update(final=version['file'], runtime=version['runtime'], previous_final=previous,
                       edl=store.rel(str(out/'timeline.edl')), timeline_revision=edit['revision'])
        outputs.pop('srt', None)
    store.update_episode(sid, eid, publish)
    job.say('Timeline export ready in Cut. Source takes retained.')
