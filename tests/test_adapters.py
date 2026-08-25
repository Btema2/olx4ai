from olx4ai.core.adapters import adapt_api_offer, adapt_html_offer
from olx4ai.core.prerendered import extract_prerendered, find_offers


def test_adapt_api_offer_is_identity(api_offer):
    assert adapt_api_offer(api_offer) is api_offer


def test_adapt_html_offer_extracts_price_from_top_level_price_object(html_offer_raw):
    adapted = adapt_html_offer(html_offer_raw)
    price_param = next(p for p in adapted["params"] if p["key"] == "price")
    assert price_param["value"]["value"] == 900
    assert price_param["value"]["currency"] == "PLN"
    assert price_param["value"]["negotiable"] is False


def test_adapt_html_offer_normalizes_condition_to_key_label_dict(html_offer_raw):
    adapted = adapt_html_offer(html_offer_raw)
    state_param = next(p for p in adapted["params"] if p["key"] == "state")
    assert state_param["value"] == {"key": "used", "label": "Używane"}


def test_adapt_html_offer_nests_location(html_offer_raw):
    adapted = adapt_html_offer(html_offer_raw)
    assert adapted["location"]["city"]["name"] == "Kraków"
    assert adapted["location"]["district"]["name"] == "Podgórze"
    assert adapted["location"]["region"]["name"] == "Małopolskie"


def test_adapt_html_offer_handles_missing_district(html_listing_html):
    state = extract_prerendered(html_listing_html)
    second_offer = find_offers(state)[1]  # fixture's second offer has no district
    adapted = adapt_html_offer(second_offer)
    assert adapted["location"]["district"] is None


def test_adapt_html_offer_renames_timestamps_and_business_flag(html_offer_raw):
    adapted = adapt_html_offer(html_offer_raw)
    assert adapted["created_time"] == html_offer_raw["createdTime"]
    assert adapted["last_refresh_time"] == html_offer_raw["lastRefreshTime"]
    assert adapted["business"] is False
