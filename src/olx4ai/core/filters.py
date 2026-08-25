"""Client-side filtering the OLX API doesn't support natively."""

from __future__ import annotations


def post_filter(rows: list[dict], args) -> list[dict]:
    out = rows
    if getattr(args, "exclude", None):
        bad = [w.strip().lower() for w in args.exclude.split(",") if w.strip()]
        out = [r for r in out if not any(b in r["title"].lower() for b in bad)]
    if getattr(args, "must", None):
        good = [w.strip().lower() for w in args.must.split(",") if w.strip()]
        out = [r for r in out if all(g in r["title"].lower() for g in good)]
    if getattr(args, "no_promoted", False):
        out = [r for r in out if not r["promoted"]]
    if getattr(args, "dedupe", False):
        seen, ded = set(), []
        for r in out:
            k = (r["title"].lower().strip(), r["price"])
            if k in seen:
                continue
            seen.add(k)
            ded.append(r)
        out = ded
    return out
