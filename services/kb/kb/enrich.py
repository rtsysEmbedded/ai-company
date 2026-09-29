"""Document enrichment (summary, tags, keywords) with graceful fallback.

Order: Apple Foundation Models (afm-helper, local, free) -> OpenRouter cheap model -> none.
Configured by machine.yaml `enrichment.backend` (afm | openrouter | none).
"""
import json
import os
import subprocess
import urllib.request
from typing import Callable, Optional, Tuple

from .config import ROOT, machine

AFM_BIN = os.path.join(ROOT, "services", "afm-helper", ".build", "release", "afm-helper")
PROMPT = ("Return JSON with keys summary (one sentence, max 30 words), tags (3-6 lowercase topics) "
          "and keywords (up to 10 exact identifiers that appear in the text). Document:\n")


def _afm(text: str) -> Optional[dict]:
    if not os.path.exists(AFM_BIN):
        return None
    r = subprocess.run([AFM_BIN, "enrich"], input=text.encode(), capture_output=True, timeout=120)
    return json.loads(r.stdout) if r.returncode == 0 else None


def afm_status() -> dict:
    if not os.path.exists(AFM_BIN):
        return {"available": False, "reason": "not built (swift build -c release in services/afm-helper)"}
    r = subprocess.run([AFM_BIN, "status"], capture_output=True, timeout=30)
    return json.loads(r.stdout)


def _openrouter(text: str, model: str) -> Optional[dict]:
    key = os.environ.get("OPENROUTER_KEY_KB") or _env_file().get("OPENROUTER_KEY_KB")
    if not key:
        return None
    body = {"model": model, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": PROMPT + text[:6000]}]}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        out = json.load(urllib.request.urlopen(req, timeout=60))
        return json.loads(out["choices"][0]["message"]["content"])
    except Exception:
        return None


def _env_file() -> dict:
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return {}
    pairs = (l.split("=", 1) for l in open(path) if "=" in l and not l.lstrip().startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs if v.strip()}


def make_enricher(log=print) -> Optional[Callable[[str, str], Tuple[str, str]]]:
    """Return enrich(path, text) -> (summary, "tag1 tag2 ... | kw1 kw2 ..."), or None if disabled."""
    cfg = machine().get("enrichment", {})
    backend = cfg.get("backend", "afm")
    fallback_model = cfg.get("fallback_model", "qwen/qwen3.8-flash")
    if backend == "none":
        return None
    use_afm = backend == "afm" and afm_status().get("available", False)
    if backend == "afm" and not use_afm:
        log(f"enrich: Apple model unavailable ({afm_status().get('reason')}), using fallback")

    def enrich(path: str, text: str) -> Tuple[str, str]:
        res = (_afm(text) if use_afm else None) or _openrouter(text, fallback_model)
        if not res:
            return "", ""
        tags = " ".join(str(t).lower() for t in res.get("tags", []))
        kws = " ".join(str(k) for k in res.get("keywords", []))
        return str(res.get("summary", "")), f"{tags} | {kws}".strip(" |")

    return enrich
