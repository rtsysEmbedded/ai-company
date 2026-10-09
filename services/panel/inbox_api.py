"""The user's inbox: everything on the task board that waits for the user, per project.

Three kinds of items, all read from the board (nothing is copied):
- question: a blocked task that asks for information or a decision, and that no lead is dealing with
- stopped:  a task that cannot go on by itself (blocked twice, provider refused, failed too often)
- finished: a job the user started whose tasks are all done, with the lead's report

A job is the task the user created (in the panel, or through the manager in a chat) plus everything that was
created from it. Items the user has dealt with are remembered in data/inbox.json; finished jobs from before the
inbox existed start as dealt with.
"""
import json
import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import activity_api
import causes
import projects_api

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE = os.path.join(os.environ.get("PANEL_DATA") or os.path.join(ROOT, "data"), "inbox.json")
OPEN = ("running", "ready", "todo", "review", "scheduled")
BLOCKED_TAG = "[blocked]"          # a specialist's block on its way to the lead (scripts/escalate.py)
SYSTEM = ("ai-company",)           # created by the company's own services, never a job of the user


def _state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {"since": time.time(), "done": {}}
        _save(data)
    data.setdefault("done", {})
    return data


def _save(data: dict):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
    os.replace(tmp, STATE)


def _project(folder: str, projects: list) -> dict:
    """{name, key} of the project a folder belongs to: the registered project, else the folder itself."""
    folder = (folder or "").rstrip("/")
    best = None
    for p in projects:
        base = p["folder"].rstrip("/")
        if folder == base or folder.startswith(base + "/"):
            if not best or len(base) > len(best["folder"].rstrip("/")):
                best = p
    if best:
        return {"name": best["name"], "key": best["id"], "folder": best["folder"]}
    return {"name": os.path.basename(folder) or "no project folder", "key": folder, "folder": folder}


def _clip(text, limit=6000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[...]"


def items() -> dict:
    """Every inbox item, newest first, with what the user can do with it."""
    kdb = os.path.join(activity_api.hermes_home(), "kanban.db")
    rows = activity_api._rows
    tasks = {t["id"]: t for t in rows(kdb, "SELECT id, title, body, assignee, status, created_by, created_at, completed_at, result, "
                                           "workspace_path, block_kind, last_failure_error FROM tasks WHERE status != 'archived'")}
    creator, blocked_at, reason = {}, {}, {}
    for e in rows(kdb, "SELECT task_id, kind, payload, created_at FROM task_events WHERE kind IN ('created', 'blocked', 'block_loop_detected', 'gave_up') ORDER BY created_at"):
        try:
            payload = json.loads(e["payload"] or "{}") or {}
        except ValueError:
            payload = {}
        if e["kind"] == "created":
            creator[e["task_id"]] = payload.get("creator_task_id")
        else:
            blocked_at[e["task_id"]] = e["created_at"]
            reason[e["task_id"]] = str(payload.get("reason") or payload.get("error") or "")
    summary = {}
    for r in rows(kdb, "SELECT task_id, summary, ended_at FROM task_runs WHERE summary IS NOT NULL ORDER BY id"):
        summary[r["task_id"]] = r["summary"]
    parents = {}
    for link in rows(kdb, "SELECT parent_id, child_id FROM task_links"):
        parents.setdefault(link["child_id"], []).append(link["parent_id"])

    # The job a task belongs to: where it was created from, the task it asks about, or the task it waits for.
    def job_of(tid, seen=()):
        t = tasks.get(tid)
        if not t or tid in seen:
            return tid
        up = creator.get(tid)
        if not up and (t["title"] or "").startswith(BLOCKED_TAG):
            found = re.search(r"blocked task (t_[0-9a-f]+)", t["body"] or "")
            up = found.group(1) if found else None
        if not up and t["created_by"] not in ("panel", "user", "worker", "default"):
            up = next((p for p in parents.get(tid, []) if p in tasks), None)
        return job_of(up, seen + (tid,)) if up and up in tasks else tid

    jobs = {}
    for tid in tasks:
        jobs.setdefault(job_of(tid), []).append(tid)
    with_lead = set()   # blocked tasks a lead is looking at right now
    for t in tasks.values():
        if (t["title"] or "").startswith(BLOCKED_TAG) and t["status"] in OPEN + ("blocked",):
            found = re.search(r"blocked task (t_[0-9a-f]+)", t["body"] or "")
            if found:
                with_lead.add(found.group(1))

    state, projects, prof, out = _state(), projects_api._load(), activity_api.profiles(), []

    def base(kind, t, root, at, text):
        r = tasks.get(root) or t
        info = prof.get(t["assignee"] or "") or {}
        return {"kind": kind, "task_id": t["id"], "job_id": root, "job_title": r["title"], "title": t["title"], "assignee": t["assignee"],
                "dept": info.get("dept", ""), "at": at, "text": _clip(text), "folder": r["workspace_path"] or t["workspace_path"] or "",
                "project": _project(r["workspace_path"] or t["workspace_path"] or "", projects)}

    for root, members in jobs.items():
        r = tasks[root]
        for tid in members:
            t = tasks[tid]
            if t["status"] not in ("blocked", "triage") or tid in with_lead:
                continue
            at = blocked_at.get(tid) or t["created_at"] or 0
            cause = causes.run_causes(tid).get("_last") if t["status"] == "blocked" and t["block_kind"] != "needs_input" else None
            if t["status"] == "blocked" and t["block_kind"] == "needs_input":
                item = base("question", t, root, at, reason.get(tid) or "The agent asks for information.")
                item["actions"] = ["answer", "archive"]
            else:
                said = reason.get(tid) or t["last_failure_error"] or ""
                if said == "initial_status":
                    said = "It was created waiting for an earlier task of this job, and that task stopped."
                    reason[tid] = said
                why = (causes.LABELS.get(cause) or ("It blocked twice for the same reason: %s" % reason.get(tid, "") if t["status"] == "triage"
                       else reason.get(tid) or t["last_failure_error"] or "It stopped without a reason."))
                item = base("stopped", t, root, at, why)
                item["actions"] = (["answer", "retry"] if t["status"] == "blocked" else []) + ["archive"]
            item["id"] = "%s:%s:%d" % (item["kind"], tid, at)
            out.append(item)
        if r["created_by"] in SYSTEM or creator.get(root) or any(tasks[m]["status"] != "done" for m in members):
            continue
        at = max(tasks[m]["completed_at"] or 0 for m in members)
        reports = sorted((m for m in members if "check and report" in (tasks[m]["title"] or "").lower()),
                         key=lambda m: tasks[m]["completed_at"] or 0)
        last = reports[-1] if reports else root
        item = base("finished", r, root, at, summary.get(last) or tasks[last]["result"] or "Finished without a written report.")
        item.update(id="finished:%s:%d" % (root, at), actions=["accept", "followup"], tasks=len(members), report_task=last)
        out.append(item)

    for item in out:
        handled = state["done"].get(item["id"])
        before = item["kind"] == "finished" and item["at"] < state.get("since", 0) and item["id"] not in state.get("open", {})
        item["handled_at"] = handled or (state.get("since") if before else None)
    out.sort(key=lambda i: -i["at"])
    open_items = [i for i in out if not i["handled_at"]]
    by_project = {}
    for i in open_items:
        by_project[i["project"]["key"]] = by_project.get(i["project"]["key"], 0) + 1
    return {"now": time.time(), "items": open_items, "handled": [i for i in out if i["handled_at"]][:40],
            "count": len(open_items), "by_project": by_project}


class DoneIn(BaseModel):
    id: str
    done: bool = True


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/api/inbox")
    def inbox():
        return items()

    @router.get("/api/inbox/count")
    def count():
        data = items()
        return {"count": data["count"], "by_project": data["by_project"]}

    @router.post("/api/inbox/done")
    def done(body: DoneIn):
        """Mark an item as dealt with (or put it back). The board is not changed."""
        if not re.fullmatch(r"(question|stopped|finished):t_[0-9a-f]{4,32}:\d{1,12}", body.id):
            raise HTTPException(400, "invalid item")
        state = _state()
        if body.done:
            state["done"][body.id] = time.time()
        else:
            state["done"].pop(body.id, None)
            state.setdefault("open", {})[body.id] = time.time()   # also for a job from before the inbox existed
        state.get("open", {}).pop(body.id, None) if body.done else None
        for key in sorted(state["done"], key=state["done"].get)[:-500]:   # keep the file small
            state["done"].pop(key)
        _save(state)
        return {"ok": True}

    return router
