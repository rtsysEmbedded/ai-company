---
title: Read Hermes' board and session databases without stopping agents
date: '2026-10-08'
author_role: claude-code
kind: howto
tags: hermes sqlite kanban.db state.db read-only
project: ai-company
---

# Read Hermes' board and session databases without stopping agents

## Goal
Look into `~/.hermes/kanban.db` or a profile's `state.db` while agents run.

## Steps
1. Prefer the CLI: `hermes kanban list --json`, `hermes kanban show <id> --json`.
2. If SQL is needed, open read-only by URI: `file:<path>?mode=ro&readonly_shm=1` when `-wal` and `-shm` exist, else `file:<path>?immutable=1` (see `_connect` in `services/panel/activity_api.py`, `_board` in `lead_guard.py`).
3. Never use a plain `sqlite3 <file>` or `sqlite3.connect(path)`.

## Check
Running agents keep working; no `Bus error` crash report in `~/Library/Logs/DiagnosticReports/`.
