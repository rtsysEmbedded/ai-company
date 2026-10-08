"""Test: a specialist's blocked task goes to its lead and the lead's answer comes back (scripts/escalate.py to_lead).

The board is a small fake: `hermes kanban` calls are recorded and answered from memory. Run: .venv/bin/python tests/test_to_lead.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["PANEL_DATA"] = tempfile.mkdtemp(prefix="to-lead-test-")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import escalate  # noqa: E402


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0
BOARD, CALLS, CLOCK = {}, [], [1000]


def task(tid, **kw):
    BOARD[tid] = {"task": {"id": tid, "title": tid, "status": "ready", "assignee": None, "created_by": "hw-lead", "created_at": CLOCK[0],
                           "body": "", "workspace_path": "/p/car", **kw},
                  "comments": [], "events": [], "runs": [], "children": [], "latest_summary": ""}
    return BOARD[tid]


def kanban(*args, check=True):
    CALLS.append(list(args))
    CLOCK[0] += 1
    cmd = args[0]
    if cmd == "list":
        status = args[args.index("--status") + 1] if "--status" in args else None
        return json.dumps([d["task"] for d in BOARD.values() if not status or d["task"]["status"] == status])
    if cmd == "show":
        return json.dumps(BOARD.get(args[1]) or {})
    if cmd == "create":
        tid = "t_%04x" % (0xa000 + len(BOARD))
        task(tid, title=args[1], assignee=args[args.index("--assignee") + 1], created_by="ai-company", body=args[args.index("--body") + 1])
        return "created %s" % tid
    if cmd == "comment":
        author = args[args.index("--author") + 1] if "--author" in args else "ai-company"
        BOARD[args[1]]["comments"].append({"author": author, "body": args[-1], "created_at": CLOCK[0]})
    elif cmd == "unblock":
        BOARD[args[1]]["task"]["status"] = "ready"
    elif cmd == "archive":
        BOARD[args[1]]["task"]["status"] = "archived"
    return ""


def block(tid, reason):
    CLOCK[0] += 1
    BOARD[tid]["task"]["status"] = "blocked"
    BOARD[tid]["events"].append({"kind": "blocked", "created_at": CLOCK[0], "payload": {"reason": reason}})


def finish(tid, summary):
    CLOCK[0] += 1
    d = BOARD[tid]
    d["runs"].append({"started_at": CLOCK[0] - 1, "ended_at": CLOCK[0] + 20, "summary": summary})
    d["task"]["status"], d["latest_summary"] = "done", summary
    CLOCK[0] += 30


def main():
    escalate.kanban = kanban
    escalate.leads = lambda team: {"hw-schematic": "hw-lead", "hw-docs": "hw-lead", "backend": "sw-lead"}
    run = lambda: escalate.to_lead({}, False, since=500)

    task("t_old", assignee="hw-docs")
    BOARD["t_old"]["task"]["status"] = "blocked"
    BOARD["t_old"]["events"].append({"kind": "blocked", "created_at": 100, "payload": {"reason": "old"}})
    task("t_lead", assignee="hw-lead"); block("t_lead", "which motor?")
    task("t_held", assignee="backend"); block("t_held", "x")
    BOARD["t_held"]["comments"].append({"body": escalate.HOLD + " out of credits", "created_at": CLOCK[0] + 1})
    task("t_sch", assignee="hw-schematic", title="draw the schematic", body="Draw it."); block("t_sch", "Which supply voltage?")
    BOARD["t_sch"]["children"] = ["t_check"]
    task("t_check", assignee="hw-lead", title="car: check and report")
    run()
    asks = [d for d in BOARD.values() if d["task"]["title"].startswith("[blocked]")]
    check("a blocked specialist task makes one task for its lead", len(asks) == 1 and asks[0]["task"]["assignee"] == "hw-lead"
          and "Which supply voltage?" in asks[0]["task"]["body"] and "Draw it." in asks[0]["task"]["body"], str([a["task"] for a in asks]))
    check("old blocks, the lead's own question and provider holds are left alone",
          not any(escalate.TO_LEAD in c["body"] for t in ("t_old", "t_lead", "t_held") for c in BOARD[t]["comments"]))
    ask = asks[0]["task"]["id"]
    check("the blocked task says who decides", any(escalate.TO_LEAD in c["body"] and ask in c["body"] for c in BOARD["t_sch"]["comments"]))
    run()
    check("it is not sent twice", len([d for d in BOARD.values() if d["task"]["title"].startswith("[blocked]")]) == 1)

    finish(ask, "Use 12 V.")
    run()
    said = [c for c in BOARD["t_sch"]["comments"] if c["author"] == "hw-lead"]
    check("the lead's answer is written onto the task and it starts again",
          BOARD["t_sch"]["task"]["status"] == "ready" and said and "Use 12 V." in said[0]["body"], str(BOARD["t_sch"]["comments"]))

    # A goal-loop task is judged on its text alone: the answer goes into the text of a continuation.
    task("t_goal", assignee="hw-docs", title="write the limit", body="Write the limit.", created_by="panel")
    BOARD["t_goal"]["events"].append({"kind": "created", "created_at": CLOCK[0], "payload": {"goal_mode": True}})
    BOARD["t_goal"]["children"], BOARD["t_goal"]["parents"] = ["t_check"], ["t_before"]
    block("t_goal", "Which current?")
    run()
    finish([d["task"]["id"] for d in BOARD.values() if d["task"]["title"] == "[blocked] write the limit"][-1], "2 A.")
    before = len(CALLS)
    run()
    made = [c for c in CALLS[before:] if c[0] == "create"]
    check("an answered goal-loop task continues as a new task with the answer in its text",
          len(made) == 1 and "ANSWER from your lead hw-lead" in made[0][made[0].index("--body") + 1] and "2 A." in made[0][made[0].index("--body") + 1]
          and "Write the limit." in made[0][made[0].index("--body") + 1] and "--goal" in made[0] and made[0][made[0].index("--parent") + 1] == "t_before", str(made))
    new_id = [d["task"]["id"] for d in BOARD.values() if d["task"]["title"] == "write the limit"][-1]
    check("the old task is archived and what waited for it waits for the new one",
          BOARD["t_goal"]["task"]["status"] == "archived" and ["link", new_id, "t_check"] in CALLS[before:] and ["unlink", "t_goal", "t_check"] in CALLS[before:])

    block("t_sch", "No tool for wires.")
    run()
    ask2 = [d["task"]["id"] for d in BOARD.values() if d["task"]["title"].startswith("[blocked]")][-1]
    check("a new block asks the lead again", ask2 != ask)
    BOARD[ask2]["runs"].append({"started_at": CLOCK[0], "ended_at": CLOCK[0] + 50})
    task("t_new", assignee="hw-docs", created_by="hw-lead", title="draw the wires by hand")
    BOARD[ask2]["task"]["status"], BOARD[ask2]["latest_summary"] = "done", "gave it to hw-docs"
    before = len(CALLS)
    run()
    check("work the lead gave to someone else closes the blocked task", BOARD["t_sch"]["task"]["status"] == "archived")
    check("the tasks that waited for it wait for the new one",
          ["link", "t_new", "t_check"] in CALLS[before:] and ["unlink", "t_sch", "t_check"] in CALLS[before:], str(CALLS[before:]))

    task("t_loop", assignee="backend"); block("t_loop", "a")
    for answer in ("one", "two"):
        run()
        finish([d["task"]["id"] for d in BOARD.values() if d["task"]["title"] == "[blocked] t_loop"][-1], answer)
        run()
        block("t_loop", "again")
    run()
    check("after two answers a task that blocks again waits for the user",
          len([d for d in BOARD.values() if d["task"]["title"] == "[blocked] t_loop"]) == 2 and BOARD["t_loop"]["task"]["status"] == "blocked")

    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
