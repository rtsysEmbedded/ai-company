"""Panel test: one agent's live feed (messages and tool calls across its sessions, in order, incrementally).

Builds a small Hermes home with its own state.db and kanban.db in a temporary folder. Run: .venv/bin/python tests/test_panel_feed.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="panel-feed-test-")
os.environ["HERMES_HOME"] = HOME
sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import activity_api  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

COLUMNS = ("id TEXT, source TEXT, title TEXT, cwd TEXT, git_repo_root TEXT, model TEXT, started_at REAL, ended_at REAL, end_reason TEXT, "
           "message_count INT, tool_call_count INT, api_call_count INT, last_activity_at REAL, last_activity_description TEXT, "
           "estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, input_tokens INT, output_tokens INT, cache_read_tokens INT, "
           "cache_write_tokens INT, reasoning_tokens INT")
STATE = os.path.join(HOME, "profiles", "backend", "state.db")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def say(session, role, content="", calls=None, tool=None, thinking=None):
    con = sqlite3.connect(STATE)
    con.execute("INSERT INTO messages (session_id, role, content, tool_calls, tool_name, reasoning, timestamp) VALUES (?,?,?,?,?,?,?)",
                (session, role, content, json.dumps(calls) if calls else None, tool, thinking, time.time()))
    con.commit()
    con.close()


def main():
    now = time.time()
    os.makedirs(os.path.dirname(STATE))
    con = sqlite3.connect(STATE)
    con.execute("CREATE TABLE sessions (%s)" % COLUMNS)
    con.execute("CREATE TABLE session_turn_leases (conversation_id TEXT, holder TEXT, acquired_at REAL, expires_at REAL)")
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT, reasoning TEXT, timestamp REAL)")
    for sid, source, title, cwd, started, ended in (("s_task", "kanban", "kanban worker", "/tmp/shop", now - 600, now - 300),
                                                    ("s_chat", "cli", "Fix the login", "/tmp/shop", now - 100, None)):
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, source, title, cwd, None, "vendor/model", started, ended, None, 4, 1, 1, None, "", 0, None, None, 0, 0, 0, 0, 0))
    con.execute("INSERT INTO session_turn_leases VALUES ('s_chat', ?, ?, ?)", ("pid=%d:x" % os.getpid(), now, now + 60))
    con.commit()
    con.close()
    board = sqlite3.connect(os.path.join(HOME, "kanban.db"))
    board.execute("CREATE TABLE tasks (id TEXT, title TEXT)")
    board.execute("CREATE TABLE task_runs (id INTEGER, task_id TEXT, profile TEXT, started_at REAL, ended_at REAL)")
    board.execute("INSERT INTO tasks VALUES ('t_abc123', 'Build the cart API')")
    board.execute("INSERT INTO task_runs VALUES (1, 't_abc123', 'backend', ?, ?)", (now - 602, now - 300))
    board.commit()
    board.close()

    command = [{"function": {"name": "terminal", "arguments": json.dumps({"command": "pytest -q"})}}]
    say("s_task", "user", "Build the cart API")
    say("s_task", "assistant", "I run the tests first.", calls=command, thinking="tests before code")
    say("s_task", "tool", "3 passed", tool="terminal")
    say("s_chat", "user", "the login is broken")

    app = FastAPI()
    app.include_router(activity_api.build_router(lambda: None))
    c = TestClient(app)

    d = c.get("/api/agents/backend/feed").json()
    m = d["messages"]
    check("the first reply has the agent's messages of every session, oldest first",
          [x["session_id"] for x in m] == ["s_task", "s_task", "s_task", "s_chat"] and [x["role"] for x in m] == ["user", "assistant", "tool", "user"], str(m)[:300])
    check("a tool call carries its name and the command", m[1]["calls"] == [{"name": "terminal", "arguments": json.dumps({"command": "pytest -q"})}]
          and m[1]["thinking"] == "tests before code" and m[2]["tool"] == "terminal" and m[2]["text"] == "3 passed")
    check("a worker's session is named after its board task", d["sessions"]["s_task"]["task_id"] == "t_abc123"
          and d["sessions"]["s_task"]["title"] == "Build the cart API" and d["sessions"]["s_task"]["state"] == "ended")
    check("a direct session keeps its own title and is reported as working", d["sessions"]["s_chat"]["task_id"] is None
          and d["sessions"]["s_chat"]["title"] == "Fix the login" and d["sessions"]["s_chat"]["state"] == "working" and d["working"] is True)
    check("the reply says where to continue", d["last"] == m[-1]["id"])

    again = c.get("/api/agents/backend/feed", params={"after": d["last"]}).json()
    check("nothing new: an empty reply that keeps the position", again["messages"] == [] and again["last"] == d["last"] and again["sessions"] == {})
    say("s_chat", "assistant", "Found it: the cookie name changed.")
    new = c.get("/api/agents/backend/feed", params={"after": d["last"]}).json()
    check("only what was written since is sent", [x["text"] for x in new["messages"]] == ["Found it: the cookie name changed."]
          and new["last"] == d["last"] + 1 and list(new["sessions"]) == ["s_chat"])

    for n in range(activity_api.FEED_START + 20):
        say("s_chat", "assistant", "line %d" % n)
    first = c.get("/api/agents/backend/feed").json()
    check("a long history starts with the latest messages only", len(first["messages"]) == activity_api.FEED_START
          and first["messages"][-1]["text"] == "line %d" % (activity_api.FEED_START + 19))
    check("an unknown agent is refused", c.get("/api/agents/nobody/feed").status_code == 404 and c.get("/api/agents/..%2Fx/feed").status_code == 404)
    check("an agent without any session has an empty feed", c.get("/api/agents/default/feed").json()["messages"] == [])

    shutil.rmtree(HOME, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
