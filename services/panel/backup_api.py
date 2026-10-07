"""Backup and restore of one department's settings.

A backup is one JSON file in data/backups/<department>/ (private, not in git) holding the
department's definition as text: for the software department team.yaml and policies/, for every
other department its folder under departments/ (department.json, roles/, policies/, presets/).
API keys are included only when asked for. Restoring writes those files back; nothing changes in
Hermes until the department is applied. The current settings are backed up first, every time.
"""
import json
import os
import re
import sys
import time

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "departments", "engine"))
import dept as engine  # noqa: E402

SOFTWARE = "software"
SOFTWARE_ROOT = os.environ.get("PANEL_SOFTWARE_ROOT") or ROOT   # where team.yaml, policies/ and .env live
BACKUPS = os.environ.get("PANEL_BACKUP_DIR") or os.path.join(ROOT, "data", "backups")
KIND = "ai-company-department-backup"
ID_RE = re.compile(r"^\d{8}-\d{6}(-[a-z0-9-]{1,40})?\.json$")
TEXT_TYPES = (".json", ".md", ".yaml", ".yml", ".txt")
MAX_FILE = 200_000
MAX_BACKUP = 5_000_000
KEEP_AUTOMATIC = 10


def _check_department(name: str):
    if name != SOFTWARE and name not in engine.list_departments():
        raise HTTPException(404, "There is no department %r" % name)


def _base(name: str) -> str:
    return SOFTWARE_ROOT if name == SOFTWARE else engine.department_dir(name)


def _managed(name: str, rel: str) -> bool:
    """True for a path that belongs to a department's settings and may be written by a restore."""
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\\" in rel or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return False
    if not rel.endswith(TEXT_TYPES):
        return False
    if name == SOFTWARE:
        return rel == "team.yaml" or (parts[0] == "policies" and rel.endswith(".md"))
    return True


def _current_files(name: str) -> dict:
    base, files = _base(name), {}
    roots = [base] if name != SOFTWARE else [os.path.join(base, "policies")]
    if name == SOFTWARE and os.path.isfile(os.path.join(base, "team.yaml")):
        files["team.yaml"] = engine.read_text(os.path.join(base, "team.yaml"))
    for root in roots:
        for folder, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            for filename in sorted(names):
                path = os.path.join(folder, filename)
                rel = os.path.relpath(path, base).replace(os.sep, "/")
                if _managed(name, rel) and os.path.getsize(path) <= MAX_FILE:
                    files[rel] = engine.read_text(path)
    return files


def _key_names(name: str, files: dict) -> list:
    """The key variables that belong to this department (never the key shared by all departments)."""
    if name == SOFTWARE:
        try:
            roles = (yaml.safe_load(files.get("team.yaml") or "") or {}).get("roles") or {}
        except yaml.YAMLError:
            roles = {}
        return sorted({str(spec.get("key_env")) for spec in roles.values() if isinstance(spec, dict) and spec.get("key_env")})
    try:
        roles = (json.loads(files.get("department.json") or "{}") or {}).get("roles") or {}
    except ValueError:
        roles = {}
    return [engine.key_var(name)] + [engine.key_var(role) for role in roles]


def _env_path(name: str) -> str:
    return os.path.join(SOFTWARE_ROOT, ".env") if name == SOFTWARE else engine.ENV_PATH


def _roles(name: str, files: dict) -> list:
    try:
        if name == SOFTWARE:
            return list(((yaml.safe_load(files.get("team.yaml") or "") or {}).get("roles") or {}))
        return list(((json.loads(files.get("department.json") or "{}") or {}).get("roles") or {}))
    except (yaml.YAMLError, ValueError):
        return []


def _folder(name: str) -> str:
    path = os.path.join(BACKUPS, name)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _path(name: str, backup_id: str) -> str:
    if not ID_RE.match(backup_id or ""):
        raise HTTPException(400, "invalid backup name")
    path = os.path.join(_folder(name), backup_id)
    if not os.path.isfile(path):
        raise HTTPException(404, "backup not found")
    return path


def _store(name: str, backup: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (backup.get("label") or "").lower()).strip("-")[:40]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(backup["created"]))
    backup_id = "%s%s.json" % (stamp, "-" + slug if slug else "")
    path, n = os.path.join(_folder(name), backup_id), 1
    while os.path.exists(path):   # two backups in the same second
        n += 1
        backup_id = "%s-%s.json" % (stamp, ((slug + "-") if slug else "") + str(n))
        path = os.path.join(_folder(name), backup_id)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(backup, handle, indent=1)
    return backup_id


def create(name: str, label: str = "", include_keys: bool = False, automatic: bool = False) -> str:
    files = _current_files(name)
    if not files:
        raise HTTPException(400, "This department has no settings to back up")
    backup = {"kind": KIND, "version": 1, "department": name, "created": time.time(),
              "label": " ".join((label or "").split())[:80], "automatic": automatic, "files": files}
    if include_keys:
        env = engine.read_env(_env_path(name))
        backup["keys"] = {var: env[var] for var in _key_names(name, files) if env.get(var)}
    backup_id = _store(name, backup)
    if automatic:   # keep the safety copies from piling up
        old = sorted(b["id"] for b in listing(name) if b["automatic"])[:-KEEP_AUTOMATIC]
        for stale in old:
            os.remove(os.path.join(_folder(name), stale))
    return backup_id


def _summary(name: str, backup_id: str, backup: dict, size: int) -> dict:
    files = backup.get("files") or {}
    return {"id": backup_id, "created": backup.get("created") or 0, "label": backup.get("label") or "",
            "automatic": bool(backup.get("automatic")), "files": len(files), "roles": _roles(name, files),
            "keys": len(backup.get("keys") or {}), "size": size}


def listing(name: str) -> list:
    out, folder = [], _folder(name)
    for filename in os.listdir(folder):
        if not ID_RE.match(filename):
            continue
        path = os.path.join(folder, filename)
        backup = engine.read_json(path)
        if isinstance(backup, dict) and backup.get("kind") == KIND:
            out.append(_summary(name, filename, backup, os.path.getsize(path)))
    return sorted(out, key=lambda b: b["created"], reverse=True)


def _validate(name: str, backup) -> dict:
    """Refuse anything that is not a backup of this department, before a single file is written."""
    if not isinstance(backup, dict) or backup.get("kind") != KIND or not isinstance(backup.get("files"), dict):
        raise HTTPException(400, "This is not a department backup file")
    if backup.get("department") != name:
        raise HTTPException(400, "This backup is of the department %r, not of %r" % (backup.get("department"), name))
    files = backup["files"]
    for rel, text in files.items():
        if not isinstance(rel, str) or not isinstance(text, str) or not _managed(name, rel) or len(text) > MAX_FILE:
            raise HTTPException(400, "The backup contains a file that cannot be restored: %s" % str(rel)[:80])
    if name == SOFTWARE:
        try:
            team = yaml.safe_load(files.get("team.yaml") or "")
        except yaml.YAMLError as exc:
            raise HTTPException(400, "team.yaml in the backup is not valid YAML: %s" % exc)
        if not isinstance(team, dict) or not isinstance(team.get("roles"), dict) or not team["roles"]:
            raise HTTPException(400, "team.yaml in the backup has no roles")
    else:
        try:
            config = json.loads(files.get("department.json") or "")
        except ValueError:
            raise HTTPException(400, "department.json in the backup is missing or not valid JSON")
        errors, _ = engine.validate(name, config)
        if errors:
            raise HTTPException(400, "The backup's configuration is not valid here: " + "; ".join(errors))
    keys = backup.get("keys") or {}
    allowed = set(_key_names(name, files))
    if not isinstance(keys, dict) or any(var not in allowed or not isinstance(value, str) or "\n" in value for var, value in keys.items()):
        raise HTTPException(400, "The backup contains keys that do not belong to this department")
    return backup


def restore(name: str, backup: dict, with_keys: bool) -> dict:
    _validate(name, backup)
    base, files, current = _base(name), backup["files"], _current_files(name)
    removed = [rel for rel in current if rel not in files]
    changed = [rel for rel, text in files.items() if current.get(rel) != text]
    env = engine.read_env(_env_path(name))
    keys = {var: value for var, value in (backup.get("keys") or {}).items() if env.get(var) != value} if with_keys else {}
    if not removed and not changed and not keys:
        return {"written": 0, "removed": 0, "keys": 0, "safety_backup": None, "unchanged": True}
    safety = create(name, "before restore", include_keys=True, automatic=True)
    for rel in removed:
        os.remove(os.path.join(base, rel))
    for rel in changed:
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(files[rel])
        os.replace(tmp, path)
    for var, value in keys.items():
        engine.set_env_value(_env_path(name), var, value)
    if keys and name != SOFTWARE:   # put them into the profiles that already exist, as the Keys tab does
        engine.sync_keys(name, engine.load_config(name))
    return {"written": len(changed), "removed": len(removed), "keys": len(keys), "safety_backup": safety, "unchanged": False}


class CreateIn(BaseModel):
    label: str = ""
    include_keys: bool = False


class RestoreIn(BaseModel):
    with_keys: bool = True


class ImportIn(BaseModel):
    backup: dict


def build_router(auth) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/api/backups/{name}")
    def list_backups(name: str):
        _check_department(name)
        return listing(name)

    @router.post("/api/backups/{name}")
    def create_backup(name: str, body: CreateIn):
        _check_department(name)
        return {"id": create(name, body.label, body.include_keys), "backups": listing(name)}

    @router.post("/api/backups/{name}/import")
    def import_backup(name: str, body: ImportIn):
        """Keep a backup file from elsewhere (another machine, a download) in the list. Does not restore it."""
        _check_department(name)
        backup = body.backup
        if len(json.dumps(backup)) > MAX_BACKUP:
            raise HTTPException(400, "backup file too large")
        _validate(name, backup)
        backup = dict(backup, automatic=False, created=float(backup.get("created") or time.time()),
                      label=("%s (imported)" % (backup.get("label") or "")).strip()[:80])
        return {"id": _store(name, backup), "backups": listing(name)}

    @router.get("/api/backups/{name}/{backup_id}/download")
    def download(name: str, backup_id: str):
        _check_department(name)
        return FileResponse(_path(name, backup_id), media_type="application/json",
                            filename="%s-backup-%s" % (name, backup_id), headers={"Cache-Control": "no-store"})

    @router.post("/api/backups/{name}/{backup_id}/restore")
    def restore_backup(name: str, backup_id: str, body: RestoreIn):
        _check_department(name)
        return dict(restore(name, engine.read_json(_path(name, backup_id)), body.with_keys), backups=listing(name))

    @router.delete("/api/backups/{name}/{backup_id}")
    def delete(name: str, backup_id: str):
        _check_department(name)
        os.remove(_path(name, backup_id))
        return listing(name)

    return router
