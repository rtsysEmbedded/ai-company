---
title: Review request without a reviewer re-runs the implementer
date: '2026-10-08'
author_role: claude-code
kind: error
tags: kanban review reviewer dispatcher cost
project: ai-company
---

# Review request without a reviewer re-runs the implementer

## Symptom
A task in status `review` is started again and again with the implementer's profile. In one job `backend` ran 5 times ($0.63) before the reviewer got the task.

## Cause
`kanban_request_review` was called without `reviewer`. The task stays assigned to the implementer, and Hermes' review dispatch starts the assignee as reviewer.

## Fix
Every specialist's guard refuses `kanban_request_review` without `reviewer: <the team's reviewer>` (`review_reason` in `lead_guard.py`).

## How to check
`hermes kanban show <task> --json`: the `review_requested` event must have a `reviewer`.
