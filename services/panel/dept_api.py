"""Departments in the control panel: the routes that drive departments/engine/dept.py.

The software department keeps its own routes in server.py (team.yaml, policies/). Every other
department is a folder under departments/ and is configured through the engine, the same
functions `./dept` uses. Keys can be written and removed here but are never sent to the browser.
"""
import json
import os
import re
import subprocess
import sys
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import key_usage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "departments", "engine"))
import dept as engine  # noqa: E402

ROLE_FIELDS = ("title", "enabled", "model", "fallback", "effort", "tools", "needs", "description")
ROUTING_FIELDS = ("lead", "reviewer", "review", "review_scope", "task_timeout")
RULE_FOLDERS = ("policies", "roles")
MAX_RULES = 20000
_apply_lock = threading.Lock()


def _config(name: str) -> dict:
    try:
        return engine.load_config(name)
    except engine.ConfigError as exc:
        raise HTTPException(404, str(exc))


def _models(department=None, refresh=False) -> dict:
    data = engine.load_models(refresh=refresh)
    usable = [m for m in data["models"].values() if m["tools"] and m["price_in"] is not None and m["price_in"] >= 0]
    usable.sort(key=lambda m: m["id"])
    return {"fetched_at": data.get("fetched_at", 0), "stale_reason": data.get("stale_reason"),
            "models": usable, "benchmarks": engine.load_benchmarks(department)}


def _state(name: str) -> dict:
    config = _config(name)
    errors, warnings = engine.validate(name, config, engine.load_models().get("models") or None)
    presets, folder = {}, os.path.join(engine.department_dir(name), "presets")
    for filename in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        preset = engine.read_json(os.path.join(folder, filename)) if filename.endswith(".json") else None
        if preset:
            presets[filename[:-5]] = preset
    return {"department": name, "config": config, "status": engine.status(name, config), "errors": errors,
            "warnings": warnings, "presets": presets,
            "choices": {"efforts": engine.EFFORTS, "review": engine.REVIEW_MODES}}


def _clean_config(submitted: dict, current: dict) -> dict:
    """The config to save, built from what the browser sent: known fields only, existing roles only."""
    if not isinstance(submitted, dict) or not isinstance(submitted.get("roles"), dict):
        raise HTTPException(400, "The request did not contain a configuration")
    config = dict(current)
    config["projects_dir"] = str(submitted.get("projects_dir") or "").strip()
    routing = dict(current.get("routing") or {})
    for key in ROUTING_FIELDS:
        if key in (submitted.get("routing") or {}):
            routing[key] = str(submitted["routing"][key]).strip()
    config["routing"] = routing
    roles = {}
    for name, old in current["roles"].items():
        new, role = submitted["roles"].get(name), dict(old)
        for field in ROLE_FIELDS if isinstance(new, dict) else ():
            if field not in new:
                continue
            value = new[field]
            if field == "enabled":
                role[field] = bool(value)
            elif field in ("tools", "needs"):
                role[field] = [str(v) for v in value] if isinstance(value, list) else []
            else:
                role[field] = str(value).strip()
        roles[name] = role
    config["roles"] = roles
    return config


def _save(name: str, config: dict):
    errors, _ = engine.validate(name, config, engine.load_models().get("models") or None)
    if errors:
        raise HTTPException(400, "Not saved: " + "; ".join(errors))
    engine.save_config(name, config)


def _rules_path(name: str, rel: str) -> str:
    """A rules file of a department: policies/<file>.md, or roles/<role>.md for one of its roles."""
    config = _config(name)
    match = re.fullmatch(r"(policies|roles)/([\w.-]+)\.md", rel or "")
    if not match or match.group(2).startswith("."):
        raise HTTPException(400, "invalid rules path")
    if match.group(1) == "roles" and match.group(2) not in config["roles"]:
        raise HTTPException(400, "there is no role %s in this department" % match.group(2))
    return os.path.join(engine.department_dir(name), match.group(1), match.group(2) + ".md")


class NewDepartmentIn(BaseModel):
    name: str
    title: str
    prefix: str


class ConfigIn(BaseModel):
    config: dict


class KeyIn(BaseModel):
    scope: str = "department"   # shared | department | role
    role: str = ""
    key: str = ""
    remove: bool = False
    source: str = ""            # import the key from this source instead (see key_sources)


class RoleIn(BaseModel):
    role: str = ""
    title: str = ""


class TextIn(BaseModel):
    text: str


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/api/departments")
    def list_departments():
        return {"departments": engine.overview(), "environment": engine.environment_status()}

    @router.post("/api/departments")
    def new_department(body: NewDepartmentIn):
        try:
            engine.new_department(body.name.strip(), body.title, body.prefix.strip())
        except engine.ConfigError as exc:
            raise HTTPException(400, str(exc))
        return {"created": body.name.strip()}

    @router.get("/api/dept-models")
    def department_models(dept: str = "", refresh: bool = False):
        return _models(dept if dept in engine.list_departments() else None, refresh)

    @router.get("/api/departments/{name}")
    def get_department(name: str):
        return _state(name)

    @router.put("/api/departments/{name}/config")
    def put_config(name: str, body: ConfigIn):
        _save(name, _clean_config(body.config, _config(name)))
        return _state(name)

    @router.post("/api/departments/{name}/roles")
    def add_role(name: str, body: RoleIn):
        config, role = _config(name), body.role.strip()
        if role in config["roles"]:
            raise HTTPException(400, "This department already has a role %s" % role)
        title = body.title.strip() or role
        config["roles"][role] = {
            "title": title, "enabled": True, "model": "google/gemini-3.8-flash", "fallback": "anthropic/claude-sonnet-5.5",
            "effort": "medium", "tools": [], "needs": ["tools"],
            "description": "%s. Replace this with what the role is good at; the lead reads it to decide who gets a task." % title}
        _save(name, config)
        path = os.path.join(engine.department_dir(name), "roles", role + ".md")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# Role: %s\n<!-- How this role does its work: the steps, the quality bar, the mistakes to avoid. -->\n" % title)
        return _state(name)

    @router.delete("/api/departments/{name}/roles/{role}")
    def delete_role(name: str, role: str):
        config = _config(name)
        if role not in config["roles"]:
            raise HTTPException(404, "unknown role")
        routing = config.get("routing") or {}
        if role in (routing.get("lead"), routing.get("reviewer")):
            raise HTTPException(400, "%s is the lead or the reviewer - choose another one in Routing first" % role)
        del config["roles"][role]
        _save(name, config)
        rules = os.path.join(engine.department_dir(name), "roles", role + ".md")
        if os.path.isfile(rules):
            os.remove(rules)
        engine.set_env_value(engine.ENV_PATH, engine.key_var(role), None)
        return dict(_state(name), profile_left=os.path.isdir(engine.profile_dir(role)))

    @router.post("/api/departments/{name}/key")
    def set_key(name: str, body: KeyIn):
        config = _config(name)
        if body.scope == "shared":
            scope = None
        elif body.scope == "role":
            if body.role not in config["roles"]:
                raise HTTPException(400, "unknown role")
            scope = body.role
        else:
            scope = name
        try:
            if body.remove:
                engine.set_env_value(engine.ENV_PATH, engine.key_var(scope), None)
            elif body.source:
                engine.import_key(body.source, scope)
            else:
                key = body.key.strip()
                if len(key) < 20 or any(c.isspace() for c in key):
                    raise HTTPException(400, "That does not look like an API key")
                engine.set_env_value(engine.ENV_PATH, engine.key_var(scope), key)
        except engine.ConfigError as exc:
            raise HTTPException(400, str(exc))
        # Put the change into the agents' profiles straight away, so no separate apply is needed.
        targets = engine.list_departments() if scope is None else [name]
        written = sum(engine.sync_keys(target, engine.load_config(target)) for target in targets)
        return dict(_state(name), profiles_updated=written)

    @router.get("/api/departments/{name}/key-usage")
    def key_usage_of(name: str):
        """Spend and limit of the key each enabled role uses, as OpenRouter reports it. Never the key itself."""
        config, env = _config(name), engine.read_env(engine.ENV_PATH)
        return key_usage.usage({role: engine.key_for(role, name, env) for role, spec in config["roles"].items() if spec.get("enabled", True)})

    @router.post("/api/departments/{name}/apply")
    def apply(name: str):
        config = _config(name)
        if not _apply_lock.acquire(blocking=False):
            raise HTTPException(409, "An apply is already running")
        try:
            lines = []
            ok = engine.apply(name, config, log=lines.append)
        finally:
            _apply_lock.release()
        return {"ok": ok, "log": "\n".join(lines), "state": _state(name)}

    @router.post("/api/departments/{name}/test")
    def test_role(name: str, body: RoleIn):
        config = _config(name)
        if body.role not in config["roles"]:
            raise HTTPException(400, "unknown role")
        ok, message = engine.test_model(config["roles"][body.role]["model"], engine.key_for(body.role, name))
        return {"ok": ok, "message": message}

    @router.get("/api/departments/{name}/rules")
    def list_rules(name: str):
        config, folder, out = _config(name), engine.department_dir(name), []
        policies = os.path.join(folder, "policies")
        for filename in sorted(os.listdir(policies)) if os.path.isdir(policies) else []:
            if filename.endswith(".md") and not filename.startswith("."):
                out.append({"path": "policies/" + filename, "chars": os.path.getsize(os.path.join(policies, filename)),
                            "kind": "Whole department"})
        for role in config["roles"]:
            path = os.path.join(folder, "roles", role + ".md")
            out.append({"path": "roles/%s.md" % role, "chars": os.path.getsize(path) if os.path.isfile(path) else 0, "kind": "One role"})
        return out

    @router.get("/api/departments/{name}/rules/{rel:path}")
    def get_rules(name: str, rel: str):
        return {"path": rel, "text": engine.read_text(_rules_path(name, rel))}

    @router.put("/api/departments/{name}/rules/{rel:path}")
    def put_rules(name: str, rel: str, body: TextIn):
        if len(body.text) > MAX_RULES:
            raise HTTPException(400, "rules too long; move long material to the knowledge base")
        path = _rules_path(name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body.text.rstrip() + "\n")
        return {"ok": True}

    @router.get("/api/departments/{name}/board")
    def board(name: str):
        roles = set(_config(name)["roles"])
        result = subprocess.run([engine.hermes_bin() or "hermes", "kanban", "list", "--json", "--sort", "updated"],
                                capture_output=True, text=True)
        try:
            tasks = json.loads(result.stdout or "[]")
        except ValueError:
            tasks = []
        keep = ("id", "title", "assignee", "status", "priority", "created_at", "completed_at")
        mine = [{k: t.get(k) for k in keep} for t in tasks if isinstance(t, dict) and t.get("assignee") in roles]
        return {"dispatcher_running": engine.dispatcher_running(), "tasks": mine[:100]}

    return router

