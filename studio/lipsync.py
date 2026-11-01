"""Synchronize chosen shots to an uploaded song, retaining a reviewable original."""
import json
import math
from pathlib import Path
import uuid

from . import comfy, media, settings, store, workflows


def plan_episode(episode, shot_ids, cfg):
    if not isinstance(shot_ids, list) or not shot_ids or any(not isinstance(x, str) for x in shot_ids):
        raise ValueError('Choose at least one single-character shot to lip-sync')
    requested = set(shot_ids)
    if requested - {sh['id'] for sh in episode['shots']}:
        raise ValueError('An unknown shot was selected')
    fps = settings.for_episode(cfg, episode)['video']['fps']
    timeline, position = [], 0
    for sh in sorted(episode['shots'], key=lambda sh: sh['order']):
        takes = sh['takes']
        selected = takes.get('selected')
        if selected is None:
            selected = 0
        items = takes.get('items', [])
        if not items:
            if sh['id'] in requested:
                raise ValueError(f"Choose a take for {sh['id']} first")
            continue
        if type(selected) is not int or not 0 <= selected < len(items):
            raise ValueError(f"Choose an existing take for {sh['id']}")
        if sh['id'] in requested and len(sh.get('characters', [])) != 1:
            raise ValueError(f"{sh['id']}: lip-sync needs a single-character shot")
        source = items[selected]['file']
        info = media.probe(cfg['ffprobe'], store.abs_media(source))
        trim_in, trim_out = [float(takes.get(k) or 0) for k in ('trim_in', 'trim_out')]
        duration = info['video_duration'] - trim_in - trim_out
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"{sh['id']}: the take trims leave no video")
        frames = max(1, round(duration * fps))
        timeline.append({'shot_id': sh['id'], 'source': source, 'trim_in': trim_in,
                         'frames': frames, 'duration': frames / fps, 'start': position / fps,
                         'sync': sh['id'] in requested})
        position += frames
    if not timeline:
        raise ValueError('Choose video takes first')
    return timeline


def correct_clip(job, cfg, client, source, voice, destination, frames, width, height, fps, prefix):
    """Apply the installed ComfyUI mouth correction to a precisely trimmed clip."""
    import tempfile
    job.check()
    with tempfile.TemporaryDirectory(prefix='reelwright_clip_sync_') as work:
        video_name = client.upload(source, prefix + '.mp4')
        audio_name = client.upload(voice, prefix + '.wav')
        result = client.run(workflows.lipsync_video(video_name, audio_name), cancel=lambda: job.cancel_flag)
        if not result['videos']:
            raise RuntimeError('Lip-sync produced no video')
        rendered = str(Path(work) / 'model.mp4')
        client.download(result['videos'][0], rendered)
        job.check()
        media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-i', rendered, '-an', '-vf',
                    f'scale={width}:{height},fps={fps},tpad=stop_mode=clone:stop_duration=1,format=yuv420p',
                    '-frames:v', str(frames), '-c:v', 'libx264', '-crf', '16', destination])
    return destination


def render(job, sid, eid, timeline, audio_file, audio_offset=0, isolate_vocals=True):
    from . import jobs
    cfg = settings.load()
    episode = store.get_episode(sid, eid)
    cfg = settings.for_episode(cfg, episode)
    client = comfy.Comfy(cfg['comfy_host'])
    jobs._gpu_prepare(job, cfg, client)
    needed = ['ReelwrightLatentSync'] + (['ReelwrightSeparateVocals'] if isolate_vocals else [])
    if any(not client.has_node(name) for name in needed):
        raise ValueError('Install the local lip-sync workflow first; see STUDIO.md')
    token = uuid.uuid4().hex
    out = Path(store.ep_dir(sid, eid)) / 'out' / ('lipsync_' + token)
    out.mkdir(parents=True)
    width, height, fps = [cfg['video'][k] for k in ('width', 'height', 'fps')]
    duration = sum(clip['frames'] for clip in timeline) / fps
    mix = out / 'uploaded_music.wav'
    media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(audio_offset), '-i', store.abs_media(audio_file),
                '-vn', '-af', f'apad,atrim=duration={duration}', '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le', str(mix)])
    guide = mix
    job.check()
    if isolate_vocals:
        job.say('Isolating vocals locally; the full music mix is retained for export')
        name = client.upload(str(mix), f'lipsync_{token}_song.wav')
        result = client.run(workflows.separate_vocals(name), cancel=lambda: job.cancel_flag)
        if not result['audios']:
            raise RuntimeError('Vocal isolation produced no audio')
        guide = out / 'vocal_guide.flac'
        client.download(result['audios'][0], str(guide))
    clips = []
    job.progress = [0, len(timeline)]
    for i, clip in enumerate(timeline):
        job.check()
        source = out / f'{i:03d}_original.mp4'
        media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(clip['trim_in']),
                    '-i', store.abs_media(clip['source']), '-an', '-vf',
                    f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
                    f'fps={fps},tpad=stop_mode=clone:stop_duration=1,format=yuv420p',
                    '-frames:v', str(clip['frames']), '-c:v', 'libx264', '-crf', '16', str(source)])
        chosen = source
        if clip['sync']:
            job.say(f"{clip['shot_id']}: synchronizing to music at {clip['start']:.3f}s")
            voice = out / f'{i:03d}_voice.wav'
            media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-ss', str(clip['start']), '-i', str(guide),
                        '-t', str(clip['duration']), '-ar', '16000', '-ac', '1', str(voice)])
            video_name = client.upload(str(source), f'lipsync_{token}_{i}.mp4')
            audio_name = client.upload(str(voice), f'lipsync_{token}_{i}.wav')
            result = client.run(workflows.lipsync_video(video_name, audio_name), cancel=lambda: job.cancel_flag)
            if not result['videos']:
                raise RuntimeError(f"{clip['shot_id']}: lip-sync produced no video")
            rendered = out / f'{i:03d}_model.mp4'
            client.download(result['videos'][0], str(rendered))
            chosen = out / f'{i:03d}_synced.mp4'
            media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-i', str(rendered), '-an', '-vf',
                        f'scale={width}:{height},fps={fps},tpad=stop_mode=clone:stop_duration=1,format=yuv420p',
                        '-frames:v', str(clip['frames']), '-c:v', 'libx264', '-crf', '16', str(chosen)])
        clips.append(str(chosen))
        job.progress = [i + 1, len(timeline)]
    job.check()
    silent = out / 'picture.mp4'
    media.concat(cfg['ffmpeg'], clips, str(silent))
    final = out / 'lip_synced.mp4'
    media._run([cfg['ffmpeg'], '-y', '-v', 'error', '-i', str(silent), '-i', str(mix),
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k',
                '-t', str(duration), '-movflags', '+faststart', str(final)])
    info = media.probe(cfg['ffprobe'], str(final))
    if abs(info['video_duration'] - duration) > 1 / fps:
        raise RuntimeError('Lip-sync changed the timeline duration; original final retained')
    job.check()
    item = {'file': store.rel(str(final)), 'created': store.now(), 'model': 'LatentSync 1.6',
            'audio_file': audio_file, 'audio_offset': audio_offset, 'isolated_vocals': isolate_vocals,
            'runtime': duration, 'timeline': timeline, 'source_final': episode.get('outputs', {}).get('final')}
    (out / 'workflow_report.json').write_text(json.dumps(item, indent=2))
    store.update_episode(sid, eid, lambda e: e.setdefault('outputs', {}).setdefault('lipsync_versions', []).append(item))
    job.say('Lip-sync preview ready in Cut. Original takes and final retained.')
