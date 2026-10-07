#!/usr/bin/env bash
# Removes the departments' web interface service and Hermes profiles. With --tools it also removes
# the FreeCAD and KiCad tools. The software department, Hermes itself, KiCad, FreeCAD, your projects
# and the department folders are left alone.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS=0
YES=()
for arg in "$@"; do
  case "$arg" in
    --tools) TOOLS=1 ;;
    --yes|-y) YES=(--yes) ;;
    -h|--help) echo "Usage: departments/uninstall.sh [--tools] [--yes]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

bash "$ROOT/departments/engine/service.sh" uninstall || true
for department in $("$ROOT/dept" list | awk '{print $1}'); do
  echo "--- $department"
  "$ROOT/dept" remove "$department" ${YES[@]+"${YES[@]}"}
done

if [ "$TOOLS" = 1 ]; then
  for pkg in freecad kicad; do
    echo "--- removing the $pkg tools"
    bash "$ROOT/packages/$pkg-mcp/uninstall.sh"
  done
fi
echo "Done. Your projects and the department folders were not touched."
