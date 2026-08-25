"""Thin argparse CLI for olx4ai -- wiring only, all logic lives in core/."""

from __future__ import annotations

import argparse
import json
import os
import sys

from olx4ai.core import adapters, api_client, cache, filters
from olx4ai.core import format as fmt
from olx4ai.core import html_client
from olx4ai.core import normalize as norm
from olx4ai.core.prerendered import extract_prerendered, find_offers

CHEAT = """olx4ai — context-cheap OLX browser. One short line per offer, no HTML.

  olx4ai stats  "asus vivobook 14"                  # price distribution first (~15 lines)
  olx4ai search "asus vivobook 14" --max 40
  olx4ai search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
  olx4ai search "iphone 13" --exclude "obudowa,etui,czesci" --dedupe --no-promoted
  olx4ai url    "https://www.olx.pl/warszawa/q-iphone/"  # /city-slug/q-*; not /oferty/w-* (404s)
  olx4ai offer  1023456789                          # description + specs for ONE id
  olx4ai search ... --json --fields id,title,price  # machine-readable, still pruned

Line format: N. [id] price flags condition city/district age title
Flags: ~ negotiable | D delivery | B business seller | * promoted
Tips: start with `stats`, then narrow with --min/--max-price. URLs are omitted by
default (they are long) — use `offer <id>`, or --urls if you really need them.
Responses are cached 10 min, so re-running a query costs nothing.
Use --domain to point at another OLX Europe site (untested outside olx.pl)."""


def cmd_search(args: argparse.Namespace) -> None:
    raw = api_client.api_search(args)
    rows = filters.post_filter([norm.normalize(adapters.adapt_api_offer(o)) for o in raw], args)
    fmt.emit(rows, args, f'search "{args.query}"')


def cmd_stats(args: argparse.Namespace) -> None:
    args.max = max(args.max, 100)
    raw = api_client.api_search(args)
    rows = filters.post_filter([norm.normalize(adapters.adapt_api_offer(o)) for o in raw], args)
    fmt.print_stats(rows, f'"{args.query}"')


def cmd_url(args: argparse.Namespace) -> None:
    raw = html_client.html_search(args.target, use_cache=not args.no_cache)
    rows = filters.post_filter([norm.normalize(adapters.adapt_html_offer(o)) for o in raw], args)[
        : args.max
    ]
    fmt.emit(rows, args, args.target)


def cmd_offer(args: argparse.Namespace) -> None:
    target = args.target
    offer = None
    adapt = adapters.adapt_api_offer
    if target.isdigit():
        try:
            payload = json.loads(
                cache.fetch(f"{cache.API}{target}/", json_mode=True, use_cache=not args.no_cache)
            )
            offer = payload.get("data") or payload
        except SystemExit:
            offer = None
        if offer is None:
            url = cache.index_get(target)
            if not url:
                raise SystemExit(
                    f"id {target} not in cache index — run a search first, "
                    f"or pass the full offer URL"
                )
            target = url
    if offer is None:
        state = extract_prerendered(
            cache.fetch(target, json_mode=False, use_cache=not args.no_cache)
        )
        cands = find_offers(state)
        offer = cands[0] if cands else None
        adapt = adapters.adapt_html_offer
        if offer is None:
            raise SystemExit("could not locate the offer object in the page state")

    d = norm.normalize_detail(adapt(offer), args.desc_chars)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        return
    price = f"{d['price']}zł" if d["price"] is not None else (d["price_label"] or "-")
    print(
        f"{d['title']}\n{price}{' (negotiable)' if d['neg'] else ''}  |  "
        f"{d['cond'] or '?'}  |  "
        f"{', '.join(x for x in (d['city'], d['district'], d['region']) if x)}  |  "
        f"{d['age']} old  |  {d['photos']} photos"
    )
    if d["specs"]:
        print("specs: " + "; ".join(f"{k}={v}" for k, v in d["specs"].items()))
    if d["seller"]:
        print(f"seller: {d['seller']}{' (business)' if d['biz'] else ''}")
    if d["url"]:
        print(d["url"])
    if d["description"]:
        print("---\n" + d["description"])


def cmd_agent_help(args: argparse.Namespace) -> None:
    print(CHEAT)


def cmd_clear_cache(args: argparse.Namespace) -> None:
    n = 0
    if os.path.isdir(cache.CACHE_DIR):
        for f in os.listdir(cache.CACHE_DIR):
            if f.endswith(".cache"):
                os.remove(os.path.join(cache.CACHE_DIR, f))
                n += 1
    print(f"removed {n} cached responses")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="olx4ai", description="context-cheap OLX browser for AI agents"
    )
    p.add_argument(
        "--domain",
        default=None,
        help="OLX domain to target (default olx.pl, or $OLX4AI_DOMAIN "
        "if set; other OLX Europe domains untested)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, with_query: bool = True) -> None:
        if with_query:
            sp.add_argument("query")
        sp.add_argument("--max", type=int, default=40, help="max offers (default 40)")
        sp.add_argument("--offset", type=int, default=0)
        sp.add_argument("--min", type=int, help="min price PLN")
        sp.add_argument("--max-price", type=int, help="max price PLN")
        sp.add_argument("--sort", choices=list(api_client.SORTS), default="relevance")
        sp.add_argument("--condition", choices=["new", "used", "damaged"])
        sp.add_argument("--used", dest="condition", action="store_const", const="used")
        sp.add_argument("--category", help="OLX category_id")
        sp.add_argument("--city-id")
        sp.add_argument("--region-id")
        sp.add_argument(
            "--param",
            action="append",
            help="raw API param, repeatable, e.g. --param filter_enum_hdd_type[0]=ssd",
        )
        sp.add_argument("--exclude", help="comma-separated words to drop from titles")
        sp.add_argument("--must", help="comma-separated words the title must contain")
        sp.add_argument("--dedupe", action="store_true")
        sp.add_argument("--no-promoted", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--fields", help="json mode: comma-separated field whitelist")
        sp.add_argument("--urls", action="store_true", help="print offer URLs too")
        sp.add_argument("--title-chars", type=int, default=80)
        sp.add_argument("--no-cache", action="store_true")

    s = sub.add_parser("search", help="search offers (JSON API)")
    common(s)
    s.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="price distribution only")
    common(st)
    st.set_defaults(func=cmd_stats)

    u = sub.add_parser("url", help="scrape any OLX listing URL via __PRERENDERED_STATE__")
    u.add_argument("target")
    common(u, with_query=False)
    u.set_defaults(func=cmd_url)

    o = sub.add_parser("offer", help="details of one offer by id or URL")
    o.add_argument("target")
    o.add_argument("--desc-chars", type=int, default=4000, help="0 = full description")
    o.add_argument("--json", action="store_true")
    o.add_argument("--no-cache", action="store_true")
    o.set_defaults(func=cmd_offer)

    a = sub.add_parser("agent-help", help="short usage contract for an LLM")
    a.set_defaults(func=cmd_agent_help)

    c = sub.add_parser("clear-cache")
    c.set_defaults(func=cmd_clear_cache)
    return p


def main() -> None:
    args = build_parser().parse_args()
    cache.configure(args.domain)
    try:
        args.func(args)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
