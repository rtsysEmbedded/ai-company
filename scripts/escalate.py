#!/usr/bin/env python3
"""Escalate kanban tasks that keep failing to the escalation profile (team.yaml routing).

A task is escalated when its runs have `escalate_after` or more failed outcomes (crash, timeout,
give-up, stale, or a reviewer's changes_requested) and it is not already with the escalation
profile. The task is reassigned, unblocked if needed, and marked with a comment so it is only
escalated once.

Failures the provider caused (no credits, rejected key, rate limit, missing model) are not the
agent's fault and a stronger agent cannot fix them. Those are never counted or escalated; the
task is put on hold (blocked with the real reason) so the dispatcher stops retrying it.

A specialist's blocked task goes to its lead, in every department: the lead gets a `[blocked]` task with the
reason. When the lead has finished that task, its answer is written onto the blocked task and the task starts
again; if the lead gave the work to someone else instead, the blocked task is closed and the tasks that waited
for it wait for the new one. A task that blocks again after two answers stays blocked for the user.

Run periodically (launchd) or by hand: uv run python scripts/escalate.py [--dry-run]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import budget_api  # noqa: E402  (monthly budget per agent)
import causes  # noqa: E402  (reads Hermes' worker logs for the real failure reason)
import reissue  # noqa: E402  (continues an answered goal-loop task with the answer in its text)

FAILED = {"crashed", "timed_out", "spawn_failed", "gave_up", "stale", "changes_requested"}
MARK = "[ai-company escalated]"
HOLD = "[ai-company on hold]"
TO_LEAD = "[ai-company to lead]"         # on the blocked task: the lead's task id follows
ANSWERED = "[ai-company lead answered]"  # on the blocked task: the lead's task that was used
BLOCKED_TAG = "[blocked]"                # title prefix of the lead's task (lead_guard.py lets the lead answer these)
MAX_ANSWERS = 2                          # after this many answers a task that blocks again waits for the user
SINCE_FILE = os.path.join(os.environ.get("PANEL_DATA") or os.path.join(ROOT, "data"), "lead_routing_since")


def kanban(*args, check=True):
    r = subprocess.run(["hermes", "kanban", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f"hermes kanban {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def leads(team):
    """{profile: its lead} for every specialist of every department whose lead can run; a lead itself is not in it."""
    out = {}
    roles = team.get("roles") or {}
    lead = (team.get("routing") or {}).get("lead")
    if lead in roles and roles[lead].get("enabled", True):
        for name, spec in roles.items():
            if name not in ("manager", lead) and spec.get("routable", True):
                out[spec.get("hermes_profile") or name] = roles[lead].get("hermes_profile") or lead
    try:
        sys.path.insert(0, os.path.join(ROOT, "departments", "engine"))
        import dept
        for name in dept.list_departments():
            cfg = dept.load_config(name)
            lead = (cfg.get("routing") or {}).get("lead")
            if lead and (cfg["roles"].get(lead) or {}).get("enabled", True) and os.path.isdir(dept.profile_dir(lead)):
                out.update({role: lead for role in cfg["roles"] if role != lead})
    except Exception as exc:   # the departments engine is optional
        print(f"departments not read: {exc}")
    return out


def routing_since():
    """Blocks older than the first run of this feature are left alone: nobody asked for old tasks to be reopened."""
    try:
        return float(open(SINCE_FILE).read().strip())
    except (OSError, ValueError):
        os.makedirs(os.path.dirname(SINCE_FILE), exist_ok=True)
        with open(SINCE_FILE, "w") as f:
            f.write(str(time.time()))
        return time.time()


def _marks(detail, mark):
    """[(time, text after the mark)] of the comments carrying `mark`, oldest first."""
    found = [(c.get("created_at") or 0, (c.get("body") or "").split(mark, 1)[1].strip())
             for c in detail.get("comments", []) if mark in (c.get("body") or "")]
    return sorted(found)


def _last_block(detail):
    """(time, reason) of the newest block of a task."""
    blocks = [e for e in detail.get("events", []) if e.get("kind") in ("blocked", "block_loop_detected", "gave_up")]
    if not blocks:
        return 0, ""
    payload = blocks[-1].get("payload") or {}
    return blocks[-1].get("created_at") or 0, str(payload.get("reason") or payload.get("error") or "")


def to_lead(team, dry_run, since=None):
    """Send each newly blocked specialist task to its lead, and bring the lead's finished answers back."""
    lead_of = leads(team)
    since = routing_since() if since is None else since
    blocked = json.loads(kanban("list", "--status", "blocked", "--json") or "[]")
    everything = None
    for t in blocked:
        lead = lead_of.get(t.get("assignee"))
        if not lead or (t.get("title") or "").startswith(BLOCKED_TAG):
            continue
        detail = json.loads(kanban("show", t["id"], "--json"))
        at, reason = _last_block(detail)
        if at < since or any(when >= at for when, _ in _marks(detail, HOLD)):
            continue   # an old block, or held because of the provider: not a question for the lead
        sent, answered = _marks(detail, TO_LEAD), _marks(detail, ANSWERED)
        open_ask = sent[-1] if sent and sent[-1][0] >= at else None

        if not open_ask:   # 1) a new block: ask the lead
            if len(answered) >= MAX_ANSWERS or (task_body := (detail.get("task") or {}).get("body") or "").count("\nANSWER from ") >= MAX_ANSWERS:
                continue
            print(f"to lead {t['id']} '{t.get('title')}' ({t.get('assignee')}) -> {lead}: {reason[:120]}")
            if dry_run:
                continue
            body = (f"Your specialist {t['assignee']} blocked task {t['id']} \"{t.get('title')}\".\n\nIts reason:\n{reason or '(none given)'}\n\n"
                    f"What the task asked for:\n{(detail.get('task') or t).get('body') or ''}\n\n"
                    "Decide as your rules say under \"When a specialist is blocked\", then finish with kanban_complete: "
                    "your answer as the result, or a new task for someone who can do it, or a [request] to another team's "
                    "lead. Only if the user must decide, use kanban_block with kind needs_input and the question.")
            cmd = ["create", f"{BLOCKED_TAG} {t.get('title') or t['id']}"[:160], "--assignee", lead, "--body", body[:20000],
                   "--created-by", "ai-company", "--max-runtime", "20m"]
            if t.get("workspace_path"):
                cmd += ["--workspace", f"dir:{t['workspace_path']}"]
            m = re.search(r"\b(t_[0-9a-f]+)\b", kanban(*cmd))
            if m:
                kanban("comment", t["id"], f"{TO_LEAD} {m.group(1)} - {lead} decides what happens with this task.")
            continue

        # 2) the lead was asked: is its task finished?
        ask_id = (re.search(r"t_[0-9a-f]+", open_ask[1]) or [None])[0]
        if not ask_id:
            continue
        ask = json.loads(kanban("show", ask_id, "--json", check=False) or "{}")
        ask_task = ask.get("task") or {}
        if ask_task.get("status") != "done":
            continue   # still planned, running, or the lead asked the user
        run = (ask.get("runs") or [{}])[-1]
        if everything is None:
            everything = json.loads(kanban("list", "--json") or "[]")
        replaced = [x["id"] for x in everything
                    if x.get("created_by") == lead and x.get("assignee") != lead and x["id"] != ask_id
                    and not (x.get("title") or "").startswith(("[request]", BLOCKED_TAG))
                    and (run.get("started_at") or 0) - 5 <= (x.get("created_at") or 0) <= (run.get("ended_at") or time.time()) + 5]
        answer = (ask.get("latest_summary") or ask_task.get("result") or run.get("summary") or "").strip()
        if replaced:
            print(f"replaced {t['id']} by {', '.join(replaced)} ({lead})")
            if dry_run:
                continue
            for child in detail.get("children") or []:
                for new in replaced:
                    kanban("link", new, child, check=False)
                kanban("unlink", t["id"], child, check=False)
            kanban("comment", t["id"], f"{ANSWERED} {ask_id}: {lead} gave this work to {', '.join(replaced)}. This task is closed.")
            kanban("archive", t["id"], check=False)
        else:
            print(f"answered {t['id']} by {lead}: {answer[:120]}")
            if dry_run:
                continue
            answer = answer or "(no text; read the project folder)"
            kanban("comment", t["id"], "--author", lead, "--", f"Answer from your lead {lead}: {answer}")
            kanban("comment", t["id"], f"{ANSWERED} {ask_id}")
            # A goal-loop task is judged on its text alone, so the answer must be in the text: continue it as a new task.
            if reissue.is_goal_task(detail) and reissue.with_answer(kanban, detail, f"your lead {lead}", answer, reason):
                continue
            kanban("unblock", t["id"], "--reason", f"answered by {lead}", check=False)


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

    lead_of = leads(team)
    known = own | set(lead_of) | set(lead_of.values())   # every department's agents: a provider refusal holds any of them

    over = budget_api.over_budget()   # any department: an agent over its monthly budget gets no new work

    # 1) Hold: a task whose latest worker run was refused by the provider must not be retried.
    for status in ("ready", "todo", "review"):
        for t in json.loads(kanban("list", "--status", status, "--json") or "[]"):
            if t.get("assignee") and t["assignee"] not in known and t["assignee"] not in over:
                continue
            cause = causes.run_causes(t["id"]).get("_last")
            if t.get("assignee") in over and status != "review":
                b = over[t["assignee"]]
                label = f"{t['assignee']} has used its monthly budget (${b['spent']:.2f} of ${b['budget']:.2f}); raise it in the panel or wait for next month"
            elif t.get("assignee") not in known:
                continue
            elif t.get("assignee") in disabled and status != "review":
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

    # 3) A specialist's block is a question for its lead (every department).
    to_lead(team, a.dry_run)


if __name__ == "__main__":
    main()
