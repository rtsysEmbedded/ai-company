"""Panel test: monthly budget per agent (budget_api.py) and project goals (projects_api.py + agent_api.py).

Uses a temporary Hermes home with a state.db for one fake agent; `hermes kanban create` is replaced.
Run: .venv/bin/python tests/test_panel_budget.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-budget-test-")
HOME = os.path.join(TMP, "hermes")
WORK = tempfile.mkdtemp(prefix=".panel-budget-test-", dir=os.path.expanduser("~"))
os.environ.update(PANEL_DATA=os.path.join(TMP, "data"), HERMES_HOME=HOME)

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import activity_api  # noqa: E402
import agent_api  # noqa: E402
import budget_api  # noqa: E402
import projects_api  # noqa: E402


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def main():
    now = time.time()
    os.makedirs(os.path.join(HOME, "profiles", "worker"))
    con = sqlite3.connect(os.path.join(HOME, "profiles", "worker", "state.db"))
    cols = ("id, source, title, cwd, git_repo_root, model, started_at, ended_at, end_reason, message_count, tool_call_count, api_call_count, "
            "last_activity_at, last_activity_description, estimated_cost_usd, actual_cost_usd, cost_status, " + ", ".join(activity_api.TOKEN_COLS))
    con.execute("CREATE TABLE sessions (%s)" % cols)
    month = budget_api.month_start(now)
    con.executemany("INSERT INTO sessions (id, source, model, started_at, estimated_cost_usd, actual_cost_usd) VALUES (?, 'kanban', 'm/x', ?, ?, ?)",
                    [("s_now", month + 5, 1.5, None), ("s_exact", month + 9, 0, 0.5),
                     ("s_last_month", month - 86400 * 3, 9.0, None)])   # last month's spend does not count
    con.execute("INSERT INTO sessions (id, source, model, started_at, estimated_cost_usd, cost_status, input_tokens) VALUES ('s_unpriced', 'kanban', 'router', ?, -1, 'unknown', 100)", (month + 20,))
    con.commit()
    con.close()
    activity_api.profiles = lambda: {"worker": {"dept": "software", "dept_title": "Software", "role": "worker", "model": "m/x", "enabled": True}}
    agent_api._agent = lambda p: activity_api.profiles()[p]
    agent_api.is_enabled = lambda p: True

    spent = budget_api.spent_this_month("worker", now)
    check("spend counts only this month (1.5 estimated + 0.5 exact)", abs(spent["cost"] - 2.0) < 1e-9, spent)
    check("unpriced session is reported", spent["unpriced"] == 1, spent)
    check("state: no budget", budget_api.state_of(5, None) == "none")
    check("state: ok / near / over", [budget_api.state_of(s, 10) for s in (7.9, 8, 10)] == ["ok", "near", "over"])

    app = FastAPI()
    app.include_router(budget_api.build_router(auth))
    app.include_router(projects_api.build_router(auth))
    app.include_router(agent_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    check("budgets need the token", anonymous.get("/api/budgets").status_code == 401)
    check("no budget by default", c.get("/api/budgets").json()["agents"][0]["state"] == "none")
    check("unknown agent is refused", c.put("/api/budgets/ghost", json={"monthly_usd": 5}).status_code == 404)
    check("no budget, no check", budget_api.over_budget() == {})

    r = c.put("/api/budgets/worker", json={"monthly_usd": 10}).json()["agents"][0]
    check("budget saved, 20% used", r["budget"] == 10 and r["state"] == "ok", r)
    r = c.put("/api/budgets/worker", json={"monthly_usd": 2.2}).json()["agents"][0]
    check("90% used is 'near'", r["state"] == "near", r)
    check("near budget still works", agent_api._require_ready("worker") is None)
    r = c.put("/api/budgets/worker", json={"monthly_usd": 2}).json()["agents"][0]
    check("100% is 'over'", r["state"] == "over", r)
    check("over_budget lists the agent", list(budget_api.over_budget()) == ["worker"])
    try:
        agent_api._require_ready("worker")
        check("over budget blocks chat and tasks", False)
    except HTTPException as exc:
        check("over budget blocks chat and tasks", exc.status_code == 409 and "monthly budget" in exc.detail, exc.detail)
    check("negative budget removes it", c.put("/api/budgets/worker", json={"monthly_usd": 0}).json()["agents"][0]["budget"] is None)
    check("removed budget no longer blocks", agent_api._require_ready("worker") is None)

    # ---- goals ----
    folder = os.path.join(WORK, "shop")
    r = c.post("/api/projects", json={"name": "Shop", "folder": folder, "goal": "Customers can pay by card", "create": True})
    check("project with goal", r.status_code == 200 and r.json()["projects"][0]["goal"] == "Customers can pay by card", r.text)
    check("goal_for finds it from a subfolder", projects_api.goal_for(os.path.join(folder, "src")) == {"name": "Shop", "goal": "Customers can pay by card"}
          if os.path.isdir(folder) else False)
    check("goal_for outside every project is empty", projects_api.goal_for(WORK) == {})
    calls = []
    agent_api.subprocess = types.SimpleNamespace(run=lambda cmd, **kw: (calls.append(cmd), types.SimpleNamespace(returncode=0, stdout="created t_abc123", stderr=""))[1], DEVNULL=None)
    agent_api._require_ready = lambda p: None
    r = c.post("/api/agents/worker/task", json={"title": "Pay page", "body": "Build it", "folder": folder})
    body = calls[0][calls[0].index("--body") + 1] if calls else ""
    check("task text starts with the project goal", r.status_code == 200 and body.startswith("GOAL (project Shop): Customers can pay by card\n\nBuild it"), body[:120])
    c.put("/api/projects/shop", json={"name": "Shop", "folder": folder, "goal": ""})
    check("goal falls back to the note, empty if neither", projects_api.goal_for(folder).get("goal") == "")
    calls.clear()
    c.post("/api/agents/worker/task", json={"title": "Plain", "body": "No goal", "folder": folder})
    check("no goal, no GOAL line", calls and "GOAL" not in calls[0][calls[0].index("--body") + 1])

    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    print("\n%s" % ("all passed" if not check.failed else "%d FAILED" % check.failed))
    sys.exit(1 if check.failed else 0)


main()
