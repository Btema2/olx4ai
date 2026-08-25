from olx4ai.core import cache, html_client


def test_html_search_returns_offers_from_listing_page(monkeypatch, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    rows = html_client.html_search("https://www.olx.pl/oferty/q-test/", use_cache=True)
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {2000000001, 2000000002}


def test_html_search_passes_use_cache_through(monkeypatch, html_listing_html):
    captured = {}

    def fake_fetch(url, **kw):
        captured.update(kw)
        return html_listing_html

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    html_client.html_search("https://www.olx.pl/oferty/q-test/", use_cache=False)
    assert captured["use_cache"] is False
