"""Panel test: the departments routes (services/panel/dept_api.py) against a temporary copy.

Copies departments/ to a temporary folder and points the engine and Hermes at temporary homes, so
no real department, key or profile is touched. Run: .venv/bin/python tests/test_panel_departments.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-dept-test-")
shutil.copytree(os.path.join(ROOT, "departments"), os.path.join(TMP, "departments"),
                ignore=shutil.ignore_patterns(".env", ".DS_Store", "__pycache__"))
os.environ.update(DEPT_ROOT=os.path.join(TMP, "departments"), DEPT_DATA=os.path.join(TMP, "data"),
                  DEPT_ENV_FILE=os.path.join(TMP, "dept.env"), HERMES_HOME=os.path.join(TMP, "hermes"), DEPT_NO_ALIAS="1")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import dept_api  # noqa: E402

FAKE_KEY = "sk-or-v1-test-not-a-real-key-000000"


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def main():
    app = FastAPI()
    app.include_router(dept_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    base = "/api/departments/zztest"

    check("needs the token", anonymous.get("/api/departments").status_code == 401)
    r = c.get("/api/departments").json()
    check("lists the hardware department", any(d["name"] == "hardware" for d in r["departments"]))
    check("unknown department is 404", c.get("/api/departments/nope").status_code == 404)

    check("creates a department", c.post("/api/departments", json={"name": "zztest", "title": "Test", "prefix": "zz"}).status_code == 200)
    check("refuses a duplicate", c.post("/api/departments", json={"name": "zztest", "title": "Test", "prefix": "zz"}).status_code == 400)

    r = c.post(base + "/roles", json={"role": "zz-worker", "title": "Worker"})
    check("adds a role", r.status_code == 200 and "zz-worker" in r.json()["config"]["roles"])
    check("writes the new role's rules file", os.path.isfile(os.path.join(TMP, "departments", "zztest", "roles", "zz-worker.md")))
    check("refuses a name another department uses", c.post(base + "/roles", json={"role": "hw-lead", "title": "x"}).status_code == 400)
    check("refuses to delete the lead", c.delete(base + "/roles/zz-lead").status_code == 400)
    r = c.delete(base + "/roles/zz-specialist")
    check("deletes a role", r.status_code == 200 and "zz-specialist" not in r.json()["config"]["roles"])

    config = c.get(base).json()["config"]
    config["roles"]["zz-worker"]["description"] = "Builds the pages."
    config["roles"]["zz-worker"]["injected"] = "ignored"
    config["roles"]["zz-ghost"] = dict(config["roles"]["zz-worker"])
    r = c.put(base + "/config", json={"config": config})
    saved = r.json()["config"]["roles"] if r.status_code == 200 else {}
    check("saves known fields of existing roles only", saved.get("zz-worker", {}).get("description") == "Builds the pages."
          and "injected" not in saved.get("zz-worker", {}) and "zz-ghost" not in saved)
    config["projects_dir"] = ""
    check("refuses an invalid configuration", c.put(base + "/config", json={"config": config}).status_code == 400)

    rules = [p["path"] for p in c.get(base + "/rules").json()]
    check("lists policies and one rules file per role", "policies/department.md" in rules and "roles/zz-worker.md" in rules)
    check("saves rules", c.put(base + "/rules/roles/zz-worker.md", json={"text": "# Worker\n- test"}).status_code == 200
          and c.get(base + "/rules/roles/zz-worker.md").json()["text"] == "# Worker\n- test\n")
    check("refuses rules outside the department", c.put(base + "/rules/roles/..%2F..%2Fx.md", json={"text": "x"}).status_code in (400, 404)
          and c.put(base + "/rules/roles/hw-lead.md", json={"text": "x"}).status_code == 400)

    r = c.post(base + "/key", json={"scope": "department", "key": FAKE_KEY})
    sources = {n: s["key_source"] for n, s in r.json()["status"]["roles"].items()}
    check("sets the department key", r.status_code == 200 and set(sources.values()) == {"department"})
    r = c.post(base + "/key", json={"scope": "role", "role": "zz-lead", "key": FAKE_KEY + "1"})
    check("a role's own key wins", r.json()["status"]["roles"]["zz-lead"]["key_source"] == "role")
    check("never returns a key", FAKE_KEY not in c.get(base).text and FAKE_KEY not in c.get("/api/departments").text)
    check("refuses something that is not a key", c.post(base + "/key", json={"scope": "shared", "key": "short"}).status_code == 400)
    r = c.post(base + "/key", json={"scope": "role", "role": "zz-lead", "remove": True})
    check("removes a key", r.json()["status"]["roles"]["zz-lead"]["key_source"] == "department")

    check("board answers", "tasks" in c.get(base + "/board").json())

    if dept_api.engine.hermes_bin():
        r = c.post(base + "/apply").json()
        check("apply creates the profiles", r["ok"] and r["state"]["status"]["in_sync"], r.get("log", ""))
        check("profiles are in the temporary Hermes home", os.path.isdir(os.path.join(TMP, "hermes", "profiles", "zz-lead")))
    else:
        print("skip apply: hermes not found")

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
