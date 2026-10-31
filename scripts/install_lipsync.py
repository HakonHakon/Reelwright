"""Install the isolated local LatentSync runtime and its ComfyUI nodes."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True, help='Python 3.10 executable')
    parser.add_argument('--comfy', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = root / '.runtime' / 'lipsync'
    work.mkdir(parents=True, exist_ok=True)
    upstream = root / '.runtime' / 'LatentSync'
    if not upstream.exists():
        subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/bytedance/LatentSync.git', str(upstream)], check=True)
    revision = 'a229c3948406bc2cf6eaf4873e662e70c6a04746'
    current = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if current != revision:
        subprocess.run(['git', '-C', str(upstream), 'fetch', '--depth', '1', 'origin', revision], check=True)
        subprocess.run(['git', '-C', str(upstream), 'checkout', revision], check=True)
    env = root / '.runtime' / 'lipsync-env'
    subprocess.run([args.python, '-m', 'venv', str(env)], check=True)
    python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', 'uv'], check=True)
    uv = [str(python), '-m', 'uv', 'pip', 'install', '--python', str(python)]
    subprocess.run(uv + ['torch==2.5.1', 'torchvision==0.20.1', 'torchaudio==2.5.1',
                        '--index-url', 'https://download.pytorch.org/whl/cu124'], check=True)
    requirements = [line for line in (upstream / 'requirements.txt').read_text().splitlines()
                    if line and not line.startswith(('torch', '--', 'gradio', 'insightface'))]
    req = work / 'requirements.txt'
    req.write_text('\n'.join(requirements + ['demucs==4.0.1', 'setuptools', 'wheel', 'cython']))
    subprocess.run(uv + ['-r', str(req)], check=True)
    subprocess.run(uv + ['insightface==0.7.3', 'onnx==1.17.0', 'protobuf==3.20.3', '--no-build-isolation'], check=True)
    models = work / 'models'
    files = [
        ('https://huggingface.co/ByteDance/LatentSync-1.6/resolve/main/latentsync_unet.pt',
         upstream / 'checkpoints/latentsync_unet.pt'),
        ('https://huggingface.co/ByteDance/LatentSync-1.6/resolve/main/whisper/tiny.pt',
         upstream / 'checkpoints/whisper/tiny.pt'),
        ('https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/config.json', models / 'vae/config.json'),
        ('https://huggingface.co/stabilityai/sd-vae-ft-mse/resolve/main/diffusion_pytorch_model.safetensors',
         models / 'vae/diffusion_pytorch_model.safetensors'),
    ]
    detector_dir = upstream / 'checkpoints/auxiliary/models/buffalo_l'
    detector_dir.mkdir(parents=True, exist_ok=True)
    for name, cached_name in (('det_10g.onnx', 'detector.onnx'), ('2d106det.onnx', 'landmark_2d_106.onnx')):
        cached = root / '.runtime/identity/models/character_swap' / cached_name
        target = detector_dir / name
        if cached.exists():
            shutil.copyfile(cached, target)
        else:
            files.append((f'https://huggingface.co/KlingTeam/LivePortrait/resolve/main/insightface/models/buffalo_l/{name}', target))

    def download(item):
        url, target = item
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.download')
        print('Downloading', target.name, flush=True)
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output, 4 << 20)
        temporary.replace(target)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, files))
    from_file = root / 'studio_data/settings.json'
    settings = json.loads(from_file.read_text()) if from_file.exists() else {}
    ffmpeg = settings.get('ffmpeg') or shutil.which('ffmpeg')
    ffprobe = settings.get('ffprobe') or shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('Configure ffmpeg and ffprobe in Studio first')
    config = {'python': str(python), 'upstream': str(upstream), 'vae': str(models / 'vae'),
              'ffmpeg': ffmpeg, 'ffprobe': ffprobe, 'torch_home': str(models / 'torch')}
    (work / 'config.json').write_text(json.dumps(config, indent=2))
    download_env = {**os.environ, 'TORCH_HOME': config['torch_home']}
    subprocess.run([str(python), '-c',
                    "from demucs.pretrained import get_model; get_model('htdemucs')"], env=download_env, check=True)
    target = args.comfy.resolve() / 'custom_nodes' / 'reelwright_lipsync'
    source = root / 'comfy_nodes/reelwright_lipsync'
    if not target.exists():
        target.symlink_to(source, target_is_directory=True)
    elif target.resolve() != source:
        raise RuntimeError(f'Custom node destination already exists: {target}')
    print('LatentSync 1.6 and Demucs ready. Restart ComfyUI to load Reelwright lip-sync nodes.', flush=True)


if __name__ == '__main__':
    main()
