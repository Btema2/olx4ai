"""HTML-scrape search path -- reuses __PRERENDERED_STATE__ from any listing URL."""

from __future__ import annotations

from olx4ai.core import cache
from olx4ai.core.prerendered import extract_prerendered, find_offers


def html_search(url: str, use_cache: bool) -> list[dict]:
    state = extract_prerendered(cache.fetch(url, json_mode=False, use_cache=use_cache))
    return find_offers(state)
