#!/usr/bin/env bash
# Update the stable studio (port 8020, your real projects) to the current development code.
# Runs the tests first; on failure nothing changes. The previous stable copy is kept as Reelwright-stable.previous.
# Roll back: systemctl --user stop reelwright && mv ~/RealAI/Reelwright-stable ~/RealAI/Reelwright-stable.bad &&
#            mv ~/RealAI/Reelwright-stable.previous ~/RealAI/Reelwright-stable && systemctl --user start reelwright
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"; DST="$HOME/RealAI/Reelwright-stable"
export PATH="$SRC/.runtime/ffmpeg/usr/bin:$PATH" LD_LIBRARY_PATH="$SRC/.runtime/ffmpeg/usr/lib/x86_64-linux-gnu"
echo "Running the test suite..."
(cd "$SRC" && env -u OPENAI_API_KEY -u GEMINI_API_KEY .venv/bin/python -m pytest -q tests/ -k "not webkit" -p no:cacheprovider) | tail -2
BUSY=$(curl -s http://127.0.0.1:8020/api/jobs | python3 -c "import json,sys;print(len([j for j in json.load(sys.stdin) if j['status'] in ('running','queued')]))" || echo 0)
[ "$BUSY" != "0" ] && { echo "The stable studio is running $BUSY job(s); deploy when it is idle."; exit 1; }
rm -rf "$DST.previous"; cp -a "$DST" "$DST.previous"
rsync -a --delete --exclude studio_data --exclude .venv --exclude .runtime --exclude .git --exclude __pycache__ --exclude '*.pyc' "$SRC/" "$DST/"
if ! cmp -s "$SRC/requirements.txt" "$DST.previous/requirements.txt"; then rm -rf "$DST/.venv"; cp -a "$SRC/.venv" "$DST/.venv"; fi
(cd "$SRC" && { echo "Stable snapshot made $(date '+%Y-%m-%d %H:%M') from $SRC"; echo "base commit: $(git rev-parse --short HEAD) on $(git branch --show-current) + uncommitted changes:"; git status --short; }) > "$DST/STABLE_VERSION.txt"
(cd "$SRC" && git diff > "$DST/STABLE_CHANGES.patch")
systemctl --user restart reelwright
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8020/api/status >/dev/null && { echo "Stable studio updated and running on http://127.0.0.1:8020"; exit 0; }; sleep 1; done
echo "Stable studio did not come back; roll back with the commands at the top of this script."; exit 1
