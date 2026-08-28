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


def test_search_rejects_empty_query():
    sys.argv = ["olx4ai", "search", ""]
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        main()
    sys.argv = ["olx4ai", "search", "   "]
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        main()


def test_stats_prints_price_distribution(monkeypatch, capsys, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    sys.argv = ["olx4ai", "stats", "test laptop"]
    main()
    out = capsys.readouterr().out
    assert "1 offers" in out
    assert "min 1500" in out


def test_stats_rejects_empty_query():
    sys.argv = ["olx4ai", "stats", ""]
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        main()
    sys.argv = ["olx4ai", "stats", "   "]
    with pytest.raises(SystemExit, match="search query cannot be empty"):
        main()


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


def test_offer_command_numeric_id_falls_back_to_html_when_api_returns_malformed_json(
    monkeypatch, capsys, html_offer_detail_html
):
    cache.index_put([{"id": 999, "url": "https://www.olx.pl/d/oferta/test-vacuum.html"}])

    def fake_fetch(url, **kw):
        if "api/v1/offers" in url:
            return "<html>not json</html>"
        return html_offer_detail_html

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    sys.argv = ["olx4ai", "offer", "999", "--json"]
    main()
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["price"] == 250
    assert "Vacuum" in d["title"]


def test_agent_help_prints_cheat_sheet(capsys):
    sys.argv = ["olx4ai", "agent-help"]
    main()
    out = capsys.readouterr().out
    assert "olx4ai" in out
    assert "context-cheap" in out


def test_agent_help_documents_url_filters_and_city_slug_pattern(capsys):
    sys.argv = ["olx4ai", "agent-help"]
    main()
    out = capsys.readouterr().out
    # keeps the generic "filters already applied" example ...
    assert "<any olx.pl listing URL with filters already applied>" in out
    # ... AND the concrete city-slug pattern tip, not one replacing the other
    assert "/city-slug/q-*" in out
    assert "/oferty/w-*" in out


def test_clear_cache_removes_cached_files(tmp_path, capsys):
    (tmp_path / "abc.cache").write_text("x")
    (tmp_path / "index.json").write_text("{}")
    sys.argv = ["olx4ai", "clear-cache"]
    main()
    out = capsys.readouterr().out
    assert "removed 1 cached responses" in out
    assert not (tmp_path / "abc.cache").exists()
    assert not (tmp_path / "index.json").exists()


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


def test_url_command_filters_by_min_and_max_price(monkeypatch, capsys, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    # Filter with min 1000 -> drops 900zł offer, keeps 1800zł offer
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--min", "1000"]
    main()
    out = capsys.readouterr().out
    assert "Test Phone Model B" in out
    assert "1800zł" in out
    assert "Test Phone Model A" not in out

    # Filter with max-price 1000 -> keeps 900zł offer, drops 1800zł offer
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--max-price", "1000"]
    main()
    out = capsys.readouterr().out
    assert "Test Phone Model A" in out
    assert "900zł" in out
    assert "Test Phone Model B" not in out


def test_url_command_filters_by_condition(monkeypatch, capsys, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    # Filter with --used -> keeps used offer, drops new offer
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--used"]
    main()
    out = capsys.readouterr().out
    assert "Test Phone Model A" in out
    assert "Test Phone Model B" not in out

    # Filter with --condition new -> keeps new offer, drops used offer
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--condition", "new"]
    main()
    out = capsys.readouterr().out
    assert "Test Phone Model B" in out
    assert "Test Phone Model A" not in out


def test_url_command_rejects_ssrf_url():
    sys.argv = ["olx4ai", "url", "https://169.254.169.254/latest/meta-data/"]
    with pytest.raises(SystemExit, match="refusing private/internal host in URL"):
        main()


def test_offer_command_rejects_ssrf_url():
    sys.argv = ["olx4ai", "offer", "https://127.0.0.1:8080/admin"]
    with pytest.raises(SystemExit, match="refusing private/internal host in URL"):
        main()


def test_url_command_rejects_plaintext_http():
    sys.argv = ["olx4ai", "url", "http://www.olx.pl/oferty/q-test/"]
    with pytest.raises(SystemExit, match="refusing non-https URL"):
        main()


def test_search_rejects_negative_min():
    sys.argv = ["olx4ai", "search", "test", "--min", "-10"]
    with pytest.raises(SystemExit, match="min price cannot be negative"):
        main()


def test_search_rejects_negative_max_price():
    sys.argv = ["olx4ai", "search", "test", "--max-price", "-10"]
    with pytest.raises(SystemExit, match="max price cannot be negative"):
        main()


def test_url_command_rejects_negative_min():
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--min", "-10"]
    with pytest.raises(SystemExit, match="min price cannot be negative"):
        main()


def test_url_command_rejects_negative_max_price():
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--max-price", "-10"]
    with pytest.raises(SystemExit, match="max price cannot be negative"):
        main()


def test_search_rejects_zero_or_negative_max():
    sys.argv = ["olx4ai", "search", "test", "--max", "0"]
    with pytest.raises(SystemExit, match="max offers must be greater than 0"):
        main()
    sys.argv = ["olx4ai", "search", "test", "--max", "-5"]
    with pytest.raises(SystemExit, match="max offers must be greater than 0"):
        main()


def test_url_command_rejects_zero_or_negative_max():
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--max", "0"]
    with pytest.raises(SystemExit, match="max offers must be greater than 0"):
        main()
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--max", "-5"]
    with pytest.raises(SystemExit, match="max offers must be greater than 0"):
        main()
