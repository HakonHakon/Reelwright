#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PATH="$PWD/.runtime/ffmpeg/usr/bin:$PATH"
export LD_LIBRARY_PATH="$PWD/.runtime/ffmpeg/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec .venv/bin/python -m uvicorn studio.app:app --host 0.0.0.0 --port 8020
