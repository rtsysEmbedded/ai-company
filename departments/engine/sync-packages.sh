#!/usr/bin/env bash
# Refresh the bundled copies of the FreeCAD and KiCad MCP packages from their source folders,
# so the company folder stays self-contained when it is copied to another machine.
# Usage: departments/engine/sync-packages.sh [folder that contains freecad-mcp and kicad-mcp]   (default: $HOME)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # the company folder
FROM="${1:-$HOME}"
for pkg in freecad-mcp kicad-mcp; do
  [ -f "$FROM/$pkg/install.sh" ] || { echo "ERROR: $FROM/$pkg/install.sh not found" >&2; exit 1; }
  rm -rf "$ROOT/packages/$pkg"
  mkdir -p "$ROOT/packages/$pkg"
  (cd "$FROM/$pkg" && tar --exclude='__pycache__' --exclude='.DS_Store' -cf - .) | (cd "$ROOT/packages/$pkg" && tar -xf -)
  echo "packages/$pkg <- $FROM/$pkg"
done
