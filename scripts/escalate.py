#!/usr/bin/env python3
"""Escalate kanban tasks that keep failing to the escalation profile (team.yaml routing).

A task is escalated when its runs have `escalate_after` or more failed outcomes (crash, timeout,
give-up, stale, or a reviewer's changes_requested) and it is not already with the escalation
profile. The task is reassigned, unblocked if needed, and marked with a comment so it is only
escalated once.

Failures the provider caused (no credits, rejected key, rate limit, missing model) are not the
agent's fault and a stronger agent cannot fix them. Those are never counted or escalated; the
task is put on hold (blocked with the real reason) so the dispatcher stops retrying it. Run periodically (launchd) or by hand: uv run python scripts/escalate.py [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import causes  # noqa: E402  (reads Hermes' worker logs for the real failure reason)

FAILED = {"crashed", "timed_out", "spawn_failed", "gave_up", "stale", "changes_requested"}
MARK = "[ai-company escalated]"
HOLD = "[ai-company on hold]"


def kanban(*args, check=True):
    r = subprocess.run(["hermes", "kanban", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f"hermes kanban {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    team = yaml.safe_load(open(os.path.join(ROOT, "team.yaml"))) or {}
    routing = team.get("routing") or {}
    limit, target = int(routing.get("escalate_after", 2)), routing.get("escalate_to", "architect")
    reviewers = {routing.get("reviewer", "reviewer"), routing.get("security_profile", "security")}
    # Only this team's tasks: other departments (departments/) share the board, and the software
    # escalation profile has neither their tools nor their rules.
    own = {spec.get("hermes_profile") or name for name, spec in (team.get("roles") or {}).items()}

    disabled = {spec.get("hermes_profile") or name for name, spec in (team.get("roles") or {}).items()
                if not spec.get("enabled", True)}

    # 1) Hold: a task whose latest worker run was refused by the provider must not be retried.
    for status in ("ready", "todo", "review"):
        for t in json.loads(kanban("list", "--status", status, "--json") or "[]"):
            if t.get("assignee") and t["assignee"] not in own:
                continue
            cause = causes.run_causes(t["id"]).get("_last")
            if t.get("assignee") in disabled and status != "review":
                label = f"{t['assignee']} is disabled (enable it in the panel, or reassign this task)"
            elif cause in causes.INFRA:
                label = causes.LABELS[cause]
            else:
                continue
            detail = json.loads(kanban("show", t["id"], "--json"))
            last_run = max((r.get("ended_at") or 0 for r in detail.get("runs", [])), default=0)
            held = max((c.get("created_at") or 0 for c in detail.get("comments", []) if HOLD in (c.get("body") or "")), default=0)
            if held >= last_run:
                continue  # already held for this failure; if it is ready again, someone released it on purpose
            print(f"hold {t['id']} '{t.get('title')}' ({t.get('assignee')}): {label}")
            if a.dry_run:
                continue
            note = f"{HOLD} {label}. Fix the cause, then unblock this task; nothing is wrong with the work itself."
            r = subprocess.run(["hermes", "kanban", "block", t["id"], note], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                # Hermes will not block a task that is in review; record the reason so it is visible
                # and not repeated. Hermes stops retrying by itself after its own failure limit.
                kanban("comment", t["id"], note, check=False)

    # 2) Escalate: repeated real failures go to the escalation profile.
    for status in ("blocked", "ready", "todo"):
        for t in json.loads(kanban("list", "--status", status, "--json") or "[]"):
            if t.get("assignee") in {target, *reviewers}:
                continue
            if t.get("assignee") and t["assignee"] not in own:
                continue
            detail = json.loads(kanban("show", t["id"], "--json"))
            if any(MARK in (c.get("body") or "") for c in detail.get("comments", [])):
                continue
            found = causes.run_causes(t["id"])
            if found.get("_last") in causes.INFRA:
                continue  # the provider refused the last run; a stronger agent would be refused too
            infra = sum(1 for sid, cause in found.items() if sid != "_last" and cause in causes.INFRA)
            failures = [r.get("outcome") for r in detail.get("runs", []) if r.get("outcome") in FAILED]
            failures = failures[:max(0, len(failures) - infra)]  # provider refusals do not count
            if len(failures) < limit:
                continue
            reason = f"{len(failures)} failed attempts ({', '.join(failures)}) by {t.get('assignee')}"
            print(f"escalate {t['id']} '{t.get('title')}' -> {target}: {reason}")
            if a.dry_run:
                continue
            kanban("reassign", t["id"], target, "--reclaim", "--reason", reason)
            if status == "blocked":
                kanban("unblock", t["id"], "--reason", f"escalated to {target}", check=False)
            kanban("comment", t["id"], f"{MARK} {reason}. Reassigned to {target}; read the history above first.")


if __name__ == "__main__":
    main()
