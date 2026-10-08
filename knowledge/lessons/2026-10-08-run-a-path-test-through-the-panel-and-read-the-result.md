---
title: Run a path test through the panel and read the result
date: '2026-10-08'
author_role: claude-code
kind: howto
tags: path-test panel kanban dispatch test
project: ai-company
---

# Run a path test through the panel and read the result

## Goal
Prove that a job goes manager or lead -> specialist -> review -> lead's check, with cheap models, and see what it cost.

## Steps
1. Create the task for a lead: panel > Projects > New task, or `POST /api/agents/<lead>/task` with `title`, `body`, `folder` (header `X-Panel-Token` from `data/panel_token`).
2. Watch it: panel > Flow > the project, or `hermes kanban list --json`.
3. Read one task: `hermes kanban show <id> --json` (runs, events, comments). All messages and tool calls: `GET /api/tasks/<id>/conversation`.
4. Cost per task: `GET /api/activity?range=today`, field `usage.cost` of each task.
5. Stop a runaway run: `hermes kanban block <id> "<reason>"`, then kill the worker pid shown by `ps aux | grep "work kanban task"`. `hermes kanban reclaim` alone restarts it.

## Check
Each task has one run per agent, the lead's `check and report` task is done, and the files named in the handoffs exist.
