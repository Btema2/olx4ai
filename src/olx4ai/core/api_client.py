"""JSON API search path -- https://www.olx.pl/api/v1/offers/ (or the
configured domain's equivalent)."""

from __future__ import annotations

import json
import time
import urllib.parse

from olx4ai.core import cache

SORTS = {
    "relevance": None,
    "newest": "created_at:desc",
    "price-asc": "filter_float_price:asc",
    "price-desc": "filter_float_price:desc",
}

SLEEP_BETWEEN_PAGES = 0.7


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

        url = cache.API + "?" + urllib.parse.urlencode(params)
        payload = json.loads(cache.fetch(url, json_mode=True, use_cache=not args.no_cache))
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
