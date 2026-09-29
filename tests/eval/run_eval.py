"""Retrieval eval: index tests/eval/corpus into a throwaway index, run questions.yaml, report hit@5."""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["KB_INDEX_DIR"] = tempfile.mkdtemp(prefix="kb-eval-")
sys.path.insert(0, os.path.join(HERE, "..", "..", "services", "kb"))

import yaml  # noqa: E402
from kb import store  # noqa: E402

corpus = os.path.join(HERE, "corpus")
try:
    print("ingest:", store.ingest(corpus, log=lambda *_: None))
    print("re-ingest (should be all unchanged):", store.ingest(corpus, log=lambda *_: None))
    qs = yaml.safe_load(open(os.path.join(HERE, "questions.yaml")))
    for mode, rr in (("hybrid+rerank", True), ("hybrid only", False)):
        hits, top1, t0 = 0, 0, time.time()
        for item in qs:
            res = store.search(item["q"], k=5, use_rerank=rr)
            paths = [r["path"].split("corpus/", 1)[-1] for r in res]
            ok = item["expect"] in paths
            hits += ok; top1 += bool(paths) and paths[0] == item["expect"]
            if rr and not ok:
                print(f"  MISS: {item['q']!r} -> {paths[:3]}")
        ms = (time.time() - t0) / len(qs) * 1000
        print(f"{mode:14} hit@5 {hits}/{len(qs)}  top1 {top1}/{len(qs)}  avg {ms:.0f} ms/query")
finally:
    shutil.rmtree(os.environ["KB_INDEX_DIR"], ignore_errors=True)


def incremental_check():
    """Change, add and delete files; only those should be re-indexed."""
    import tempfile as tf
    os.environ["KB_INDEX_DIR"] = tf.mkdtemp(prefix="kb-inc-")
    import importlib, kb.config, kb.store  # noqa: E401
    importlib.reload(kb.config); importlib.reload(kb.store)
    work = tf.mkdtemp(prefix="kb-corpus-")
    shutil.copytree(corpus, work, dirs_exist_ok=True)
    try:
        s = kb.store
        s.ingest(work, log=lambda *_: None)
        open(os.path.join(work, "docs/can-bus-notes.md"), "a").write("\n## Termination\nUse 120 ohm at both ends.\n")
        open(os.path.join(work, "docs/new.md"), "w").write("# Watchdog\nKick the IWDG every 100 ms from the idle task.\n")
        os.remove(os.path.join(work, "code/pid.c"))
        st = s.ingest(work, log=lambda *_: None)
        ok = st["added"] == 1 and st["updated"] == 1 and st["removed"] == 1 and st["unchanged"] == 8
        top = s.search("how often do we kick the watchdog", k=1)[0]["path"]
        print("incremental:", st, "| new doc found:", top.endswith("docs/new.md"), "| PASS" if ok else "| FAIL")
    finally:
        shutil.rmtree(work, ignore_errors=True); shutil.rmtree(os.environ["KB_INDEX_DIR"], ignore_errors=True)


incremental_check()
