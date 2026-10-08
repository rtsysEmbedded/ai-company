"""Activity in the control panel: who is working now, what each task cost, and token totals.

Read-only. Everything comes from Hermes' own SQLite files: the kanban board (tasks, runs, events)
and each profile's state.db (sessions with token counts and cost). The only API call is to the local
Paperclip server (paperclip_usage.py), to label the sessions its agents started.
Costs are Hermes' estimates unless the provider reported an actual cost.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

import yaml
from fastapi import APIRouter, Depends, HTTPException

import causes
import paperclip_usage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "departments", "engine"))
try:
    import dept as engine
except Exception:  # the departments engine is optional
    engine = None

RANGES = {"today": None, "7d": 7 * 86400, "30d": 30 * 86400, "all": 0}
TOKEN_COLS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
MATCH_WINDOW = 180  # a worker's session starts within this many seconds of its run


def hermes_home() -> str:
    return os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes")


def _connect(db: str):
    """Open one of Hermes' SQLite files without ever creating, deleting or resetting its -wal/-shm side files.

    Two ways a reader can kill a running agent:
    - A normal connection that is the last one to close checkpoints and DELETES the -wal file. Hermes watches
      that file and aborts the agent's turn when it disappears ("retired state.db-wal generation").
    - Even a mode=ro connection opens the -shm index read-write. Hermes drops its own lock on that file (it
      opens and closes it to fix permissions), so SQLite here thinks nobody uses the database and truncates
      the index; the agent's next read of its mapping dies with "Bus error: 10".
    So: read-only with a read-only -shm while a WAL exists (it can neither remove nor reset anything), and
    immutable when there is none (everything is in the main file; nothing gets created).
    """
    from urllib.parse import quote
    live = os.path.exists(db + "-wal") and os.path.exists(db + "-shm")
    mode = "mode=ro&readonly_shm=1" if live else "immutable=1"
    return sqlite3.connect(f"file:{quote(db)}?{mode}", uri=True, timeout=3)


def _rows(db: str, sql: str, args=()) -> list:
    if not os.path.exists(db):
        return []
    try:
        con = _connect(db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def profiles() -> dict:
    """profile name -> {dept, role, title}. Software comes from team.yaml, the rest from departments/."""
    out = {}
    try:
        team = yaml.safe_load(open(os.path.join(ROOT, "team.yaml"))) or {}
        for role, spec in (team.get("roles") or {}).items():
            out[spec.get("hermes_profile") or role] = {"dept": "software", "dept_title": "Software", "role": role,
                                                        "model": spec.get("model"), "enabled": bool(spec.get("enabled", True))}
    except OSError:
        pass
    if engine:
        for name in engine.list_departments():
            try:
                cfg = engine.load_config(name)
            except Exception:
                continue
            for role, spec in (cfg.get("roles") or {}).items():
                out.setdefault(role, {"dept": name, "dept_title": cfg.get("name") or name, "role": role,
                                      "model": (spec or {}).get("model"), "enabled": bool((spec or {}).get("enabled", True))})
    return out


def _state_db(profile: str) -> str:
    home = hermes_home()
    return os.path.join(home, "state.db") if profile == "default" else os.path.join(home, "profiles", profile, "state.db")


def _installed_profiles() -> list:
    base = os.path.join(hermes_home(), "profiles")
    names = sorted(n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))) if os.path.isdir(base) else []
    return ["default"] + names


def _usage(row: dict) -> dict:
    u = {c.replace("_tokens", ""): int(row.get(c) or 0) for c in TOKEN_COLS}
    u["total"] = u["input"] + u["output"] + u["cache_read"] + u["cache_write"]
    actual = row.get("actual_cost_usd")
    cost = float(actual if actual is not None else (row.get("estimated_cost_usd") or 0))
    # Hermes stores a negative estimate for models whose price it cannot resolve (e.g. routers).
    # It also stores 0 with cost_status "unknown" for them; that is not a free session.
    unpriced = cost < 0 or (cost == 0 and actual is None and row.get("cost_status") == "unknown" and u["total"] > 0)
    u["cost_unknown"] = 1 if unpriced else 0
    u["cost"] = max(cost, 0.0)
    u["cost_exact"] = actual is not None
    u["api_calls"] = int(row.get("api_call_count") or 0)
    return u


def _add(total: dict, u: dict):
    for k in ("input", "output", "cache_read", "cache_write", "reasoning", "total", "cost", "api_calls", "cost_unknown"):
        total[k] = total.get(k, 0) + u.get(k, 0)
    return total


def _sessions(profile: str, since: float) -> list:
    cols = ("id, source, title, cwd, git_repo_root, model, started_at, ended_at, end_reason, message_count, tool_call_count, api_call_count, "
            "last_activity_at, last_activity_description, estimated_cost_usd, actual_cost_usd, cost_status, " + ", ".join(TOKEN_COLS))
    return _rows(_state_db(profile), f"SELECT {cols} FROM sessions WHERE started_at >= ? ORDER BY started_at", (since,))


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _pid_cwd(pid) -> str:
    """The folder a running Hermes was started in (for a chat that has no session row yet)."""
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    return next((line[1:] for line in out.splitlines() if line.startswith("n")), "")


def _live(profile: str, now: float) -> tuple:
    """(open, busy): sessions of a profile that a running Hermes has open, and those in the middle of a turn."""
    db = _state_db(profile)
    open_now = {}
    try:
        with open(os.path.join(os.path.dirname(db), "runtime", "active_sessions.json"), encoding="utf-8") as handle:
            for entry in json.load(handle).get("entries") or []:
                if _pid_alive(entry.get("pid")):
                    open_now[entry.get("session_id")] = entry
                    live = (entry.get("metadata") or {}).get("live_session_id")   # differs after a resume or a compression
                    if live:
                        open_now[live] = entry
    except (OSError, ValueError):
        pass
    busy = set()
    for lease in _rows(db, "SELECT conversation_id, holder FROM session_turn_leases WHERE expires_at > ?", (now,)):
        pid = (lease["holder"] or "").split(":")[0].replace("pid=", "")
        if _pid_alive(pid):
            busy.add(lease["conversation_id"])
    return open_now, busy


def direct_sessions(sessions: dict, skip: set, prof: dict, since: float, now: float, wanted, paperclip_ids=()) -> list:
    """Work done by talking to an agent directly (hermes in a project folder, the panel chat, a messenger),
    not through the task board: one entry per session, with the folder it ran in and whether it is live."""
    out = []
    for profile, rows in sessions.items():
        if not wanted(profile):
            continue
        open_now, busy = _live(profile, now)
        info = prof.get(profile) or {}
        seen = set()
        for s in rows:
            if s["source"] == "kanban" or s["id"] in skip:
                continue
            seen.add(s["id"])
            is_open = s["id"] in open_now or (not s["ended_at"] and s["id"] in busy)
            recent = bool(s["last_activity_at"]) and now - s["last_activity_at"] < 60
            working = not s["ended_at"] and (s["id"] in busy or (is_open and recent))
            if (s["started_at"] or 0) < since and not is_open:
                continue
            out.append({
                "id": s["id"], "profile": profile, "role": info.get("role", profile), "dept": info.get("dept", "other"),
                "source": "paperclip" if s["id"] in paperclip_ids else s["source"], "title": s["title"] or "",
                "cwd": s["git_repo_root"] or s["cwd"] or "", "model": s["model"] or info.get("model"),
                "started_at": s["started_at"], "ended_at": s["ended_at"],
                "seconds": round((s["ended_at"] or (now if is_open else s["last_activity_at"] or s["started_at"] or now)) - (s["started_at"] or now)),
                "state": "working" if working else "open" if is_open else "ended",
                "activity": s["last_activity_description"], "activity_at": s["last_activity_at"],
                "messages": s["message_count"], "tool_calls": s["tool_call_count"], "usage": _usage(s),
            })
        shown = set()
        for sid, entry in open_now.items():   # a chat that was opened but has not been asked anything yet
            if sid not in seen and entry.get("pid") not in shown and not any(k in seen for k, e in open_now.items() if e is entry):
                shown.add(entry.get("pid"))
                out.append({"id": sid, "profile": profile, "role": info.get("role", profile), "dept": info.get("dept", "other"),
                            "source": entry.get("surface") or "cli", "title": "", "cwd": _pid_cwd(entry.get("pid")), "model": info.get("model"),
                            "started_at": entry.get("started_at"), "ended_at": None, "seconds": round(now - (entry.get("started_at") or now)),
                            "state": "open", "activity": None, "activity_at": None, "messages": 0, "tool_calls": 0, "usage": None})
    order = {"working": 0, "open": 1, "ended": 2}
    return sorted(out, key=lambda d: (order[d["state"]], -(d["started_at"] or 0)))[:80]


def _since(range_key: str) -> float:
    if range_key == "today":
        t = time.localtime()
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    span = RANGES.get(range_key, 0)
    return time.time() - span if span else 0.0


def _match(run: dict, sessions: list, used: set):
    """The kanban session a run produced: same profile, started right after the run was claimed."""
    best, gap = None, MATCH_WINDOW
    for s in sessions:
        if s["source"] != "kanban" or s["id"] in used:
            continue
        d = (s["started_at"] or 0) - (run["started_at"] or 0)
        if -5 <= d < gap:
            best, gap = s, d
    if best:
        used.add(best["id"])
    return best


def snapshot(range_key: str = "today", department: str = "") -> dict:
    now = time.time()
    since = _since(range_key if range_key in RANGES else "today")
    prof = profiles()
    kdb = os.path.join(hermes_home(), "kanban.db")

    def dept_of(p):
        return (prof.get(p) or {}).get("dept", "other")

    def wanted(p):
        return not department or dept_of(p) == department

    sessions = {p: _sessions(p, 0) for p in _installed_profiles()}

    # ---- tasks with their runs ----
    tasks = _rows(kdb, "SELECT id, title, body, assignee, status, created_by, created_at, started_at, completed_at, "
                       "result, consecutive_failures, last_failure_error, block_kind, workspace_path, max_runtime_seconds "
                       "FROM tasks WHERE status != 'archived' ORDER BY created_at DESC LIMIT 300")
    runs = _rows(kdb, "SELECT id, task_id, profile, status, outcome, started_at, ended_at, last_heartbeat_at, "
                      "summary, error FROM task_runs ORDER BY started_at")
    blocked = {}
    for e in _rows(kdb, "SELECT task_id, payload FROM task_events WHERE kind = 'blocked' ORDER BY created_at"):
        try:
            blocked[e["task_id"]] = (json.loads(e["payload"] or "{}") or {}).get("reason") or ""
        except ValueError:
            pass
    parents = {}  # child task -> the tasks that must finish first
    for link in _rows(kdb, "SELECT parent_id, child_id FROM task_links"):
        parents.setdefault(link["child_id"], []).append(link["parent_id"])
    used, by_task, cause_cache = set(), {}, {}
    failed_outcomes = {"crashed", "gave_up", "timed_out", "spawn_failed", "stale"}
    for r in runs:
        s = _match(r, sessions.get(r["profile"], []), used)
        usage = _usage(s) if s else None
        info = prof.get(r["profile"]) or {}
        cause = None
        if r["outcome"] in failed_outcomes:  # the worker log knows why (e.g. the provider refused the call)
            if r["task_id"] not in cause_cache:
                cause_cache[r["task_id"]] = causes.run_causes(r["task_id"])
            cause = cause_cache[r["task_id"]].get((s or {}).get("id"))
        by_task.setdefault(r["task_id"], []).append({
            "id": r["id"], "profile": r["profile"], "dept": dept_of(r["profile"]),
            "model": (s or {}).get("model") or info.get("model"),
            "status": r["status"], "outcome": r["outcome"], "started_at": r["started_at"], "ended_at": r["ended_at"],
            "seconds": round((r["ended_at"] or now) - (r["started_at"] or now)),
            "summary": r["summary"], "error": r["error"], "usage": usage,
            "cause": cause, "cause_label": causes.LABELS.get(cause),
            "activity": (s or {}).get("last_activity_description"), "activity_at": (s or {}).get("last_activity_at"),
            "tool_calls": (s or {}).get("tool_call_count"), "heartbeat_at": r["last_heartbeat_at"],
        })

    out_tasks, running, counts = [], [], {}
    for t in tasks:
        truns = by_task.get(t["id"], [])
        owners = {r["profile"] for r in truns} | ({t["assignee"]} if t["assignee"] else set())
        if department and not any(dept_of(p) == department for p in owners):
            continue
        counts[t["status"]] = counts.get(t["status"], 0) + 1
        total = {}
        for r in truns:
            if r["usage"]:
                _add(total, r["usage"])
        summary = next((r["summary"] for r in reversed(truns) if r["summary"]), None) or t["result"]
        reason = None
        if t["status"] == "blocked":
            reason = blocked.get(t["id"]) or t["last_failure_error"] or ""
            if reason == "initial_status":
                reason = "created as blocked: waiting for input or an earlier task"
            last_failed = next((r for r in reversed(truns) if r["outcome"] in failed_outcomes), None)
            if last_failed and last_failed["cause"]:
                reason = last_failed["cause_label"]
        out_tasks.append({
            "id": t["id"], "title": t["title"], "body": t["body"], "assignee": t["assignee"], "status": t["status"],
            "dept": dept_of(t["assignee"]) if t["assignee"] else "other", "created_by": t["created_by"],
            "created_at": t["created_at"], "started_at": t["started_at"], "completed_at": t["completed_at"],
            "workspace": t["workspace_path"], "failures": t["consecutive_failures"], "blocked_reason": reason,
            "summary": summary, "runs": truns, "usage": total, "parents": parents.get(t["id"], []),
            "cause": next((r["cause"] for r in reversed(truns) if r["outcome"] in failed_outcomes), None) if t["status"] == "blocked" else None,
        })
        for r in truns:
            if r["status"] == "running":
                running.append({"task_id": t["id"], "title": t["title"], **r,
                                "role": (prof.get(r["profile"]) or {}).get("role", r["profile"]),
                                "timeout": t["max_runtime_seconds"]})

    # ---- totals over every session in the range (kanban workers and direct chats) ----
    # Paperclip's agents run in the default profile; list them separately from the manager.
    paperclip, paperclip_ids = (paperclip_usage.report(sessions.get("default", []), since, _usage, _add, now)
                                if not department else (None, set()))
    total, by_profile, by_model = {}, {}, {}
    for p, rows in sessions.items():
        if not wanted(p):
            continue
        for s in rows:
            if (s["started_at"] or 0) < since:
                continue
            u = _usage(s)
            if not u["total"]:
                continue
            _add(total, u)
            row = ("paperclip", "paperclip") if p == "default" and s["id"] in paperclip_ids else (p, dept_of(p))
            _add(by_profile.setdefault(row[0], {"profile": row[0], "dept": row[1], "sessions": 0}), u)
            by_profile[row[0]]["sessions"] += 1
            _add(by_model.setdefault(s["model"] or "?", {"model": s["model"] or "?"}), u)

    direct = direct_sessions(sessions, used, prof, since, now, wanted, paperclip_ids)
    depts = [{"name": "software", "title": "Software"}]
    if engine:
        seen = {}
        for info in prof.values():
            if info["dept"] != "software":
                seen[info["dept"]] = info["dept_title"]
        depts += [{"name": n, "title": t} for n, t in sorted(seen.items())]
    installed = set(_installed_profiles())
    agents = [{"profile": p, "role": info["role"], "dept": info["dept"], "model": info.get("model"),
               "installed": p in installed, "enabled": info.get("enabled", True)} for p, info in prof.items() if wanted(p)]
    return {
        "now": now, "range": range_key, "department": department, "departments": depts, "agents": agents,
        "running": sorted(running, key=lambda r: r["started_at"] or 0),
        "direct": direct, "working": len(running) + sum(1 for d in direct if d["state"] == "working"),
        "counts": counts, "tasks": out_tasks,
        "total": total,
        "by_profile": sorted(by_profile.values(), key=lambda r: -r.get("cost", 0)),
        "by_model": sorted(by_model.values(), key=lambda r: -r.get("cost", 0)),
        "paperclip": paperclip,
    }


MAX_TEXT = 20000        # one message shown in the panel
MAX_TOOL_RESULT = 4000  # one tool result shown in the panel
QUIET_EVENTS = {"heartbeat", "claimed", "spawned", "commented"}   # noise, or shown another way


def _clip(text, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n… (%d more characters)" % (len(text) - limit)


MESSAGE_COLS = "id, session_id, role, content, tool_calls, tool_name, reasoning, timestamp"
FEED_START, FEED_STEP = 80, 300   # messages in the first reply of an agent's live feed, and at most per later reply


def _message(m: dict) -> dict:
    calls = []
    try:
        for c in json.loads(m["tool_calls"] or "[]"):
            fn = c.get("function") or {}
            calls.append({"name": fn.get("name") or "?", "arguments": _clip(fn.get("arguments"), MAX_TOOL_RESULT)})
    except (ValueError, AttributeError):
        pass
    return {"role": m["role"], "at": m["timestamp"], "tool": m["tool_name"], "calls": calls,
            "text": _clip(m["content"], MAX_TOOL_RESULT if m["role"] == "tool" else MAX_TEXT),
            "thinking": _clip(m["reasoning"], MAX_TOOL_RESULT)}


def _session_messages(profile: str, session_id: str) -> list:
    return [_message(m) for m in _rows(_state_db(profile), f"SELECT {MESSAGE_COLS} FROM messages WHERE session_id = ? ORDER BY id", (session_id,))]


def agent_feed(profile: str, after: int = 0):
    """What one agent says and does, across all its sessions, in the order it happens.

    The first call (after=0) returns its latest messages; each later call passes the last id it has and gets
    only what was written since. Every message names its session, and the session names its board task if any.
    """
    if profile not in _installed_profiles():
        return None
    db, now = _state_db(profile), time.time()
    if after > 0:
        rows = _rows(db, f"SELECT {MESSAGE_COLS} FROM messages WHERE id > ? ORDER BY id LIMIT ?", (after, FEED_STEP))
    else:
        rows = _rows(db, f"SELECT {MESSAGE_COLS} FROM messages ORDER BY id DESC LIMIT ?", (FEED_START,))[::-1]
    open_now, busy = _live(profile, now)
    sessions = {}
    if rows:
        ids = sorted({m["session_id"] for m in rows})
        marks = ",".join("?" * len(ids))
        found = {r["id"]: r for r in _rows(db, f"SELECT id, source, title, cwd, model, started_at, ended_at FROM sessions WHERE id IN ({marks})", ids)}
        # Which board task a session belongs to: the same pairing of runs and sessions as everywhere else.
        task_of, used, all_sessions = {}, set(), _sessions(profile, 0)
        titles = {t["id"]: t["title"] for t in _rows(os.path.join(hermes_home(), "kanban.db"), "SELECT id, title FROM tasks")}
        for r in _rows(os.path.join(hermes_home(), "kanban.db"),
                       "SELECT id, task_id, profile, started_at, ended_at FROM task_runs WHERE profile = ? ORDER BY started_at", (profile,)):
            match = _match(r, all_sessions, used)
            if match:
                task_of[match["id"]] = r["task_id"]
        for sid in ids:
            x = found.get(sid) or {}
            task = task_of.get(sid)
            sessions[sid] = {"id": sid, "title": titles.get(task) or x.get("title") or "", "task_id": task, "source": x.get("source") or "",
                             "cwd": x.get("cwd") or "", "model": x.get("model") or "", "started_at": x.get("started_at"),
                             "state": "working" if sid in busy else "open" if sid in open_now and not x.get("ended_at") else "ended"}
    return {"profile": profile, "messages": [dict(_message(m), id=m["id"], session_id=m["session_id"]) for m in rows],
            "sessions": sessions, "last": rows[-1]["id"] if rows else after, "working": bool(busy), "now": now}


def conversation(task_id: str):
    """Everything said and done on one task, oldest first: comments, board events, and every run's messages."""
    kdb = os.path.join(hermes_home(), "kanban.db")
    task = _rows(kdb, "SELECT id, title, body, assignee, status FROM tasks WHERE id = ?", (task_id,))
    if not task:
        return None
    # Pair runs with sessions the same way the activity snapshot does: over the whole board, in start order.
    sessions, used, items = {}, set(), []
    for r in _rows(kdb, "SELECT id, task_id, profile, status, outcome, started_at, ended_at, summary, error FROM task_runs ORDER BY started_at"):
        if r["profile"] not in sessions:
            sessions[r["profile"]] = _sessions(r["profile"], 0)
        s = _match(r, sessions[r["profile"]], used)
        if r["task_id"] != task_id:
            continue
        items.append({"kind": "run", "at": r["started_at"], "run_id": r["id"], "profile": r["profile"], "status": r["status"],
                      "outcome": r["outcome"], "ended_at": r["ended_at"], "summary": r["summary"], "error": r["error"],
                      "model": (s or {}).get("model"), "session_id": (s or {}).get("id"),
                      "messages": _session_messages(r["profile"], s["id"]) if s else []})
    for c in _rows(kdb, "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (task_id,)):
        items.append({"kind": "comment", "at": c["created_at"], "author": c["author"], "text": c["body"]})
    for e in _rows(kdb, "SELECT kind, payload, run_id, created_at FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)):
        if e["kind"] in QUIET_EVENTS:
            continue
        try:
            payload = json.loads(e["payload"] or "{}") or {}
        except ValueError:
            payload = {}
        note = payload.get("reason") or payload.get("error") or payload.get("summary") or ""
        items.append({"kind": "event", "at": e["created_at"], "event": e["kind"], "run_id": e["run_id"], "text": _clip(str(note), 600)})
    order = {"run": 0, "event": 1, "comment": 2}
    items.sort(key=lambda i: (i["at"] or 0, order[i["kind"]]))
    return {"task": task[0], "items": items, "now": time.time()}


def session_conversation(profile: str, session_id: str):
    """One direct session (hermes in a folder, panel chat, Paperclip) in the same shape as a task's conversation."""
    if profile not in _installed_profiles():
        return None
    rows = _rows(_state_db(profile), "SELECT id, title, model, cwd, started_at, ended_at FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        return None
    s, now = rows[0], time.time()
    open_now, busy = _live(profile, now)
    state = "ended" if s["ended_at"] else "working" if s["id"] in busy else "open" if s["id"] in open_now else "ended"
    info = profiles().get(profile) or {}
    run = {"kind": "run", "at": s["started_at"], "profile": info.get("role", profile), "model": s["model"], "session_id": s["id"],
           "status": "running" if state == "working" else "done", "outcome": "", "ended_at": s["ended_at"], "summary": None, "error": None,
           "messages": _session_messages(profile, s["id"])}
    return {"session": {"id": s["id"], "profile": profile, "role": info.get("role", profile), "title": s["title"] or "",
                        "cwd": s["cwd"] or "", "state": state}, "items": [run], "now": now}


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/api/sessions/{profile}/{session_id}/conversation")
    def direct_conversation(profile: str, session_id: str):
        ok = re.fullmatch(r"[\w-]{1,64}", profile) and re.fullmatch(r"[\w-]{3,64}", session_id)
        data = session_conversation(profile, session_id) if ok else None
        if not data:
            raise HTTPException(404, "unknown session")
        return data

    @router.get("/api/agents/{profile}/feed")
    def feed(profile: str, after: int = 0):
        data = agent_feed(profile, max(0, after)) if re.fullmatch(r"[\w-]{1,64}", profile) else None
        if data is None:
            raise HTTPException(404, "unknown agent")
        return data

    @router.get("/api/tasks/{task_id}/conversation")
    def task_conversation(task_id: str):
        data = conversation(task_id) if re.fullmatch(r"t_[0-9a-f]{4,32}", task_id) else None
        if not data:
            raise HTTPException(404, "unknown task")
        return data

    @router.get("/api/activity")
    def activity(range: str = "today", department: str = ""):
        return snapshot(range, department)

    @router.get("/api/credits")
    def credits(refresh: bool = False):
        return causes.credits(0 if refresh else 60)

    return router
