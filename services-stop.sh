#!/usr/bin/env bash
# Stop Reelwright Studio, ComfyUI and Ollama so the GPU and RAM are free (e.g. for ai-toolkit training).
# Projects, settings and keys are not touched. Start again with services-start.sh.
SERVICES="reelwright reelwright-dev reelwright-comfy reelwright-ollama reelwright-voice"
echo "Stopping: $SERVICES"
systemctl --user stop $SERVICES
for s in $SERVICES; do printf '  %-20s %s\n' "$s" "$(systemctl --user is-active $s)"; done
if command -v nvidia-smi >/dev/null; then
  echo "GPU memory in use: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
fi
