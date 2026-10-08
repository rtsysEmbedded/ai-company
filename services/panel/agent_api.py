"""Work with one agent from the panel: switch it on or off, chat with it, or give it a task.

- Enabled/disabled is stored where the agent is defined (team.yaml for software, department.json
  for the other departments). A disabled agent is left out of the lead's team list, cannot be
  chatted with or given tasks here, and its waiting tasks are put on hold by scripts/escalate.py.
- Chat runs `hermes -p <profile> chat -q ... -Q` and continues the same session with --resume.
  The agent works directly, with its own tools; the manager and the task board are not involved.
- A task is one kanban card assigned to exactly that agent.
- Retry puts a blocked task back in the queue (`hermes kanban unblock`); the dispatcher starts it again.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import activity_api
import budget_api
import causes
import projects_api

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEAM = os.path.join(ROOT, "team.yaml")
UV = os.environ.get("UV_BIN", "uv")
CHATS = os.path.join(os.environ.get("PANEL_DATA") or os.path.join(ROOT, "data"), "panel_chats")
CHAT_BUDGET = 1800          # seconds one chat turn may run
MAX_MESSAGE = 20000
JOBS: dict = {}             # job id -> state; chat turns run in background threads
_busy: dict = {}            # profile -> job id (one chat turn per agent at a time)
_lock = threading.Lock()


def _agent(profile: str) -> dict:
    info = activity_api.profiles().get(profile)
    if not info:
        raise HTTPException(404, f"unknown agent: {profile}")
    return info


# A task for a lead is a request to plan and hand out the work, not to do it alone.
LEAD_TASK = ("\n\nThis task comes from the user, for you as the lead. Do not do the specialist work yourself. Plan it, "
             "write your decisions into the project folder, then create one task per specialist with kanban_create "
             "(same workspace folder, parents in the order of work), as your rules describe. The last task you create is "
             "one for yourself, titled '... check and report', with all the other tasks as parents. Then finish with "
             "kanban_complete and the plan: the task ids, who does what, in which order. If something that changes the "
             "plan is unclear, use kanban_block with one precise question; the user answers it on the task. If this task starts "
             "with a GOAL line, copy that line to the top of every task you create, so each specialist knows why the work exists.")


def goal_block(goal: dict) -> str:
    """Why the work exists: the project's goal, put at the top of the task so that it travels with it."""
    return "GOAL (project %s): %s\n\n" % (goal["name"], goal["goal"])


def _is_lead(info: dict) -> bool:
    if info["dept"] == "software":
        return info["role"] in ("manager", (_team().get("routing") or {}).get("lead"))
    try:
        return (activity_api.engine.load_config(info["dept"]).get("routing") or {}).get("lead") == info["role"]
    except Exception:
        return False


def _blocked_task(task_id: str) -> dict:
    """The task, if it exists, is blocked and its agent can run; otherwise the matching HTTP error."""
    if not re.fullmatch(r"t_[0-9a-f]{4,32}", task_id):
        raise HTTPException(400, "invalid task id")
    rows = activity_api._rows(os.path.join(activity_api.hermes_home(), "kanban.db"),
                              "SELECT status, assignee FROM tasks WHERE id = ?", (task_id,))
    if not rows:
        raise HTTPException(404, "unknown task")
    if rows[0]["status"] != "blocked":
        raise HTTPException(400, "Only a blocked task can be continued; this one is %s" % rows[0]["status"])
    assignee = rows[0]["assignee"]
    if assignee and assignee in activity_api.profiles() and not is_enabled(assignee):
        raise HTTPException(400, "%s is disabled. Enable the agent first." % assignee)
    if assignee and assignee in activity_api.profiles():
        try:
            budget_api.check(assignee)
        except HTTPException as exc:
            raise HTTPException(400, exc.detail)
    return rows[0]


def _kanban(*args: str):
    r = subprocess.run(["hermes", "kanban", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise HTTPException(500, (r.stderr or r.stdout or "hermes kanban %s failed" % args[0])[-300:])


def _team() -> dict:
    return yaml.safe_load(open(TEAM)) or {}


def is_enabled(profile: str) -> bool:
    info = _agent(profile)
    if info["dept"] == "software":
        return bool((_team().get("roles") or {}).get(info["role"], {}).get("enabled", True))
    try:
        cfg = activity_api.engine.load_config(info["dept"])
        return bool((cfg.get("roles") or {}).get(info["role"], {}).get("enabled", True))
    except Exception:
        return True


def _require_ready(profile: str):
    if not is_enabled(profile):
        raise HTTPException(409, f"{profile} is disabled. Enable it first.")
    budget_api.check(profile)
    home = activity_api.hermes_home()
    if profile != "default" and not os.path.isdir(os.path.join(home, "profiles", profile)):
        raise HTTPException(409, f"{profile} has no Hermes profile yet. Apply its department first.")


def _folder(path: str) -> str:
    """An existing folder inside the user's home, or '' for none."""
    path = (path or "").strip()
    if not path:
        return ""
    full = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    if not os.path.isdir(full) or not (full == home or full.startswith(home + os.sep)):
        raise HTTPException(400, f"not a folder inside your home directory: {path}")
    return full


def _hermes(profile: str) -> list:
    return ["hermes"] if profile == "default" else ["hermes", "-p", profile]


# ---------- enable / disable ----------
class EnabledIn(BaseModel):
    enabled: bool


def set_enabled(profile: str, enabled: bool) -> dict:
    info = _agent(profile)
    role, dept, note = info["role"], info["dept"], ""
    if dept == "software":
        team = _team()
        routing = team.get("routing") or {}
        if not enabled:
            if role == "manager":
                raise HTTPException(400, "The manager cannot be disabled: it runs the task board.")
            used = [k for k in ("lead", "reviewer", "security_profile", "escalate_to", "default_assignee") if routing.get(k) == role]
            if used:
                raise HTTPException(400, f"{role} is used by the routing rules ({', '.join(used)}). Change them under Software > Routing first.")
        from ruamel.yaml import YAML
        ry = YAML(); ry.preserve_quotes = True; ry.width = 4096
        doc = ry.load(open(TEAM))
        spec = doc["roles"][role]
        if enabled:
            spec.pop("enabled", None)   # enabled is the default; keep the file tidy
        else:
            spec["enabled"] = False
        with open(TEAM, "w") as f:
            ry.dump(doc, f)
        # Only the team lists of the manager and the lead change; models and keys are not touched.
        r = subprocess.run([UV, "run", "--project", ROOT, "-q", "python", os.path.join(ROOT, "scripts", "apply_team.py"),
                            "--only", "manager", str(routing.get("lead") or "manager"), "--soul-only"], capture_output=True, text=True, cwd=ROOT)
        note = "The team list was updated." if r.returncode == 0 else f"Saved, but updating the team list failed: {(r.stderr or r.stdout)[-200:]}"
    else:
        engine = activity_api.engine
        cfg = engine.load_config(dept)
        lead = (cfg.get("routing") or {}).get("lead")
        if not enabled and role == lead:
            raise HTTPException(400, f"{role} is the lead of this department and cannot be disabled.")
        cfg["roles"][role]["enabled"] = bool(enabled)
        engine.save_config(dept, cfg)
        note = "Saved. Press Apply in this department so its lead learns the change."
    return {"profile": profile, "enabled": enabled, "note": note}


# ---------- chat ----------
class ChatIn(BaseModel):
    message: str
    session_id: str = ""
    folder: str = ""


def _session_usage(profile: str, session_id: str) -> dict:
    rows = activity_api._rows(activity_api._state_db(profile),
                              "SELECT model, last_activity_description, last_activity_at, tool_call_count, api_call_count, "
                              "estimated_cost_usd, actual_cost_usd, " + ", ".join(activity_api.TOKEN_COLS) +
                              " FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        return {}
    r = rows[0]
    return {"model": r["model"], "activity": r["last_activity_description"], "activity_at": r["last_activity_at"],
            "tool_calls": r["tool_call_count"], "usage": activity_api._usage(r)}


def _chat_file(profile: str, session_id: str) -> str:
    return os.path.join(CHATS, profile, f"{session_id}.jsonl")


def _remember(profile: str, session_id: str, *entries: dict):
    """The panel keeps its own transcript: Hermes does not reliably store the reply of a resumed quiet run."""
    if not session_id:
        return
    path = _chat_file(profile, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _messages(profile: str, session_id: str) -> list:
    path = _chat_file(profile, session_id)
    if not os.path.isfile(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _chat_list(profile: str) -> list:
    folder = os.path.join(CHATS, profile)
    if not os.path.isdir(folder):
        return []
    items = []
    for name in os.listdir(folder):
        if not name.endswith(".jsonl"):
            continue
        path, msgs = os.path.join(folder, name), _messages(profile, name[:-6])
        first = next((m["text"] for m in msgs if m.get("role") == "user"), "")
        items.append({"id": name[:-6], "title": " ".join(first.split())[:70], "updated_at": os.path.getmtime(path), "messages": len(msgs)})
    return sorted(items, key=lambda i: -i["updated_at"])[:30]


def _newest_panel_session(profile: str, since: float) -> str:
    rows = activity_api._rows(activity_api._state_db(profile),
                              "SELECT id FROM sessions WHERE source = 'panel' AND started_at >= ? ORDER BY started_at DESC LIMIT 1",
                              (since,))
    return rows[0]["id"] if rows else ""


def _run_chat(job: dict):
    profile = job["profile"]
    cmd = _hermes(profile) + ["chat", "-q", job["message"], "-Q", "--source", "panel", "--run-budget", str(CHAT_BUDGET)]
    if job["session_id"]:
        cmd += ["--resume", job["session_id"]]
    if job["folder"]:
        cmd += ["--in", job["folder"]]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, cwd=job["folder"] or os.path.expanduser("~"))
        job["pid"] = proc.pid
        out, _ = proc.communicate(timeout=CHAT_BUDGET + 120)
        m = re.search(r"^session_id:\s*(\S+)", out or "", re.M)
        sid = (m.group(1) if m else "") or job["session_id"] or _newest_panel_session(profile, job["started_at"] - 2)
        job["session_id"] = sid
        reply = (out[m.end():] if m else out or "").strip()
        cause = next((label for name, pattern in activity_api.causes.PATTERNS if pattern.search(out or "")
                      for label in [activity_api.causes.LABELS[name]]), None) if proc.returncode != 0 or not reply else None
        job.update(state="error" if cause or proc.returncode != 0 else "done",
                   reply=reply, error=cause or (None if proc.returncode == 0 else (out or "")[-600:].strip() or "the agent exited with an error"))
    except subprocess.TimeoutExpired:
        proc.kill()
        job.update(state="error", error=f"stopped after {CHAT_BUDGET // 60} minutes")
    except Exception as exc:
        job.update(state="error", error=str(exc)[:300])
    finally:
        job["ended_at"] = time.time()
        _remember(profile, job.get("session_id") or "", {"role": "user", "text": job["message"], "at": job["started_at"]},
                  {"role": "assistant", "text": job.get("reply") or "", "error": job.get("error"), "at": job["ended_at"],
                   "folder": job.get("folder") or ""})
        with _lock:
            if _busy.get(profile) == job["id"]:
                _busy.pop(profile, None)


def _job_view(job: dict) -> dict:
    view = {k: job.get(k) for k in ("id", "profile", "state", "session_id", "reply", "error", "started_at", "ended_at")}
    sid = job.get("session_id") or (_newest_panel_session(job["profile"], job["started_at"] - 2) if job["state"] == "running" else "")
    if sid:
        view["session_id"] = sid
        view.update(_session_usage(job["profile"], sid))
    return view


# ---------- direct task ----------
class TaskIn(BaseModel):
    title: str
    body: str = ""
    folder: str = ""
    review: bool = False
    timeout_minutes: int = 45


class AnswerIn(BaseModel):
    text: str


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.post("/api/agents/{profile}/enabled")
    def enabled(profile: str, body: EnabledIn):
        return set_enabled(profile, body.enabled)

    @router.post("/api/agents/{profile}/chat")
    def chat(profile: str, body: ChatIn):
        _require_ready(profile)
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "empty message")
        if len(message) > MAX_MESSAGE:
            raise HTTPException(400, f"message too long (max {MAX_MESSAGE} characters)")
        if body.session_id and not re.fullmatch(r"[\w-]{6,64}", body.session_id):
            raise HTTPException(400, "invalid session id")
        folder = _folder(body.folder)
        if body.session_id and body.session_id in activity_api._live(profile, time.time())[0]:
            # Another Hermes has this session open and keeps its own copy of the conversation; a second writer would split it.
            raise HTTPException(409, "This session is open in a terminal. Answer it there, or close it and then continue here.")
        with _lock:
            if profile in _busy:
                raise HTTPException(409, f"{profile} is still answering your previous message.")
            job = {"id": uuid.uuid4().hex[:12], "profile": profile, "state": "running", "message": message,
                   "session_id": body.session_id, "folder": folder, "started_at": time.time(), "reply": None, "error": None}
            JOBS[job["id"]] = job
            _busy[profile] = job["id"]
            for old in sorted(JOBS.values(), key=lambda j: j["started_at"])[:-50]:
                if old["state"] != "running":
                    JOBS.pop(old["id"], None)
        threading.Thread(target=_run_chat, args=(job,), daemon=True).start()
        return _job_view(job)

    @router.get("/api/agents/chat/{job_id}")
    def chat_job(job_id: str):
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "unknown chat job (the panel may have restarted)")
        return _job_view(job)

    @router.post("/api/agents/chat/{job_id}/stop")
    def chat_stop(job_id: str):
        job = JOBS.get(job_id)
        if not job or job["state"] != "running" or not job.get("pid"):
            raise HTTPException(404, "nothing to stop")
        try:
            os.kill(job["pid"], 15)
        except OSError:
            pass
        return {"ok": True}

    @router.get("/api/agents/{profile}/sessions")
    def sessions(profile: str):
        _agent(profile)
        return {"sessions": _chat_list(profile), "running_job": _busy.get(profile), "enabled": is_enabled(profile)}

    @router.get("/api/agents/{profile}/sessions/{session_id}")
    def session(profile: str, session_id: str):
        _agent(profile)
        if not re.fullmatch(r"[\w-]{6,64}", session_id):
            raise HTTPException(400, "invalid session id")
        return {"messages": _messages(profile, session_id), **_session_usage(profile, session_id)}

    @router.post("/api/agents/{profile}/task")
    def task(profile: str, body: TaskIn):
        _require_ready(profile)
        info = _agent(profile)
        title = " ".join(body.title.split())[:160]
        if not title:
            raise HTTPException(400, "give the task a title")
        folder = _folder(body.folder)
        text = body.body.strip()[:MAX_MESSAGE]
        goal = projects_api.goal_for(folder)
        if goal.get("goal"):
            text = goal_block(goal) + text
        routing = _team().get("routing") or {}
        reviewer = routing.get("reviewer", "reviewer")
        wants_review = body.review and info["dept"] == "software" and profile != reviewer
        if _is_lead(info):
            text += LEAD_TASK
        elif wants_review:
            text += f"\n\nWhen done, call kanban_request_review with reviewer={reviewer}."
        else:
            text += "\n\nThis is a direct task from the user. No review is needed."
        cmd = ["hermes", "kanban", "create", title, "--assignee", profile, "--body", text.strip(), "--created-by", "panel",
               "--max-runtime", f"{max(5, min(body.timeout_minutes, 240))}m"]
        # Not with a review line in the body: the loop's judge reads the body as the goal and refuses the review call it asks for.
        if not _is_lead(info) and not wants_review:   # Hermes' goal loop: a specialist that stops early is told to go on and to close the task
            cmd += ["--goal", "--goal-max-turns", str(getattr(activity_api.engine, "GOAL_TURNS", 6))]
        if folder:
            cmd += ["--workspace", f"dir:{folder}"]
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        m = re.search(r"\b(t_[0-9a-f]+)\b", r.stdout or "")
        if r.returncode != 0 or not m:
            raise HTTPException(500, (r.stderr or r.stdout or "could not create the task")[-300:])
        return {"task_id": m.group(1), "assignee": profile}

    @router.post("/api/tasks/{task_id}/retry")
    def retry(task_id: str):
        assignee = _blocked_task(task_id)["assignee"]
        _kanban("unblock", task_id, "--reason", "retry from the panel")
        # The last run's real cause: if the provider refused the call, the retry fails the same way until that is fixed.
        cause = causes.run_causes(task_id).get("_last")
        return {"task_id": task_id, "assignee": assignee, "warning": causes.LABELS.get(cause)}

    @router.post("/api/tasks/{task_id}/archive")
    def archive(task_id: str):
        """Take a task off the board (Hermes keeps it in its archive). Not while an agent is working on it."""
        if not re.fullmatch(r"t_[0-9a-f]{4,32}", task_id):
            raise HTTPException(400, "invalid task id")
        rows = activity_api._rows(os.path.join(activity_api.hermes_home(), "kanban.db"),
                                  "SELECT status FROM tasks WHERE id = ?", (task_id,))
        if not rows:
            raise HTTPException(404, "unknown task")
        if rows[0]["status"] == "running":
            raise HTTPException(400, "An agent is working on this task right now. Wait until it stops.")
        _kanban("archive", task_id)
        return {"task_id": task_id, "archived": True}

    @router.post("/api/tasks/{task_id}/answer")
    def answer(task_id: str, body: AnswerIn):
        """The user's reply to a blocked task: stored as a comment, which the agent reads on its next run."""
        text = body.text.strip()[:MAX_MESSAGE]
        if not text:
            raise HTTPException(400, "write an answer first")
        assignee = _blocked_task(task_id)["assignee"]
        _kanban("comment", task_id, "--author", "user", "--", text)
        _kanban("unblock", task_id)
        return {"task_id": task_id, "assignee": assignee}

    return router
