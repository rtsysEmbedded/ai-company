"""Panel test: the software department shown like any other department (services/panel/software_api.py).

Works on a temporary copy of team.yaml and a temporary Hermes home. Run: .venv/bin/python tests/test_panel_software.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-software-test-")
SW, HOME = os.path.join(TMP, "software"), os.path.join(TMP, "hermes")
os.makedirs(SW)
os.makedirs(os.path.join(HOME, "profiles", "backend"))
shutil.copy(os.path.join(ROOT, "team.yaml"), SW)
shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(SW, "policies"), ignore=shutil.ignore_patterns(".DS_Store"))
os.environ.update(PANEL_SOFTWARE_ROOT=SW, HERMES_HOME=HOME, DEPT_DATA=os.path.join(TMP, "data"),
                  DEPT_MODELS_URL="http://127.0.0.1:9/none")   # no model list: the checks must not need the network

import yaml  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import dept_api  # noqa: E402
import software_api  # noqa: E402

TEAM = os.path.join(SW, "team.yaml")
URL = "/api/departments/software"


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def team():
    with open(TEAM, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main():
    app = FastAPI()
    applied = []

    def fake_apply(message):
        applied.append(message)
        return True, "applied"

    def fake_check(key):
        if "bad" in key:
            raise ValueError("rejected")
    app.include_router(software_api.build_router(auth, fake_apply, fake_check))
    app.include_router(dept_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    before = team()
    backend = before["roles"]["backend"]
    write(os.path.join(SW, ".env"), "%s=sk-or-v1-test-not-a-real-key-000000\n" % backend["key_env"])
    write(os.path.join(HOME, "profiles", "backend", "config.yaml"),
          "model:\n  default: %s\n  provider: openrouter\nfallback_providers:\n  - provider: openrouter\n    model: %s\nagent:\n  reasoning_effort: %s\n"
          % (backend["model"], backend["fallback"], backend["effort"]))
    write(os.path.join(HOME, "config.yaml"), "model:\n  default: some/other-model\nagent:\n  reasoning_effort: medium\n")

    check("needs the token", anonymous.get(URL).status_code == 401)
    d = c.get(URL).json()
    check("has the shape of a department", d["department"] == "software" and set(d) >= {"config", "status", "errors", "warnings", "presets", "choices"})
    check("lists every role of team.yaml", list(d["config"]["roles"]) == list(before["roles"]))
    check("the manager is the lead", d["config"]["routing"]["lead"] == "manager")
    check("a role in sync with its profile has no differences", d["status"]["roles"]["backend"] == {
        "enabled": True, "exists": True, "has_key": True, "key_source": "role", "differences": []}, str(d["status"]["roles"]["backend"]))
    check("a role without a key is reported, not compared", d["status"]["roles"]["manager"]["has_key"] is False
          and d["status"]["roles"]["manager"]["differences"] == [] and any("manager" in w for w in d["warnings"]))
    check("the reply contains no key", "sk-or-v1" not in c.get(URL).text)
    check("the tools are fixed: the task board for the manager, the knowledge base for all",
          d["capabilities"]["tools"] == "fixed" and d["config"]["roles"]["manager"]["tools"] == ["kanban", "kb"] and d["config"]["roles"]["backend"]["tools"] == ["kb"])
    check("review is given in the engine's words", d["config"]["routing"]["review"] == "changes" and before["routing"]["review"] == "code")

    write(os.path.join(SW, ".env"), "%s=k\n%s=k\n" % (backend["key_env"], before["roles"]["manager"]["key_env"]))
    d = c.get(URL).json()
    check("a profile with another model shows what Apply would change",
          any("some/other-model" in x for x in d["status"]["roles"]["manager"]["differences"]) and d["status"]["in_sync"] is False)

    config = d["config"]
    config["roles"]["backend"].update(effort="high", description="Main coder,   changed.", fallback="")
    config["roles"]["tester"]["enabled"] = False
    config["roles"]["backend"]["key_env"] = "INJECTED"
    config["roles"]["ghost"] = dict(config["roles"]["backend"])
    r = c.put(URL + "/config", json={"config": config})
    after = team()
    check("saves effort, description, fallback and enabled", r.status_code == 200 and after["roles"]["backend"]["effort"] == "high"
          and after["roles"]["backend"]["description"] == "Main coder, changed." and after["roles"]["backend"]["fallback"] is None
          and after["roles"]["tester"]["enabled"] is False, r.text[:200])
    check("ignores unknown fields and unknown roles", after["roles"]["backend"]["key_env"] == backend["key_env"] and "ghost" not in after["roles"])
    check("leaves other roles and the routing alone", after["roles"]["frontend"] == before["roles"]["frontend"] and after["routing"] == before["routing"])
    check("a title equal to the name is not stored", "title" not in after["roles"]["backend"])
    check("keeps the comments of team.yaml", "# Single source of truth" in open(TEAM).read() and "# How the manager distributes work" in open(TEAM).read())
    check("the saved state shows the profile now differs", any("effort" in x for x in r.json()["status"]["roles"]["backend"]["differences"]))

    config = c.get(URL).json()["config"]
    config["roles"]["tester"]["enabled"] = True
    c.put(URL + "/config", json={"config": config})
    check("enabling again removes the flag", "enabled" not in team()["roles"]["tester"])

    snapshot = open(TEAM).read()
    for change, why in (({"model": ""}, "no model"), ({"model": "not a model"}, "bad model id"), ({"effort": "xhigh"}, "unknown effort"),
                        ({"description": "  "}, "empty description")):
        config = c.get(URL).json()["config"]
        config["roles"]["backend"].update(change)
        check("refuses %s and writes nothing" % why, c.put(URL + "/config", json={"config": config}).status_code == 400 and open(TEAM).read() == snapshot)
    config = c.get(URL).json()["config"]
    config["roles"]["manager"]["enabled"] = False
    check("refuses to disable the manager", c.put(URL + "/config", json={"config": config}).status_code == 400)
    check("test of an unknown role is refused", c.post(URL + "/test", json={"role": "nope"}).status_code == 400)

    # ---- routing ----
    config = c.get(URL).json()["config"]
    config["routing"].update(review="all", reviewer="security", task_timeout="30m", escalate_after=3, max_parallel="2", lead="backend")
    r = c.put(URL + "/config", json={"config": config})
    routing = team()["routing"]
    check("saves routing, numbers as numbers", r.status_code == 200 and routing["review"] == "all" and routing["reviewer"] == "security"
          and routing["task_timeout"] == "30m" and routing["escalate_after"] == 3 and routing["max_parallel"] == 2, r.text[:200])
    check("the lead stays the manager", r.json()["config"]["routing"]["lead"] == "manager" and "lead" not in routing)
    config["routing"]["review"] = "changes"
    c.put(URL + "/config", json={"config": config})
    check("'changes' is stored as 'code'", team()["routing"]["review"] == "code")
    for change, why in (({"reviewer": "nobody"}, "an unknown reviewer"), ({"escalate_after": 99}, "a number out of range"),
                        ({"task_timeout": "soon"}, "a bad duration"), ({"security_review": "maybe"}, "an unknown choice")):
        bad = c.get(URL).json()["config"]
        bad["routing"].update(change)
        check("refuses %s" % why, c.put(URL + "/config", json={"config": bad}).status_code == 400)

    # ---- title and needs ----
    config = c.get(URL).json()["config"]
    config["roles"]["frontend"].update(title="Frontend engineer", needs=["tools", "image"])
    r = c.put(URL + "/config", json={"config": config}).json()
    check("saves a title and what the model must be able to do", team()["roles"]["frontend"]["title"] == "Frontend engineer"
          and team()["roles"]["frontend"]["needs"] == ["image"] and r["config"]["roles"]["frontend"]["needs"] == ["tools", "image"])

    # ---- roles ----
    r = c.post(URL + "/roles", json={"role": "data", "title": "Data engineer"})
    new = team()["roles"].get("data", {})
    check("adds a role with its own key name and a rules file", r.status_code == 200 and new.get("key_env") == "OPENROUTER_KEY_DATA"
          and new.get("title") == "Data engineer" and os.path.isfile(os.path.join(SW, "policies", "roles", "data.md")), r.text[:200])
    check("refuses a name that is taken", c.post(URL + "/roles", json={"role": "backend", "title": "x"}).status_code == 400
          and c.post(URL + "/roles", json={"role": "hw-lead", "title": "x"}).status_code == 400
          and c.post(URL + "/roles", json={"role": "Bad Name", "title": "x"}).status_code == 400)
    check("refuses to delete the manager or a role used in routing", c.delete(URL + "/roles/manager").status_code == 400
          and c.delete(URL + "/roles/architect").status_code == 400)
    r = c.delete(URL + "/roles/data")
    check("deletes a role and its rules file", r.status_code == 200 and "data" not in team()["roles"]
          and not os.path.exists(os.path.join(SW, "policies", "roles", "data.md")))

    # ---- rules ----
    rules = c.get(URL + "/rules").json()
    check("lists the department rules and one file per role", {"path": "policies/global.md", "kind": "Whole department"}.items() <= next(
        x for x in rules if x["path"] == "policies/global.md").items() and any(x["path"] == "roles/backend.md" and x["kind"] == "One role" for x in rules))
    check("reads and saves a role's rules in policies/roles/", c.put(URL + "/rules/roles/backend.md", json={"text": "# Backend\n- test"}).status_code == 200
          and open(os.path.join(SW, "policies", "roles", "backend.md")).read() == "# Backend\n- test\n"
          and c.get(URL + "/rules/roles/backend.md").json()["text"] == "# Backend\n- test\n")
    check("refuses rules outside the department", c.put(URL + "/rules/roles/nobody.md", json={"text": "x"}).status_code == 400
          and c.put(URL + "/rules/policies/..%2F..%2Fx.md", json={"text": "x"}).status_code in (400, 404))

    # ---- keys ----
    r = c.post(URL + "/key", json={"scope": "role", "role": "tester", "key": "sk-or-v1-good-test-key-0000000000"})
    check("saves a role's key after checking it", r.status_code == 200 and r.json()["status"]["roles"]["tester"]["has_key"]
          and software_api.engine.read_env(os.path.join(SW, ".env"))[before["roles"]["tester"]["key_env"]].startswith("sk-or-v1-good"))
    check("the reply never contains the key", "sk-or-v1-good" not in r.text)
    check("refuses a key OpenRouter rejects", c.post(URL + "/key", json={"scope": "role", "role": "tester", "key": "sk-or-v1-bad-test-key-00000000000"}).status_code == 400)
    check("there is no shared key in this department", c.post(URL + "/key", json={"scope": "shared", "key": "sk-or-v1-good-test-key-0000000000"}).status_code == 400)
    r = c.post(URL + "/key", json={"scope": "role", "role": "tester", "remove": True})
    check("removes a role's key", r.json()["status"]["roles"]["tester"]["has_key"] is False)

    # ---- apply ----
    r = c.post(URL + "/apply", json={"message": "note"}).json()
    check("apply runs the team's own apply with the note", r["ok"] and r["log"] == "applied" and applied == ["note"] and "config" in r["state"])

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
