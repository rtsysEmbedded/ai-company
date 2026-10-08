#!/usr/bin/env python3
"""Lead guard: a Hermes pre_tool_call hook that keeps a team lead from doing the specialists' work.

It mainly acts while the lead runs a task from the board (found through the run's worker pid); in a chat with the
user the lead keeps all its tools, and only the tasks it creates for specialists are checked (goal loop on). On a board task the lead may read, plan and hand out tasks. It may not
run commands, change files other than its notes, or use a department tool that produces work, and it
cannot finish a task without having given work to its team. Every refusal tells it whom to give the work to.

Installed by `dept apply` / `apply_team.py`, which also write lead_guard.json (the team) into the profile.
Fails open: if anything here goes wrong, the tool call is allowed.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import quote

WORK_TOOLS = re.compile(r"terminal|execute_code|patch|delegate_task|image_gen.*|computer_use.*|browser_.*|process.*")
NOTES = (".md", ".txt")                                    # what a lead may write: its plan, spec and decisions
LOOK = re.compile(r"(info|inspect|search|library_item|list|read|get|show|describe|status)", re.I)   # department tools that only look
ANSWER_TASKS = ("[blocked]", "[request]")                  # titles of tasks a lead may answer itself
GOAL_TURNS = 6                                             # turns a specialist gets in the goal loop before the task is blocked
OPEN_MCP = {"kb"}                                          # tool servers a lead may use freely


def allow():
    print("{}")
    sys.exit(0)


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(2)


def team_text(config):
    members = ["%s (%s)" % (m["name"], m.get("tools") or "no special tools") for m in config.get("team") or []]
    return "Your team: " + ", ".join(members) + "." if members else ""


def created_tasks(profile, task_id, run_id):
    """How many tasks this lead created since the current run started; None when the board cannot be read."""
    db = os.environ.get("HERMES_KANBAN_DB") or os.path.join(os.path.expanduser("~/.hermes"), "kanban.db")
    if not os.path.exists(db):
        return None
    live = os.path.exists(db + "-wal") and os.path.exists(db + "-shm")   # never create or reset Hermes' side files
    try:
        con = sqlite3.connect("file:%s?%s" % (quote(db), "mode=ro&readonly_shm=1" if live else "immutable=1"), uri=True, timeout=2)
        try:
            row = con.execute("SELECT started_at FROM task_runs WHERE id = ?", (run_id,)).fetchone() if run_id else None
            if not row:
                row = con.execute("SELECT started_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
            since = (row[0] if row and row[0] else 0) - 5
            return con.execute("SELECT COUNT(*) FROM tasks WHERE created_by = ? AND created_at >= ? AND id != ?",
                               (profile, since, task_id)).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _board():
    """A read-only connection to the board that never creates or resets Hermes' side files; None when it cannot be read."""
    db = os.environ.get("HERMES_KANBAN_DB") or os.path.join(os.path.expanduser("~/.hermes"), "kanban.db")
    if not os.path.exists(db):
        return None
    live = os.path.exists(db + "-wal") and os.path.exists(db + "-shm")
    try:
        return sqlite3.connect("file:%s?%s" % (quote(db), "mode=ro&readonly_shm=1" if live else "immutable=1"), uri=True, timeout=2)
    except sqlite3.Error:
        return None


def _ancestors(limit=6):
    """The pids above this hook: Hermes itself and whatever started it."""
    pids, pid = [], os.getppid()
    while pid > 1 and len(pids) < limit:
        pids.append(pid)
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return pids


def board_task():
    """(task id, run id) of the board task this lead is working on, or (None, None) in a chat.

    Hermes does not pass HERMES_KANBAN_TASK on to a hook, so the running task is found on the board:
    the run whose worker is the Hermes process that started this hook."""
    if os.environ.get("HERMES_KANBAN_TASK"):
        return os.environ["HERMES_KANBAN_TASK"], os.environ.get("HERMES_KANBAN_RUN_ID")
    pids = _ancestors()
    con = _board() if pids else None
    if not con:
        return None, None
    try:
        row = con.execute("SELECT task_id, id FROM task_runs WHERE ended_at IS NULL AND worker_pid IN (%s) ORDER BY id DESC LIMIT 1"
                          % ",".join("?" * len(pids)), pids).fetchone()
        return (row[0], str(row[1])) if row else (None, None)
    except sqlite3.Error:
        return None, None
    finally:
        con.close()


def own_task(profile, task_id):
    """True for a task the lead may finish without handing out work: its own closing "check and report" task,
    a blocked specialist's question, or another lead's request (it may simply answer)."""
    db = os.environ.get("HERMES_KANBAN_DB") or os.path.join(os.path.expanduser("~/.hermes"), "kanban.db")
    if not os.path.exists(db):
        return False
    live = os.path.exists(db + "-wal") and os.path.exists(db + "-shm")
    try:
        con = sqlite3.connect("file:%s?%s" % (quote(db), "mode=ro&readonly_shm=1" if live else "immutable=1"), uri=True, timeout=2)
        try:
            row = con.execute("SELECT created_by, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return bool(row and (row[0] == profile or str(row[1] or "").lower().startswith(ANSWER_TASKS)))
        finally:
            con.close()
    except sqlite3.Error:
        return False


def goal_reason(args, config):
    """A reason to refuse a kanban_create: a specialist's task without the goal loop, or a check that waits for nothing."""
    args = args if isinstance(args, dict) else {}
    names = {m["name"] for m in config.get("team") or []}
    if str(args.get("assignee") or "") == config.get("lead") and "check and report" in str(args.get("title") or "").lower() and not args.get("parents"):
        return ("A `check and report` task needs `parents`: the ids of the tasks it checks. Without them it starts before "
                "that work is finished. Create it again with `parents`.")
    if str(args.get("assignee") or "") not in names or str(args.get("goal_mode")).lower() in ("true", "1"):
        return None
    return ("A task for a specialist needs `goal_mode: true` and `goal_max_turns: %d`: without it the specialist often stops "
            "before closing the task and the whole run is repeated. Create the task again with both fields." % GOAL_TURNS)


# The real field names of the board tools. Some models append a suffix to a name ("summary_ui", "body_html"); Hermes
# then drops the field without a word, so a task is created without its body or a run can never be completed.
FIELDS = {
    "kanban_create": {"assignee", "board", "body", "completion_contract", "goal_max_turns", "goal_mode", "idempotency_key", "initial_status",
                      "max_runtime_seconds", "model", "parents", "priority", "project", "provider", "skills", "tenant", "title", "triage",
                      "workspace_kind", "workspace_path"},
    "kanban_complete": {"artifacts", "board", "created_cards", "metadata", "result", "summary", "task_id"},
    "kanban_block": {"board", "kind", "reason", "task_id"},
    "kanban_comment": {"board", "body", "task_id"},
    "kanban_request_review": {"board", "metadata", "reviewer", "summary", "task_id"},
}


def field_reason(name, args):
    """A reason to refuse a board call whose field names are misspelled, or None."""
    known = FIELDS.get(name)
    if not known or not isinstance(args, dict):
        return None
    wrong = {key: next((f for f in sorted(known, key=len, reverse=True) if str(key).startswith(f + "_")), None)
             for key in args if key not in known}
    wrong = {key: field for key, field in wrong.items() if field}
    if not wrong:
        return None
    return ("%s has no field %s. Send the same call again with the exact field name%s: %s. Nothing may be added to a field name."
            % (name, ", ".join("`%s`" % k for k in wrong), "s" if len(wrong) > 1 else "",
               ", ".join("`%s` instead of `%s`" % (f, k) for k, f in wrong.items())))


def first_time(task_id, run_id, what):
    """True the first time `what` happens in this run. The second time the lead means it."""
    path = os.path.join(tempfile.gettempdir(), "lead-guard-%s-%s-%s" % (task_id, run_id or "0", what))
    if os.path.exists(path):
        return False
    try:
        open(path, "w").close()
    except OSError:
        return False
    return True


def check(name, args, config, profile, task_id, run_id):
    """A reason to refuse this tool call, or None."""
    team = team_text(config)
    hand_over = ("You are the lead on a board task: you plan and hand out the work, you do not do it. Put this step into "
                 "a task for the specialist who does it (kanban_create, same workspace_path). %s" % team)
    if name == "tool_call":
        for call in (args.get("calls") or []) if isinstance(args, dict) else []:
            if isinstance(call, dict):
                reason = check(str(call.get("name") or ""), call.get("arguments") or {}, config, profile, task_id, run_id)
                if reason:
                    return reason
        return None
    if WORK_TOOLS.fullmatch(name):
        return "%s is switched off for you on a board task. %s" % (name, hand_over)
    if name == "write_file":
        path = str((args or {}).get("path") or "")
        if not path.lower().endswith(NOTES):
            return "On a board task you write only your notes (%s files): plan, spec, decisions. %s" % (" or ".join(NOTES), hand_over)
        return None
    mcp = re.fullmatch(r"mcp__([\w-]+?)__(.+)", name)
    if mcp and mcp.group(1) not in OPEN_MCP and not LOOK.search(mcp.group(2)):
        return "%s produces or checks work; that is a specialist's step. %s" % (name, hand_over)
    if name == "kanban_block" and str((args or {}).get("kind") or "") == "capability" and first_time(task_id, run_id, "block"):
        return ("A missing tool or skill is not a reason for a lead to block: give the work to the specialist who has it. "
                "%s If truly nobody in any team can do it, call kanban_block again and name exactly what is missing." % team)
    if name == "kanban_create":
        return goal_reason(args, config)
    if name == "kanban_complete":
        count = created_tasks(profile, task_id, run_id)
        if count == 0 and not own_task(profile, task_id) and first_time(task_id, run_id, "complete"):
            return ("You have not given any work to your team in this run. Create the tasks for your specialists with "
                    "kanban_create first, then complete with the plan. %s If this job really needs no specialist (a question "
                    "you answered from what you read), call kanban_complete again." % team)
    return None


def main():
    try:
        task_id, run_id = board_task()
        payload = json.load(sys.stdin)
        home = os.environ.get("HERMES_HOME") or ""
        with open(os.path.join(home, "lead_guard.json"), encoding="utf-8") as handle:
            config = json.load(handle)
        reason = field_reason(str(payload.get("tool_name") or ""), payload.get("tool_input"))
        if reason:
            block(reason)
        if not task_id:   # in a chat the lead keeps all its tools; only the tasks it hands out are checked
            reason = goal_reason(payload.get("tool_input"), config) if payload.get("tool_name") == "kanban_create" else None
            if reason:
                block(reason)
            allow()
        profile = os.environ.get("HERMES_PROFILE") or config.get("lead") or payload.get("profile") or ""   # the board's created_by
        reason = check(str(payload.get("tool_name") or ""), payload.get("tool_input") or {}, config,
                       profile, task_id, run_id)
    except SystemExit:
        raise
    except Exception:
        allow()
    if reason:
        block(reason)
    allow()


if __name__ == "__main__":
    main()
