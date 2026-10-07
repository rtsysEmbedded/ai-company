"""Paperclip runs in the Activity page: tokens and cost per Paperclip agent and task.

Paperclip starts Hermes without a profile, so its sessions land in the default profile's state.db
(source "tool") and Hermes reports no usage back to Paperclip. This module asks the local Paperclip
server which runs it started and pairs each run with the Hermes session that began right after it.
Read-only; the only network call is to Paperclip on this Mac, and the page works without it.
"""
import calendar
import json
import os
import time
import urllib.request

MATCH_WINDOW = 60  # a Paperclip run's Hermes session starts within this many seconds of the run
RUN_LIMIT = 500
_cache = {"at": 0.0, "data": None}


def api_url() -> str:
    return (os.environ.get("PAPERCLIP_API_URL") or "http://127.0.0.1:3100").rstrip("/")


def _get(path: str):
    with urllib.request.urlopen(api_url() + "/api" + path, timeout=2) as r:
        return json.load(r)


def _ts(iso):
    """'2026-10-07T08:38:03.912Z' -> epoch seconds (Paperclip timestamps are UTC)."""
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + iso[19:].rstrip("Z") or 0)
    except (TypeError, ValueError):
        return None


def fetch(max_age: float = 5.0) -> dict:
    """Runs of every active Paperclip company, oldest first. Never raises."""
    if _cache["data"] is not None and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    try:
        runs = []
        for c in _get("/companies"):
            if c.get("status") != "active":
                continue
            agents = {a["id"]: a for a in _get(f"/companies/{c['id']}/agents")}
            issues = {i["id"]: i for i in _get(f"/companies/{c['id']}/issues")}
            for r in _get(f"/companies/{c['id']}/heartbeat-runs?limit={RUN_LIMIT}"):
                agent = agents.get(r.get("agentId")) or {}
                issue = issues.get((r.get("contextSnapshot") or {}).get("issueId")) or {}
                runs.append({
                    "id": r["id"], "company": c.get("name"), "status": r.get("status"),
                    "agent": agent.get("name") or "?", "agent_model": (agent.get("adapterConfig") or {}).get("model"),
                    "adapter": agent.get("adapterType"),
                    "task": issue.get("identifier") or "", "title": issue.get("title") or "(no task)",
                    "task_status": issue.get("status"),
                    "started_at": _ts(r.get("startedAt")), "ended_at": _ts(r.get("finishedAt")),
                })
        data = {"available": True, "url": api_url(), "runs": sorted(runs, key=lambda r: r["started_at"] or 0)}
    except Exception as e:  # Paperclip not installed or not running: the page simply hides the section
        data = {"available": False, "url": api_url(), "error": str(e)[:200], "runs": []}
    _cache.update(at=time.time(), data=data)
    return data


def _match(run: dict, sessions: list, used: set):
    best, gap = None, MATCH_WINDOW
    for s in sessions:
        if s["source"] != "tool" or s["id"] in used:
            continue
        d = (s["started_at"] or 0) - (run["started_at"] or 0)
        if -5 <= d < gap:
            best, gap = s, d
    if best:
        used.add(best["id"])
    return best


def report(default_sessions: list, since: float, usage, add, now: float = None):
    """(section for the page, ids of the default-profile sessions that belong to Paperclip)."""
    now = now or time.time()
    data = fetch()
    used, total, by_agent, tasks, unmatched = set(), {}, {}, {}, 0
    for r in data["runs"]:
        if r["adapter"] != "hermes_local" or not r["started_at"]:
            continue
        s = _match(r, default_sessions, used)  # match every run so an older run cannot take a newer session
        if r["started_at"] < since:
            continue
        u = usage(s) if s else None
        if u:
            add(total, u)
            add(by_agent.setdefault(r["agent"], {"agent": r["agent"], "company": r["company"], "runs": 0}), u)
        else:
            by_agent.setdefault(r["agent"], {"agent": r["agent"], "company": r["company"], "runs": 0})
            unmatched += 1
        by_agent[r["agent"]]["runs"] += 1
        t = tasks.setdefault(r["task"] or r["id"], {"task": r["task"], "title": r["title"], "company": r["company"],
                                                    "status": r["task_status"], "runs": [], "usage": {}})
        if u:
            add(t["usage"], u)
        t["runs"].append({"id": r["id"], "agent": r["agent"], "status": r["status"], "started_at": r["started_at"],
                          "seconds": round((r["ended_at"] or now) - r["started_at"]),
                          "model": (s or {}).get("model") or r["agent_model"], "usage": u})
    section = {
        "available": data["available"], "url": data["url"], "error": data.get("error"),
        "total": total, "unmatched": unmatched,
        "by_agent": sorted(by_agent.values(), key=lambda a: -a.get("total", 0)),
        "tasks": sorted(tasks.values(), key=lambda t: -max(r["started_at"] for r in t["runs"])),
        "running": sum(1 for t in tasks.values() for r in t["runs"] if r["status"] == "running"),
    }
    return section, used
