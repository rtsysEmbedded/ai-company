---
title: 'How the company keeps knowledge: fixed note kinds, written by the lead at
  the end of a job'
date: '2026-10-08'
author_role: claude-code
kind: decision
tags: knowledge-management kb process lead
project: ai-company
---

# How the company keeps knowledge: fixed note kinds, written by the lead at the end of a job

## Context
After several jobs the knowledge base had almost no new notes. Software agents had the tools and a rule and did not use them; hardware agents had no knowledge tool.

## Decision
Knowledge is kept as in a company knowledge base: fixed kinds with fixed forms (lesson, error, howto, research, decision), each with project, task, author and date. Specialists end every handoff with Sources, Tools, Errors and fixes. The lead searches the knowledge base before it hands out work and writes the notes in its `check and report` task. The guard asks for the search, the notes and the handoff lines.

## Why
A rule alone was not followed. A fixed point in the process (the closing task that every job has) and a check by the system work with cheap models. One writer per job keeps duplicates low.

## Consequences
Direct work in Manual mode has no closing task and still depends on the agent's rule. Cheap models will write some weak notes; they can be retired (`kb_retire`) or deleted in the panel under Knowledge.
