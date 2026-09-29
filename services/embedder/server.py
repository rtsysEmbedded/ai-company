"""Local embedding + rerank server on Apple Silicon (MLX, GPU).

OpenAI-compatible:  POST /v1/embeddings   {"input": str | [str], "input_type"?: "query"|"document"}
Rerank:             POST /v1/rerank       {"query": str, "documents": [str], "top_n"?: int}
Health:             GET  /health

Models (Qwen3 family, MLX 4-bit):
  embedding: last-token pooling + L2 norm, queries get an instruction prefix
  reranker:  P("yes") vs P("no") at the last position
"""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("HF_HOME", os.path.join(ROOT, "data", "models"))  # before HF imports

import threading
import time
from typing import List, Optional, Union

import mlx.core as mx
import yaml
from fastapi import FastAPI
from mlx_lm import load
from pydantic import BaseModel

def _machine_cfg() -> dict:
    for name in ("machine.yaml", "machine.example.yaml"):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            return yaml.safe_load(open(path)).get("embedder", {})
    return {}


CFG = _machine_cfg()
EMBED_MODEL = CFG.get("embed_model", "mlx-community/Qwen3-Embedding-4B-4bit-DWQ")
RERANK_MODEL = CFG.get("rerank_model", "mlx-community/Qwen3-Reranker-0.6B-4bit")
MAX_TOKENS = 8192
BATCH = int(os.environ.get("EMBED_BATCH", "32"))
QUERY_TASK = "Given a search query, retrieve relevant passages or code that answer the query"
RERANK_TASK = "Judge whether the Document is relevant to the Query (code, docs, or datasheets)"

_lock = threading.Lock()  # MLX GPU work is serialized
emb_model, emb_tok = load(EMBED_MODEL)
rr_model, rr_tok = load(RERANK_MODEL)
YES = rr_tok.convert_tokens_to_ids("yes")
NO = rr_tok.convert_tokens_to_ids("no")
EOT = emb_tok.convert_tokens_to_ids("<|endoftext|>")


def _padded_mask(lengths: List[int], L: int, dtype) -> mx.array:
    """Causal mask that also hides left padding. Shape (B, 1, L, L), additive."""
    pos = mx.arange(L)
    causal = pos[None, :] <= pos[:, None]                        # (L, L)
    starts = mx.array([L - n for n in lengths])[:, None]         # (B, 1)
    keep_key = pos[None, :] >= starts                            # (B, L)
    keep = causal[None, :, :] & keep_key[:, None, :]             # (B, L, L)
    # Padded query rows would be fully masked (NaN); let them see themselves.
    keep = keep | mx.eye(L, dtype=mx.bool_)[None]
    return mx.where(keep, 0.0, -1e9).astype(dtype)[:, None, :, :]


def _last_hidden(model, seqs: List[List[int]], pad_id: int) -> mx.array:
    """Run the transformer on left-padded seqs; return the final hidden state per seq."""
    L = max(len(s) for s in seqs)
    ids = mx.array([[pad_id] * (L - len(s)) + s for s in seqs])
    inner = model.model
    h = inner.embed_tokens(ids)
    mask = _padded_mask([len(s) for s in seqs], L, h.dtype)
    for layer in inner.layers:
        h = layer(h, mask)
    h = inner.norm(h)
    return h[:, -1, :]


def embed(texts: List[str], is_query: bool) -> List[List[float]]:
    if is_query:
        texts = [f"Instruct: {QUERY_TASK}\nQuery:{t}" for t in texts]
    seqs = [emb_tok.encode(t)[: MAX_TOKENS - 1] + [EOT] for t in texts]
    order = sorted(range(len(seqs)), key=lambda i: len(seqs[i]))
    out: List[Optional[List[float]]] = [None] * len(seqs)
    with _lock:
        for b in range(0, len(order), BATCH):
            idx = order[b : b + BATCH]
            v = _last_hidden(emb_model, [seqs[i] for i in idx], EOT)
            v = v / mx.linalg.norm(v, axis=-1, keepdims=True)
            v = v.astype(mx.float32).tolist()
            for i, vec in zip(idx, v):
                out[i] = vec
    return out  # type: ignore[return-value]


_RR_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
_RR_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def rerank(query: str, docs: List[str]) -> List[float]:
    pre = rr_tok.encode(_RR_PREFIX)
    suf = rr_tok.encode(_RR_SUFFIX)
    seqs = []
    for d in docs:
        body = rr_tok.encode(f"<Instruct>: {RERANK_TASK}\n<Query>: {query}\n<Document>: {d}")
        seqs.append(pre + body[: MAX_TOKENS - len(pre) - len(suf)] + suf)
    scores: List[float] = []
    pad = rr_tok.pad_token_id if rr_tok.pad_token_id is not None else EOT
    with _lock:
        for b in range(0, len(seqs), BATCH):
            h = _last_hidden(rr_model, seqs[b : b + BATCH], pad)
            logits = rr_model.lm_head(h) if hasattr(rr_model, "lm_head") else rr_model.model.embed_tokens.as_linear(h)
            pair = mx.stack([logits[:, NO], logits[:, YES]], axis=-1)
            scores += mx.softmax(pair.astype(mx.float32), axis=-1)[:, 1].tolist()
    return scores


app = FastAPI(title="ai-company embedder")


class EmbedReq(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    input_type: str = "document"  # "query" adds the retrieval instruction


class RerankReq(BaseModel):
    query: str
    documents: List[str]
    top_n: Optional[int] = None


@app.get("/health")
def health():
    return {"status": "ok", "embed_model": EMBED_MODEL, "rerank_model": RERANK_MODEL,
            "dim": emb_model.args.hidden_size, "device": str(mx.default_device())}


@app.post("/v1/embeddings")
def embeddings(req: EmbedReq):
    texts = [req.input] if isinstance(req.input, str) else req.input
    t = time.time()
    vecs = embed(texts, req.input_type == "query")
    return {"object": "list", "model": EMBED_MODEL,
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
            "usage": {"seconds": round(time.time() - t, 3)}}


@app.post("/v1/rerank")
def rerank_ep(req: RerankReq):
    scores = rerank(req.query, req.documents)
    ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[: req.top_n or len(scores)]
    return {"model": RERANK_MODEL,
            "results": [{"index": i, "relevance_score": scores[i]} for i in ranked]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("EMBEDDER_PORT", "8765")))
