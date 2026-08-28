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


async def test_search_tool_rejects_empty_query():
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"query": ""})
    assert result.is_error is True
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"query": "   "})
    assert result.is_error is True


async def test_stats_tool_returns_structured_distribution(monkeypatch, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    async with Client(mcp) as client:
        result = await client.call_tool("stats", {"query": "test laptop"})
    assert result.structured_content["min"] == 1500
    assert result.structured_content["count"] == 1


async def test_stats_tool_rejects_empty_query():
    async with Client(mcp) as client:
        result = await client.call_tool("stats", {"query": ""})
    assert result.is_error is True
    async with Client(mcp) as client:
        result = await client.call_tool("stats", {"query": "   "})
    assert result.is_error is True


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


async def test_offer_tool_numeric_id_falls_back_to_html_when_api_returns_malformed_json(
    monkeypatch, html_offer_detail_html
):
    cache.index_put([{"id": 999, "url": "https://www.olx.pl/d/oferta/test-vacuum.html"}])

    def fake_fetch(url, **kw):
        if "api/v1/offers" in url:
            return "<html>not json</html>"
        return html_offer_detail_html

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    async with Client(mcp) as client:
        result = await client.call_tool("offer", {"target": "999"})
    assert result.structured_content["price"] == 250
    assert "Vacuum" in result.structured_content["title"]


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


async def test_search_and_search_url_parameter_descriptions():
    async with Client(mcp) as client:
        result = await client.list_tools()
    search_schema = next(t for t in result.tools if t.name == "search").input_schema
    search_url_schema = next(t for t in result.tools if t.name == "search_url").input_schema

    for schema in (search_schema, search_url_schema):
        assert "exclude" in schema["properties"]
        assert "must" in schema["properties"]
        assert "dedupe" in schema["properties"]
        # If descriptions are populated:
        exclude_desc = schema["properties"]["exclude"].get("description", "")
        must_desc = schema["properties"]["must"].get("description", "")
        dedupe_desc = schema["properties"]["dedupe"].get("description", "")
        if exclude_desc:
            assert "ANY" in exclude_desc or "whole-word" in exclude_desc
        if must_desc:
            assert "ALL" in must_desc or "whole-word" in must_desc
        if dedupe_desc:
            assert "title" in dedupe_desc and "price" in dedupe_desc



async def test_clear_cache_tool_reports_removed_count(tmp_path):
    (tmp_path / "abc.cache").write_text("x")
    (tmp_path / "index.json").write_text("{}")
    async with Client(mcp) as client:
        result = await client.call_tool("clear_cache", {})
    assert result.structured_content["removed"] == 1
    assert not (tmp_path / "index.json").exists()


async def test_search_url_tool_applies_min_and_max_price_filters(monkeypatch, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    async with Client(mcp) as client:
        result_min = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "min": 1000}
        )
        offers_min = result_min.structured_content["result"]
        assert len(offers_min) == 1
        assert offers_min[0]["price"] == 1800

        result_max = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "max_price": 1000}
        )
        offers_max = result_max.structured_content["result"]
        assert len(offers_max) == 1
        assert offers_max[0]["price"] == 900


async def test_search_url_tool_applies_condition_filter(monkeypatch, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    async with Client(mcp) as client:
        result_new = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "condition": "new"}
        )
        offers_new = result_new.structured_content["result"]
        assert len(offers_new) == 1
        assert offers_new[0]["title"] == "Test Phone Model B 256GB"


async def test_search_url_tool_rejects_ssrf_url():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_url", {"url": "https://169.254.169.254/latest/meta-data/"}
        )
    assert result.is_error is True


async def test_offer_tool_rejects_ssrf_url():
    async with Client(mcp) as client:
        result = await client.call_tool("offer", {"target": "https://127.0.0.1:8080/admin"})
    assert result.is_error is True


async def test_search_url_tool_rejects_plaintext_http():
    async with Client(mcp) as client:
        result = await client.call_tool("search_url", {"url": "http://www.olx.pl/oferty/q-test/"})
    assert result.is_error is True


async def test_search_tool_rejects_zero_or_negative_max():
    async with Client(mcp) as client:
        res1 = await client.call_tool("search", {"query": "laptop", "max": 0})
        assert res1.is_error is True
        res2 = await client.call_tool("search", {"query": "laptop", "max": -5})
        assert res2.is_error is True


async def test_search_url_tool_rejects_zero_or_negative_max():
    async with Client(mcp) as client:
        res1 = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "max": 0}
        )
        assert res1.is_error is True
        res2 = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "max": -5}
        )
        assert res2.is_error is True


async def test_search_url_tool_rejects_negative_min_or_max_price():
    async with Client(mcp) as client:
        res1 = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "min": -10}
        )
        assert res1.is_error is True
        res2 = await client.call_tool(
            "search_url", {"url": "https://www.olx.pl/oferty/q-test/", "max_price": -10}
        )
        assert res2.is_error is True


async def test_search_url_tool_supports_sort(monkeypatch, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_url",
            {"url": "https://www.olx.pl/oferty/q-test/", "sort": "price-desc"},
        )
        offers = result.structured_content["result"]
        assert len(offers) == 2
        assert offers[0]["price"] == 1800
        assert offers[1]["price"] == 900


def test_mcp_entrypoint_missing_dependency(monkeypatch):
    import olx4ai.mcp_server as server

    monkeypatch.setattr(server, "MCPServer", None)
    monkeypatch.setattr(server, "mcp", server._DummyMCP())
    with pytest.raises(SystemExit, match="olx4ai-mcp requires the mcp extra"):
        server.main()
