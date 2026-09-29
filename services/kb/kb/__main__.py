"""CLI: python -m kb ingest [dir] | search "query" [-k N] [--path P] | status"""
import argparse
import json

from . import store
from .enrich import afm_status, make_enricher


def main():
    ap = argparse.ArgumentParser(prog="kb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest"); ing.add_argument("base", nargs="?"); ing.add_argument("--no-enrich", action="store_true")
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("-k", type=int, default=6)
    s.add_argument("--path"); s.add_argument("--kind"); s.add_argument("--no-rerank", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "ingest":
        enrich = None if a.no_enrich else make_enricher()
        kw = {"enrich": enrich} | ({"base": a.base} if a.base else {})
        print(json.dumps(store.ingest(**kw)))
    elif a.cmd == "search":
        for h in store.search(a.query, a.k, a.path, a.kind, use_rerank=not a.no_rerank):
            print(f"[{h['score']}] {h['path']} > {h['section']}\n    {h['text'][:160]!r}")
    else:
        print(json.dumps(store.status() | {"apple_model": afm_status()}, indent=2))


if __name__ == "__main__":
    main()
