"""The software department behind the same routes as the engine's departments.

The software team is still defined by team.yaml and policies/, and applied by scripts/apply_team.py.
These routes present it the way dept_api presents a department (/api/departments/software/...), so
the panel has one set of tabs for every department. What the software team has and the others do
not (security review, escalation, the dispatcher's limits) is described in `routing_extra`, and
what it cannot do is listed in `capabilities`. Saving writes team.yaml and keeps its comments;
nothing reaches Hermes until Apply.
"""
import json
import os
import re
import subprocess
import sys

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import key_usage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "departments", "engine"))
import dept as engine  # noqa: E402

SOFTWARE_ROOT = os.environ.get("PANEL_SOFTWARE_ROOT") or ROOT
TEAM = os.path.join(SOFTWARE_ROOT, "team.yaml")
ENV = os.path.join(SOFTWARE_ROOT, ".env")
POLICIES = os.path.join(SOFTWARE_ROOT, "policies")
EFFORTS = ("low", "medium", "high")
ROLE_FIELDS = ("title", "enabled", "model", "fallback", "effort", "description", "needs")
LEAD = "manager"
MAX_RULES = 20000
REVIEW_TO_FILE = {"changes": "code", "all": "all", "off": "off"}   # the engine's word -> team.yaml's word
REVIEW_FROM_FILE = {v: k for k, v in REVIEW_TO_FILE.items()}

# The routing settings only this department has, drawn by the Routing tab below the common ones.
ROUTING_EXTRA = [
    {"key": "security_review", "label": "Security review", "type": "choice",
     "choices": [["sensitive", "sensitive code only"], ["always", "every change"], ["off", "off"]],
     "help": "sensitive = authentication, untrusted input, crypto, network endpoints, dependencies"},
    {"key": "security_profile", "label": "Security reviewer", "type": "role", "help": ""},
    {"key": "escalate_after", "label": "Escalate after", "type": "int", "min": 1, "max": 10, "help": "failed runs or rejected reviews"},
    {"key": "escalate_to", "label": "Escalate to", "type": "role", "help": "gets a task that keeps failing"},
    {"key": "default_assignee", "label": "Default assignee", "type": "role", "help": "when no role clearly fits"},
    {"key": "max_parallel", "label": "Max agents at once", "type": "int", "min": 1, "max": 9, "help": "whole machine, all departments together"},
    {"key": "max_per_profile", "label": "Max per role", "type": "int", "min": 1, "max": 5, "help": "agents of one role at the same time"},
    {"key": "dispatch_interval_seconds", "label": "Check interval (s)", "type": "int", "min": 5, "max": 600,
     "help": "how often the dispatcher looks for ready tasks"},
    {"key": "planner_model", "label": "Planner model", "type": "model", "help": "cheap model that splits work into tasks"},
]
CAPABILITIES = {"tools": "fixed", "projects_dir": False, "review_scope": False, "lead": False, "shared_key": False, "history": True}


def _team() -> dict:
    with open(TEAM, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _edit_team(change):
    """Load team.yaml keeping comments and order, let `change(doc)` edit it, write it back in one step."""
    from ruamel.yaml import YAML
    ry = YAML(); ry.preserve_quotes = True; ry.width = 4096
    with open(TEAM, encoding="utf-8") as handle:
        doc = ry.load(handle)
    change(doc)
    tmp = TEAM + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        ry.dump(doc, handle)
    os.replace(tmp, TEAM)


def _profile(role: str, spec: dict) -> str:
    return spec.get("hermes_profile") or role


def _actual(profile: str) -> dict:
    """What the Hermes profile has now, read from its config file (read-only)."""
    home = engine.hermes_home()
    path = os.path.join(home, "config.yaml") if profile == "default" else os.path.join(home, "profiles", profile, "config.yaml")
    if not os.path.isfile(path):
        return {"exists": False}
    text = engine.read_text(path)
    return {"exists": True,
            "model": engine._yaml_value(engine._yaml_block(text, "model"), "default"),
            "fallback": engine._yaml_value(engine._yaml_block(text, "fallback_providers"), "model"),
            "effort": engine._yaml_value(engine._yaml_block(text, "agent"), "reasoning_effort")}


def _differences(spec: dict, actual: dict) -> list:
    if not actual["exists"]:
        return ["profile does not exist yet"]
    diffs = []
    if actual["model"] != spec.get("model"):
        diffs.append("model is %s, should be %s" % (actual["model"] or "unset", spec.get("model")))
    if (actual["fallback"] or "") != (spec.get("fallback") or ""):
        diffs.append("fallback is %s, should be %s" % (actual["fallback"] or "unset", spec.get("fallback") or "none"))
    if actual["effort"] != spec.get("effort", "medium"):
        diffs.append("effort is %s, should be %s" % (actual["effort"] or "unset", spec.get("effort", "medium")))
    return diffs


def _pending() -> bool:
    """True when team.yaml or the policies have changes that were not applied (they are committed on Apply)."""
    def git(*args):
        return subprocess.run(["git", "-C", SOFTWARE_ROOT, *args], capture_output=True, text=True)
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return False
    return bool(git("diff", "--", "team.yaml", "policies").stdout.strip()
                or git("ls-files", "--others", "--exclude-standard", "policies").stdout.strip())


def state() -> dict:
    team, env = _team(), engine.read_env(ENV)
    models = engine.load_models().get("models") or {}
    routing = dict(team.get("routing") or {})
    roles, status, warnings = {}, {}, []
    for role, spec in (team.get("roles") or {}).items():
        enabled, has_key = spec.get("enabled", True) is not False, bool(env.get(spec.get("key_env") or ""))
        needs = [n for n in (spec.get("needs") or []) if n in ("image", "pdf")]
        roles[role] = {"title": spec.get("title") or role, "enabled": enabled, "model": spec.get("model") or "",
                       "fallback": spec.get("fallback") or "", "effort": spec.get("effort", "medium"),
                       "description": spec.get("description") or "",
                       "tools": (["kanban"] if role == LEAD else []) + ["kb"],   # what apply_team.py gives every role
                       "needs": ["tools"] + needs, "profile": _profile(role, spec),
                       "key_env": spec.get("key_env") or "", "routable": spec.get("routable", True) is not False}
        actual = _actual(_profile(role, spec))
        status[role] = {"enabled": enabled, "exists": actual["exists"], "has_key": has_key,
                        "key_source": "role" if has_key else None,
                        "differences": _differences(spec, actual) if enabled and has_key else []}
        for field in ("model", "fallback"):
            model_id = spec.get(field)
            if enabled and model_id and models and model_id not in models:
                warnings.append("role %s: %s %s is not in OpenRouter's current model list" % (role, field, model_id))
        info = models.get(spec.get("model") or "")
        for need, label in (("image", "see images"), ("pdf", "read PDF files")):
            if enabled and info and need in needs and not info.get(need):
                warnings.append("role %s: %s cannot %s, which this role needs" % (role, spec.get("model"), label))
        if spec.get("fallback") and spec.get("fallback") == spec.get("model"):
            warnings.append("role %s: the fallback is the same model as the primary" % role)
        if enabled and not has_key:
            warnings.append("role %s: no API key yet, so Apply skips it" % role)
    routing["review"] = REVIEW_FROM_FILE.get(routing.get("review", "code"), "changes")
    routing["lead"] = LEAD if LEAD in roles else None
    pending = _pending()
    active = [s for s in status.values() if s["enabled"]]
    return {
        "department": "software",
        "config": {"name": "Software", "provider": team.get("provider", "openrouter"), "projects_dir": "", "roles": roles, "routing": routing},
        "status": {"department": "software", "roles": status, "pending": pending,
                   "in_sync": not pending and all(not s["differences"] for s in active),
                   "key": {"shared": False, "department": False, "sources": []}},
        "errors": [], "warnings": warnings, "presets": {}, "choices": {"efforts": EFFORTS, "review": list(REVIEW_TO_FILE)},
        "capabilities": CAPABILITIES, "routing_extra": ROUTING_EXTRA, "file": "team.yaml", "rules_prefix": "",
    }


def _role_errors(role: str, spec: dict, new: dict, known: dict) -> list:
    errors = []
    model, fallback = str(new.get("model", "")).strip(), str(new.get("fallback", "")).strip()
    if not model:
        errors.append("role %s: a model is required" % role)
    for label, model_id in (("model", model), ("fallback", fallback)):
        if model_id and not engine.MODEL_RE.match(model_id):
            errors.append("role %s: %s %r does not look like 'vendor/model'" % (role, label, model_id))
        elif model_id and known and model_id not in known and model_id != (spec.get(label) or ""):
            errors.append("role %s: %s %s is not in OpenRouter's model list" % (role, label, model_id))
    if new.get("effort") not in EFFORTS:
        errors.append("role %s: effort must be one of %s" % (role, ", ".join(EFFORTS)))
    if not " ".join(str(new.get("description", "")).split()):
        errors.append("role %s: a description is required" % role)
    if not new.get("enabled", True) and role == LEAD:
        errors.append("role %s: the manager cannot be disabled, it hands out the work" % role)
    return errors


def _routing_changes(submitted: dict, team: dict, known: dict):
    """Validated routing values to write, as team.yaml stores them. Returns (changes, errors)."""
    roles, changes, errors = set(team.get("roles") or {}), {}, []
    if "review" in submitted:
        if submitted["review"] not in REVIEW_TO_FILE:
            errors.append("review must be one of %s" % ", ".join(REVIEW_TO_FILE))
        else:
            changes["review"] = REVIEW_TO_FILE[submitted["review"]]
    fields = [{"key": "reviewer", "type": "role"}, {"key": "task_timeout", "type": "duration"}] + ROUTING_EXTRA
    for field in fields:
        key = field["key"]
        if key not in submitted:
            continue
        value = submitted[key]
        if field["type"] == "choice" and value not in [c[0] for c in field["choices"]]:
            errors.append("%s must be one of %s" % (key, ", ".join(c[0] for c in field["choices"])))
        elif field["type"] == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                errors.append("%s must be a number" % key)
                continue
            if not field["min"] <= value <= field["max"]:
                errors.append("%s must be between %d and %d" % (key, field["min"], field["max"]))
        elif field["type"] == "role" and value not in roles:
            errors.append("%s: there is no role %s" % (key, value))
        elif field["type"] == "duration" and not re.fullmatch(r"\d+[smhd]?", str(value)):
            errors.append("%s: use e.g. 45m, 2h or 900" % key)
        elif field["type"] == "model" and (not engine.MODEL_RE.match(str(value)) or (known and value not in known
                                                                                  and value != (team.get("routing") or {}).get(key))):
            errors.append("%s: %s is not in OpenRouter's model list" % (key, value))
        changes[key] = value
    current = team.get("routing") or {}
    return {k: v for k, v in changes.items() if current.get(k) != v}, errors


def save(submitted: dict) -> dict:
    """Write what the Team and Routing tabs edit back to team.yaml. Known fields of existing roles only."""
    if not isinstance(submitted, dict) or not isinstance(submitted.get("roles"), dict):
        raise HTTPException(400, "The request did not contain a configuration")
    team, known = _team(), engine.load_models().get("models") or {}
    errors, role_changes = [], {}
    for role, spec in team["roles"].items():
        new = submitted["roles"].get(role)
        if not isinstance(new, dict):
            continue
        merged = {"model": spec.get("model") or "", "fallback": spec.get("fallback") or "", "effort": spec.get("effort", "medium"),
                  "description": spec.get("description") or "", "enabled": spec.get("enabled", True) is not False,
                  "title": spec.get("title") or role, "needs": spec.get("needs") or []}
        merged.update({f: new[f] for f in ROLE_FIELDS if f in new})
        errors += _role_errors(role, spec, merged, known)
        role_changes[role] = merged
    routing, routing_errors = _routing_changes(submitted.get("routing") or {}, team, known)
    errors += routing_errors
    if errors:
        raise HTTPException(400, "Not saved: " + "; ".join(errors))

    def change(doc):
        for role, new in role_changes.items():
            spec = doc["roles"][role]
            for field in ("model", "effort"):
                if str(new[field]).strip() != (spec.get(field) or ""):
                    spec[field] = str(new[field]).strip()
            fallback = str(new["fallback"]).strip()
            if fallback != (spec.get("fallback") or ""):
                spec["fallback"] = fallback or None
            description = " ".join(str(new["description"]).split())
            if description != " ".join(str(spec.get("description") or "").split()):
                spec["description"] = description
            title = " ".join(str(new["title"]).split())
            if title and title != role:
                spec["title"] = title
            else:
                spec.pop("title", None)
            needs = [n for n in ("image", "pdf") if n in (new["needs"] or [])]
            if needs:
                spec["needs"] = needs
            else:
                spec.pop("needs", None)
            if new["enabled"]:
                spec.pop("enabled", None)   # enabled is the default; keep the file tidy
            else:
                spec["enabled"] = False
        for key, value in routing.items():
            doc["routing"][key] = value
    _edit_team(change)
    return state()


def _key_name(role: str) -> str:
    return "OPENROUTER_KEY_" + re.sub(r"[^A-Z0-9]", "_", role.upper())


def add_role(role: str, title: str) -> dict:
    team = _team()
    if not engine.NAME_RE.match(role or ""):
        raise HTTPException(400, "A role name is lowercase letters, digits and '-', starting with a letter")
    owners = engine.names_in_use(None)
    if role in owners or role in team["roles"] or role in engine.list_departments():
        raise HTTPException(400, "The name %s is already used by %s" % (role, owners.get(role, "this department")))
    title = " ".join((title or "").split()) or role
    spec = {"model": "google/gemini-3.8-flash", "fallback": "anthropic/claude-sonnet-5.5", "effort": "medium", "key_env": _key_name(role),
            "description": "%s. Replace this with what the role is good at; the manager reads it to decide who gets a task." % title}
    if title != role:
        spec["title"] = title

    def change(doc):
        doc["roles"][role] = spec
    _edit_team(change)
    rules = os.path.join(POLICIES, "roles", role + ".md")
    if not os.path.exists(rules):
        os.makedirs(os.path.dirname(rules), exist_ok=True)
        with open(rules, "w", encoding="utf-8") as handle:
            handle.write("# Role: %s\n<!-- Role-specific rules. -->\n" % role)
    example = os.path.join(SOFTWARE_ROOT, ".env.example")   # documents which keys a new machine needs
    if os.path.isfile(example) and spec["key_env"] + "=" not in engine.read_text(example):
        with open(example, "a", encoding="utf-8") as handle:
            handle.write("%s=\n" % spec["key_env"])
    return state()


def delete_role(role: str) -> dict:
    team = _team()
    spec = team["roles"].get(role)
    if spec is None:
        raise HTTPException(404, "unknown role")
    if role == LEAD:
        raise HTTPException(400, "The manager cannot be deleted, it hands out the work")
    routing = team.get("routing") or {}
    used = [key for key in ("reviewer", "security_profile", "escalate_to", "default_assignee") if routing.get(key) == role]
    if used:
        raise HTTPException(400, "%s is still used in Routing (%s) - choose another role there first" % (role, ", ".join(used)))

    def change(doc):
        del doc["roles"][role]
    _edit_team(change)
    rules = os.path.join(POLICIES, "roles", role + ".md")
    if os.path.isfile(rules):
        os.remove(rules)
    profile = _profile(role, spec)
    left = profile != "default" and os.path.isdir(os.path.join(engine.hermes_home(), "profiles", profile))
    return dict(state(), profile_left=left)


def _rules_path(rel: str) -> str:
    """policies/<file>.md is the file of that name; roles/<role>.md is policies/roles/<role>.md."""
    match = re.fullmatch(r"(policies|roles)/([\w.-]+)\.md", rel or "")
    if not match or match.group(2).startswith("."):
        raise HTTPException(400, "invalid rules path")
    if match.group(1) == "roles":
        if match.group(2) not in (_team().get("roles") or {}):
            raise HTTPException(400, "there is no role %s in this department" % match.group(2))
        return os.path.join(POLICIES, "roles", match.group(2) + ".md")
    return os.path.join(POLICIES, match.group(2) + ".md")


def list_rules() -> list:
    out = []
    for filename in sorted(os.listdir(POLICIES)) if os.path.isdir(POLICIES) else []:
        path = os.path.join(POLICIES, filename)
        if filename.endswith(".md") and not filename.startswith(".") and os.path.isfile(path):
            out.append({"path": "policies/" + filename, "chars": os.path.getsize(path), "kind": "Whole department"})
    for role in _team().get("roles") or {}:
        path = os.path.join(POLICIES, "roles", role + ".md")
        out.append({"path": "roles/%s.md" % role, "chars": os.path.getsize(path) if os.path.isfile(path) else 0, "kind": "One role"})
    return out


class ConfigIn(BaseModel):
    config: dict


class RoleIn(BaseModel):
    role: str = ""
    title: str = ""


class KeyIn(BaseModel):
    scope: str = "role"
    role: str = ""
    key: str = ""
    remove: bool = False
    source: str = ""


class TextIn(BaseModel):
    text: str


class ApplyIn(BaseModel):
    message: str = ""


def build_router(auth, apply_fn=None, check_key=key_usage.key_info) -> APIRouter:
    """Register before dept_api's router: these paths would otherwise be read as a department named 'software'.

    `apply_fn(message)` commits and applies the team and returns (ok, log); the panel server supplies it.
    """
    router = APIRouter(dependencies=[Depends(auth)])
    base = "/api/departments/software"

    @router.get(base)
    def get_software():
        return state()

    @router.put(base + "/config")
    def put_software(body: ConfigIn):
        return save(body.config)

    @router.post(base + "/roles")
    def post_role(body: RoleIn):
        return add_role(body.role.strip(), body.title)

    @router.delete(base + "/roles/{role}")
    def remove_role(role: str):
        return delete_role(role)

    @router.post(base + "/key")
    def set_key(body: KeyIn):
        spec = (_team().get("roles") or {}).get(body.role)
        if body.scope != "role" or not spec or not spec.get("key_env"):
            raise HTTPException(400, "In this department every role has its own key - choose a role")
        if body.remove:
            engine.set_env_value(ENV, spec["key_env"], None)
        else:
            key = body.key.strip()
            if len(key) < 20 or any(c.isspace() for c in key):
                raise HTTPException(400, "That does not look like an API key")
            try:
                check_key(key)
            except Exception:
                raise HTTPException(400, "OpenRouter rejected this key")
            engine.set_env_value(ENV, spec["key_env"], key)
        return dict(state(), profiles_updated=0, note="Apply to push it to the Hermes profile.")

    @router.get(base + "/key-usage")
    def usage():
        env = engine.read_env(ENV)
        return key_usage.usage({role: env.get(spec.get("key_env") or "", "") for role, spec in (_team().get("roles") or {}).items()})

    @router.post(base + "/apply")
    def apply(body: ApplyIn = ApplyIn()):
        if apply_fn is None:
            raise HTTPException(501, "Apply is not available here")
        ok, log = apply_fn(body.message)
        return {"ok": ok, "log": log, "state": state()}

    @router.post(base + "/test")
    def test_role(body: RoleIn):
        spec = (_team().get("roles") or {}).get(body.role)
        if not spec:
            raise HTTPException(400, "unknown role")
        ok, message = engine.test_model(spec["model"], engine.read_env(ENV).get(spec.get("key_env") or "", ""))
        return {"ok": ok, "message": message}

    @router.get(base + "/rules")
    def rules():
        return list_rules()

    @router.get(base + "/rules/{rel:path}")
    def get_rules(rel: str):
        return {"path": rel, "text": engine.read_text(_rules_path(rel))}

    @router.put(base + "/rules/{rel:path}")
    def put_rules(rel: str, body: TextIn):
        if len(body.text) > MAX_RULES:
            raise HTTPException(400, "rules too long; move long rulebooks to knowledge/policies-full/")
        path = _rules_path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body.text.rstrip() + "\n")
        return {"ok": True}

    @router.get(base + "/board")
    def board():
        profiles = {_profile(role, spec) for role, spec in (_team().get("roles") or {}).items()} | set(_team().get("roles") or {})
        result = subprocess.run([engine.hermes_bin() or "hermes", "kanban", "list", "--json", "--sort", "updated"], capture_output=True, text=True)
        try:
            tasks = json.loads(result.stdout or "[]")
        except ValueError:
            tasks = []
        keep = ("id", "title", "assignee", "status", "priority", "created_at", "completed_at")
        mine = [{k: t.get(k) for k in keep} for t in tasks if isinstance(t, dict) and t.get("assignee") in profiles]
        return {"dispatcher_running": engine.dispatcher_running(), "tasks": mine[:100]}

    return router
