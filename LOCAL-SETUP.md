# Local Linux installation

The Studio is served on port 8020 on the computer's LAN address.
On the current Wi-Fi: http://192.168.1.133:8020
This address may change if DHCP assigns a new address.

User services are enabled, with lingering so they can stay running after logout:

```bash
systemctl --user status reelwright reelwright-comfy
systemctl --user restart reelwright
journalctl --user -u reelwright -f
```

Studio source and project data: `~/RealAI/Reelwright`, with projects in `studio_data/`.
Local dependencies, FFmpeg and face restorers: `.runtime/` (excluded from Git).
ComfyUI: `~/ComfyUI`, API listening only on localhost:8188.

For manual Studio startup use `./start-studio.sh`.
The Studio is intended for your trusted home network; no internet tunnel or router
port forwarding has been configured.

MiniMax is hosted and requires your own `MINIMAX_API_KEY`. To supply it to the service:

```bash
systemctl --user edit reelwright
# Add:
# [Service]
# Environment=MINIMAX_API_KEY=your-key
systemctl --user daemon-reload
systemctl --user restart reelwright
```

New projects use Z-Image for first frames and Wan 2.2 for local video. These defaults
are configured under `style_defaults` in `studio_data/settings.json`. Existing projects
keep their chosen settings. LTX needs the separately exported workflow described in
STUDIO.md; Flux and Qwen Edit are optional additional model families.

Installed local model families:
- Z-Image Turbo BF16 with Qwen 3 4B text encoder and image VAE.
- Wan 2.2 I2V high/low noise FP8, 4-step Lightning LoRAs, UMT5 encoder and Wan VAE.
- Ollama Qwen 2.5 VL 7B for planning and image review.
- KEEP and CodeFormer, including face detection/parsing weights, in a separate Python 3.10 CUDA environment.

Ollama is managed by `reelwright-ollama.service`. The UI/backend services are set to
start automatically for the user account. Checks and install logs are in `.runtime/`;
small generated verification clips are in `studio_data/setup-checks/`.

## MiniMax H3 local

The local H3 engine uses native ComfyUI `MiniMaxH3ImageToVideo` nodes, quantized
FL2VA INT8 weights, the Qwen3-VL NVFP4 encoder, native video/audio VAEs, and a
4-step Turbo LoRA. These are separate from the paid MiniMax Hailuo API models.
Downloads and the hardware check are logged in `.runtime/download-h3.log` and
`.runtime/h3-inference-check.log`. No API key is used for this engine.

## Android and iPhone home-screen app

Mobile upload/crop fixes work at the existing HTTP address:
http://192.168.1.133:8020/

The installable PWA uses the local HTTPS reverse proxy:
https://192.168.1.133:8443/

Open the HTTP address first and tap **Install app** in the bottom navigation.
Its instructions link to this computer's public local CA certificate at `/local-ca.crt`.
Install/trust that certificate on each phone, then open the HTTPS address.
Android: Chrome menu → Install app / Add to Home screen.
iPhone/iPad: Safari Share → Add to Home Screen (enable Open as Web App if shown).

The certificate trust step must be performed on the phone. On iPhone, installing the
profile is followed by enabling full trust under Settings → General → About →
Certificate Trust Settings. Android's CA import is under Security → Encryption &
credentials, with menu names varying by manufacturer.

Caddy runs as `reelwright-https.service` and automatically renews local server
certificates. Its config and private CA keys remain in `.runtime/caddy/`; only the
public root is served to phones. The per-installation public certificate is excluded
from Git. Existing port 8020 continues working without certificate installation.

```bash
systemctl --user status reelwright-https
journalctl --user -u reelwright-https -f
```

Uploads, saved projects and GPU generation require this PC and the same home
network. The service worker caches only app assets and an offline explanation;
it never caches project API responses/media or queues offline edits/uploads.
