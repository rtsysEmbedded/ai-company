#!/usr/bin/env bash
# Sets up the departments in this folder on a machine: Hermes Agent (if missing), the FreeCAD and KiCad
# tools, one Hermes profile per role of every department, and the web interface as a background service.
# The software department (team.yaml, knowledge base) has its own setup and is not touched.
# macOS and Linux. Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the company folder
DEPT="$ROOT/dept"
CLIENTS=""
INSTALL_HERMES="ask"
INSTALL_APPS=0
RUN_TESTS=1
APPLY=1
START_UI=0
SERVICE=1

usage() {
  cat <<EOF
Usage: departments/install.sh [options]

  --install-hermes   Install Hermes Agent without asking if it is missing
  --install-apps     Install KiCad and FreeCAD without asking if they are missing
  --clients LIST     Which agents also get the KiCad/FreeCAD tools directly: claude,codex,hermes
                     (default: every one that is installed)
  --skip-tests       Skip the end-to-end tests (faster)
  --no-apply         Install everything but do not create the Hermes profiles yet
  --no-service       Do not run the web interface as a background service
  --ui               Open the web interface when done
  -h, --help         Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --install-hermes) INSTALL_HERMES="yes"; shift ;;
    --install-apps) INSTALL_APPS=1; shift ;;
    --clients) CLIENTS="$2"; shift 2 ;;
    --skip-tests) RUN_TESTS=0; shift ;;
    --no-apply) APPLY=0; shift ;;
    --no-service) SERVICE=0; shift ;;
    --ui) START_UI=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\n=== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- 1. Python ----------------------------------------------------------------
command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' ||
  die "Python 3.8 or newer is required (python3 was not found on PATH)."
say "1/5 Python: $(python3 --version 2>&1)"

# --- 2. Hermes Agent ------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
say "2/5 Hermes Agent"
if ! command -v hermes >/dev/null 2>&1; then
  if [ "$INSTALL_HERMES" = "ask" ] && [ -t 0 ]; then
    printf 'Hermes Agent is not installed. Install it now with its official installer? [y/N] '
    read -r answer
    case "$answer" in y|Y|yes|YES) INSTALL_HERMES="yes" ;; esac
  fi
  [ "$INSTALL_HERMES" = "yes" ] ||
    die "Hermes Agent is required. Re-run with --install-hermes, or install it from https://hermes-agent.nousresearch.com/docs/getting-started/installation"
  command -v git >/dev/null 2>&1 || die "git is needed to install Hermes Agent. Install git and re-run."
  command -v curl >/dev/null 2>&1 || die "curl is needed to install Hermes Agent. Install curl and re-run."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
  hash -r
  command -v hermes >/dev/null 2>&1 || die "Hermes was installed but 'hermes' is not on PATH. Open a new terminal and re-run this script."
fi
hermes --version 2>&1 | head -1

# --- 3. FreeCAD and KiCad tools ---------------------------------------------------
say "3/5 FreeCAD and KiCad tools"
for pkg in freecad kicad; do
  installer="$ROOT/packages/$pkg-mcp/install.sh"
  [ -f "$installer" ] || die "packages/$pkg-mcp is missing. Copy the complete company folder."
  args=()
  [ -n "$CLIENTS" ] && args+=(--clients "$CLIENTS")
  [ "$INSTALL_APPS" = 1 ] && args+=("--install-$pkg")
  [ "$RUN_TESTS" = 0 ] && args+=(--skip-test)
  echo "--- $pkg"
  bash "$installer" ${args[@]+"${args[@]}"} ||
    die "The $pkg tools could not be installed (see above). If the application is missing, re-run with --install-apps."
done

# --- 4. Model list, configuration, tests ------------------------------------------
say "4/5 Configuration"
"$DEPT" models --refresh || warn "Could not fetch the model list from OpenRouter (offline?). Model ids cannot be checked."
"$DEPT" validate || die "A department's configuration is not valid (see above)."
if [ "$RUN_TESTS" = 1 ]; then
  echo "Running the department test in a temporary Hermes home (your own profiles are not touched)..."
  python3 "$ROOT/departments/engine/tests/test_departments.py" | grep -E "FAIL|All checks passed|FAILED|cannot run" ||
    die "The department test failed. Run python3 departments/engine/tests/test_departments.py to see why."
fi

# --- 5. Hermes profiles -----------------------------------------------------------
if [ "$APPLY" = 1 ]; then
  say "5/5 Creating the Hermes profiles"
  "$DEPT" apply || die "Some profiles could not be set up (see above). Fix the cause and run ./dept apply."
else
  say "5/5 Skipped creating the Hermes profiles (--no-apply). Run ./dept apply when ready."
fi

if [ "$SERVICE" = 1 ]; then
  say "Web interface service"
  bash "$ROOT/departments/engine/service.sh" install || warn "The background service could not be installed. Start the interface by hand with ./dept ui."
fi

say "Done"
"$DEPT" status
cat <<EOF

Next steps:
  1. Open the web interface and add your OpenRouter API key:   ./dept open
     then press "Save and apply to Hermes".
  2. Talk to a department's lead, for example hardware:        hermes -p hw-lead
     "Design a 555 LED blinker on a small through-hole board with a 3D-printable holder."
  3. Tasks are carried out by Hermes's dispatcher. If the status above says it is not running,
     start it with:                                            hermes gateway start
See departments/README.md for details.
EOF
if [ "$START_UI" = 1 ]; then
  exec "$DEPT" open
fi
