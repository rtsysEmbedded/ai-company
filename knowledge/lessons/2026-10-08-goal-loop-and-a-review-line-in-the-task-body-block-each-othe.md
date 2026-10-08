---
title: Goal loop and a review line in the task body block each other
date: '2026-10-08'
author_role: claude-code
kind: error
tags: hermes goal-mode judge review kanban_request_review
project: ai-company
---

# Goal loop and a review line in the task body block each other

## Symptom
A specialist on a goal-loop task calls `kanban_request_review` and gets "Goal review handoff rejected by judge: ... has not yet called kanban_request_review". After several tries it closes the task with `kanban_complete`, so the change is never reviewed.

## Cause
The judge of Hermes' goal loop reads the task's title and body as the goal. A body that ends with "When done, call kanban_request_review ..." makes the review call itself an unmet part of the goal.

## Fix
No review instruction in a task body. The review is the specialist's own rule (`NO_REVIEW_LINE` in `dept.py`, specialist rules in `dept.py` and `apply_team.py`). The guard refuses a specialist task whose body contains `kanban_request_review`, and a review request without `reviewer`.

## How to check
A code task must show two runs: implementer `review_requested`, then reviewer `completed`.
