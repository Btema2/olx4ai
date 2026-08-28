"""HTML-scrape search path -- reuses __PRERENDERED_STATE__ from any listing URL."""

from __future__ import annotations

import time
import urllib.parse
from typing import Any


from olx4ai.core import api_client, cache
from olx4ai.core.prerendered import extract_prerendered, find_offers


def _build_page_url(url: str, page_num: int) -> str:
    parsed = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in qs if k != "page"]
    if page_num > 1:
        filtered.append(("page", str(page_num)))
    new_query = urllib.parse.urlencode(filtered)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
    )


def html_search(
    url: str, use_cache: bool, max_results: int | None = None
) -> list[dict]:
    parsed = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    page_param = next((v for k, v in qs if k == "page"), None)
    try:
        start_page = int(page_param) if page_param else 1
    except ValueError:
        start_page = 1
    if start_page < 1:
        start_page = 1

    current_page = start_page
    all_offers: list[dict] = []
    seen_keys: set[Any] = set()

    while True:
        if current_page == 1 and not page_param:
            page_url = url
        elif current_page == start_page and page_param:
            page_url = url
        else:
            page_url = _build_page_url(url, current_page)

        try:
            raw_html = cache.fetch(page_url, json_mode=False, use_cache=use_cache)
            state = extract_prerendered(raw_html)
            batch = find_offers(state)
        except Exception:
            if all_offers:
                break
            raise

        if not batch:
            break

        new_count = 0
        for offer in batch:
            offer_id = offer.get("id")
            key = str(offer_id) if offer_id is not None else (offer.get("title"), offer.get("url"))
            if key not in seen_keys:
                seen_keys.add(key)
                all_offers.append(offer)
                new_count += 1

        if new_count == 0:
            break

        if max_results is not None and len(all_offers) >= max_results:
            break

        current_page += 1
        if max_results is not None and len(all_offers) < max_results:
            time.sleep(api_client.SLEEP_BETWEEN_PAGES)

    return all_offers

