"""Panel test: backup and restore of a department (services/panel/backup_api.py) against temporary copies.

No real department, team.yaml, key or backup is touched. Run: .venv/bin/python tests/test_panel_backup.py
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-backup-test-")
shutil.copytree(os.path.join(ROOT, "departments"), os.path.join(TMP, "departments"),
                ignore=shutil.ignore_patterns(".env", ".DS_Store", "__pycache__"))
SW = os.path.join(TMP, "software")
os.makedirs(SW)
shutil.copy(os.path.join(ROOT, "team.yaml"), SW)
shutil.copytree(os.path.join(ROOT, "policies"), os.path.join(SW, "policies"), ignore=shutil.ignore_patterns(".DS_Store"))
os.environ.update(DEPT_ROOT=os.path.join(TMP, "departments"), DEPT_DATA=os.path.join(TMP, "data"),
                  DEPT_ENV_FILE=os.path.join(TMP, "dept.env"), HERMES_HOME=os.path.join(TMP, "hermes"),
                  PANEL_SOFTWARE_ROOT=SW, PANEL_BACKUP_DIR=os.path.join(TMP, "backups"))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
import backup_api  # noqa: E402

engine = backup_api.engine
FAKE_KEY = "sk-or-v1-test-not-a-real-key-000000"
HW = os.path.join(TMP, "departments", "hardware")


def auth(request: Request):
    if request.headers.get("x-panel-token") != "t":
        raise HTTPException(401, "no token")


def check(name, condition, detail=""):
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, (" - %s" % detail) if detail and not condition else ""))
    if not condition:
        check.failed += 1


check.failed = 0


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main():
    app = FastAPI()
    app.include_router(backup_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    hw = "/api/backups/hardware"

    check("needs the token", anonymous.get(hw).status_code == 401)
    check("unknown department is 404", c.get("/api/backups/nope").status_code == 404)
    check("starts empty", c.get(hw).json() == [])

    # ---- a department of the engine ----
    engine.set_env_value(engine.ENV_PATH, engine.key_var("hw-lead"), FAKE_KEY)
    engine.set_env_value(engine.ENV_PATH, engine.KEY_VAR, FAKE_KEY + "-shared")
    config_before, rules_before = read(os.path.join(HW, "department.json")), read(os.path.join(HW, "roles", "hw-lead.md"))
    r = c.post(hw, json={"label": "Before changing models"}).json()
    plain = r["id"]
    check("creates a backup with a readable name", plain.endswith("-before-changing-models.json") and len(r["backups"]) == 1)
    check("a backup has no keys unless asked", r["backups"][0]["keys"] == 0 and FAKE_KEY not in read(os.path.join(TMP, "backups", "hardware", plain)))
    check("it lists the roles", "hw-lead" in r["backups"][0]["roles"])
    with_keys = c.post(hw, json={"label": "with keys", "include_keys": True}).json()["id"]
    stored = json.loads(read(os.path.join(TMP, "backups", "hardware", with_keys)))
    check("keys are included when asked, without the shared key",
          stored["keys"] == {engine.key_var("hw-lead"): FAKE_KEY})
    check("backup files are private", oct(os.stat(os.path.join(TMP, "backups", "hardware", with_keys)).st_mode & 0o777) == "0o600")
    check("the list never contains a key", FAKE_KEY not in c.get(hw).text)

    config = json.loads(config_before)
    config["roles"]["hw-lead"]["model"] = "x-ai/grok-4.7"
    del config["roles"]["hw-docs"]
    write(os.path.join(HW, "department.json"), json.dumps(config))
    write(os.path.join(HW, "roles", "hw-lead.md"), "changed\n")
    os.remove(os.path.join(HW, "roles", "hw-docs.md"))
    write(os.path.join(HW, "roles", "hw-extra.md"), "a file the backup does not have\n")
    engine.set_env_value(engine.ENV_PATH, engine.key_var("hw-lead"), "changed-key-0000000000000000")

    r = c.post("%s/%s/restore" % (hw, plain), json={}).json()
    check("restore brings the configuration back", read(os.path.join(HW, "department.json")) == config_before, str(r))
    check("and the rules, including a deleted role's file", read(os.path.join(HW, "roles", "hw-lead.md")) == rules_before
          and os.path.isfile(os.path.join(HW, "roles", "hw-docs.md")))
    check("and removes files the backup does not have", not os.path.exists(os.path.join(HW, "roles", "hw-extra.md")) and r["removed"] == 1)
    check("a backup without keys leaves the keys alone", engine.read_env(engine.ENV_PATH)[engine.key_var("hw-lead")].startswith("changed"))
    safety = [b for b in r["backups"] if b["automatic"]]
    check("the settings from before the restore are kept as a backup", len(safety) == 1 and safety[0]["label"] == "before restore")
    r = c.post("%s/%s/restore" % (hw, safety[0]["id"]), json={}).json()
    check("so a restore can be undone", json.loads(read(os.path.join(HW, "department.json")))["roles"]["hw-lead"]["model"] == "x-ai/grok-4.7"
          and not os.path.exists(os.path.join(HW, "roles", "hw-docs.md")))
    r = c.post("%s/%s/restore" % (hw, with_keys), json={"with_keys": True}).json()
    check("a backup with keys restores them", r["keys"] == 1 and engine.read_env(engine.ENV_PATH)[engine.key_var("hw-lead")] == FAKE_KEY)
    check("the shared key is never touched", engine.read_env(engine.ENV_PATH)[engine.KEY_VAR] == FAKE_KEY + "-shared")
    check("restoring the same again changes nothing", c.post("%s/%s/restore" % (hw, with_keys), json={}).json()["unchanged"])

    # ---- download and import ----
    r = c.get("%s/%s/download" % (hw, plain))
    downloaded = r.json()
    check("download returns the backup file", r.status_code == 200 and downloaded["department"] == "hardware"
          and "attachment" in r.headers.get("content-disposition", ""))
    count = len(c.get(hw).json())
    r = c.post(hw + "/import", json={"backup": downloaded})
    check("a backup file can be added to the list", r.status_code == 200 and len(r.json()["backups"]) == count + 1)
    check("a backup of another department is refused", c.post("/api/backups/software/import", json={"backup": downloaded}).status_code == 400)
    check("a file that is not a backup is refused", c.post(hw + "/import", json={"backup": {"hello": 1}}).status_code == 400)
    evil = dict(downloaded, files=dict(downloaded["files"], **{"../../escape.md": "x"}))
    check("a path outside the department is refused", c.post(hw + "/import", json={"backup": evil}).status_code == 400
          and not os.path.exists(os.path.join(TMP, "escape.md")))
    evil = dict(downloaded, keys={"PATH": "x"})
    check("keys of another name are refused", c.post(hw + "/import", json={"backup": evil}).status_code == 400)
    broken = dict(downloaded, files=dict(downloaded["files"], **{"department.json": "{}"}))
    check("an invalid configuration is refused before anything is written",
          c.post(hw + "/import", json={"backup": broken}).status_code == 400 and read(os.path.join(HW, "department.json")) == config_before)
    check("a bad backup name is refused", c.post(hw + "/..%2Fx/restore", json={}).status_code in (400, 404)
          and c.delete(hw + "/nope.json").status_code == 400)
    check("delete removes one backup", len(c.delete("%s/%s" % (hw, plain)).json()) == count)

    # ---- the software department ----
    sw = "/api/backups/software"
    team_before = read(os.path.join(SW, "team.yaml"))
    policy = os.path.join(SW, "policies", "global.md")
    policy_before = read(policy)
    r = c.post(sw, json={"label": "software"}).json()
    files = json.loads(read(os.path.join(TMP, "backups", "software", r["id"])))["files"]
    check("software: backs up team.yaml and the policies only", "team.yaml" in files and "policies/global.md" in files
          and all(f == "team.yaml" or f.startswith("policies/") for f in files))
    check("software: lists its roles", "manager" in r["backups"][0]["roles"])
    write(os.path.join(SW, "team.yaml"), team_before.replace("effort: medium", "effort: low"))
    write(policy, "changed\n")
    write(os.path.join(SW, "policies", "extra.md"), "new\n")
    c.post("%s/%s/restore" % (sw, r["id"]), json={})
    check("software: restore brings team.yaml and the policies back", read(os.path.join(SW, "team.yaml")) == team_before
          and read(policy) == policy_before and not os.path.exists(os.path.join(SW, "policies", "extra.md")))

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
