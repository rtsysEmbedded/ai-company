"""Projects in the control panel: a name for a folder on this machine.

A project is only a named folder (plus a note and an optional goal: why the work exists, which agents get with every task). The list lives in data/projects.json because the
paths belong to this machine; it is not in git. Agents are not changed by it: the panel uses it to
group activity by project, to start a task in the right folder, and to show the command that
starts Hermes there. Removing a project removes it from this list and never touches the folder.
"""
import json
import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import activity_api

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STORE = os.path.join(os.environ.get("PANEL_DATA") or os.path.join(ROOT, "data"), "projects.json")
SCRATCH = ("/private/tmp/", "/tmp/", "/private/var/", "/var/folders/")   # temporary folders are not suggested as projects


def _load() -> list:
    try:
        with open(STORE, encoding="utf-8") as handle:
            data = json.load(handle)
        return [p for p in data.get("projects", []) if isinstance(p, dict) and p.get("id") and p.get("folder")]
    except (OSError, ValueError):
        return []


def _save(projects: list):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "projects": projects}, handle, indent=1)
    os.replace(tmp, STORE)


def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def _folder(path: str, create: bool) -> str:
    """The real path of a project folder inside the user's home. Created only when asked."""
    path = (path or "").strip()
    if not path:
        raise HTTPException(400, "Choose a folder")
    full = os.path.realpath(os.path.expanduser(path))
    home = _home()
    if not full.startswith(home + os.sep):
        raise HTTPException(400, "The folder must be inside your home folder (%s)" % home)
    if os.path.exists(full) and not os.path.isdir(full):
        raise HTTPException(400, "%s is a file, not a folder" % path)
    if not os.path.isdir(full):
        if not create:
            raise HTTPException(400, "The folder %s does not exist. Tick 'create it' to make it." % path)
        os.makedirs(full)
    return full


def _inside(path: str, folder: str) -> bool:
    return bool(path) and (path == folder or path.startswith(folder.rstrip("/") + "/"))


def _git_branch(folder: str) -> str:
    try:
        with open(os.path.join(folder, ".git", "HEAD"), encoding="utf-8") as handle:
            head = handle.read().strip()
        return head.split("refs/heads/", 1)[1] if "refs/heads/" in head else head[:8]
    except (OSError, IndexError):
        return ""


def listing() -> dict:
    projects, now = _load(), time.time()
    snap = activity_api.snapshot("30d")
    owner = {}   # every folder agents worked in -> the project it belongs to (the longest matching folder)

    def project_of(path):
        if path not in owner:
            hits = [p for p in projects if _inside(path, p["folder"])]
            owner[path] = max(hits, key=lambda p: len(p["folder"]))["id"] if hits else None
        return owner[path]
    stats = {p["id"]: {"tasks": 0, "open_tasks": 0, "blocked": 0, "sessions": 0, "working": 0, "last": 0, "cost": 0.0} for p in projects}
    seen = {}
    for t in snap["tasks"]:
        path, last = t.get("workspace") or "", max(t.get("created_at") or 0, t.get("started_at") or 0, t.get("completed_at") or 0)
        pid = project_of(path) if path else None
        if pid:
            s = stats[pid]
            s["tasks"] += 1
            s["open_tasks"] += t["status"] not in ("done", "archived")
            s["blocked"] += t["status"] == "blocked"
            s["working"] += any(r["task_id"] == t["id"] for r in snap["running"])
            s["last"] = max(s["last"], last)
            s["cost"] += (t.get("usage") or {}).get("cost", 0)
        elif path:
            seen[path] = max(seen.get(path, 0), last)
    for d in snap["direct"]:
        path, last = d.get("cwd") or "", max(d.get("started_at") or 0, d.get("ended_at") or 0, d.get("activity_at") or 0)
        pid = project_of(path) if path else None
        if pid:
            s = stats[pid]
            s["sessions"] += 1
            s["working"] += d["state"] == "working"
            s["last"] = max(s["last"], last)
            s["cost"] += (d.get("usage") or {}).get("cost", 0)
        elif path:
            seen[path] = max(seen.get(path, 0), last)
    out = [dict(p, exists=os.path.isdir(p["folder"]), branch=_git_branch(p["folder"]), **stats[p["id"]]) for p in projects]
    out.sort(key=lambda p: (-p["working"], -(p["last"] or p.get("created") or 0)))
    home = _home()
    suggestions = [{"folder": path, "name": os.path.basename(path.rstrip("/")), "last": last}
                   for path, last in sorted(seen.items(), key=lambda kv: -kv[1])
                   if os.path.isdir(path) and path.startswith(home + os.sep) and not path.startswith(SCRATCH)
                   and path != activity_api.hermes_home() and not _inside(path, ROOT)][:8]
    return {"projects": out, "suggestions": suggestions, "home": home, "now": now}


def _slug(name: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "project"
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = "%s-%d" % (base, n)
    return slug


def _clean(name: str, note: str):
    name, note = " ".join((name or "").split())[:60], (note or "").strip()[:500]
    if not name:
        raise HTTPException(400, "Give the project a name")
    return name, note


def _goal(text: str) -> str:
    return (text or "").strip()[:500]


def goal_for(folder: str) -> dict:
    """{'name', 'goal'} of the project that contains folder (the longest matching one), or {}.
    Used to tell an agent why it is doing a task: the goal travels with the task text."""
    folder = os.path.realpath(folder) if folder else ""
    hits = [p for p in _load() if _inside(folder, p["folder"])]
    if not hits:
        return {}
    best = max(hits, key=lambda p: len(p["folder"]))
    return {"name": best["name"], "goal": best.get("goal") or best.get("note") or ""}


class ProjectIn(BaseModel):
    name: str
    folder: str
    note: str = ""
    goal: str = ""
    create: bool = False


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/api/projects")
    def get_projects():
        return listing()

    @router.post("/api/projects")
    def add(body: ProjectIn):
        projects = _load()
        name, note = _clean(body.name, body.note)
        folder = _folder(body.folder, body.create)
        if any(p["name"].lower() == name.lower() for p in projects):
            raise HTTPException(400, "There is already a project named %s" % name)
        if any(p["folder"] == folder for p in projects):
            raise HTTPException(400, "That folder already is the project %s" % next(p["name"] for p in projects if p["folder"] == folder))
        project = {"id": _slug(name, {p["id"] for p in projects}), "name": name, "folder": folder, "note": note, "goal": _goal(body.goal), "created": time.time()}
        _save(projects + [project])
        return dict(listing(), added=project["id"])

    @router.put("/api/projects/{project_id}")
    def edit(project_id: str, body: ProjectIn):
        projects = _load()
        project = next((p for p in projects if p["id"] == project_id), None)
        if not project:
            raise HTTPException(404, "unknown project")
        name, note = _clean(body.name, body.note)
        folder = _folder(body.folder, body.create)
        others = [p for p in projects if p["id"] != project_id]
        if any(p["name"].lower() == name.lower() for p in others):
            raise HTTPException(400, "There is already a project named %s" % name)
        if any(p["folder"] == folder for p in others):
            raise HTTPException(400, "That folder already is another project")
        project.update(name=name, folder=folder, note=note, goal=_goal(body.goal))
        _save(projects)
        return listing()

    @router.delete("/api/projects/{project_id}")
    def remove(project_id: str):
        """Take a project off the list. The folder and everything in it stay as they are."""
        projects = _load()
        if not any(p["id"] == project_id for p in projects):
            raise HTTPException(404, "unknown project")
        _save([p for p in projects if p["id"] != project_id])
        return listing()

    return router
