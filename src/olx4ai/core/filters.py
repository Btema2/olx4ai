"""Client-side filtering and sorting that the OLX API doesn't support natively.

Filter semantics:
- exclude: drops offers where ANY word in the comma-separated list is present in the title
  (case-insensitive, whole-word matching via regex `\bword\b`).
- must: keeps only offers where ALL words in the comma-separated list are present in the title
  (AND condition, case-insensitive, whole-word matching via regex `\bword\b`).
- dedupe: deduplicates offers based on `(title.lower().strip(), price)` only
  (identically titled and priced offers across different cities, regions, or sellers are collapsed into one).
- min / max_price: filters by price bounds (inclusive), dropping unpriced items.
- condition: filters by condition string ('new', 'used', 'damaged').
- no_promoted: drops offers flagged as promoted.
- sort: client-side sorting ('price-asc', 'price-desc', 'relevance').
"""

from __future__ import annotations

import re


def post_filter(rows: list[dict], args) -> list[dict]:
    """Apply client-side post-filtering and sorting to offer rows.

    Parameters:
        rows: List of normalized offer dictionaries.
        args: Argument namespace or object with optional filter attributes:
            - min (int): Minimum price threshold (inclusive).
            - max_price (int): Maximum price threshold (inclusive).
            - condition (str): Filter by condition ('new', 'used', 'damaged').
            - exclude (str): Comma-separated words to drop. Drops an offer if ANY
              word matches in the title (case-insensitive, whole-word matching via regex `\bword\b`).
            - must (str): Comma-separated words required. Keeps an offer only if ALL
              words match in the title (AND condition, case-insensitive, whole-word matching via regex `\bword\b`).
            - dedupe (bool): If True, deduplicates offers based on `(title.lower().strip(), price)`
              only (identically titled and priced offers across different cities, regions,
              or sellers are collapsed into one).
            - no_promoted (bool): If True, drops promoted offers.
            - sort (str): 'price-asc', 'price-desc', or 'relevance'.

    Returns:
        Filtered and sorted list of offer dictionaries.
    """
    if getattr(args, "min", None) is not None and args.min < 0:
        raise SystemExit("min price cannot be negative")
    if getattr(args, "max_price", None) is not None and args.max_price < 0:
        raise SystemExit("max price cannot be negative")
    out = rows
    if getattr(args, "min", None) is not None:
        out = [r for r in out if r.get("price") is not None and r["price"] >= args.min]
    if getattr(args, "max_price", None) is not None:
        out = [r for r in out if r.get("price") is not None and r["price"] <= args.max_price]

    if getattr(args, "condition", None):
        out = [r for r in out if r.get("cond") == args.condition]
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
        out = [r for r in out if not r.get("promoted")]
    if getattr(args, "dedupe", False):
        seen, ded = set(), []
        for r in out:
            k = (r["title"].lower().strip(), r["price"])
            if k in seen:
                continue
            seen.add(k)
            ded.append(r)
        out = ded

    sort_opt = getattr(args, "sort", None)
    if sort_opt == "price-asc":
        out = sorted(
            out,
            key=lambda r: (
                r.get("price") is None,
                r.get("price") if r.get("price") is not None else 0,
            ),
        )
    elif sort_opt == "price-desc":
        out = sorted(
            out,
            key=lambda r: (
                r.get("price") is None,
                -(r.get("price") if r.get("price") is not None else 0),
            ),
        )

    return out
