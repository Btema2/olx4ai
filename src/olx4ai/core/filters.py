"""Client-side filtering the OLX API doesn't support natively."""

from __future__ import annotations

import re


def post_filter(rows: list[dict], args) -> list[dict]:
    out = rows
    if getattr(args, "exclude", None):
        bad = [w.strip() for w in args.exclude.split(",") if w.strip()]
        out = [
            r
            for r in out
            if not any(
                re.search(r"\b" + re.escape(b) + r"\b", r["title"], re.IGNORECASE) for b in bad
            )
        ]
    if getattr(args, "must", None):
        good = [w.strip() for w in args.must.split(",") if w.strip()]
        out = [
            r
            for r in out
            if all(re.search(r"\b" + re.escape(g) + r"\b", r["title"], re.IGNORECASE) for g in good)
        ]
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
