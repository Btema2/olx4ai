from olx4ai.core.prerendered import extract_prerendered, find_offers

RAW_OBJECT_HTML = """<html><body><script>
window.__PRERENDERED_STATE__ = {"listing":{"listing":{"data":[
  {"id": 1, "title": "Offer A", "url": "https://example.com/a", "params": []},
  {"id": 2, "title": "Offer B", "url": "https://example.com/b", "params": []}
]}}};
</script></body></html>"""

JS_STRING_HTML = """<html><body><script>
window.__PRERENDERED_STATE__ = "{\\"listing\\":{\\"listing\\":{\\"data\\":[{\\"id\\":1,\\"title\\":\\"Offer A\\",\\"url\\":\\"https://example.com/a\\",\\"params\\":[]}]}}}";
</script></body></html>"""

SINGLE_OFFER_HTML = """<html><body><script>
window.__PRERENDERED_STATE__ = {"ad":{"ad":{"id": 99, "title": "Solo Offer", "url": "https://example.com/solo", "params": []}}};
</script></body></html>"""


def test_extract_prerendered_raw_object_variant():
    state = extract_prerendered(RAW_OBJECT_HTML)
    assert state["listing"]["listing"]["data"][0]["title"] == "Offer A"


def test_extract_prerendered_js_string_variant():
    state = extract_prerendered(JS_STRING_HTML)
    assert state["listing"]["listing"]["data"][0]["title"] == "Offer A"


def test_extract_prerendered_raises_when_marker_absent():
    with pytest_raises_system_exit():
        extract_prerendered("<html><body>nothing here</body></html>")


def test_extract_prerendered_raises_on_malformed_json():
    import pytest

    with pytest.raises(SystemExit, match="malformed __PRERENDERED_STATE__"):
        extract_prerendered(
            "<html><body><script>window.__PRERENDERED_STATE__ = {invalid json};</script></body></html>"
        )
    with pytest.raises(SystemExit, match="malformed __PRERENDERED_STATE__"):
        extract_prerendered(
            '<html><body><script>window.__PRERENDERED_STATE__ = "{invalid json string";</script></body></html>'
        )
    with pytest.raises(SystemExit, match="malformed __PRERENDERED_STATE__"):
        extract_prerendered(
            "<html><body><script>window.__PRERENDERED_STATE__ = </script></body></html>"
        )


def pytest_raises_system_exit():
    import pytest

    return pytest.raises(SystemExit)


def test_find_offers_locates_list_of_offers():
    state = extract_prerendered(RAW_OBJECT_HTML)
    offers = find_offers(state)
    assert len(offers) == 2
    assert {o["id"] for o in offers} == {1, 2}


def test_find_offers_locates_single_bare_offer_dict():
    """Regression test for Bug 2: detail pages hold the offer as a lone
    dict, not inside a list."""
    state = extract_prerendered(SINGLE_OFFER_HTML)
    offers = find_offers(state)
    assert len(offers) == 1
    assert offers[0]["id"] == 99
    assert offers[0]["title"] == "Solo Offer"


def test_find_offers_prefers_list_over_single_dict_when_both_present():
    state = {
        "solo": {"id": 1, "title": "Should Not Win", "url": "https://example.com/x"},
        "list": {
            "data": [
                {"id": 2, "title": "A", "url": "https://example.com/a"},
                {"id": 3, "title": "B", "url": "https://example.com/b"},
            ]
        },
    }
    offers = find_offers(state)
    assert len(offers) == 2
    assert {o["id"] for o in offers} == {2, 3}


def test_find_offers_returns_empty_list_when_nothing_matches():
    assert find_offers({"unrelated": {"nested": [1, 2, 3]}}) == []


def test_find_offers_on_real_listing_fixture_returns_two(html_listing_html):
    state = extract_prerendered(html_listing_html)
    offers = find_offers(state)
    assert len(offers) == 2
    assert {o["id"] for o in offers} == {2000000001, 2000000002}
