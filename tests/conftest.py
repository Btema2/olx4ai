from pathlib import Path

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
