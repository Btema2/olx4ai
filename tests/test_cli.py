import json
import sys

import pytest

from olx4ai.cli import main
from olx4ai.core import cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.configure("olx.pl")
    yield


def test_search_prints_offer_lines(monkeypatch, capsys, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    sys.argv = ["olx4ai", "search", "test laptop", "--max", "5"]
    main()
    out = capsys.readouterr().out
    assert "Test Laptop 14 inch" in out
    assert "1500zł" in out
    assert "Warszawa" in out


def test_stats_prints_price_distribution(monkeypatch, capsys, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    sys.argv = ["olx4ai", "stats", "test laptop"]
    main()
    out = capsys.readouterr().out
    assert "1 offers" in out
    assert "min 1500" in out


def test_url_command_prints_html_sourced_offers(monkeypatch, capsys, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/"]
    main()
    out = capsys.readouterr().out
    assert "Test Phone Model A" in out
    assert "900zł" in out
    assert "Kraków" in out
    assert "used" in out


def test_offer_command_json_mode_from_html_fallback(monkeypatch, capsys, html_offer_detail_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_offer_detail_html)
    sys.argv = ["olx4ai", "offer", "https://www.olx.pl/d/oferta/test-vacuum.html", "--json"]
    main()
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["price"] == 250
    assert d["city"] == "Wrocław"
    assert d["cond"] == "used"
    assert "Vacuum" in d["title"]


def test_agent_help_prints_cheat_sheet(capsys):
    sys.argv = ["olx4ai", "agent-help"]
    main()
    out = capsys.readouterr().out
    assert "olx4ai" in out
    assert "context-cheap" in out


def test_clear_cache_removes_cached_files(tmp_path, capsys):
    (tmp_path / "abc.cache").write_text("x")
    (tmp_path / "index.json").write_text("{}")
    sys.argv = ["olx4ai", "clear-cache"]
    main()
    out = capsys.readouterr().out
    assert "removed 1 cached responses" in out
    assert not (tmp_path / "abc.cache").exists()
    assert (tmp_path / "index.json").exists()


def test_domain_flag_reconfigures_target_urls(monkeypatch):
    captured = {}

    def fake_fetch(url, **kw):
        captured["url"] = url
        return json.dumps({"data": [], "metadata": {"total_elements": 0}})

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    sys.argv = ["olx4ai", "--domain", "olx.ua", "search", "test"]
    main()
    assert "olx.ua" in captured["url"]


def test_domain_env_var_survives_when_flag_omitted(monkeypatch):
    captured = {}

    def fake_fetch(url, **kw):
        captured["url"] = url
        return json.dumps({"data": [], "metadata": {"total_elements": 0}})

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    cache.configure("olx.ua")  # simulate what import-time OLX4AI_DOMAIN would have set
    try:
        sys.argv = ["olx4ai", "search", "test"]  # no --domain flag
        main()
        assert "olx.ua" in captured["url"]
    finally:
        cache.configure("olx.pl")
