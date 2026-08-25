from pathlib import Path

import json

import pytest

from olx4ai.core.prerendered import extract_prerendered, find_offers

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def html_listing_html() -> str:
    return (FIXTURES / "html_listing_page.html").read_text(encoding="utf-8")


@pytest.fixture
def html_offer_raw(html_listing_html: str) -> dict:
    state = extract_prerendered(html_listing_html)
    return find_offers(state)[0]


@pytest.fixture
def api_search_payload() -> dict:
    return json.loads((FIXTURES / "api_search_response.json").read_text(encoding="utf-8"))


@pytest.fixture
def api_offer(api_search_payload) -> dict:
    return api_search_payload["data"][0]


@pytest.fixture
def html_offer_detail_html() -> str:
    return (FIXTURES / "html_offer_page.html").read_text(encoding="utf-8")


@pytest.fixture
def html_offer_detail_raw(html_offer_detail_html) -> dict:
    state = extract_prerendered(html_offer_detail_html)
    return find_offers(state)[0]
