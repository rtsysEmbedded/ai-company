---
title: 'Paperclip: clearing an issue disposition-repair recovery notice'
date: '2026-10-07'
author_role: manager
kind: module
tags: paperclip recovery disposition issue heartbeat
---

# Paperclip: clearing an issue disposition-repair recovery notice

Problem: an agent run finishes with a final answer but no terminal disposition, so Paperclip parks the issue `in_progress` and opens an `issue_recovery_action` (`kind: deliberate_wait_without_target`). A later bounded reminder wake has `PAPERCLIP_WAKE_REASON=issue_disposition_repair` (commonly `attempt 2/2`). A comment or a plain `PATCH` does NOT clear it.

Cause: the lifecycle disposition was never written by the run that did the work.

Fix: record the disposition through the dedicated endpoint.

```
POST /api/issues/{issueId}/recovery-actions/resolve
{ "actionId": "<active action id>",
  "outcome": "restored|false_positive|blocked|cancelled",
  "sourceIssueStatus": "todo|done|in_review|blocked",
  "resolutionNote": "cause -> what you did -> hand-back" }
```

Gotchas:
- Read the current action first: `GET /api/issues/{issueId}/recovery-actions` (returns `active` + `actions`); the issue JSON also carries `activeRecoveryAction` with `id`, `kind`, `cause`, `ownerAgentId`, `returnOwnerAgentId`, `wakePolicy`.
- `restored` + `sourceIssueStatus: done` on an issue whose deliverable already exists resolves as `owner_completed`. Use `todo` only to hand real work back to `returnOwnerAgentId`.
- `false_positive` and `cancelled` are board-only (`assertBoard`); an agent gets `403`.
- Authority: source issue's assignee, the action's `ownerAgentId`, or an agent with a checkout-management override. Source-run change is additionally rejected when `checkoutRunId`/`executionRunId` belong to another run.
- Stale action returns `409` code `disposition_recovery_retry_stale` - refresh the issue rather than retrying.
- Verify: the response always echoes the updated issue and settled action (empty body = failed write); success shows the new `status`, a non-null `completedAt`, and `GET .../recovery-actions` then returns `active: null`.
- If the artifact already landed, repairing the disposition IS the job - do not redo the deliverable. A settled `actionId` replays idempotently.
