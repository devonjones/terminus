#!/usr/bin/env bash
# Run the app on Windows from this WSL checkout, and look at it from WSL.
#
#   app/scripts/win-dev.sh run            sync, install if needed, start `npm run dev` (blocks)
#   app/scripts/win-dev.sh snap [args]    dev-snap.mjs under Windows node; args as dev-snap
#   app/scripts/win-dev.sh state          GET /dev/state with Windows curl.exe
#
# Electron and Python must be Windows builds, and npm cannot install over a \\wsl$
# path, so `run` rsyncs the repo to %LOCALAPPDATA%\terminus\src and works there.
# Everything that reaches the app's 127.0.0.1 ports runs as a Windows process
# (node.exe, curl.exe): WSL2 without mirrored networking cannot reach them.
# See app/README.md.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
cd /mnt/c # cmd.exe refuses a \\wsl$ working directory
win() { cmd.exe /d /c "$@" 2>&1 | stdbuf -oL tr -d '\r'; }
local_w=$(cmd.exe /d /c 'echo %LOCALAPPDATA%' | tr -d '\r')
roaming_w=$(cmd.exe /d /c 'echo %APPDATA%' | tr -d '\r')
dst=$(wslpath -u "$local_w")/terminus/src
dst_w=$(wslpath -w "$dst")
dev=$(wslpath -u "$roaming_w")/terminus/dev

case "${1:-}" in
run)
  mkdir -p "$dst"
  # Never copy the interop key or config off the WSL side.
  rsync -a --delete \
    --exclude .git --exclude .venv --exclude node_modules --exclude /app/dist \
    --exclude /captures --exclude /scratch --exclude '*.pem' --exclude config.toml \
    --exclude test-results --exclude playwright-report \
    "$repo/" "$dst/"
  if [ ! -x "$dst/.venv/Scripts/python.exe" ] || ! cmp -s "$dst/pyproject.toml" "$dst/.venv/pyproject.stamp"; then
    win "cd /d $dst_w && python -m venv .venv && .venv\\Scripts\\python -m pip install -q -e ."
    cp "$dst/pyproject.toml" "$dst/.venv/pyproject.stamp"
  fi
  if ! cmp -s "$dst/app/package-lock.json" "$dst/app/node_modules/.lock.stamp"; then
    win "cd /d $dst_w\\app && npm ci"
    cp "$dst/app/package-lock.json" "$dst/app/node_modules/.lock.stamp"
  fi
  win "cd /d $dst_w\\app && npm run dev"
  ;;
snap)
  shift
  # Playwright resolves from the Windows copy's node_modules.
  win "cd /d $dst_w\\app && node scripts\\dev-snap.mjs $*"
  echo "screenshot: $dev/screenshot.png"
  ;;
state)
  s=$dev/session.json
  token=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$s")
  url=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sidecar"])' "$s")
  curl.exe -sS --max-time 10 -H "Authorization: Bearer $token" "$url/dev/state"
  echo
  ;;
*)
  sed -n '2,6p' "$0"
  exit 2
  ;;
esac
