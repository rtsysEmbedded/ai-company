#!/usr/bin/env bash
# Runs the departments web interface as a background service: started at login and restarted if it stops,
# so it is always ready. macOS uses launchd, Linux uses a systemd user service.
# Usage: dept service install|uninstall|status [--port 8780]
set -euo pipefail

ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$ENGINE/../.." && pwd)"
ACTION="${1:-status}"
PORT="${DEPT_UI_PORT:-8780}"
[ "${2:-}" = "--port" ] && PORT="$3"
LABEL="com.aicompany.departments"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UNIT="ai-company-departments.service"
UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT"
LOG_DIR="$ROOT/data/departments/logs"
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "ERROR: python3 is required" >&2; exit 1; }
# The interface calls the `hermes` command, so the service needs it on its PATH.
HERMES_DIR="$(dirname "$(command -v hermes 2>/dev/null || echo "$HOME/.local/bin/hermes")")"
SERVICE_PATH="$HERMES_DIR:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

listening() {
  "$PY" - "$PORT" <<'EOF'
import socket, sys
s = socket.socket(); s.settimeout(1)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
EOF
}

wait_until_listening() {
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    listening && return 0
    sleep 0.5
  done
  return 1
}

case "$(uname)" in
  Darwin)
    DOMAIN="gui/$(id -u)"
    case "$ACTION" in
      install)
        OLD_PLIST="$HOME/Library/LaunchAgents/com.hardwaredept.ui.plist"   # from the earlier stand-alone hardware-dept folder
        if [ -f "$OLD_PLIST" ]; then launchctl bootout "$DOMAIN" "$OLD_PLIST" 2>/dev/null || true; rm -f "$OLD_PLIST"; fi
        mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
        cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string><string>$ENGINE/ui/server.py</string><string>--no-open</string><string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$SERVICE_PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/ui.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/ui.log</string>
</dict>
</plist>
EOF
        launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
        launchctl bootstrap "$DOMAIN" "$PLIST"
        wait_until_listening || { echo "ERROR: the service was installed but nothing is listening on port $PORT. See $LOG_DIR/ui.log" >&2; exit 1; }
        echo "Web interface service installed: it starts at login and is running now on http://127.0.0.1:$PORT"
        echo "Open it with: $ROOT/dept open"
        ;;
      uninstall)
        launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        echo "Web interface service removed."
        ;;
      status)
        if [ -f "$PLIST" ]; then echo "Service: installed ($PLIST)"; else echo "Service: not installed"; fi
        if listening; then echo "Web interface: running on http://127.0.0.1:$PORT"; else echo "Web interface: not running"; fi
        ;;
      *) echo "Usage: dept service install|uninstall|status [--port N]" >&2; exit 2 ;;
    esac
    ;;
  Linux)
    case "$ACTION" in
      install)
        command -v systemctl >/dev/null 2>&1 || { echo "ERROR: systemd is required for the background service. Run ./dept ui by hand instead." >&2; exit 1; }
        mkdir -p "$LOG_DIR" "$(dirname "$UNIT_PATH")"
        cat > "$UNIT_PATH" <<EOF
[Unit]
Description=AI company departments web interface

[Service]
ExecStart=$PY $ENGINE/ui/server.py --no-open --port $PORT
WorkingDirectory=$ROOT
Environment=PATH=$SERVICE_PATH
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/ui.log
StandardError=append:$LOG_DIR/ui.log

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now "$UNIT"
        wait_until_listening || { echo "ERROR: the service was installed but nothing is listening on port $PORT. See $LOG_DIR/ui.log" >&2; exit 1; }
        echo "Web interface service installed: it starts at login and is running now on http://127.0.0.1:$PORT"
        echo "Open it with: $ROOT/dept open"
        echo "To keep it running while you are logged out: loginctl enable-linger $USER"
        ;;
      uninstall)
        systemctl --user disable --now "$UNIT" 2>/dev/null || true
        rm -f "$UNIT_PATH"
        systemctl --user daemon-reload 2>/dev/null || true
        echo "Web interface service removed."
        ;;
      status)
        if [ -f "$UNIT_PATH" ]; then echo "Service: installed ($UNIT_PATH)"; else echo "Service: not installed"; fi
        if listening; then echo "Web interface: running on http://127.0.0.1:$PORT"; else echo "Web interface: not running"; fi
        ;;
      *) echo "Usage: dept service install|uninstall|status [--port N]" >&2; exit 2 ;;
    esac
    ;;
  *) echo "ERROR: background service is only supported on macOS and Linux. Run ./dept ui by hand." >&2; exit 1 ;;
esac
