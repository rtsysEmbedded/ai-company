"""What OpenRouter says about a key: spend so far and its limit. Used by the Keys tab of every department."""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

KEY_URL = os.environ.get("PANEL_KEY_URL", "https://openrouter.ai/api/v1/key")


def key_info(key: str) -> dict:
    """Raises when OpenRouter does not accept the key."""
    request = urllib.request.Request(KEY_URL, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["data"]


def usage(keys: dict) -> dict:
    """role -> {valid, usage, limit, remaining, reset} for every role that has a key. Each key is asked once."""
    def ask(key):
        try:
            data = key_info(key)
            return {"valid": True, "usage": round(data.get("usage") or 0, 4), "limit": data.get("limit"),
                    "remaining": data.get("limit_remaining"), "reset": data.get("limit_reset")}
        except Exception as exc:
            return {"valid": False, "error": str(exc)[:120]}
    distinct = sorted({key for key in keys.values() if key})
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = dict(zip(distinct, pool.map(ask, distinct)))
    out = {}
    for role, key in keys.items():
        if key:
            others = sorted(r for r, k in keys.items() if k == key and r != role)
            out[role] = dict(answers[key], shared_with=others)
    return out
