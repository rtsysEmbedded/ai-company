---
title: Closing the dispatch loop between leads and specialists
date: '2026-10-08'
author_role: claude-code
kind: lesson
tags: dispatch kanban lead specialist goal-mode guard
project: ai-company
---

# Closing the dispatch loop between leads and specialists

## What was the job
Review and improve how agents and task dispatching work together: manager -> lead -> specialist and back, between departments, and in the panel.

## What worked
Checks by the system instead of longer rules: the guard hook, Hermes' goal loop for specialists, a `check and report` task of the lead with the specialist tasks as parents, `[request]` tasks between leads, `[blocked]` tasks from a specialist to its lead. A clean software job is now 3 tasks with one run each, about $0.19.

## What went wrong
Every mechanism needed a live run to show its real fault: the guard never saw the board task, the goal loop fought with the review line and with answers given as comments, one model misspelled field names, a stopped planning run was repeated and made duplicates. Tests against a simulated board found none of these.

## Next time
After a change to rules or guards run one small real job per team before trusting it. Look at the runs of each task: more than one run of the same agent on a task is the sign of a fault. Keep tests small; a path test should cost cents.
