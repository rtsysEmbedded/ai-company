---
title: An answer given as a comment does not reach the goal loop's judge
date: '2026-10-08'
author_role: claude-code
kind: error
tags: hermes goal-mode judge comment answer triage reissue
project: ai-company
---

# An answer given as a comment does not reach the goal loop's judge

## Symptom
A blocked goal-loop task gets its answer as a comment and is unblocked. The specialist does the work, but `kanban_complete` is refused ("judge ruled the goal unachievable"), the task blocks a second time and lands in status `triage`.

## Cause
The judge reads only title and body, not the comments. For it the missing information is still missing.

## Fix
An answered goal-loop task is continued as a new task with the answer written into its body; the old task is archived and its children are linked to the new one (`services/panel/reissue.py`, used by `scripts/escalate.py` and the panel's Answer button).

## How to check
After an answer the board shows a new task with the same title whose body ends with "ANSWER from ...". Test: `tests/test_to_lead.py`.
