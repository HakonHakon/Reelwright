#!/usr/bin/env bash
# Start Ollama, ComfyUI and Reelwright Studio again (after training).
SERVICES="reelwright-ollama reelwright-voice reelwright-comfy reelwright"
echo "Starting: $SERVICES"
systemctl --user start $SERVICES
for s in $SERVICES; do printf '  %-20s %s\n' "$s" "$(systemctl --user is-active $s)"; done
echo "Studio: http://127.0.0.1:8020"
