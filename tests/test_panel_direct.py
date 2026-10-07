"""Panel test: direct sessions (hermes run in a project folder, panel chat) in the activity data.

Builds a small Hermes home with its own state.db in a temporary folder. Run: .venv/bin/python tests/test_panel_direct.py
Set KEEP_HOME=<folder> to build the same data there and keep it (used to look at the pages by hand).
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP = os.environ.get("KEEP_HOME")
HOME = KEEP or tempfile.mkdtemp(prefix="panel-direct-test-")
os.environ["HERMES_HOME"] = HOME
sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import activity_api  # noqa: E402

COLUMNS = ("id TEXT, source TEXT, title TEXT, cwd TEXT, git_repo_root TEXT, model TEXT, started_at REAL, ended_at REAL, end_reason TEXT, "
           "message_count INT, tool_call_count INT, api_call_count INT, last_activity_at REAL, last_activity_description TEXT, "
           "estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, input_tokens INT, output_tokens INT, cache_read_tokens INT, "
           "cache_write_tokens INT, reasoning_tokens INT")


def build(now):
    os.makedirs(os.path.join(HOME, "runtime"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "profiles", "backend"), exist_ok=True)
    path = os.path.join(HOME, "state.db")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (%s)" % COLUMNS)
    con.execute("CREATE TABLE session_turn_leases (conversation_id TEXT, holder TEXT, acquired_at REAL, expires_at REAL)")

    def session(sid, source, title, cwd, started, ended=None, activity=None, doing="", tokens=1000):
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, source, title, cwd, None, "vendor/model", started, ended, "cli_close" if ended else None, 12, 5, 3,
                     activity, doing, 0.01, None, None, tokens, 100, 0, 0, 0))
    session("busy", "cli", "Add Persian language support", "/tmp/projects/shop", now - 300, activity=now - 4, doing="running terminal command")
    session("idle", "cli", "Fix the login page", "/tmp/projects/blog", now - 900, activity=now - 600)
    session("done", "cli", "Write the README", "/tmp/projects/shop", now - 5000, ended=now - 4000, activity=now - 4000)
    session("board", "kanban", "a board task", "/tmp/projects/shop", now - 200, activity=now - 3)
    session("old", "cli", "yesterday", "/tmp/projects/shop", now - 3 * 86400, ended=now - 3 * 86400 + 60)
    con.execute("INSERT INTO session_turn_leases VALUES ('busy', ?, ?, ?)", ("pid=%d:turn=busy" % os.getpid(), now - 10, now + 300))
    con.execute("INSERT INTO session_turn_leases VALUES ('done', 'pid=999999:turn=done', ?, ?)", (now - 10, now + 300))
    con.commit()
    con.close()
    entries = [{"session_id": sid, "pid": os.getpid() if not KEEP else os.getppid(), "surface": "cli", "started_at": now - 60, "metadata": {}}
               for sid in ("busy", "idle", "fresh")]
    entries.append({"session_id": "gone", "pid": 999999, "surface": "cli", "started_at": now - 60})
    with open(os.path.join(HOME, "runtime", "active_sessions.json"), "w") as handle:
        json.dump({"entries": entries}, handle)


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def main():
    now = time.time()
    build(now)
    if KEEP:
        print("built in", HOME)
        return 0
    d = activity_api.snapshot("today")
    by = {x["id"]: x for x in d["direct"]}
    check("a chat in the middle of a turn is 'working'", by.get("busy", {}).get("state") == "working", str(by.get("busy")))
    check("with its folder, title and agent", by["busy"]["cwd"] == "/tmp/projects/shop" and by["busy"]["title"] == "Add Persian language support"
          and by["busy"]["profile"] == "default")
    check("an open chat that waits for the user is 'open'", by.get("idle", {}).get("state") == "open")
    check("a closed chat is 'ended'", by.get("done", {}).get("state") == "ended")
    check("a lease of a process that is gone does not make a chat working", by["done"]["state"] == "ended")
    check("a chat just opened, with nothing asked yet, is listed", by.get("fresh", {}).get("state") == "open" and by["fresh"]["messages"] == 0)
    check("an entry of a process that is gone is not listed", "gone" not in by)
    check("task-board sessions are not direct work", "board" not in by)
    check("sessions before the period are left out", "old" not in by)
    check("working ones come first", d["direct"][0]["id"] == "busy")
    check("the working count includes direct work", d["working"] == 1 and d["running"] == [])
    check("reading did not create or remove database side files",
          sorted(f for f in os.listdir(HOME) if f.startswith("state.db")) == ["state.db"])
    shutil.rmtree(HOME, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
