"""Rendering: compact per-offer lines, price-distribution stats, and the
emit() dispatcher shared by search/url output."""

from __future__ import annotations

import json
import statistics

from olx4ai.core import cache


def fmt_line(r: dict, n: int, title_chars: int, show_url: bool) -> str:
    price = f"{r['price']}zł" if r["price"] is not None else (r["price_label"] or "-")
    flags = ("~" if r["neg"] else "") + ("D" if r["delivery"] else "") + \
            ("B" if r["biz"] else "") + ("*" if r["promoted"] else "")
    where = "/".join(x for x in (r["city"], r["district"]) if x) or "?"
    title = r["title"]
    if len(title) > title_chars:
        title = title[: title_chars - 1] + "…"
    line = (f"{n:>3}. [{r['id']}] {price:<9}{flags:<4} "
            f"{(r['cond'] or '?'):<8}{where:<26} {r['age']:>4}  {title}")
    if show_url and r.get("url"):
        line += "\n      " + r["url"]
    return line


def compute_stats(rows: list[dict]) -> dict:
    prices = sorted(r["price"] for r in rows if r["price"])
    result = {"count": len(rows), "priced_count": len(prices)}
    if not prices:
        return result
    q = statistics.quantiles(prices, n=4) if len(prices) > 3 else [prices[0]] * 3
    result.update({
        "min": prices[0], "p25": int(q[0]), "median": int(statistics.median(prices)),
        "p75": int(q[2]), "max": prices[-1],
    })
    lo, hi = prices[0], prices[-1]
    step = max(1, (hi - lo) // 8 or 1)
    histogram = []
    for b in range(lo, hi + 1, step):
        c = sum(1 for p in prices if b <= p < b + step)
        if c:
            histogram.append({"low": b, "high": b + step - 1, "count": c})
    result["histogram"] = histogram
    cheap = [r for r in rows if r["price"] and r["price"] <= q[0]]
    result["cheapest_ids"] = [r["id"] for r in sorted(cheap, key=lambda r: r["price"])[:5]]
    return result


def print_stats(rows: list[dict], label: str) -> None:
    stats = compute_stats(rows)
    print(f"{label}: {stats['count']} offers, {stats['priced_count']} with a numeric price")
    if stats["priced_count"] == 0:
        return
    print(f"  min {stats['min']}  p25 {stats['p25']}  median {stats['median']}  "
          f"p75 {stats['p75']}  max {stats['max']}")
    for b in stats["histogram"]:
        print(f"  {b['low']:>6}-{b['high']:<6} {'#' * min(b['count'], 40)} {b['count']}")
    if stats["cheapest_ids"]:
        print("  cheapest ids: " + " ".join(str(i) for i in stats["cheapest_ids"]))


def emit(rows: list[dict], args, label: str) -> None:
    cache.index_put(rows)
    if args.json:
        fields = args.fields.split(",") if args.fields else None
        data = [{k: v for k, v in r.items() if not fields or k in fields} for r in rows]
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        return
    if not rows:
        print("no results")
        return
    print(f"# {label} — {len(rows)} offers   (flags: ~negotiable D=delivery "
          f"B=business *=promoted)")
    for i, r in enumerate(rows, 1):
        print(fmt_line(r, i, args.title_chars, args.urls))
    prices = [r["price"] for r in rows if r["price"]]
    if prices:
        print(f"# price: min {min(prices)} / median {int(statistics.median(prices))} "
              f"/ max {max(prices)} zł")
