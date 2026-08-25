import json

import pytest

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402

from olx4ai.core import cache  # noqa: E402
from olx4ai.mcp_server import mcp  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.configure("olx.pl")
    yield


async def test_search_tool_returns_pruned_offers(monkeypatch, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"query": "test laptop", "max": 5})
    offers = result.structured_content["result"]
    assert offers[0]["price"] == 1500
    assert offers[0]["city"] == "Warszawa"


async def test_search_tool_populates_cache_index(monkeypatch, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"query": "test laptop", "max": 5})
    offers = result.structured_content["result"]
    offer_id = offers[0]["id"]
    assert cache.index_get(offer_id) == offers[0]["url"]


async def test_stats_tool_returns_structured_distribution(monkeypatch, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    async with Client(mcp) as client:
        result = await client.call_tool("stats", {"query": "test laptop"})
    assert result.structured_content["min"] == 1500
    assert result.structured_content["count"] == 1


async def test_search_url_tool_uses_html_adapter(monkeypatch, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    async with Client(mcp) as client:
        result = await client.call_tool("search_url", {"url": "https://www.olx.pl/oferty/q-test/"})
    offers = result.structured_content["result"]
    assert len(offers) == 2
    assert offers[0]["price"] == 900
    assert offers[0]["city"] == "Kraków"


async def test_offer_tool_from_html_fallback(monkeypatch, html_offer_detail_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_offer_detail_html)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "offer", {"target": "https://www.olx.pl/d/oferta/test-vacuum.html"}
        )
    assert result.structured_content["price"] == 250
    assert result.structured_content["city"] == "Wrocław"


async def test_offer_tool_translates_fetch_errors_to_tool_errors(monkeypatch):
    def raise_system_exit(url, **kw):
        raise SystemExit("network error for https://example.com: boom")

    monkeypatch.setattr(cache, "fetch", raise_system_exit)
    async with Client(mcp) as client:
        result = await client.call_tool("offer", {"target": "https://example.com/x"})
    assert result.is_error is True


async def test_offer_tool_desc_chars_default_matches_cli():
    async with Client(mcp) as client:
        result = await client.list_tools()
    schema = next(t for t in result.tools if t.name == "offer").input_schema
    assert schema["properties"]["desc_chars"]["default"] == 4000


async def test_clear_cache_tool_reports_removed_count(tmp_path):
    (tmp_path / "abc.cache").write_text("x")
    async with Client(mcp) as client:
        result = await client.call_tool("clear_cache", {})
    assert result.structured_content["removed"] == 1
