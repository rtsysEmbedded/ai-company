"""LanceDB index: ingest (incremental by file hash) and hybrid search with rerank."""
import hashlib
import os
import time
from typing import Dict, List, Optional

import lancedb
import pyarrow as pa

from . import embedder
from .chunk import chunk_file, supported
from .config import INDEX_DIR, KNOWLEDGE, ROOT

CHUNKS, FILES = "chunks", "files"


def _db():
    os.makedirs(INDEX_DIR, exist_ok=True)
    return lancedb.connect(INDEX_DIR)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT)


def _esc(s: str) -> str:
    return s.replace("'", "''")


def _schema(dim: int) -> pa.Schema:
    return pa.schema([
        ("id", pa.string()), ("path", pa.string()), ("section", pa.string()),
        ("kind", pa.string()), ("lang", pa.string()), ("text", pa.string()),
        ("summary", pa.string()), ("tags", pa.string()),
        ("file_hash", pa.string()), ("model", pa.string()), ("mtime", pa.float64()),
        ("vector", pa.list_(pa.float32(), dim)),
    ])


def _tables(db, dim: int, model: str):
    names = db.table_names()
    if CHUNKS in names:
        chunks = db.open_table(CHUNKS)
        # A different embedding model means incompatible vectors: rebuild from scratch.
        if chunks.count_rows() and chunks.search().limit(1).to_list()[0]["model"] != model:
            db.drop_table(CHUNKS); db.drop_table(FILES); names = []
    if CHUNKS not in names:
        db.create_table(CHUNKS, schema=_schema(dim))
    if FILES not in names:
        db.create_table(FILES, schema=pa.schema([("path", pa.string()), ("file_hash", pa.string())]))
    return db.open_table(CHUNKS), db.open_table(FILES)


def walk(base: str = KNOWLEDGE):
    for d, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            p = os.path.join(d, f)
            if not f.startswith(".") and supported(p):
                yield p


def ingest(base: str = KNOWLEDGE, enrich=None, log=print) -> Dict[str, int]:
    """Index new/changed files, drop removed ones. `enrich(path, text) -> (summary, tags)` is optional."""
    info = embedder.health()
    db = _db()
    chunks_t, files_t = _tables(db, info["dim"], info["embed_model"])
    known = {r["path"]: r["file_hash"] for r in files_t.search().limit(10**9).to_list()}
    seen, stats = set(), {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "chunks": 0}
    t0 = time.time()
    for path in walk(base):
        rel, digest = _rel(path), _sha(path)
        seen.add(rel)
        if known.get(rel) == digest:
            stats["unchanged"] += 1
            continue
        pieces = chunk_file(path)
        summary, tags = enrich(path, "\n".join(c.text for c in pieces)[:12000]) if enrich and pieces else ("", "")
        # Embed with a context header so a chunk knows which file/section it came from.
        header = f"{summary}\nTags: {tags}\n" if summary or tags else ""
        texts = [f"{rel} > {c.section}\n{header}{c.text}" for c in pieces]
        vecs = embedder.embed(texts) if texts else []
        chunks_t.delete(f"path = '{_esc(rel)}'")
        files_t.delete(f"path = '{_esc(rel)}'")
        if pieces:
            chunks_t.add([{
                "id": f"{rel}#{i}", "path": rel, "section": c.section, "kind": c.kind, "lang": c.lang,
                "text": c.text, "summary": summary, "tags": tags, "file_hash": digest,
                "model": info["embed_model"], "mtime": os.path.getmtime(path), "vector": v,
            } for i, (c, v) in enumerate(zip(pieces, vecs))])
        files_t.add([{"path": rel, "file_hash": digest}])
        stats["updated" if rel in known else "added"] += 1
        stats["chunks"] += len(pieces)
        log(f"indexed {rel}: {len(pieces)} chunks")
    base_rel = _rel(base)
    for rel in set(known) - seen:
        if rel.startswith(base_rel):
            chunks_t.delete(f"path = '{_esc(rel)}'"); files_t.delete(f"path = '{_esc(rel)}'")
            stats["removed"] += 1
            log(f"removed {rel}")
    if stats["added"] or stats["updated"] or stats["removed"]:
        chunks_t.create_fts_index("text", replace=True)
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def search(query: str, k: int = 6, path_prefix: Optional[str] = None, kind: Optional[str] = None,
           candidates: int = 50, use_rerank: bool = True) -> List[dict]:
    """Hybrid search: vector top-N + BM25 top-N, fused with RRF, then reranked."""
    db = _db()
    if CHUNKS not in db.table_names():
        return []
    t = db.open_table(CHUNKS)
    where = []
    if path_prefix:
        where.append(f"path LIKE '{_esc(path_prefix)}%'")
    if kind:
        where.append(f"kind = '{_esc(kind)}'")
    flt = " AND ".join(where) or None
    cols = ["id", "path", "section", "kind", "text", "summary", "tags"]

    qv = embedder.embed([query], query=True)[0]
    vq = t.search(qv).limit(candidates).select(cols + ["_distance"])
    vec_hits = (vq.where(flt, prefilter=True) if flt else vq).to_list()
    try:
        fq = t.search(query, query_type="fts").limit(candidates).select(cols + ["_score"])
        fts_hits = (fq.where(flt, prefilter=True) if flt else fq).to_list()
    except Exception:  # no FTS index yet, or a query the FTS parser rejects
        fts_hits = []

    fused: Dict[str, dict] = {}
    for hits in (vec_hits, fts_hits):
        for rank, h in enumerate(hits):
            e = fused.setdefault(h["id"], {c: h[c] for c in cols} | {"rrf": 0.0})
            e["rrf"] += 1.0 / (60 + rank)
    pool = sorted(fused.values(), key=lambda e: -e["rrf"])[:candidates]
    if use_rerank and pool:
        scores = embedder.rerank(query, [f"{e['path']} > {e['section']}\n{e['text']}" for e in pool])
        for e, s in zip(pool, scores):
            e["score"] = round(s, 4)
        pool.sort(key=lambda e: -e["score"])
    else:
        for e in pool:
            e["score"] = round(e["rrf"], 4)
    return pool[:k]


def status() -> dict:
    db = _db()
    if CHUNKS not in db.table_names():
        return {"files": 0, "chunks": 0}
    t = db.open_table(CHUNKS)
    row = t.search().limit(1).to_list()
    return {"files": db.open_table(FILES).count_rows(), "chunks": t.count_rows(),
            "model": row[0]["model"] if row else None, "index_dir": INDEX_DIR}
