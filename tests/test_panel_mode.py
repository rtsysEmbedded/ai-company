"""Test: manager / general mode of the main agent (team.yaml `mode`, scripts/apply_team.py, the panel's /api/mode).

Runs apply_team.py from a temporary copy against a temporary Hermes home. Run: .venv/bin/python tests/test_panel_mode.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-mode-test-")
SW, HOME = os.path.join(TMP, "company"), os.path.join(TMP, "hermes")
os.makedirs(os.path.join(SW, "scripts"))
os.makedirs(HOME)
shutil.copy(os.path.join(ROOT, "team.yaml"), SW)
shutil.copy(os.path.join(ROOT, "scripts", "apply_team.py"), os.path.join(SW, "scripts"))
shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(SW, "policies"), ignore=shutil.ignore_patterns(".DS_Store"))
os.environ.update(PANEL_SOFTWARE_ROOT=SW, HERMES_HOME=HOME, DEPT_DATA=os.path.join(TMP, "data"), DEPT_MODELS_URL="http://127.0.0.1:9/none")

import yaml  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import software_api  # noqa: E402

SOUL = os.path.join(HOME, "SOUL.md")


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def team():
    with open(os.path.join(SW, "team.yaml"), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def apply_soul(mode):
    r = subprocess.run([sys.executable, os.path.join(SW, "scripts", "apply_team.py"), "--only", "manager", "--soul-only"],
                       capture_output=True, text=True, env=dict(os.environ, HERMES_HOME=HOME))
    return r.returncode == 0 and "instructions updated" in r.stdout, (r.stdout + r.stderr)[-300:]


def main():
    with open(os.path.join(SW, ".env"), "w") as handle:
        handle.write("\n".join("%s=sk-test-not-a-real-key" % s["key_env"] for s in team()["roles"].values()) + "\n")
    with open(SOUL, "w") as handle:
        handle.write("You are Hermes. My own first lines.\n")
    failing = {"on": False}

    def mode_fn(mode):
        return (False, "boom") if failing["on"] else apply_soul(mode)
    app = FastAPI()
    app.include_router(software_api.build_router(auth, mode_fn=mode_fn))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})

    check("needs the token", anonymous.get("/api/mode").status_code == 401)
    check("starts in manager mode", c.get("/api/mode").json() == {"mode": "manager"})
    ok, out = apply_soul("manager")
    soul = open(SOUL).read()
    check("manager mode: the main agent hands work to the team", ok and "# The teams" in soul and "kanban_create" in soul and "# Mode: general" not in soul, out)

    r = c.post("/api/mode", json={"mode": "general"})
    soul = open(SOUL).read()
    check("switching to general is saved in team.yaml", r.status_code == 200 and r.json()["changed"] and team().get("mode") == "general", r.text[:200])
    check("general mode: it works alone, without the team list", "# Mode: general" in soul and "# Your role: general assistant" in soul
          and "# The teams" not in soul and "kanban_create" not in soul)
    model = lambda: (yaml.safe_load(open(os.path.join(HOME, "config.yaml"))) or {}).get("model", {}).get("default")
    check("general mode: the main agent runs on the general role's model", model() == team()["roles"]["general"]["model"], str(model()))
    check("it keeps the company rules and the user's own first lines", "My own first lines." in soul and "# Coding policy" in soul and "kb_search" in soul)
    check("the department state reports the mode", c.get("/api/departments/software").json()["mode"] == "general")
    check("switching to the same mode changes nothing", c.post("/api/mode", json={"mode": "general"}).json()["changed"] is False)

    r = c.post("/api/mode", json={"mode": "manager"})
    soul = open(SOUL).read()
    check("switching back restores the manager", r.json()["mode"] == "manager" and "mode" not in team() and "# The teams" in soul and "# Mode: general" not in soul)
    check("manager mode: the main agent is back on the manager's model", model() == team()["roles"]["manager"]["model"], str(model()))
    check("the other agents' instructions are not written", not os.path.exists(os.path.join(HOME, "profiles")))
    check("an unknown mode is refused", c.post("/api/mode", json={"mode": "turbo"}).status_code == 400)
    failing["on"] = True
    check("when the instructions cannot be updated the mode is put back", c.post("/api/mode", json={"mode": "general"}).status_code == 500
          and "mode" not in team() and c.get("/api/mode").json()["mode"] == "manager")
    check("team.yaml keeps its comments", "# Single source of truth" in open(os.path.join(SW, "team.yaml")).read())

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
