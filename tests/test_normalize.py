from olx4ai.core.adapters import adapt_api_offer, adapt_html_offer
from olx4ai.core.normalize import normalize, normalize_detail


def test_normalize_api_sourced_offer(api_offer):
    d = normalize(adapt_api_offer(api_offer))
    assert d["price"] == 1500
    assert d["cond"] == "used"
    assert d["city"] == "Warszawa"
    assert d["district"] == "Mokotów"
    assert d["neg"] is True
    assert d["promoted"] is True


def test_normalize_html_sourced_offer_regression(html_offer_raw):
    """Regression test for Bug 1: before the adapter existed, price/city/
    district/age all came out None/'?' for HTML-sourced offers."""
    d = normalize(adapt_html_offer(html_offer_raw))
    assert d["price"] == 900
    assert d["cond"] == "used"
    assert d["city"] == "Kraków"
    assert d["district"] == "Podgórze"
    assert d["age"] != "?"
    assert d["biz"] is False


def test_normalize_missing_price_falls_back_to_dash():
    offer = {"id": 1, "title": "No price", "params": [], "location": {}}
    d = normalize(offer)
    assert d["price"] is None
    assert d["price_label"] is None


def test_normalize_detail_extracts_specs_and_strips_html_from_description(api_offer):
    d = normalize_detail(adapt_api_offer(api_offer), desc_chars=0)
    assert d["specs"]["Pamięć RAM"] == "16 GB"
    assert "<br />" not in d["description"]
    assert "Great condition laptop." in d["description"]
    assert d["seller"] == "TestSeller"
    assert d["photos"] == 2


def test_normalize_detail_truncates_long_description(api_offer):
    d = normalize_detail(adapt_api_offer(api_offer), desc_chars=10)
    assert d["description"].endswith(" […]")
    assert len(d["description"]) <= 14
