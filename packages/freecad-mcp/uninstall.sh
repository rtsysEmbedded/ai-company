#!/usr/bin/env bash
# Removes the FreeCAD MCP server and its registrations. Your models and FreeCAD itself are left alone.
set -uo pipefail

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/freecad-mcp"
[ "${1:-}" = "--prefix" ] && PREFIX="$2"
NAME="freecad"

if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user "$NAME" </dev/null >/dev/null 2>&1 && echo "Removed from claude"
fi
if command -v codex >/dev/null 2>&1; then
  codex mcp remove "$NAME" </dev/null >/dev/null 2>&1 && echo "Removed from codex"
fi
if command -v hermes >/dev/null 2>&1; then
  hermes mcp remove "$NAME" <<< "y" >/dev/null 2>&1
  hermes mcp list 2>/dev/null | grep -q "^ *$NAME " || echo "Removed from hermes"
fi

for skills in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" "${CODEX_HOME:-$HOME/.codex}/skills" "${HERMES_HOME:-$HOME/.hermes}/skills"; do
  if [ -f "$skills/$NAME/SKILL.md" ]; then
    rm -f "$skills/$NAME/SKILL.md"
    rmdir "$skills/$NAME" 2>/dev/null
    echo "Removed skill from $skills"
  fi
done

rm -f "$PREFIX/freecad_mcp.py" "$PREFIX/fc_runner.py"
rm -rf "$PREFIX/__pycache__"
rmdir "$PREFIX" 2>/dev/null && echo "Removed $PREFIX"
echo "Done. Models in ~/FreeCAD_Components were not touched."
