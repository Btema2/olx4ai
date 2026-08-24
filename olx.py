#!/usr/bin/env python3
"""
olx.py - a context-cheap OLX.pl browser for AI agents.

Why: fetching an OLX page dumps ~30k tokens of markup / __PRERENDERED_STATE__
into the model. This tool fetches the same data, prunes it to the ~8 fields that
actually matter, and prints one short line per offer (~20 tokens each).
40 offers ~= 900 tokens instead of ~30k.

Data sources (in order of preference):
  1. OLX public JSON API   https://www.olx.pl/api/v1/offers/?query=...
  2. window.__PRERENDERED_STATE__ scraped out of any OLX listing HTML page
     (used for arbitrary URLs with filters already applied, and as a fallback)

Stdlib only. No pip install.

--------------------------------------------------------------------
AGENT CHEAT SHEET  (print with: ./olx.py agent-help)
--------------------------------------------------------------------
  ./olx.py search "asus vivobook 14" --max 40
  ./olx.py search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
  ./olx.py search "iphone 13" --exclude "obudowa,etui,szybka,części" --dedupe
  ./olx.py stats  "asus vivobook 14"          # price distribution only, ~15 lines
  ./olx.py url    "https://www.olx.pl/oferty/q-asus-vivobook-14/?search%5Bfilter_float_price%3Ato%5D=1500"
  ./olx.py offer  1023456789                  # full description + specs for ONE id
  ./olx.py offer  1023456789 --json
Rules of thumb:
  - Start with `stats` to learn the price band, then `search --min/--max-price`.
  - Default output has no URLs (they are long). Use `offer <id>` or --urls.
  - Everything is cached for 10 min, so repeated calls are free and polite.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone

CHEAT = """olx.py — context-cheap OLX.pl browser. One short line per offer, no HTML.

  olx.py stats  "asus vivobook 14"                  # price distribution first (~15 lines)
  olx.py search "asus vivobook 14" --max 40
  olx.py search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
  olx.py search "iphone 13" --exclude "obudowa,etui,czesci" --dedupe --no-promoted
  olx.py url    "<any olx.pl listing URL with filters already applied>"
  olx.py offer  1023456789                          # description + specs for ONE id
  olx.py search ... --json --fields id,title,price  # machine-readable, still pruned

Line format: N. [id] price flags condition city/district age title
Flags: ~ negotiable | D delivery | B business seller | * promoted
Tips: start with `stats`, then narrow with --min/--max-price. URLs are omitted by
default (they are long) — use `offer <id>`, or --urls if you really need them.
Responses are cached 10 min, so re-running a query costs nothing."""

API = "https://www.olx.pl/api/v1/offers/"
BASE = "https://www.olx.pl"
CACHE_DIR = os.path.expanduser(os.environ.get("OLX_CACHE_DIR", "~/.cache/olx-agent"))
CACHE_TTL = int(os.environ.get("OLX_CACHE_TTL", "600"))
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SLEEP_BETWEEN_PAGES = 0.7


# ----------------------------------------------------------------------------
# transport + cache
# ----------------------------------------------------------------------------
def _cache_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".cache")


def fetch(url: str, *, json_mode: bool, use_cache: bool = True, ttl: int = CACHE_TTL) -> str:
    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*" if json_mode
                  else "text/html,application/xhtml+xml",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": BASE + "/",
        "Connection": "close",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} for {url}\n{e.read()[:400].decode('utf-8', 'replace')}")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"network error for {url}: {e}")

    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    text = raw.decode("utf-8", "replace")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# ----------------------------------------------------------------------------
# __PRERENDERED_STATE__ extraction (for arbitrary HTML pages)
# ----------------------------------------------------------------------------
def _scan_js_string(s: str) -> str:
    """s starts at the opening quote. Return the raw literal including quotes."""
    quote, i, n = s[0], 1, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return s[: i + 1]
        i += 1
    raise ValueError("unterminated JS string")


def _scan_balanced(s: str) -> str:
    depth, i, in_str, esc = 0, 0, False, False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return s[: i + 1]
    raise ValueError("unbalanced JSON")


def extract_prerendered(html: str) -> dict:
    """Pull window.__PRERENDERED_STATE__ out of an OLX page, whatever its encoding."""
    idx = html.find("__PRERENDERED_STATE__")
    if idx == -1:
        raise SystemExit("no __PRERENDERED_STATE__ on this page (bot wall or layout change?)")
    rest = html[html.index("=", idx) + 1:].lstrip()

    if rest[0] in "\"'":
        literal = _scan_js_string(rest)
        if literal[0] == "'":  # normalise to a JSON-parsable double-quoted literal
            literal = '"' + literal[1:-1].replace('"', '\\"').replace("\\'", "'") + '"'
        inner = json.loads(literal)          # -> str
    else:
        inner = _scan_balanced(rest)

    if isinstance(inner, str):
        if inner.lstrip().startswith("%"):   # sometimes URI-encoded
            inner = urllib.parse.unquote(inner)
        return json.loads(inner)
    return inner


def find_offers(node, best=None):
    """Structure-agnostic: find the longest list of dicts that look like offers."""
    best = best or []
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts and len(dicts) >= max(1, len(node) // 2):
            hits = sum(1 for d in dicts if "id" in d and "title" in d
                       and ("url" in d or "params" in d or "price" in d))
            if hits >= max(1, len(dicts) // 2) and len(dicts) > len(best):
                best = dicts
        for x in node:
            best = find_offers(x, best)
    elif isinstance(node, dict):
        for v in node.values():
            best = find_offers(v, best)
    return best


# ----------------------------------------------------------------------------
# normalisation -> tiny dicts
# ----------------------------------------------------------------------------
def _param_map(offer: dict) -> dict:
    out = {}
    for p in offer.get("params") or []:
        if not isinstance(p, dict):
            continue
        v = p.get("value")
        if isinstance(v, dict):
            out[p.get("key")] = v
        else:
            out[p.get("key")] = {"label": v}
    return out


def _rel_age(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    if mins < 60:
        return f"{int(mins)}m"
    if mins < 1440:
        return f"{int(mins // 60)}h"
    return f"{int(mins // 1440)}d"


CONDITION = {"used": "used", "new": "new", "damaged": "damaged",
             "uzywane": "used", "nowe": "new", "uszkodzone": "damaged"}


def normalize(offer: dict) -> dict:
    pm = _param_map(offer)
    price = pm.get("price", {})
    loc = offer.get("location") or {}
    city = (loc.get("city") or {}).get("name")
    district = (loc.get("district") or {}).get("name")
    region = (loc.get("region") or {}).get("name")
    state = (pm.get("state") or {}).get("key") or (pm.get("state") or {}).get("label")

    val = price.get("value")
    if isinstance(val, (int, float)):
        val = int(val)
    else:
        val = None

    return {
        "id": offer.get("id"),
        "title": (offer.get("title") or "").strip(),
        "price": val,
        "price_label": price.get("label") or ("Zamienię" if price.get("arranged") else None),
        "currency": price.get("currency") or "PLN",
        "neg": bool(price.get("negotiable")),
        "cond": CONDITION.get(str(state).lower(), state),
        "city": city,
        "district": district,
        "region": region,
        "age": _rel_age(offer.get("last_refresh_time") or offer.get("created_time")),
        "delivery": bool(((offer.get("delivery") or {}).get("rock") or {}).get("mode")),
        "biz": bool(offer.get("business")),
        "url": offer.get("url"),
        "promoted": bool((offer.get("promotion") or {}).get("top_ad")),
    }


def normalize_detail(offer: dict, desc_chars: int) -> dict:
    d = normalize(offer)
    specs = {}
    for p in offer.get("params") or []:
        if not isinstance(p, dict) or p.get("key") == "price":
            continue
        v = p.get("value")
        label = v.get("label") if isinstance(v, dict) else v
        if label:
            specs[p.get("name") or p.get("key")] = label
    desc = re.sub(r"<[^>]+>", " ", offer.get("description") or "")
    desc = re.sub(r"[ \t]+", " ", desc).strip()
    if desc_chars and len(desc) > desc_chars:
        desc = desc[:desc_chars].rsplit(" ", 1)[0] + " […]"
    d.update({
        "specs": specs,
        "description": desc,
        "seller": (offer.get("user") or {}).get("name"),
        "seller_since": (offer.get("user") or {}).get("created"),
        "photos": len(offer.get("photos") or []),
        "created": offer.get("created_time"),
    })
    return d


# ----------------------------------------------------------------------------
# id -> url index (so the default listing can omit long URLs)
# ----------------------------------------------------------------------------
def index_put(rows):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, "index.json")
    try:
        with open(p, encoding="utf-8") as fh:
            idx = json.load(fh)
    except Exception:  # noqa: BLE001
        idx = {}
    for r in rows:
        if r.get("id") and r.get("url"):
            idx[str(r["id"])] = r["url"]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(idx, fh)


def index_get(offer_id: str):
    try:
        with open(os.path.join(CACHE_DIR, "index.json"), encoding="utf-8") as fh:
            return json.load(fh).get(str(offer_id))
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------
# querying
# ----------------------------------------------------------------------------
SORTS = {
    "relevance": None,
    "newest": "created_at:desc",
    "price-asc": "filter_float_price:asc",
    "price-desc": "filter_float_price:desc",
}


def api_search(args) -> list[dict]:
    rows, offset = [], args.offset
    while len(rows) < args.max:
        params = {
            "offset": offset,
            "limit": min(50, args.max - len(rows)),
            "query": args.query,
            "filter_refiners": "spell_checker",
        }
        if args.min is not None:
            params["filter_float_price:from"] = args.min
        if args.max_price is not None:
            params["filter_float_price:to"] = args.max_price
        if args.category:
            params["category_id"] = args.category
        if args.city_id:
            params["city_id"] = args.city_id
        if args.region_id:
            params["region_id"] = args.region_id
        if args.condition:
            params["filter_enum_state[0]"] = args.condition
        if SORTS.get(args.sort):
            params["sort_by"] = SORTS[args.sort]
        for kv in args.param or []:
            k, _, v = kv.partition("=")
            params[k] = v

        url = API + "?" + urllib.parse.urlencode(params)
        payload = json.loads(fetch(url, json_mode=True, use_cache=not args.no_cache))
        batch = payload.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        total = ((payload.get("metadata") or {}).get("total_elements"))
        if total is not None and offset >= total:
            break
        if len(rows) < args.max:
            time.sleep(SLEEP_BETWEEN_PAGES)
    return rows[: args.max]


def html_search(url: str, use_cache: bool) -> list[dict]:
    state = extract_prerendered(fetch(url, json_mode=False, use_cache=use_cache))
    return find_offers(state)


# ----------------------------------------------------------------------------
# client-side filtering / output
# ----------------------------------------------------------------------------
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


def print_stats(rows: list[dict], label: str):
    prices = sorted(r["price"] for r in rows if r["price"])
    print(f"{label}: {len(rows)} offers, {len(prices)} with a numeric price")
    if not prices:
        return
    q = statistics.quantiles(prices, n=4) if len(prices) > 3 else [prices[0]] * 3
    print(f"  min {prices[0]}  p25 {int(q[0])}  median {int(statistics.median(prices))}  "
          f"p75 {int(q[2])}  max {prices[-1]}")
    lo, hi = prices[0], prices[-1]
    step = max(1, (hi - lo) // 8 or 1)
    for b in range(lo, hi + 1, step):
        c = sum(1 for p in prices if b <= p < b + step)
        if c:
            print(f"  {b:>6}-{b + step - 1:<6} {'#' * min(c, 40)} {c}")
    cheap = [r for r in rows if r["price"] and r["price"] <= q[0]]
    if cheap:
        print("  cheapest ids: " + " ".join(str(r["id"]) for r in sorted(
            cheap, key=lambda r: r["price"])[:5]))


def emit(rows: list[dict], args, label: str):
    index_put(rows)
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


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
def cmd_search(args):
    raw = api_search(args)
    rows = post_filter([normalize(o) for o in raw], args)
    emit(rows, args, f'search "{args.query}"')


def cmd_stats(args):
    args.max = max(args.max, 100)
    raw = api_search(args)
    rows = post_filter([normalize(o) for o in raw], args)
    print_stats(rows, f'"{args.query}"')


def cmd_url(args):
    raw = html_search(args.target, use_cache=not args.no_cache)
    rows = post_filter([normalize(o) for o in raw], args)[: args.max]
    emit(rows, args, args.target)


def cmd_offer(args):
    target = args.target
    offer = None
    if target.isdigit():
        try:
            payload = json.loads(fetch(f"{API}{target}/", json_mode=True,
                                       use_cache=not args.no_cache))
            offer = payload.get("data") or payload
        except SystemExit:
            offer = None
        if offer is None:
            url = index_get(target)
            if not url:
                raise SystemExit(f"id {target} not in cache index — run a search first, "
                                 f"or pass the full offer URL")
            target = url
    if offer is None:
        state = extract_prerendered(fetch(target, json_mode=False,
                                          use_cache=not args.no_cache))
        cands = find_offers(state)
        offer = cands[0] if cands else None
        if offer is None:
            raise SystemExit("could not locate the offer object in the page state")

    d = normalize_detail(offer, args.desc_chars)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        return
    price = f"{d['price']}zł" if d["price"] is not None else (d["price_label"] or "-")
    print(f"{d['title']}\n{price}{' (negotiable)' if d['neg'] else ''}  |  "
          f"{d['cond'] or '?'}  |  "
          f"{', '.join(x for x in (d['city'], d['district'], d['region']) if x)}  |  "
          f"{d['age']} old  |  {d['photos']} photos")
    if d["specs"]:
        print("specs: " + "; ".join(f"{k}={v}" for k, v in d["specs"].items()))
    if d["seller"]:
        print(f"seller: {d['seller']}{' (business)' if d['biz'] else ''}")
    if d["url"]:
        print(d["url"])
    if d["description"]:
        print("---\n" + d["description"])


def cmd_agent_help(args):
    print(CHEAT)


def cmd_clear_cache(args):
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".cache"):
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
    print(f"removed {n} cached responses")


# ----------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="olx.py", description="context-cheap OLX.pl browser for AI agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_query=True):
        if with_query:
            sp.add_argument("query")
        sp.add_argument("--max", type=int, default=40, help="max offers (default 40)")
        sp.add_argument("--offset", type=int, default=0)
        sp.add_argument("--min", type=int, help="min price PLN")
        sp.add_argument("--max-price", type=int, help="max price PLN")
        sp.add_argument("--sort", choices=list(SORTS), default="relevance")
        sp.add_argument("--condition", choices=["new", "used", "damaged"])
        sp.add_argument("--used", dest="condition", action="store_const", const="used")
        sp.add_argument("--category", help="OLX category_id")
        sp.add_argument("--city-id")
        sp.add_argument("--region-id")
        sp.add_argument("--param", action="append",
                        help="raw API param, repeatable, e.g. --param filter_enum_hdd_type[0]=ssd")
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
    o.add_argument("--desc-chars", type=int, default=1200, help="0 = full description")
    o.add_argument("--json", action="store_true")
    o.add_argument("--no-cache", action="store_true")
    o.set_defaults(func=cmd_offer)

    a = sub.add_parser("agent-help", help="short usage contract for an LLM")
    a.set_defaults(func=cmd_agent_help)

    c = sub.add_parser("clear-cache")
    c.set_defaults(func=cmd_clear_cache)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    try:
        args.func(args)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        sys.exit(130)
