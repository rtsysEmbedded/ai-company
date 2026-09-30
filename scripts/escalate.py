#!/usr/bin/env python3
"""Escalate kanban tasks that keep failing to the escalation profile (team.yaml routing).

A task is escalated when its runs have `escalate_after` or more failed outcomes (crash, timeout,
give-up, stale, or a reviewer's changes_requested) and it is not already with the escalation
profile. The task is reassigned, unblocked if needed, and marked with a comment so it is only
escalated once. Run periodically (launchd) or by hand: uv run python scripts/escalate.py [--dry-run]
"""
import argparse
import json
import os
import subprocess

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED = {"crashed", "timed_out", "spawn_failed", "gave_up", "stale", "changes_requested"}
MARK = "[ai-company escalated]"


def kanban(*args, check=True):
    r = subprocess.run(["hermes", "kanban", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f"hermes kanban {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    routing = (yaml.safe_load(open(os.path.join(ROOT, "team.yaml"))) or {}).get("routing") or {}
    limit, target = int(routing.get("escalate_after", 2)), routing.get("escalate_to", "architect")
    reviewers = {routing.get("reviewer", "reviewer"), routing.get("security_profile", "security")}

    for status in ("blocked", "ready", "todo"):
        for t in json.loads(kanban("list", "--status", status, "--json") or "[]"):
            if t.get("assignee") in {target, *reviewers}:
                continue
            detail = json.loads(kanban("show", t["id"], "--json"))
            if any(MARK in (c.get("body") or "") for c in detail.get("comments", [])):
                continue
            failures = [r.get("outcome") for r in detail.get("runs", []) if r.get("outcome") in FAILED]
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
