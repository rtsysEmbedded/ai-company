"""AI Company Control Panel - local web UI for team.yaml, policies, keys, knowledge base and services.

Runs on 127.0.0.1 only. Every API call needs the token from data/panel_token (sent as a cookie
set by /login?token=..., or the X-Panel-Token header). Keys are never sent to the browser.
Open it with: scripts/panel-open
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "services", "kb"))
from kb import embedder as kb_embedder  # noqa: E402
from kb import store as kb_store  # noqa: E402
from kb.config import LESSONS  # noqa: E402
from kb.enrich import afm_status, make_enricher  # noqa: E402

TEAM = os.path.join(ROOT, "team.yaml")
POLICIES = os.path.join(ROOT, "policies")
ENV = os.path.join(ROOT, ".env")
TOKEN_FILE = os.path.join(ROOT, "data", "panel_token")
PORT = int(os.environ.get("PANEL_PORT", "8770"))
UV = os.environ.get("UV_BIN", "uv")
# Typical Hermes call: system prompt + tool schemas + a short answer.
EST_IN, EST_OUT = 17000, 500

os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
if not os.path.exists(TOKEN_FILE):
    open(TOKEN_FILE, "w").write(secrets.token_urlsafe(32))
    os.chmod(TOKEN_FILE, 0o600)
TOKEN = open(TOKEN_FILE).read().strip()

app = FastAPI(title="AI Company Control Panel", docs_url=None, redoc_url=None)
_jobs: dict = {}


# ---------- security ----------
def auth(request: Request):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):  # blocks DNS-rebinding
        raise HTTPException(403, "bad host")
    origin = request.headers.get("origin")
    if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):%d$" % PORT, origin):
        raise HTTPException(403, "bad origin")
    supplied = request.cookies.get("panel_token") or request.headers.get("x-panel-token") or ""
    if not secrets.compare_digest(supplied, TOKEN):
        raise HTTPException(401, "missing or wrong token - open the panel with scripts/panel-open")


@app.get("/login")
def login(token: str):
    if not secrets.compare_digest(token, TOKEN):
        raise HTTPException(401, "wrong token")
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("panel_token", TOKEN, httponly=True, samesite="strict", max_age=30 * 86400)
    return r


@app.get("/")
def index(request: Request):
    if request.cookies.get("panel_token") != TOKEN:
        raise HTTPException(401, "Open the panel with: scripts/panel-open")
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


# ---------- helpers ----------
def read_env() -> dict:
    env = {}
    if os.path.exists(ENV):
        for line in open(ENV):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def write_env_key(name: str, value: str):
    lines = open(ENV).readlines() if os.path.exists(ENV) else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name:
            out.append(f"{name}={value}\n"); done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{name}={value}\n")
    open(ENV, "w").writelines(out)
    os.chmod(ENV, 0o600)


def mask(v: str) -> str:
    return f"{v[:8]}…{v[-4:]}" if len(v) > 16 else ("set" if v else "")


def or_key_info(key: str) -> dict:
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    return json.load(urllib.request.urlopen(req, timeout=20))["data"]


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def run_apply(dry: bool) -> str:
    cmd = [UV, "run", "--project", ROOT, "-q", "python", os.path.join(ROOT, "scripts", "apply_team.py")]
    r = subprocess.run(cmd + (["--dry-run"] if dry else []), capture_output=True, text=True, cwd=ROOT)
    return (r.stdout + r.stderr).strip()


_models_cache = {"t": 0, "data": []}


def models() -> list:
    if time.time() - _models_cache["t"] > 3600:
        d = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30))["data"]
        rows = []
        for m in d:
            if m["id"].endswith(":batch") or m["id"].startswith("~"):
                continue
            p = m.get("pricing", {})
            try:
                pin, pout = float(p.get("prompt", 0)) * 1e6, float(p.get("completion", 0)) * 1e6
            except (TypeError, ValueError):
                continue
            rows.append({"id": m["id"], "in": round(pin, 3), "out": round(pout, 3),
                         "ctx": m.get("context_length"), "created": m.get("created", 0),
                         "per_call": round((EST_IN * pin + EST_OUT * pout) / 1e6, 4)})
        rows.sort(key=lambda r: r["id"])
        _models_cache.update(t=time.time(), data=rows)
    return _models_cache["data"]


# ---------- team ----------
class RoleIn(BaseModel):
    model: str
    fallback: str = ""
    effort: str = "medium"
    description: str


@app.get("/api/team", dependencies=[Depends(auth)])
def get_team():
    return yaml.safe_load(open(TEAM))


@app.put("/api/team/{role}", dependencies=[Depends(auth)])
def put_role(role: str, body: RoleIn):
    team = yaml.safe_load(open(TEAM))
    if role not in team["roles"]:
        raise HTTPException(404, "unknown role")
    ids = {m["id"] for m in models()}
    for mid in filter(None, (body.model, body.fallback)):
        if mid not in ids:
            raise HTTPException(400, f"unknown OpenRouter model: {mid}")
    if body.effort not in ("low", "medium", "high"):
        raise HTTPException(400, "effort must be low, medium or high")
    from ruamel.yaml import YAML
    ry = YAML(); ry.preserve_quotes = True; ry.width = 4096
    doc = ry.load(open(TEAM))  # round-trip keeps comments and order
    spec = doc["roles"][role]
    spec["model"], spec["effort"] = body.model, body.effort
    spec["fallback"] = body.fallback or None
    spec["description"] = " ".join(body.description.split())
    with open(TEAM, "w") as f:
        ry.dump(doc, f)
    return {"ok": True}


@app.get("/api/models", dependencies=[Depends(auth)])
def get_models():
    return models()


# ---------- policies ----------
def _policy_path(rel: str) -> str:
    full = os.path.realpath(os.path.join(POLICIES, rel))
    if not full.startswith(os.path.realpath(POLICIES) + os.sep) or not full.endswith(".md"):
        raise HTTPException(400, "invalid policy path")
    return full


@app.get("/api/policies", dependencies=[Depends(auth)])
def list_policies():
    out = []
    for d, _, files in os.walk(POLICIES):
        for f in sorted(files):
            if f.endswith(".md"):
                p = os.path.join(d, f)
                out.append({"path": os.path.relpath(p, POLICIES), "chars": os.path.getsize(p)})
    return sorted(out, key=lambda x: (x["path"].count("/"), x["path"]))


@app.get("/api/policies/{rel:path}", dependencies=[Depends(auth)])
def get_policy(rel: str):
    return {"path": rel, "text": open(_policy_path(rel), encoding="utf-8").read()}


class TextIn(BaseModel):
    text: str


@app.put("/api/policies/{rel:path}", dependencies=[Depends(auth)])
def put_policy(rel: str, body: TextIn):
    if len(body.text) > 20000:
        raise HTTPException(400, "policy too long; move long rules to knowledge/policies-full/")
    open(_policy_path(rel), "w", encoding="utf-8").write(body.text.rstrip() + "\n")
    return {"ok": True}


# ---------- keys ----------
@app.get("/api/keys", dependencies=[Depends(auth)])
def get_keys():
    env, out = read_env(), []
    names = [l.split("=", 1)[0].strip() for l in open(os.path.join(ROOT, ".env.example")) if l.startswith("OPENROUTER_KEY_")]
    for name in names:
        v, row = env.get(name, ""), {"name": name, "masked": mask(env.get(name, ""))}
        if v:
            try:
                d = or_key_info(v)
                row.update(valid=True, usage=round(d.get("usage") or 0, 4), limit=d.get("limit"),
                           reset=d.get("limit_reset"), remaining=d.get("limit_remaining"))
            except Exception as e:
                row.update(valid=False, error=str(e)[:120])
        out.append(row)
    return out


class KeyIn(BaseModel):
    value: str


@app.put("/api/keys/{name}", dependencies=[Depends(auth)])
def put_key(name: str, body: KeyIn):
    if not re.fullmatch(r"OPENROUTER_KEY_[A-Z]+", name):
        raise HTTPException(400, "invalid key name")
    v = body.value.strip()
    try:
        or_key_info(v)
    except Exception:
        raise HTTPException(400, "OpenRouter rejected this key")
    write_env_key(name, v)
    return {"ok": True, "masked": mask(v)}


# ---------- knowledge ----------
@app.get("/api/kb/status", dependencies=[Depends(auth)])
def kb_status():
    return kb_store.status() | {"apple_model": afm_status(), "reindex": _jobs.get("reindex")}


class SearchIn(BaseModel):
    query: str
    k: int = 6


@app.post("/api/kb/search", dependencies=[Depends(auth)])
def kb_search(body: SearchIn):
    return kb_store.search(body.query, k=max(1, min(body.k, 15)))


@app.post("/api/kb/reindex", dependencies=[Depends(auth)])
def kb_reindex():
    if (_jobs.get("reindex") or {}).get("state") != "running":
        _index_in_background("full reindex")
    return _jobs["reindex"]


@app.get("/api/kb/lessons", dependencies=[Depends(auth)])
def lessons():
    if not os.path.isdir(LESSONS):
        return []
    out = []
    for f in sorted(os.listdir(LESSONS), reverse=True):
        if f.endswith(".md"):
            text = open(os.path.join(LESSONS, f), encoding="utf-8").read()
            role = re.search(r"^author_role:\s*(.*)$", text, re.M)
            out.append({"name": f, "role": role.group(1) if role else "", "text": text})
    return out


@app.delete("/api/kb/lessons/{name}", dependencies=[Depends(auth)])
def delete_lesson(name: str):
    if not re.fullmatch(r"[\w.-]+\.md", name):
        raise HTTPException(400, "invalid name")
    os.remove(os.path.join(LESSONS, name))
    kb_store.ingest(LESSONS, log=lambda *_: None)
    return {"ok": True}


# ---------- library (folders + uploads) ----------
from fastapi import File, Form, UploadFile  # noqa: E402

from kb.chunk import supported as kb_supported  # noqa: E402
from kb.config import KNOWLEDGE  # noqa: E402

MAX_UPLOAD = 100 * 1024 * 1024
FOLDER_RE = r"[\w\- ]+(/[\w\- ]+)*"
_index_lock = threading.Lock()


def _kb_path(rel: str) -> str:
    """Resolve a path relative to knowledge/, refusing anything outside it, hidden, or in lessons/."""
    rel = (rel or "").strip().strip("/")
    base = os.path.realpath(KNOWLEDGE)
    full = os.path.realpath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(400, "path outside knowledge/")
    parts = [] if full == base else os.path.relpath(full, base).split(os.sep)
    if parts and parts[0] == "lessons":
        raise HTTPException(400, "lessons/ is reserved for agents")
    if any(p.startswith(".") for p in parts):
        raise HTTPException(400, "hidden paths are not allowed")
    return full


def _clean_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ()]+", "_", os.path.basename(name or "").strip()).strip(". ")
    if not name:
        raise HTTPException(400, "invalid file name")
    return name[:160]


def _index_in_background(label: str):
    """Incrementally index knowledge/ (only new, changed or removed files are touched)."""
    def work():
        with _index_lock:
            _jobs["reindex"] = {"state": "running", "msg": label}
            try:
                stats = kb_store.ingest(enrich=make_enricher(log=lambda *_: None), log=lambda *_: None)
                _jobs["reindex"] = {"state": "done", "stats": stats, "at": time.strftime("%H:%M:%S"), "msg": label}
            except Exception as e:
                _jobs["reindex"] = {"state": "error", "error": str(e)[:300], "msg": label}
    _jobs["reindex"] = {"state": "running", "msg": label}
    threading.Thread(target=work, daemon=True).start()


def _pdf_has_text(path: str) -> bool:
    import pymupdf
    with pymupdf.open(path) as doc:
        return any(page.get_text().strip() for page in doc)


@app.get("/api/kb/tree", dependencies=[Depends(auth)])
def kb_tree():
    counts = kb_store.chunk_counts()
    folders = []
    for d, dirs, files in os.walk(KNOWLEDGE):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        rel = os.path.relpath(d, KNOWLEDGE)
        rel = "" if rel == "." else rel
        items = []
        for f in sorted(files):
            if f.startswith(".") or f.endswith(".part"):
                continue
            p = os.path.join(d, f)
            items.append({"name": f, "size": os.path.getsize(p), "mtime": os.path.getmtime(p),
                          "chunks": counts.get(os.path.relpath(p, ROOT), 0), "supported": kb_supported(p)})
        folders.append({"path": rel, "files": items, "reserved": rel.split("/")[0] == "lessons"})
    return folders


class FolderIn(BaseModel):
    path: str


@app.post("/api/kb/folder", dependencies=[Depends(auth)])
def kb_new_folder(body: FolderIn):
    if not re.fullmatch(FOLDER_RE, body.path.strip().strip("/")):
        raise HTTPException(400, "use letters, digits, - _ and / only")
    os.makedirs(_kb_path(body.path), exist_ok=True)
    return {"ok": True}


class RenameIn(BaseModel):
    path: str
    to: str


@app.post("/api/kb/folder/rename", dependencies=[Depends(auth)])
def kb_rename_folder(body: RenameIn):
    if not re.fullmatch(FOLDER_RE, body.to.strip().strip("/")):
        raise HTTPException(400, "use letters, digits, - _ and / only")
    src, dst = _kb_path(body.path), _kb_path(body.to)
    if src == os.path.realpath(KNOWLEDGE) or not os.path.isdir(src):
        raise HTTPException(400, "not a folder")
    if os.path.exists(dst):
        raise HTTPException(400, "target exists")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.rename(src, dst)
    _index_in_background(f"renamed {body.path} → {body.to}")
    return {"ok": True}


@app.delete("/api/kb/folder/{rel:path}", dependencies=[Depends(auth)])
def kb_delete_folder(rel: str):
    full = _kb_path(rel)
    if full == os.path.realpath(KNOWLEDGE):
        raise HTTPException(400, "cannot delete the root")
    if any(not f.startswith(".") for _, _, fs in os.walk(full) for f in fs):
        raise HTTPException(400, "folder is not empty - move or delete its files first")
    shutil.rmtree(full)
    return {"ok": True}


@app.post("/api/kb/upload", dependencies=[Depends(auth)])
async def kb_upload(folder: str = Form(""), overwrite: bool = Form(False), files: list[UploadFile] = File(...)):
    target = _kb_path(folder)
    if not os.path.isdir(target):
        raise HTTPException(400, "folder does not exist")
    saved, skipped, exists, warnings = [], [], [], []
    for up in files:
        name = _clean_name(up.filename)
        dest = os.path.join(target, name)
        if not kb_supported(dest):
            skipped.append(f"{name}: unsupported file type"); continue
        if os.path.exists(dest) and not overwrite:
            exists.append(name); continue
        tmp, size = dest + ".part", 0
        with open(tmp, "wb") as out:
            while chunk := await up.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    break
                out.write(chunk)
        if size > MAX_UPLOAD:
            os.remove(tmp); skipped.append(f"{name}: larger than 100 MB"); continue
        os.replace(tmp, dest)
        saved.append(name)
        if name.lower().endswith(".pdf") and not _pdf_has_text(dest):
            warnings.append(f"{name}: no text found (scanned PDF?) - needs OCR to be searchable")
    if saved:
        _index_in_background(f"indexing {len(saved)} uploaded file(s)")
    return {"saved": saved, "skipped": skipped, "exists": exists, "warnings": warnings}


class MoveIn(BaseModel):
    path: str
    to_folder: str


@app.post("/api/kb/file/move", dependencies=[Depends(auth)])
def kb_move_file(body: MoveIn):
    src, dst_dir = _kb_path(body.path), _kb_path(body.to_folder)
    if not os.path.isfile(src) or not os.path.isdir(dst_dir):
        raise HTTPException(400, "bad source or target")
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        raise HTTPException(400, "a file with that name exists in the target folder")
    os.rename(src, dst)
    _index_in_background(f"moved {os.path.basename(src)}")
    return {"ok": True}


@app.delete("/api/kb/file/{rel:path}", dependencies=[Depends(auth)])
def kb_delete_file(rel: str):
    full = _kb_path(rel)
    if not os.path.isfile(full):
        raise HTTPException(404, "not found")
    os.remove(full)
    _index_in_background(f"removed {os.path.basename(full)}")
    return {"ok": True}


# ---------- routing + board ----------
ROUTING_FIELDS = {
    "review": ("choice", ["code", "all", "off"]),
    "security_review": ("choice", ["sensitive", "always", "off"]),
    "escalate_after": ("int", (1, 10)),
    "escalate_to": ("role", None),
    "reviewer": ("role", None),
    "security_profile": ("role", None),
    "default_assignee": ("role", None),
    "max_parallel": ("int", (1, 9)),
    "max_per_profile": ("int", (1, 5)),
    "dispatch_interval_seconds": ("int", (5, 600)),
    "task_timeout": ("duration", None),
    "planner_model": ("model", None),
}


@app.get("/api/routing", dependencies=[Depends(auth)])
def get_routing():
    team = yaml.safe_load(open(TEAM))
    return {"routing": team.get("routing") or {}, "roles": list(team["roles"])}


@app.put("/api/routing", dependencies=[Depends(auth)])
def put_routing(body: dict):
    from ruamel.yaml import YAML
    ry = YAML(); ry.preserve_quotes = True; ry.width = 4096
    doc = ry.load(open(TEAM))
    roles = set(doc["roles"])
    for key, value in body.items():
        if key not in ROUTING_FIELDS:
            raise HTTPException(400, f"unknown routing field: {key}")
        kind, arg = ROUTING_FIELDS[key]
        if kind == "choice" and value not in arg:
            raise HTTPException(400, f"{key} must be one of {arg}")
        if kind == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} must be a number")
            if not arg[0] <= value <= arg[1]:
                raise HTTPException(400, f"{key} must be between {arg[0]} and {arg[1]}")
        if kind == "role" and value not in roles:
            raise HTTPException(400, f"{key}: unknown role {value}")
        if kind == "duration" and not re.fullmatch(r"\d+[smhd]?", str(value)):
            raise HTTPException(400, f"{key}: use e.g. 45m, 2h or 900")
        if kind == "model" and value not in {m["id"] for m in models()}:
            raise HTTPException(400, f"unknown OpenRouter model: {value}")
        doc["routing"][key] = value
    with open(TEAM, "w") as f:
        ry.dump(doc, f)
    return {"ok": True}


@app.get("/api/board", dependencies=[Depends(auth)])
def board():
    r = subprocess.run(["hermes", "kanban", "list", "--json", "--sort", "updated"], capture_output=True, text=True)
    try:
        tasks = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        tasks = []
    svc = {}
    for name in ("dispatcher", "escalator"):
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.aicompany.{name}"],
                             capture_output=True, text=True).stdout
        m = re.search(r"^\s*state = (.+)$", out, re.M)
        svc[name] = m.group(1) if m else "not installed"
    keep = ("id", "title", "assignee", "status", "priority", "created_at", "completed_at")
    return {"services": svc, "tasks": [{k: t.get(k) for k in keep} for t in tasks][:100]}


# ---------- services ----------
@app.get("/api/services", dependencies=[Depends(auth)])
def services():
    try:
        emb = kb_embedder.health() | {"ok": True}
    except Exception as e:
        emb = {"ok": False, "error": str(e)[:120]}
    r = subprocess.run(["hermes", "profile", "list"], capture_output=True, text=True)
    return {"embedder": emb, "apple_model": afm_status(), "profiles": r.stdout.strip()}


class PingIn(BaseModel):
    roles: list


@app.post("/api/ping", dependencies=[Depends(auth)])
def ping(body: PingIn):
    team = yaml.safe_load(open(TEAM))["roles"]
    out = {}
    for role in body.roles:
        if role not in team:
            continue
        prof = team[role].get("hermes_profile") or role
        t = time.time()
        r = subprocess.run(["hermes", "-p", prof, "chat", "-q", "Reply with the single word: ready"],
                           capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL)
        ok = r.returncode == 0 and "error" not in (r.stderr or "").lower()
        out[role] = {"ok": ok, "seconds": round(time.time() - t, 1)}
    return out


# ---------- apply / history ----------
@app.get("/api/pending", dependencies=[Depends(auth)])
def pending():
    diff = git("diff", "--", "team.yaml", "policies").stdout
    untracked = git("ls-files", "--others", "--exclude-standard", "policies").stdout
    return {"diff": diff + untracked, "dry_run": run_apply(dry=True) if diff or untracked else ""}


class ApplyIn(BaseModel):
    message: str = ""


@app.post("/api/apply", dependencies=[Depends(auth)])
def apply(body: ApplyIn):
    git("add", "team.yaml", "policies")
    if git("diff", "--cached", "--quiet").returncode != 0:
        msg = "panel: " + (body.message.strip() or "update team/policies")
        git("-c", "user.name=AI Company Panel", "-c", "user.email=panel@localhost", "commit", "-qm", msg)
    return {"output": run_apply(dry=False), "commit": git("log", "--oneline", "-1").stdout.strip()}


@app.get("/api/history", dependencies=[Depends(auth)])
def history():
    return git("log", "--oneline", "-15").stdout.splitlines()


@app.post("/api/undo", dependencies=[Depends(auth)])
def undo():
    last = git("log", "-1", "--format=%s").stdout.strip()
    if not last.startswith("panel:") and not last.startswith("Revert \"panel:"):
        raise HTTPException(400, f"Last commit is not a panel change: {last}")
    if git("diff", "--quiet", "--", "team.yaml", "policies").returncode != 0:
        raise HTTPException(400, "Unsaved edits pending - apply or discard them first")
    r = git("-c", "user.name=AI Company Panel", "-c", "user.email=panel@localhost", "revert", "--no-edit", "HEAD")
    if r.returncode != 0:
        raise HTTPException(500, r.stderr[:300])
    return {"reverted": last, "output": run_apply(dry=False)}


@app.post("/api/discard", dependencies=[Depends(auth)])
def discard():
    git("checkout", "--", "team.yaml", "policies")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
