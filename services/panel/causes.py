"""Why a kanban worker run really failed, read from Hermes' worker logs, plus the account balance.

Hermes records a worker that exits without finishing as "crashed / protocol violation" even when
the provider refused the request (no credits, bad key, rate limit). The worker log has the real
error. Shared by the panel (activity_api) and scripts/escalate.py.
"""
import json
import os
import re
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
SESSION = re.compile(r"^Session:\s+(\S+)", re.M)
MAX_LOG = 4 * 1024 * 1024

# Checked in order; the first match wins. These are infrastructure causes, not the agent's fault.
PATTERNS = [
    ("out_of_credits", re.compile(r"HTTP 402|Out of credits|credits exhausted|requires more credits", re.I)),
    ("bad_key", re.compile(r"HTTP 401|User not found|invalid api key|No auth credentials", re.I)),
    ("rate_limited", re.compile(r"HTTP 429|rate limit", re.I)),
    ("model_unavailable", re.compile(r"HTTP 404.{0,80}model|No endpoints found|model .{0,60}not (found|available)", re.I)),
]
LABELS = {
    "out_of_credits": "out of credits (OpenRouter refused the request, HTTP 402)",
    "bad_key": "API key rejected (HTTP 401)",
    "rate_limited": "rate limited by the provider (HTTP 429)",
    "model_unavailable": "model not available on OpenRouter",
}
# Causes that another agent cannot fix: do not escalate, do not keep retrying.
INFRA = set(LABELS)


def hermes_home() -> str:
    return os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes")


def _classify(text: str):
    # Only the end of a run decides: an early retryable error that later succeeded is not the cause.
    tail = text[-6000:]
    for name, pattern in PATTERNS:
        if pattern.search(tail):
            return name
    return None


def run_causes(task_id: str) -> dict:
    """{session_id: cause} for every finished worker session of a task, and "_last" for the newest."""
    path = os.path.join(hermes_home(), "kanban", "logs", f"{task_id}.log")
    if not re.fullmatch(r"[\w-]+", task_id or "") or not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - MAX_LOG))
        text = ANSI.sub("", f.read().decode("utf-8", "replace"))
    out, start, last = {}, 0, None
    for m in SESSION.finditer(text):
        last = _classify(text[start:m.start()])
        if last:
            out[m.group(1)] = last
        start = m.end()
    out["_last"] = last
    return out


# ---------- account balance ----------
_credits = {"at": 0.0, "data": None}


def _env_keys() -> list:
    """OpenRouter keys from the company .env files (values stay on this machine)."""
    keys = []
    for path in (os.environ.get("AICO_ENV_FILE") or os.path.join(ROOT, ".env"),
                 os.environ.get("DEPT_ENV_FILE") or os.path.join(ROOT, "departments", ".env")):
        if not os.path.isfile(path):
            continue
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if value.startswith("sk-or-") and value not in keys:
                    keys.append(value)
    return keys


def credits(max_age: int = 60) -> dict:
    """Account credit balance from OpenRouter, cached. {total, used, left, checked_at} or {error}."""
    if _credits["data"] and time.time() - _credits["at"] < max_age:
        return _credits["data"]
    data = {"error": "no OpenRouter key configured"}
    for key in _env_keys()[:3]:
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization": f"Bearer {key}"})
            c = json.load(urllib.request.urlopen(req, timeout=10))["data"]
            total, used = float(c.get("total_credits") or 0), float(c.get("total_usage") or 0)
            data = {"total": total, "used": used, "left": total - used, "checked_at": time.time()}
            break
        except Exception as exc:  # try the next key
            data = {"error": str(exc)[:160]}
    _credits.update(at=time.time(), data=data)
    return data
