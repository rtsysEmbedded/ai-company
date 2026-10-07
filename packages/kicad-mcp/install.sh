#!/usr/bin/env bash
# Installs the KiCad MCP server and skill, and registers them with Claude Code, Codex and Hermes.
# macOS and Linux. Safe to re-run: it replaces the previous install and registration.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/kicad-mcp"
CLIENTS="auto"
WORKDIR=""
KICAD_CLI_OPT=""
INSTALL_KICAD="ask"
RUN_TEST=1
INSTALL_SKILL=1
NAME="kicad"

usage() {
  cat <<EOF
Usage: ./install.sh [options]

  --clients LIST     Comma-separated agents to register with: claude,codex,hermes
                     (default: every one whose CLI is on PATH; "none" to skip)
  --prefix DIR       Where to install the server (default: $PREFIX)
  --workdir DIR      Where projects are stored (default: ~/KiCad_Projects)
  --kicad-cli PATH   Full path to kicad-cli, if it is not found automatically
  --install-kicad    Install KiCad without asking if it is missing
  --skip-test        Do not run the end-to-end test after installing
  --no-skill         Register the tools only; do not install the 'kicad' skill
  -h, --help         Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --clients) CLIENTS="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --kicad-cli) KICAD_CLI_OPT="$2"; shift 2 ;;
    --install-kicad) INSTALL_KICAD="yes"; shift ;;
    --skip-test) RUN_TEST=0; shift ;;
    --no-skill) INSTALL_SKILL=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- Python -----------------------------------------------------------------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$(command -v "$candidate")"
    break
  fi
done
[ -n "$PY" ] || die "Python 3.8 or newer is required (python3 was not found on PATH)."
say "Using $("$PY" --version 2>&1) at $PY"

# --- Server files -----------------------------------------------------------
say "Installing server to $PREFIX"
mkdir -p "$PREFIX"
cp "$SRC"/server/kicad_mcp.py "$SRC"/server/kicad_runner.py "$SRC"/server/kicad_lib.py "$SRC"/server/kicad_sch.py "$PREFIX/"
SERVER="$PREFIX/kicad_mcp.py"

# Settings handed to the server by each agent, as KEY=VALUE.
ENVS=()
[ -n "$WORKDIR" ] && ENVS+=("KICAD_MCP_WORKDIR=$WORKDIR")
[ -n "$KICAD_CLI_OPT" ] && ENVS+=("KICAD_CLI=$KICAD_CLI_OPT")

server_check() {
  env ${ENVS[@]+"${ENVS[@]}"} "$PY" "$SERVER" --check
}

# --- KiCad ------------------------------------------------------------------
install_kicad() {
  if [ "$(uname)" = "Darwin" ]; then
    command -v brew >/dev/null 2>&1 || return 1
    brew install --cask kicad
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y kicad
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y kicad
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm kicad kicad-library
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y kicad
  else
    return 1
  fi
}

say "Looking for KiCad"
if ! server_check; then
  if [ "$INSTALL_KICAD" = "ask" ] && [ -t 0 ]; then
    printf 'KiCad (with its libraries) was not found or is not usable. Install it now with the system package manager? [y/N] '
    read -r answer
    case "$answer" in y|Y|yes|YES) INSTALL_KICAD="yes" ;; esac
  fi
  [ "$INSTALL_KICAD" = "yes" ] ||
    die "KiCad is required. Re-run with --install-kicad, install it from https://www.kicad.org/download/, or pass --kicad-cli PATH."
  say "Installing KiCad"
  install_kicad || die "No supported package manager found. Install KiCad from https://www.kicad.org/download/ and re-run."
  server_check || die "KiCad was installed but the server still cannot use it. See the message above; pass --kicad-cli PATH if needed."
fi

# --- End-to-end test --------------------------------------------------------
if [ "$RUN_TEST" = 1 ]; then
  say "Running the end-to-end test against the installed server"
  env ${ENVS[@]+"${ENVS[@]}"} "$PY" "$SRC/tests/smoke_test.py" "$SERVER" ||
    die "The end-to-end test failed, so nothing was registered. See the FAIL lines above."
fi

# --- Register with agents ---------------------------------------------------
if [ "$CLIENTS" = "auto" ]; then
  CLIENTS=""
  for cli in claude codex hermes; do
    command -v "$cli" >/dev/null 2>&1 && CLIENTS="$CLIENTS,$cli"
  done
  CLIENTS="${CLIENTS#,}"
  [ -n "$CLIENTS" ] || warn "No agent CLI (claude, codex, hermes) found on PATH; nothing to register with."
fi

REGISTERED=""
FAILED=""

# The codex and hermes CLIs rewrite their whole config file, so keep the original once.
backup_once() {
  if [ -f "$1" ] && [ ! -e "$1.bak-kicad-mcp" ]; then
    cp -p "$1" "$1.bak-kicad-mcp"
    echo "Backed up $1 to $1.bak-kicad-mcp"
  fi
}

register() {
  local client="$1" flag
  local args=()
  if ! command -v "$client" >/dev/null 2>&1; then
    warn "$client is not on PATH; skipped."
    FAILED="$FAILED $client"
    return
  fi
  say "Registering '$NAME' with $client"
  case "$client" in
    claude)
      claude mcp remove --scope user "$NAME" >/dev/null 2>&1 || true
      for flag in ${ENVS[@]+"${ENVS[@]}"}; do args+=(-e "$flag"); done
      claude mcp add --scope user "$NAME" ${args[@]+"${args[@]}"} -- "$PY" "$SERVER" </dev/null
      ;;
    codex)
      backup_once "${CODEX_HOME:-$HOME/.codex}/config.toml"
      codex mcp remove "$NAME" >/dev/null 2>&1 || true
      for flag in ${ENVS[@]+"${ENVS[@]}"}; do args+=(--env "$flag"); done
      codex mcp add "$NAME" ${args[@]+"${args[@]}"} -- "$PY" "$SERVER" </dev/null
      ;;
    hermes)
      backup_once "${HERMES_HOME:-$HOME/.hermes}/config.yaml"
      hermes mcp remove "$NAME" <<< "y" >/dev/null 2>&1 || true
      [ ${#ENVS[@]} -gt 0 ] && args+=(--env "${ENVS[@]}")
      # "y" answers hermes' prompt to enable the discovered tools. It exits 0 even when
      # nothing was saved, so confirm the entry really exists.
      hermes mcp add "$NAME" --command "$PY" ${args[@]+"${args[@]}"} --args "$SERVER" <<< "y"
      hermes mcp list 2>/dev/null | grep -q "^ *$NAME "
      ;;
    *)
      warn "Unknown client '$client' (expected claude, codex or hermes)."
      return 1
      ;;
  esac
}

# The skill teaches the agent how to use the tools well; every agent reads <skills>/<name>/SKILL.md.
install_skill() {
  local dir
  case "$1" in
    claude) dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" ;;
    codex) dir="${CODEX_HOME:-$HOME/.codex}/skills" ;;
    hermes) dir="${HERMES_HOME:-$HOME/.hermes}/skills" ;;
  esac
  mkdir -p "$dir/$NAME" && cp "$SRC/skill/kicad/SKILL.md" "$dir/$NAME/SKILL.md" &&
    echo "Installed skill to $dir/$NAME/SKILL.md"
}

if [ "$CLIENTS" != "none" ]; then
  OLD_IFS="$IFS"; IFS=","
  for client in $CLIENTS; do
    IFS="$OLD_IFS"
    if register "$client"; then
      case " $FAILED " in *" $client "*) ;; *)
        REGISTERED="$REGISTERED $client"
        [ "$INSTALL_SKILL" = 0 ] || install_skill "$client" || warn "Could not install the skill for $client."
        ;;
      esac
    else
      warn "Could not register with $client. See README.md for the manual configuration."
      FAILED="$FAILED $client"
    fi
  done
  IFS="$OLD_IFS"
fi

say "Done"
echo "Server:         $SERVER"
echo "Launch command: $PY $SERVER"
echo "Registered:    ${REGISTERED:- none}"
[ -z "$FAILED" ] || echo "Not registered:$FAILED"
echo "Restart the agent (or start a new session) so it picks up the '$NAME' tools."
[ -z "$FAILED" ] || exit 1
