#!/usr/bin/env bash
# Install ai-company background services (macOS launchd). Idempotent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
mkdir -p "$ROOT/data/logs" "$HOME/Library/LaunchAgents"
for tpl in "$ROOT"/launchd/*.plist.template; do
  name="$(basename "$tpl" .template)"
  dest="$HOME/Library/LaunchAgents/$name"
  sed -e "s#__ROOT__#$ROOT#g" -e "s#__UV__#$UV#g" -e "s#__PATH__#$(dirname "$UV"):$(dirname "$(command -v hermes)"):/usr/bin:/bin:/usr/sbin:/sbin#g" "$tpl" > "$dest"
  launchctl bootout "gui/$(id -u)" "$dest" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
  echo "installed $name"
done
