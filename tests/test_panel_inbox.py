"""Panel test: the user's inbox (inbox_api.py) on a small board of its own.

Run: .venv/bin/python tests/test_panel_inbox.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-inbox-test-")
HOME = os.path.join(TMP, "hermes")
os.environ.update(PANEL_DATA=os.path.join(TMP, "data"), HERMES_HOME=HOME)

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import inbox_api  # noqa: E402
import projects_api  # noqa: E402


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0
NOW = int(time.time())


def main():
    os.makedirs(os.path.join(HOME, "kanban", "logs"))
    os.makedirs(os.environ["PANEL_DATA"])
    with open(os.path.join(os.environ["PANEL_DATA"], "inbox.json"), "w") as handle:
        json.dump({"since": NOW - 5000, "done": {}}, handle)
    projects_api._load = lambda: [{"id": "car", "name": "Car", "folder": "/p/car"}]
    con = sqlite3.connect(os.path.join(HOME, "kanban.db"))
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, assignee TEXT, status TEXT, created_by TEXT, created_at INTEGER, "
                "completed_at INTEGER, result TEXT, workspace_path TEXT, block_kind TEXT, last_failure_error TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, summary TEXT, ended_at INTEGER)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")

    def task(tid, title, assignee, status, by, creator=None, done=None, folder="/p/car/hw", kind=None, body=""):
        con.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)", (tid, title, body, assignee, status, by, NOW - 900, done, folder, kind))
        con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'created', ?, ?)", (tid, json.dumps({"creator_task_id": creator}), NOW - 900))

    def blocked(tid, reason, at):
        con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'blocked', ?, ?)", (tid, json.dumps({"reason": reason}), at))

    # a finished job: the user's task, a specialist task and the lead's report
    task("t_a001", "[hw] car: draw it", "hw-lead", "done", "panel", done=NOW - 600)
    task("t_a002", "[hw] car: schematic", "hw-schematic", "done", "hw-lead", creator="t_a001", done=NOW - 500)
    task("t_a003", "[hw] car: check and report", "hw-lead", "done", "hw-lead", creator="t_a001", done=NOW - 400)
    con.execute("INSERT INTO task_runs (task_id, summary, ended_at) VALUES ('t_a003', 'All three criteria pass. Files: docs/x.md', ?)", (NOW - 400,))
    con.execute("INSERT INTO task_runs (task_id, summary, ended_at) VALUES ('t_a001', 'Planned and handed out.', ?)", (NOW - 600,))
    # a job still running, one from before the inbox existed, and one with a question
    task("t_b001", "running job", "hw-lead", "done", "panel", done=NOW - 300, folder="/q/other")
    task("t_b002", "still working", "hw-docs", "running", "hw-lead", creator="t_b001", folder="/q/other")
    task("t_c001", "old job", "shell", "done", "panel", done=NOW - 9000, folder="")
    task("t_d001", "job with a question", "sw-lead", "blocked", "worker", kind="needs_input", folder="/q/other")
    blocked("t_d001", "Which port?", NOW - 100)
    # a specialist's block that its lead is looking at, and the lead's own question about it
    task("t_e001", "write the limit", "hw-docs", "blocked", "panel", kind="needs_input")
    blocked("t_e001", "Which current?", NOW - 200)
    task("t_e002", "[blocked] write the limit", "hw-lead", "blocked", "ai-company", kind="needs_input", body="Your specialist hw-docs blocked task t_e001 \"write the limit\".")
    blocked("t_e002", "User must decide the current.", NOW - 150)
    task("t_f001", "stopped twice", "hw-lead", "triage", "panel", kind="capability")
    blocked("t_f001", "no tool", NOW - 50)
    con.commit()
    con.close()

    app = FastAPI()
    app.include_router(inbox_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    check("needs the token", anonymous.get("/api/inbox").status_code == 401)
    d = c.get("/api/inbox").json()
    by = {i["task_id"]: i for i in d["items"]}
    check("a finished job is one item with the lead's report", "t_a001" in by and by["t_a001"]["kind"] == "finished" and "All three criteria pass" in by["t_a001"]["text"]
          and by["t_a001"]["tasks"] == 3 and by["t_a001"]["actions"] == ["accept", "followup"], str(by.get("t_a001")))
    check("its own tasks are not listed separately, a running job is not finished", "t_a002" not in by and "t_a003" not in by and "t_b001" not in by)
    check("a question is listed with what can be done", by["t_d001"]["kind"] == "question" and by["t_d001"]["text"] == "Which port?" and "answer" in by["t_d001"]["actions"])
    check("a block the lead is dealing with is hidden; the lead's question to the user is shown, as part of that job",
          "t_e001" not in by and by["t_e002"]["kind"] == "question" and by["t_e002"]["job_id"] == "t_e001" and by["t_e002"]["job_title"] == "write the limit")
    check("a task that stopped is listed and can be archived", by["t_f001"]["kind"] == "stopped" and by["t_f001"]["actions"] == ["archive"] and "blocked twice" in by["t_f001"]["text"])
    check("items carry their project: the registered one, or the folder", by["t_a001"]["project"] == {"name": "Car", "key": "car", "folder": "/p/car"}
          and by["t_d001"]["project"]["name"] == "other")
    check("jobs finished before the inbox existed are in 'dealt with'", "t_c001" not in by and any(i["task_id"] == "t_c001" for i in d["handled"]))
    check("count per project", d["count"] == 4 and d["by_project"] == {"car": 3, "/q/other": 1} and c.get("/api/inbox/count").json()["count"] == 4, str(d["by_project"]))
    check("newest first", [i["task_id"] for i in d["items"]] == ["t_f001", "t_d001", "t_e002", "t_a001"], str([i["task_id"] for i in d["items"]]))

    item = by["t_a001"]["id"]
    check("accepting moves the item to 'dealt with'", c.post("/api/inbox/done", json={"id": item}).status_code == 200
          and "t_a001" not in {i["task_id"] for i in c.get("/api/inbox").json()["items"]} and any(i["id"] == item for i in c.get("/api/inbox").json()["handled"]))
    check("it can be put back", c.post("/api/inbox/done", json={"id": item, "done": False}).status_code == 200
          and "t_a001" in {i["task_id"] for i in c.get("/api/inbox").json()["items"]})
    old = next(i["id"] for i in d["handled"] if i["task_id"] == "t_c001")
    c.post("/api/inbox/done", json={"id": old, "done": False})
    check("an old job can be put back too", "t_c001" in {i["task_id"] for i in c.get("/api/inbox").json()["items"]})
    c.post("/api/inbox/done", json={"id": old, "done": True})
    check("and accepted again", "t_c001" not in {i["task_id"] for i in c.get("/api/inbox").json()["items"]})
    check("a bad item id is refused", c.post("/api/inbox/done", json={"id": "finished:../x:1"}).status_code == 400)

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
