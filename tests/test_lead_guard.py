"""Test: the lead guard hook (departments/engine/lead_guard.py) on a small board of its own.

Run: .venv/bin/python tests/test_lead_guard.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "departments", "engine", "lead_guard.py")
TMP = tempfile.mkdtemp(prefix="lead-guard-test-")
HOME, DB = os.path.join(TMP, "profile"), os.path.join(TMP, "kanban.db")
os.makedirs(HOME)


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def guard(tool, args=None, task="t_lead01", run="7"):
    """(blocked, reason) for one tool call of the lead."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env.update(HERMES_HOME=HOME, HERMES_KANBAN_DB=DB, HERMES_PROFILE="hw-lead", TMPDIR=TMP)
    if task:
        env.update(HERMES_KANBAN_TASK=task, HERMES_KANBAN_RUN_ID=run)
    r = subprocess.run([sys.executable, GUARD], input=json.dumps({"tool_name": tool, "tool_input": args or {}, "profile": "hw-lead"}),
                       capture_output=True, text=True, env=env)
    out = json.loads(r.stdout or "{}")
    return r.returncode == 2 and out.get("decision") == "block", out.get("reason", "")


def main():
    now = int(time.time())
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE tasks (id TEXT, created_by TEXT, created_at INTEGER, started_at INTEGER, title TEXT)")
    con.execute("CREATE TABLE task_runs (id INTEGER, task_id TEXT, started_at INTEGER, worker_pid INTEGER, ended_at INTEGER)")
    con.execute("INSERT INTO tasks VALUES ('t_lead01', 'worker', ?, ?, 'job')", (now - 100, now - 60))
    con.execute("INSERT INTO tasks VALUES ('t_old', 'hw-lead', ?, NULL, 'old')", (now - 5000,))      # from an earlier run
    con.execute("INSERT INTO task_runs VALUES (7, 't_lead01', ?, NULL, NULL)", (now - 60,))
    con.commit()
    with open(os.path.join(HOME, "lead_guard.json"), "w") as handle:
        json.dump({"lead": "hw-lead", "team": [{"name": "hw-schematic", "title": "Schematic engineer", "tools": "kicad"},
                                               {"name": "hw-research", "title": "Researcher", "tools": "no special tools"}]}, handle)

    blocked, reason = guard("terminal", {"command": "kicad-cli sch export netlist x"})
    check("a lead cannot run commands on a board task", blocked and "switched off" in reason, reason)
    check("the refusal names the team and their tools", "hw-schematic (kicad)" in reason and "kanban_create" in reason)
    check("in a chat with the user the lead keeps its tools", guard("terminal", {"command": "ls"}, task=None) == (False, ""))
    check("it cannot run code or change source files", guard("execute_code", {"code": "1"})[0] and guard("patch", {"path": "a.py"})[0]
          and guard("write_file", {"path": "/p/board.kicad_sch", "content": "x"})[0])
    check("it can write its plan and read anything", not guard("write_file", {"path": "/p/decisions.md", "content": "x"})[0]
          and not guard("read_file", {"path": "/p/board.kicad_sch"})[0] and not guard("search_files", {"pattern": "*"})[0])
    check("it can look with a department tool but not produce with it", not guard("mcp__kicad__kicad_info")[0]
          and not guard("mcp__kicad__inspect_board", {"project": "x"})[0] and guard("mcp__kicad__render", {"project": "x"})[0]
          and guard("mcp__kicad__create_schematic")[0] and guard("mcp__freecad__run_script")[0])
    check("the knowledge base stays open", not guard("mcp__kb__kb_add", {"text": "x"})[0])
    check("a tool run through tool_call is checked the same way",
          guard("tool_call", {"calls": [{"name": "mcp__kicad__kicad_info", "arguments": {}}, {"name": "mcp__kicad__render", "arguments": {}}]})[0]
          and not guard("tool_call", {"calls": [{"name": "mcp__kicad__kicad_info", "arguments": {}}]})[0])
    blocked, reason = guard("kanban_create", {"title": "x", "assignee": "hw-schematic"})
    check("a task for a specialist needs the goal loop", blocked and "goal_mode: true" in reason, reason)
    blocked, reason = guard("kanban_create", {"title": "x", "assignee": "hw-schematic", "goal_mode": True, "body": "Draw it. When done, call kanban_request_review with reviewer=hw-review."})
    check("a body must not ask for the review", blocked and "review instruction" in reason, reason)
    check("the same in a chat with the user", guard("kanban_create", {"title": "x", "assignee": "hw-schematic"}, task=None)[0])
    check("a task for another lead or for itself does not", not guard("kanban_create", {"title": "x", "assignee": "sw-lead"})[0]
          and not guard("kanban_create", {"title": "check", "assignee": "hw-lead"})[0])
    blocked, reason = guard("kanban_create", {"title": "[hw] car: check and report", "assignee": "hw-lead"})
    check("a check-and-report task must wait for the tasks it checks", blocked and "parents" in reason
          and not guard("kanban_create", {"title": "[hw] car: check and report", "assignee": "hw-lead", "parents": ["t_1"]})[0], reason)
    check("handing out and asking are always allowed", not guard("kanban_create", {"title": "x", "assignee": "hw-schematic", "goal_mode": True, "goal_max_turns": 6})[0]
          and not guard("kanban_comment", {"task_id": "t_x", "body": "?"})[0] and not guard("kanban_block", {"reason": "which motor?", "kind": "needs_input"})[0])

    blocked, reason = guard("kanban_complete", {"created_cards": ["t_1"], "summary_ui": "planned"})
    check("a misspelled field name is refused with the right name", blocked and "`summary` instead of `summary_ui`" in reason, reason)
    blocked, reason = guard("kanban_create", {"title": "x", "assignee": "sw-lead", "body_html": "..", "parents_string": ["t_1"]}, task=None)
    check("also when creating a task, in a chat too", blocked and "`body` instead of `body_html`" in reason and "`parents` instead of `parents_string`" in reason, reason)
    check("real field names pass", not guard("kanban_create", {"title": "x", "assignee": "sw-lead", "workspace_path": "/p", "max_runtime_seconds": 60})[0])
    blocked, reason = guard("kanban_block", {"reason": "no KiCad tool", "kind": "capability"})
    check("a missing tool is refused as a reason to block, once", blocked and "give the work to the specialist" in reason
          and not guard("kanban_block", {"reason": "nobody can", "kind": "capability"})[0], reason)
    blocked, reason = guard("kanban_complete", {"summary": "done"})
    check("the task cannot be completed before the team has work", blocked and "kanban_create first" in reason, reason)
    check("a second complete is accepted (a job that needs no specialist)", not guard("kanban_complete", {"summary": "answered"})[0])
    con.execute("INSERT INTO tasks VALUES ('t_child', 'hw-lead', ?, NULL, 'child')", (now,))
    con.execute("INSERT INTO tasks VALUES ('t_check', 'hw-lead', ?, ?, 'check and report')", (now - 4000, now - 10))   # the lead's own closing task
    con.execute("INSERT INTO task_runs VALUES (9, 't_check', ?, NULL, NULL)", (now + 100,))                     # nothing created in this run
    con.commit()
    con.execute("INSERT INTO tasks VALUES ('t_req', 'sw-lead', ?, ?, '[request] car: which UART pins?')", (now - 50, now - 10))
    con.commit()
    check("a request from another lead can simply be answered", not guard("kanban_complete", {"summary": "GPIO 16/17"}, task="t_req", run="10")[0])
    check("the lead's own check-and-report task completes without handing out work", not guard("kanban_complete", {"summary": "report"}, task="t_check", run="9")[0])
    con.close()
    check("after handing out work the task completes at once", not guard("kanban_complete", {"summary": "plan"}, run="8")[0])
    # A planning run that is started again must not put the same tasks on the board twice.
    con = sqlite3.connect(DB)
    con.execute("ALTER TABLE tasks ADD COLUMN status TEXT")
    con.execute("ALTER TABLE tasks ADD COLUMN assignee TEXT")
    con.execute("CREATE TABLE task_events (task_id TEXT, kind TEXT, payload TEXT)")
    con.execute("INSERT INTO tasks VALUES ('t_job', 'panel', ?, ?, 'job', 'running', 'hw-lead')", (now - 900, now - 800))
    con.execute("INSERT INTO tasks VALUES ('t_first', 'hw-lead', ?, NULL, '[hw] car: Draw the  schematic', 'done', 'hw-schematic')", (now - 700,))
    con.execute("INSERT INTO task_events VALUES ('t_first', 'created', ?)", (json.dumps({"assignee": "hw-schematic", "creator_task_id": "t_job"}),))
    con.execute("INSERT INTO task_runs VALUES (20, 't_job', ?, NULL, NULL)", (now - 30,))   # the second run of the job
    con.commit(); con.close()
    spec = {"assignee": "hw-schematic", "goal_mode": True, "goal_max_turns": 6}
    blocked, reason = guard("kanban_create", {"title": "[hw] car: draw the schematic", **spec}, task="t_job", run="20")
    check("a task that already exists in this job is not created again", blocked and "already exists: t_first" in reason, reason)
    blocked, reason = guard("kanban_create", {"title": "[hw] car: produce the schematic", **spec}, task="t_job", run="20")
    check("a repeated planning run is shown what is already on the board", blocked and "already planned" in reason and "t_first" in reason, reason)
    check("a task that is really missing can still be added", not guard("kanban_create", {"title": "[hw] car: write the docs", "assignee": "hw-research", "goal_mode": True}, task="t_job", run="20")[0])
    check("the repeated run can complete with the earlier tasks", not guard("kanban_complete", {"summary": "plan"}, task="t_job", run="20")[0])
    check("a first planning run is not disturbed", not guard("kanban_create", {"title": "new", **spec})[0])

    # Every other agent: the small guard for its board calls.
    with open(os.path.join(HOME, "lead_guard.json")) as handle:
        lead_config = handle.read()
    with open(os.path.join(HOME, "lead_guard.json"), "w") as handle:
        json.dump({"specialist": "hw-schematic", "reviewer": "hw-review"}, handle)
    blocked, reason = guard("kanban_request_review", {"summary": "done"})
    check("a review request must name the reviewer", blocked and 'reviewer: "hw-review"' in reason, reason)
    check("with the reviewer it passes", not guard("kanban_request_review", {"summary": "done", "reviewer": "hw-review"})[0])
    check("a specialist's misspelled field is refused too", guard("kanban_complete", {"summary_x": "done"})[0])
    check("a specialist keeps its work tools", not guard("terminal", {"command": "ls"})[0] and not guard("kanban_complete", {"summary": "done"})[0])
    with open(os.path.join(HOME, "lead_guard.json"), "w") as handle:
        handle.write(lead_config)

    # Hermes strips HERMES_KANBAN_TASK from a hook's environment: the task is found through the worker's pid.
    con = sqlite3.connect(DB)
    con.execute("INSERT INTO task_runs VALUES (11, 't_lead01', ?, ?, NULL)", (now, os.getpid()))
    con.commit(); con.close()
    blocked, reason = guard("terminal", {"command": "ls"}, task=None)
    check("without the variable the board task is found through the worker's pid", blocked and "switched off" in reason, reason)
    con = sqlite3.connect(DB)
    con.execute("UPDATE task_runs SET ended_at = ? WHERE id = 11", (now,))
    con.commit(); con.close()
    check("once that run has ended it is a chat again", not guard("terminal", {"command": "ls"}, task=None)[0])
    os.remove(os.path.join(HOME, "lead_guard.json"))
    check("without its team file the guard allows everything", not guard("terminal", {"command": "ls"})[0])

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
