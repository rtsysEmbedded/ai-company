---
title: deepseek-v4-pro appends suffixes to board tool field names
date: '2026-10-08'
author_role: claude-code
kind: error
tags: deepseek kanban tool-call field-name loop hw-lead
project: ai-company
---

# deepseek-v4-pro appends suffixes to board tool field names

## Symptom
A lead on `deepseek/deepseek-v4-pro-0813` calls `kanban_complete` again and again and gets "provide at least one of: summary (preferred), result". In one run 270 calls, about $0.23. Tasks it creates can arrive without body or parents.

## Cause
The model writes field names with a suffix: `summary_ui`, `summary_x`, `body_html`, `parents_string`. Hermes drops unknown fields without an error that names them.

## Fix
`departments/engine/lead_guard.py` (`FIELDS`, `field_reason`) refuses a board call with a suffixed field and names the right one. Every agent has this guard. `tool_loop_guardrails.hard_stop_enabled: true` stops a run after 8 failures of the same tool.

## How to check
In the task's "All messages" in the panel, look at the arguments of the failing call. Test: `tests/test_lead_guard.py`.
