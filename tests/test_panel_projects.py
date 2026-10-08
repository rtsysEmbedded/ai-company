"""Panel test: projects (services/panel/projects_api.py). A project is a named folder inside the home folder.

Uses a temporary list file and a temporary folder in the home folder, removed afterwards.
Run: .venv/bin/python tests/test_panel_projects.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="panel-projects-test-")
WORK = tempfile.mkdtemp(prefix=".panel-projects-test-", dir=os.path.expanduser("~"))   # project folders must be inside the home folder
os.environ.update(PANEL_DATA=os.path.join(TMP, "data"), HERMES_HOME=os.path.join(TMP, "hermes"))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "services", "panel"))
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
    app = FastAPI()
    app.include_router(projects_api.build_router(auth))
    anonymous, c = TestClient(app), TestClient(app, headers={"x-panel-token": "t"})
    shop, blog = os.path.join(WORK, "shop"), os.path.join(WORK, "blog")
    os.makedirs(shop)
    with open(os.path.join(shop, "keep.txt"), "w") as handle:
        handle.write("mine")
    os.makedirs(os.path.join(shop, ".git"))
    with open(os.path.join(shop, ".git", "HEAD"), "w") as handle:
        handle.write("ref: refs/heads/main\n")
    try:
        check("needs the token", anonymous.get("/api/projects").status_code == 401)
        check("starts empty", c.get("/api/projects").json()["projects"] == [])
        r = c.post("/api/projects", json={"name": "  Shop   website ", "folder": shop, "note": "the shop"})
        p = r.json()["projects"][0] if r.status_code == 200 else {}
        check("adds a project for an existing folder", p.get("name") == "Shop website" and p.get("id") == "shop-website"
              and p.get("folder") == os.path.realpath(shop) and p.get("exists") is True, r.text[:200])
        check("shows the git branch and empty activity", p.get("branch") == "main" and p.get("tasks") == 0 and p.get("sessions") == 0 and p.get("working") == 0)
        check("refuses a folder that does not exist", c.post("/api/projects", json={"name": "Blog", "folder": blog}).status_code == 400 and not os.path.exists(blog))
        r = c.post("/api/projects", json={"name": "Blog", "folder": blog, "create": True})
        check("creates the folder when asked", r.status_code == 200 and os.path.isdir(blog) and len(r.json()["projects"]) == 2)
        check("accepts ~ for the home folder", c.post("/api/projects", json={"name": "Tilde", "folder": "~/" + os.path.relpath(shop, os.path.expanduser("~")) + "/"}).status_code == 400)   # same folder as Shop
        check("refuses a second project with the same name or folder", c.post("/api/projects", json={"name": "shop WEBSITE", "folder": blog}).status_code == 400
              and c.post("/api/projects", json={"name": "Other", "folder": shop}).status_code == 400)
        check("refuses a folder outside the home folder", c.post("/api/projects", json={"name": "Tmp", "folder": "/tmp"}).status_code == 400
              and c.post("/api/projects", json={"name": "Up", "folder": shop + "/../../.."}).status_code == 400)
        check("refuses a file and an empty name", c.post("/api/projects", json={"name": "File", "folder": os.path.join(shop, "keep.txt")}).status_code == 400
              and c.post("/api/projects", json={"name": "  ", "folder": blog}).status_code == 400)
        r = c.put("/api/projects/shop-website", json={"name": "Shop", "folder": shop, "note": "new note"})
        p = next(x for x in r.json()["projects"] if x["id"] == "shop-website")
        check("edits name and note, keeps the id", r.status_code == 200 and p["name"] == "Shop" and p["note"] == "new note")
        check("editing an unknown project is 404", c.put("/api/projects/nope", json={"name": "x", "folder": shop}).status_code == 404)
        r = c.delete("/api/projects/shop-website")
        check("remove takes it off the list only", r.status_code == 200 and len(r.json()["projects"]) == 1
              and open(os.path.join(shop, "keep.txt")).read() == "mine")
        check("the list survives a restart", len(projects_api._load()) == 1 and projects_api._load()[0]["name"] == "Blog")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL OK" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
