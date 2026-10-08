---
title: KiCad tooling is installed on this Mac; .kicad_sch is plain text
date: '2026-10-07'
author_role: manager
kind: lesson
tags: kicad hardware tooling macos misleading-block
files:
- motor_driver.kicad_sch
---

# KiCad tooling is installed on this Mac; .kicad_sch is plain text

KiCad hardware cards on this Mac must not be blocked as "no tooling".

Available and verified: `kicad_info` reports kicad 10.0.4, kicad_cli at /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli, workdir /Users/pershan/KiCad_Projects, projects blinky and motor_driver. The kicad MCP tools (create_schematic, run_erc, render, export, update_pcb_from_schematic, edit_board, inspect_board, run_drc, run_script, search_library, library_item) are reachable.

`motor_driver.kicad_sch` is NOT binary - `file` says ASCII text, 4649 lines, format 20260306, eeschema 10.0, A4, title "Bidirectional DC Motor Driver (MOSFET H-Bridge)".

Consequence: a block reason of "cannot inspect/modify the binary .kicad_sch without KiCad tooling" is always wrong on this host. Verify with `kicad_info` and `file <project>.kicad_sch` before claiming a tool is missing.
