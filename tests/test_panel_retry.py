"""Panel test: retry or answer a blocked task (agent_api.py) and read its full conversation (activity_api.py).

Uses a temporary Hermes home with its own kanban.db; the `hermes kanban unblock` call is replaced.
Run: .venv/bin/python tests/test_panel_retry.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-retry-test-")
HOME = os.path.join(TMP, "hermes")
os.environ.update(PANEL_DATA=os.path.join(TMP, "data"), HERMES_HOME=HOME)

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import activity_api  # noqa: E402
import agent_api  # noqa: E402


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def main():
    os.makedirs(os.path.join(HOME, "kanban", "logs"))
    con = sqlite3.connect(os.path.join(HOME, "kanban.db"))
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, assignee TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?, ?, ?)", [("t_aaaa1111", "blocked", "nobody-known"),
                                                          ("t_bbbb2222", "done", "nobody-known"),
                                                          ("t_cccc3333", "blocked", "nobody-known")])
    con.execute("CREATE TABLE task_runs (id INTEGER, task_id TEXT, profile TEXT, status TEXT, outcome TEXT, started_at REAL, "
                "ended_at REAL, last_heartbeat_at REAL, summary TEXT, error TEXT)")
    con.execute("INSERT INTO task_runs VALUES (1, 't_aaaa1111', 'worker', 'done', 'blocked', 1000, 1060, 1050, NULL, NULL)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER)")
    con.execute("INSERT INTO task_comments VALUES (1, 't_aaaa1111', 'user', 'use 12 V', 1100)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO task_events VALUES (?, 't_aaaa1111', 1, ?, ?, ?)",
                    [(1, "heartbeat", "{}", 1010), (2, "blocked", '{"reason": "Which supply voltage?"}', 1060)])
    con.execute("ALTER TABLE tasks ADD COLUMN title TEXT")
    con.execute("ALTER TABLE tasks ADD COLUMN body TEXT")
    con.commit()
    con.close()
    os.makedirs(os.path.join(HOME, "profiles", "worker"))
    con = sqlite3.connect(os.path.join(HOME, "profiles", "worker", "state.db"))
    cols = ("id, source, title, cwd, git_repo_root, model, started_at, ended_at, end_reason, message_count, tool_call_count, api_call_count, "
            "last_activity_at, last_activity_description, estimated_cost_usd, actual_cost_usd, cost_status, " + ", ".join(activity_api.TOKEN_COLS))
    con.execute("CREATE TABLE sessions (%s)" % cols)
    con.execute("INSERT INTO sessions (id, source, model, started_at) VALUES ('s_one', 'kanban', 'm/x', 1002)")
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT, reasoning TEXT, timestamp REAL)")
    con.executemany("INSERT INTO messages (session_id, role, content, tool_calls, tool_name, reasoning, timestamp) VALUES ('s_one', ?, ?, ?, ?, ?, ?)", [
        ("user", "work kanban task t_aaaa1111", None, None, None, 1003),
        ("assistant", "", '[{"function": {"name": "kanban_block", "arguments": "{\\"reason\\": \\"Which supply voltage?\\"}"}}]', None, "I must ask", 1004),
        ("tool", "x" * 9000, None, "kanban_block", None, 1005)])
    con.commit()
    con.close()
    with open(os.path.join(HOME, "kanban", "logs", "t_cccc3333.log"), "w") as handle:
        handle.write("Session: s1\nError: HTTP 401: Missing Authentication header\nSession: s2\n")

    calls, result = [], types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    agent_api.subprocess = types.SimpleNamespace(run=lambda cmd, **kw: (calls.append(cmd), result)[1], DEVNULL=None)

    app = FastAPI()
    app.include_router(agent_api.build_router(auth))
    app.include_router(activity_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    try:
        check("needs the token", anonymous.post("/api/tasks/t_aaaa1111/retry").status_code == 401)
        check("rejects a bad id", c.post("/api/tasks/t_aaaa;rm/retry").status_code in (400, 404))
        check("unknown task", c.post("/api/tasks/t_dddd4444/retry").status_code == 404)
        r = c.post("/api/tasks/t_bbbb2222/retry")
        check("only blocked tasks", r.status_code == 400 and "done" in r.json()["detail"], r.text)
        check("nothing ran yet", calls == [])

        r = c.post("/api/tasks/t_aaaa1111/retry")
        check("retries a blocked task", r.status_code == 200 and r.json()["task_id"] == "t_aaaa1111" and r.json()["warning"] is None, r.text)
        check("runs hermes kanban unblock", calls and calls[-1][:4] == ["hermes", "kanban", "unblock", "t_aaaa1111"], str(calls))

        r = c.post("/api/tasks/t_bbbb2222/archive")
        check("archives a finished or stuck task", r.status_code == 200 and calls[-1][:4] == ["hermes", "kanban", "archive", "t_bbbb2222"], r.text + str(calls[-1:]))
        check("archive: unknown task and bad id are refused", c.post("/api/tasks/t_dddd4444/archive").status_code == 404
              and c.post("/api/tasks/t_aaaa;rm/archive").status_code in (400, 404))

        r = c.post("/api/tasks/t_cccc3333/retry")
        check("warns when the provider refused the last run", r.status_code == 200 and "401" in (r.json()["warning"] or ""), r.text)

        check("answer needs text", c.post("/api/tasks/t_aaaa1111/answer", json={"text": "  "}).status_code == 400)
        check("answer only for blocked tasks", c.post("/api/tasks/t_bbbb2222/answer", json={"text": "hi"}).status_code == 400)
        before = len(calls)
        r = c.post("/api/tasks/t_aaaa1111/answer", json={"text": "--use 12 V"})
        check("answers a blocked task", r.status_code == 200, r.text)
        check("answer is a comment by the user, then unblock",
              calls[before:] == [["hermes", "kanban", "comment", "t_aaaa1111", "--author", "user", "--", "--use 12 V"],
                                 ["hermes", "kanban", "unblock", "t_aaaa1111"]], str(calls[before:]))

        check("conversation needs the token", anonymous.get("/api/tasks/t_aaaa1111/conversation").status_code == 401)
        check("conversation of an unknown task", c.get("/api/tasks/t_dddd4444/conversation").status_code == 404)
        d = c.get("/api/tasks/t_aaaa1111/conversation").json()
        kinds = [i["kind"] for i in d["items"]]
        check("conversation is in time order, without heartbeats", kinds == ["run", "event", "comment"], str(kinds))
        run = d["items"][0]
        check("run has its session's messages", run["session_id"] == "s_one" and [m["role"] for m in run["messages"]] == ["user", "assistant", "tool"], str(run)[:300])
        check("tool calls and thinking are readable", run["messages"][1]["calls"][0]["name"] == "kanban_block" and run["messages"][1]["thinking"] == "I must ask")
        check("long tool results are cut", len(run["messages"][2]["text"]) < 4200 and "more characters" in run["messages"][2]["text"])
        check("the question and the answer are there", d["items"][1]["text"] == "Which supply voltage?" and d["items"][2]["text"] == "use 12 V")

        d = c.get("/api/sessions/worker/s_one/conversation")
        check("a direct session has the same view", d.status_code == 200 and d.json()["session"]["state"] == "ended"
              and len(d.json()["items"][0]["messages"]) == 3, d.text[:300])
        check("unknown session or profile", c.get("/api/sessions/worker/s_nothere/conversation").status_code == 404
              and c.get("/api/sessions/nobody/s_one_x/conversation").status_code == 404)

        check("a lead is told to delegate", agent_api._is_lead({"dept": "software", "role": "manager"}) and not agent_api._is_lead({"dept": "software", "role": "backend"})
              and "kanban_create" in agent_api.LEAD_TASK)

        result.returncode, result.stderr = 1, "board is locked"
        r = c.post("/api/tasks/t_aaaa1111/retry")
        check("reports a failed unblock", r.status_code == 500 and "locked" in r.json()["detail"], r.text)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("all passed" if not check.failed else "%d failed" % check.failed))
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
