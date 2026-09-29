"""Client for the local OpenAI-compatible embedder (services/embedder)."""
import json
import urllib.request
from typing import List

from .config import EMBEDDER_URL


def _post(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(EMBEDDER_URL + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def health() -> dict:
    return json.load(urllib.request.urlopen(EMBEDDER_URL.rsplit("/v1", 1)[0] + "/health", timeout=5))


def embed(texts: List[str], query: bool = False, batch: int = 64) -> List[List[float]]:
    out: List[List[float]] = []
    for i in range(0, len(texts), batch):
        res = _post("/embeddings", {"input": texts[i:i + batch],
                                    "input_type": "query" if query else "document"})
        out += [d["embedding"] for d in sorted(res["data"], key=lambda d: d["index"])]
    return out


def rerank(query: str, docs: List[str]) -> List[float]:
    res = _post("/rerank", {"query": query, "documents": docs})
    scores = [0.0] * len(docs)
    for r in res["results"]:
        scores[r["index"]] = r["relevance_score"]
    return scores
