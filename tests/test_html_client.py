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


def test_html_search_paginates_when_max_exceeds_page_size(monkeypatch, html_listing_html):
    fetched_urls = []

    page1_html = html_listing_html
    page2_html = html_listing_html.replace("2000000001", "2000000003").replace(
        "2000000002", "2000000004"
    )

    def fake_fetch(url, **kw):
        fetched_urls.append(url)
        if "page=2" in url:
            return page2_html
        return page1_html

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    monkeypatch.setattr(html_client.time, "sleep", lambda s: None)

    rows = html_client.html_search(
        "https://www.olx.pl/warszawa/q-asus/", use_cache=True, max_results=3
    )
    assert len(rows) == 4
    assert [r["id"] for r in rows] == [2000000001, 2000000002, 2000000003, 2000000004]
    assert len(fetched_urls) == 2
    assert fetched_urls[0] == "https://www.olx.pl/warszawa/q-asus/"
    assert fetched_urls[1] == "https://www.olx.pl/warszawa/q-asus/?page=2"


def test_html_search_stops_when_no_new_offers_found(monkeypatch, html_listing_html):
    fetched_urls = []

    def fake_fetch(url, **kw):
        fetched_urls.append(url)
        # Returns the same html with same IDs on page 2
        return html_listing_html

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    monkeypatch.setattr(html_client.time, "sleep", lambda s: None)

    rows = html_client.html_search(
        "https://www.olx.pl/warszawa/q-asus/", use_cache=True, max_results=10
    )
    assert len(rows) == 2
    # Page 1 (2 offers), page 2 returned duplicate IDs so it stopped
    assert len(fetched_urls) == 2


def test_html_search_preserves_query_params_across_pages(monkeypatch, html_listing_html):
    fetched_urls = []

    page1_html = html_listing_html
    page2_html = html_listing_html.replace("2000000001", "2000000003").replace(
        "2000000002", "2000000004"
    )

    def fake_fetch(url, **kw):
        fetched_urls.append(url)
        if "page=2" in url:
            return page2_html
        return page1_html

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    monkeypatch.setattr(html_client.time, "sleep", lambda s: None)

    html_client.html_search(
        "https://www.olx.pl/elektronika/?search%5Bfilter_enum_state%5D%5B0%5D=used",
        use_cache=True,
        max_results=3,
    )
    assert len(fetched_urls) == 2
    assert "search%5Bfilter_enum_state%5D%5B0%5D=used" in fetched_urls[1]
    assert "page=2" in fetched_urls[1]

