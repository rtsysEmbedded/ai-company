---
title: Hermes does not pass HERMES_KANBAN_TASK to a hook
date: '2026-10-08'
author_role: claude-code
kind: error
tags: hermes hook pre_tool_call lead-guard environment kanban
project: ai-company
---

# Hermes does not pass HERMES_KANBAN_TASK to a hook

## Symptom
The lead guard allowed a lead to run terminal commands on a board task, although it should refuse them. Its refusals worked only for checks that also run in a chat.

## Cause
Hermes starts a shell hook with a cleaned environment (`build_subprocess_env` in `tools/environments/local.py`): `HERMES_KANBAN_TASK` and `HERMES_KANBAN_RUN_ID` are removed, `HERMES_HOME` and `HERMES_PROFILE` stay. The guard therefore saw every board run as a chat.

## Fix
The guard finds the task on the board: the run in `task_runs` whose `worker_pid` is one of the processes above the hook (`board_task()` in `lead_guard.py`).

## How to check
Give a lead a board task and look for "terminal is switched off for you on a board task" in its messages. Test: "without the variable the board task is found through the worker's pid" in `tests/test_lead_guard.py`.
