"""Local LatentSync 1.6 and vocal isolation with an isolated Python runtime."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import wave

import numpy as np
import torch
import folder_paths
import comfy.model_management as memory
from comfy_api.latest import ComfyExtension, io, InputImpl, Types

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / '.runtime/lipsync/config.json'


def write_audio(audio, path):
    samples = audio['waveform'][0].detach().cpu().float().numpy()
    pcm = (np.clip(samples, -1, 1).T * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(pcm.shape[1])
        stream.setsampwidth(2)
        stream.setframerate(audio['sample_rate'])
        stream.writeframes(pcm.tobytes())


def run_worker(work, *args):
    if not CONFIG.is_file():
        raise RuntimeError('Install the local runtime with scripts/install_lipsync.py first')
    cfg = json.loads(CONFIG.read_text())
    command = [cfg['python'], str(Path(__file__).parent / 'worker.py'), '--config', str(CONFIG), *args]
    environment = {**os.environ, 'OMP_NUM_THREADS': '4'}
    libraries = Path(cfg['ffmpeg']).parent.parent / 'lib/x86_64-linux-gnu'
    if libraries.is_dir():
        environment['LD_LIBRARY_PATH'] = str(libraries) + os.pathsep + environment.get('LD_LIBRARY_PATH', '')
    memory.unload_all_models()
    memory.soft_empty_cache()
    with (work / 'worker.log').open('w+') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, env=environment,
                                   start_new_session=os.name != 'nt')
        try:
            while True:
                memory.throw_exception_if_processing_interrupted()
                try:
                    code = process.wait(timeout=.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        if code:
            log.seek(0)
            raise RuntimeError('Local lip-sync worker failed: ' + log.read()[-2500:])


class ReelwrightLatentSync(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='ReelwrightLatentSync', display_name='Lip sync · LatentSync 1.6',
            category='Reelwright/audio', inputs=[io.Video.Input('video'), io.Audio.Input('audio'),
                io.Int.Input('steps', default=20, min=1, max=100),
                io.Float.Input('guidance', default=1.5, min=1, max=3, step=.1),
                io.Int.Input('seed', default=1247, min=0, max=2**32-1),
                io.Audio.Input('soundtrack', optional=True)],
            outputs=[io.Video.Output()])

    @classmethod
    def execute(cls, video, audio, steps, guidance, seed, soundtrack=None):
        work = Path(tempfile.mkdtemp(prefix='reelwright_sync_', dir=folder_paths.get_temp_directory()))
        try:
            source, voice, result = work / 'source.mp4', work / 'voice.wav', work / 'synced.mp4'
            video.save_to(str(source), format=Types.VideoContainer.MP4, codec='auto')
            write_audio(audio, voice)
            extra = []
            if soundtrack is not None:
                original_audio = work / 'soundtrack.wav'
                write_audio(soundtrack, original_audio)
                extra = ['--soundtrack', str(original_audio)]
            run_worker(work, '--mode', 'sync', '--video', str(source), '--audio', str(voice),
                       '--output', str(result), '--steps', str(steps), '--guidance', str(guidance), '--seed', str(seed), *extra)
            if not result.is_file():
                raise RuntimeError('LatentSync returned no video')
            source.unlink(); voice.unlink()
            if soundtrack is not None:
                original_audio.unlink()
            shutil.rmtree(work / 'inference', ignore_errors=True)
            return io.NodeOutput(InputImpl.VideoFromFile(str(result)))
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise


class ReelwrightSeparateVocals(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='ReelwrightSeparateVocals', display_name='Isolate singing voice · Demucs',
            category='Reelwright/audio', inputs=[io.Audio.Input('audio')], outputs=[io.Audio.Output()])

    @classmethod
    def execute(cls, audio):
        with tempfile.TemporaryDirectory(prefix='reelwright_vocals_', dir=folder_paths.get_temp_directory()) as directory:
            work = Path(directory)
            source, result = work / 'music.wav', work / 'vocals.wav'
            write_audio(audio, source)
            run_worker(work, '--mode', 'vocals', '--audio', str(source), '--output', str(result))
            with wave.open(str(result), 'rb') as stream:
                samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').copy()
                samples = torch.from_numpy(samples.reshape(-1, stream.getnchannels()).T).float() / 32768
                return io.NodeOutput({'waveform': samples.unsqueeze(0), 'sample_rate': stream.getframerate()})


class ReelwrightLipSyncExtension(ComfyExtension):
    async def get_node_list(self):
        return [ReelwrightLatentSync, ReelwrightSeparateVocals]


async def comfy_entrypoint():
    return ReelwrightLipSyncExtension()
