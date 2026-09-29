"""Paths and machine settings shared by all kb modules."""
import os

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
KNOWLEDGE = os.path.join(ROOT, "knowledge")
POLICIES = os.path.join(ROOT, "policies")
LESSONS = os.path.join(KNOWLEDGE, "lessons")


def machine() -> dict:
    for name in ("machine.yaml", "machine.example.yaml"):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            return yaml.safe_load(open(path)) or {}
    return {}


_M = machine()
EMBEDDER_URL = _M.get("embedder", {}).get("url", "http://127.0.0.1:8765/v1").rstrip("/")
INDEX_DIR = os.environ.get("KB_INDEX_DIR") or os.path.join(ROOT, _M.get("paths", {}).get("index", "data/index"))
PRIVATE_FOLDERS = _M.get("private_folders", []) or []

# Chunk sizes in characters (~4 chars per token): ~150-700 tokens per chunk.
CHUNK_MAX = 2800
CHUNK_OVERLAP = 300
CHUNK_MIN = 200
