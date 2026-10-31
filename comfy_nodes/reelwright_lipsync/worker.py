"""Run pinned lip-sync dependencies outside ComfyUI's Python environment."""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--mode', choices=['sync', 'vocals'], required=True)
    parser.add_argument('--audio', required=True)
    parser.add_argument('--video')
    parser.add_argument('--soundtrack')
    parser.add_argument('--output', required=True)
    parser.add_argument('--steps', type=int, default=20)
    parser.add_argument('--guidance', type=float, default=1.5)
    parser.add_argument('--seed', type=int, default=1247)
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    os.environ.update(TORCH_HOME=cfg['torch_home'], HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    os.environ['PATH'] = str(Path(cfg['ffmpeg']).parent) + os.pathsep + os.environ.get('PATH', '')
    import torch
    torch.set_num_threads(4)
    if args.mode == 'vocals':
        from demucs import pretrained, apply, audio
        import torchaudio
        model = pretrained.get_model('htdemucs').cpu().eval()
        wav = audio.AudioFile(args.audio).read(streams=0, samplerate=model.samplerate, channels=model.audio_channels)
        ref = wav.mean(0)
        deviation = ref.std()
        if deviation < 1e-7:
            torchaudio.save(args.output, wav, model.samplerate, encoding='PCM_S', bits_per_sample=16)
            return
        normalized = (wav - ref.mean()) / deviation
        with torch.inference_mode():
            separated = apply.apply_model(model, normalized[None], device='cpu', shifts=1,
                                          split=True, overlap=.25, progress=True)[0]
        vocals = separated[model.sources.index('vocals')] * deviation + ref.mean()
        torchaudio.save(args.output, vocals, model.samplerate, encoding='PCM_S', bits_per_sample=16)
        return
    import onnxruntime
    onnxruntime.preload_dlls()
    from omegaconf import OmegaConf
    from diffusers import AutoencoderKL, DDIMScheduler
    from accelerate.utils import set_seed
    upstream = Path(cfg['upstream'])
    os.chdir(upstream)
    sys.path.insert(0, str(upstream))
    from latentsync.models.unet import UNet3DConditionModel
    from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
    from latentsync.whisper.audio2feature import Audio2Feature
    config = OmegaConf.load(upstream / 'configs/unet/stage2_512.yaml')
    dtype = torch.float16
    scheduler = DDIMScheduler.from_pretrained(str(upstream / 'configs'))
    encoder = Audio2Feature(model_path=str(upstream / 'checkpoints/whisper/tiny.pt'), device='cuda',
                           num_frames=config.data.num_frames, audio_feat_length=config.data.audio_feat_length)
    vae = AutoencoderKL.from_pretrained(cfg['vae'], torch_dtype=dtype, local_files_only=True)
    vae.config.scaling_factor = .18215
    vae.config.shift_factor = 0
    unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model),
        str(upstream / 'checkpoints/latentsync_unet.pt'), device='cpu')
    pipeline = LipsyncPipeline(vae=vae, audio_encoder=encoder, unet=unet.to(dtype=dtype), scheduler=scheduler).to('cuda')
    set_seed(args.seed)
    pipeline(video_path=args.video, audio_path=args.audio, video_out_path=args.output,
             num_frames=config.data.num_frames, num_inference_steps=args.steps,
             guidance_scale=args.guidance, weight_dtype=dtype,
             width=512, height=512, mask_image_path=config.data.mask_image_path,
             temp_dir=str(Path(args.output).parent / 'inference'))
    if args.soundtrack:
        mixed = Path(args.output).with_name('full_music.mp4')
        subprocess.run([cfg['ffmpeg'], '-y', '-v', 'error', '-i', args.output, '-i', args.soundtrack,
                        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
                        '-af', 'apad', '-shortest', '-movflags', '+faststart', str(mixed)], check=True)
        mixed.replace(args.output)


if __name__ == '__main__':
    main()
