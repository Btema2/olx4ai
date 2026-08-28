import argparse
import json

import pytest

from olx4ai.core import api_client, cache


def make_args(**overrides):
    defaults = dict(
        query="test laptop",
        max=40,
        offset=0,
        min=None,
        max_price=None,
        category=None,
        city_id=None,
        region_id=None,
        condition=None,
        sort="relevance",
        param=None,
        no_cache=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_api_search_returns_offers_from_single_page(monkeypatch, api_search_payload):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return json.dumps(api_search_payload)

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    rows = api_client.api_search(make_args(max=5))
    assert len(rows) == 1
    assert rows[0]["title"] == "Test Laptop 14 inch 16GB RAM 512GB SSD"
    assert len(calls) == 1
    assert "test+laptop" in calls[0] or "test%20laptop" in calls[0]


def test_api_search_respects_domain_configuration(monkeypatch, api_search_payload):
    captured = {}

    def fake_fetch(url, **kw):
        captured["url"] = url
        return json.dumps(api_search_payload)

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    cache.configure("olx.ua")
    try:
        api_client.api_search(make_args())
    finally:
        cache.configure("olx.pl")
    assert captured["url"].startswith("https://www.olx.ua/api/v1/offers/")


def test_api_search_stops_when_batch_empty(monkeypatch):
    monkeypatch.setattr(
        cache,
        "fetch",
        lambda url, **kw: json.dumps({"data": [], "metadata": {"total_elements": 0}}),
    )
    rows = api_client.api_search(make_args())
    assert rows == []


def test_api_search_includes_condition_and_price_range_params(monkeypatch, api_search_payload):
    captured = {}

    def fake_fetch(url, **kw):
        captured["url"] = url
        return json.dumps(api_search_payload)

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    api_client.api_search(make_args(min=800, max_price=2000, condition="used"))
    assert "filter_float_price%3Afrom=800" in captured["url"]
    assert "filter_float_price%3Ato=2000" in captured["url"]
    assert "filter_enum_state%5B0%5D=used" in captured["url"]


def test_api_search_rejects_empty_or_whitespace_query():
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        api_client.api_search(make_args(query=""))
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        api_client.api_search(make_args(query="   "))
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        api_client.api_search(make_args(query=None))


def test_api_search_handles_malformed_json_response(monkeypatch):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: "<html>not json</html>")
    with pytest.raises(SystemExit, match="malformed JSON response"):
        api_client.api_search(make_args())


def test_api_search_handles_non_dict_json_response(monkeypatch):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: "[1, 2, 3]")
    with pytest.raises(SystemExit, match="malformed JSON response"):
        api_client.api_search(make_args())
