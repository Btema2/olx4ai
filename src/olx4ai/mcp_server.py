"""MCP server exposing the core pipeline as tools over stdio."""

from __future__ import annotations

import functools
import json
import os
from typing import Any, Literal

try:
    from mcp.server import MCPServer
except (ImportError, ModuleNotFoundError):
    MCPServer = None


from olx4ai.core import adapters, api_client, cache, filters
from olx4ai.core import format as fmt
from olx4ai.core import html_client
from olx4ai.core import normalize as norm
from olx4ai.core.prerendered import extract_prerendered, find_offers


class _DummyMCP:
    def tool(self):
        def decorator(fn):
            return fn

        return decorator

    def run(self):
        raise SystemExit(
            "olx4ai-mcp requires the mcp extra: pip install 'olx4ai[mcp]' or "
            "uv tool install 'olx4ai[mcp]'"
        )


if MCPServer is not None:
    mcp = MCPServer("olx4ai")
else:
    mcp = _DummyMCP()

SortOption = Literal["relevance", "newest", "price-asc", "price-desc"]
Condition = Literal["new", "used", "damaged"]


class _Args:
    """Duck-types argparse.Namespace for api_client.api_search() and
    filters.post_filter(), which were written against CLI args."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _mcp_safe(fn):
    """cache.fetch() raises SystemExit on network/HTTP errors -- correct for
    a one-shot CLI process, fatal for a long-running server. Translate it
    into a normal exception so one failed fetch doesn't kill the server."""

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit as e:
            raise ValueError(str(e)) from e

    return wrapper


def _search_rows(
    query: str,
    max: int,
    min: int | None,
    max_price: int | None,
    condition: Condition | None,
    sort: SortOption,
    city_id: str | None,
    region_id: str | None,
    category: str | None,
    exclude: str | None,
    must: str | None,
    dedupe: bool,
    no_promoted: bool,
) -> list[dict]:
    args = _Args(
        query=query,
        max=max,
        offset=0,
        min=min,
        max_price=max_price,
        category=category,
        city_id=city_id,
        region_id=region_id,
        condition=condition,
        sort=sort,
        param=None,
        no_cache=False,
        exclude=exclude,
        must=must,
        dedupe=dedupe,
        no_promoted=no_promoted,
    )
    raw = api_client.api_search(args)
    rows = [norm.normalize(adapters.adapt_api_offer(o)) for o in raw]
    return filters.post_filter(rows, args)


@mcp.tool()
@_mcp_safe
def search(
    query: str,
    max: int = 40,
    min: int | None = None,
    max_price: int | None = None,
    condition: Condition | None = None,
    sort: SortOption = "relevance",
    city_id: str | None = None,
    region_id: str | None = None,
    category: str | None = None,
    exclude: str | None = None,
    must: str | None = None,
    dedupe: bool = False,
    no_promoted: bool = False,
) -> list[dict]:
    """Search OLX offers via the JSON API. Returns pruned offer dicts, no raw HTML.

    Args:
        query: Search query string.
        max: Maximum number of offers to return (default 40).
        min: Minimum price in PLN.
        max_price: Maximum price in PLN.
        condition: Filter by condition ('new', 'used', 'damaged').
        sort: Sort order ('relevance', 'newest', 'price-asc', 'price-desc').
        city_id: OLX city ID.
        region_id: OLX region ID.
        category: OLX category ID.
        exclude: Comma-separated words to drop. Drops offers where ANY word is present in title (case-insensitive whole-word matching via regex `\\bword\\b`).
        must: Comma-separated words required. Keeps only offers where ALL words are present in title (AND condition, case-insensitive whole-word matching via regex `\\bword\\b`).
        dedupe: If True, deduplicates offers based on (title.lower().strip(), price) only (collapsing identical title+price across cities/sellers).
        no_promoted: Drop promoted offers.
    """
    rows = _search_rows(
        query,
        max,
        min,
        max_price,
        condition,
        sort,
        city_id,
        region_id,
        category,
        exclude,
        must,
        dedupe,
        no_promoted,
    )
    cache.index_put(rows)
    return rows


@mcp.tool()
@_mcp_safe
def stats(
    query: str,
    min: int | None = None,
    max_price: int | None = None,
    condition: Condition | None = None,
) -> dict[str, Any]:
    """Price distribution (min/p25/median/p75/max + histogram) for a query."""
    rows = _search_rows(
        query,
        100,
        min,
        max_price,
        condition,
        "relevance",
        None,
        None,
        None,
        None,
        None,
        False,
        False,
    )
    return fmt.compute_stats(rows)


@mcp.tool()
@_mcp_safe
def search_url(
    url: str,
    max: int = 40,
    min: int | None = None,
    max_price: int | None = None,
    condition: Condition | None = None,
    sort: SortOption = "relevance",
    exclude: str | None = None,
    must: str | None = None,
    dedupe: bool = False,
    no_promoted: bool = False,
) -> list[dict]:
    """Scrape any OLX listing URL (with OLX's own filters already applied)
    via __PRERENDERED_STATE__.

    Args:
        url: OLX listing URL to scrape.
        max: Maximum number of offers to return (default 40).
        min: Minimum price in PLN.
        max_price: Maximum price in PLN.
        condition: Filter by condition ('new', 'used', 'damaged').
        sort: Sort order ('relevance', 'newest', 'price-asc', 'price-desc').
        exclude: Comma-separated words to drop. Drops offers where ANY word is present in title (case-insensitive whole-word matching via regex `\\bword\\b`).
        must: Comma-separated words required. Keeps only offers where ALL words are present in title (AND condition, case-insensitive whole-word matching via regex `\\bword\\b`).
        dedupe: If True, deduplicates offers based on (title.lower().strip(), price) only (collapsing identical title+price across cities/sellers).
        no_promoted: Drop promoted offers.
    """
    if max <= 0:
        raise SystemExit("max offers must be greater than 0")
    if min is not None and min < 0:
        raise SystemExit("min price cannot be negative")
    if max_price is not None and max_price < 0:
        raise SystemExit("max price cannot be negative")
    raw = html_client.html_search(url, use_cache=True, max_results=max)

    args = _Args(
        min=min,
        max_price=max_price,
        condition=condition,
        sort=sort,
        exclude=exclude,
        must=must,
        dedupe=dedupe,
        no_promoted=no_promoted,
    )
    rows = filters.post_filter([norm.normalize(adapters.adapt_html_offer(o)) for o in raw], args)[
        :max
    ]
    cache.index_put(rows)
    return rows


@mcp.tool()
@_mcp_safe
def offer(target: str, desc_chars: int = 4000) -> dict[str, Any]:
    """Full details (description, specs, seller) for one offer by numeric id or URL."""
    offer_dict = None
    adapt = adapters.adapt_api_offer
    if target.isdigit():
        try:
            payload = json.loads(cache.fetch(f"{cache.API}{target}/", json_mode=True))
            offer_dict = payload.get("data") or payload if isinstance(payload, dict) else None
        except (SystemExit, json.JSONDecodeError, ValueError):
            offer_dict = None
        if offer_dict is None:
            url = cache.index_get(target)
            if not url:
                raise ValueError(
                    f"id {target} not in cache index — run search first, "
                    f"or pass the full offer URL"
                )
            target = url
    if offer_dict is None:
        state = extract_prerendered(cache.fetch(target, json_mode=False))
        cands = find_offers(state)
        offer_dict = cands[0] if cands else None
        adapt = adapters.adapt_html_offer
        if offer_dict is None:
            raise ValueError("could not locate the offer object in the page state")
    return norm.normalize_detail(adapt(offer_dict), desc_chars)


@mcp.tool()
def clear_cache() -> dict[str, Any]:
    """Remove all cached HTTP responses and the id-to-url index."""
    n = 0
    if os.path.isdir(cache.CACHE_DIR):
        for f in os.listdir(cache.CACHE_DIR):
            if f.endswith(".cache"):
                os.remove(os.path.join(cache.CACHE_DIR, f))
                n += 1
        index_file = os.path.join(cache.CACHE_DIR, "index.json")
        if os.path.exists(index_file):
            os.remove(index_file)
    return {"removed": n}


def main() -> None:
    if MCPServer is None or isinstance(mcp, _DummyMCP):
        raise SystemExit(
            "olx4ai-mcp requires the mcp extra: pip install 'olx4ai[mcp]' or "
            "uv tool install 'olx4ai[mcp]'"
        )
    mcp.run()


if __name__ == "__main__":
    main()
