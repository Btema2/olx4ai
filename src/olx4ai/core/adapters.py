"""Adapters that reshape each fetch path's raw offer dict into the JSON-API
shape that normalize()/normalize_detail() expect (see the design spec's
"Bugs found during testing" section for why this exists)."""

from __future__ import annotations


def adapt_api_offer(raw: dict) -> dict:
    """The JSON API's own shape is what normalize() already expects — no
    adaptation needed. Named explicitly (rather than skipped) so every call
    site treats both sources symmetrically."""
    return raw


def adapt_html_offer(raw: dict) -> dict:
    """Reshape an offer scraped from __PRERENDERED_STATE__ into the JSON
    API's shape: nested location, price folded into params, condition as a
    key/label dict, snake_case timestamps, snake_case `business`."""
    loc = raw.get("location") or {}
    price = raw.get("price") or {}
    regular = price.get("regularPrice") or {}

    adapted_params = []
    for p in raw.get("params") or []:
        if not isinstance(p, dict):
            continue
        if p.get("key") == "state":
            adapted_params.append(
                {
                    **p,
                    "value": {"key": p.get("normalizedValue"), "label": p.get("value")},
                }
            )
        else:
            adapted_params.append(p)
    adapted_params.append(
        {
            "key": "price",
            "name": "Cena",
            "type": "price",
            "value": {
                "value": regular.get("value"),
                "label": price.get("displayValue"),
                "currency": regular.get("currencyCode"),
                "negotiable": bool(regular.get("negotiable")),
                "arranged": bool(price.get("exchange")),
            },
        }
    )

    return {
        **raw,
        "params": adapted_params,
        "location": {
            "city": {"name": loc.get("cityName")},
            "district": {"name": loc.get("districtName")} if loc.get("districtName") else None,
            "region": {"name": loc.get("regionName")},
        },
        "created_time": raw.get("createdTime"),
        "last_refresh_time": raw.get("lastRefreshTime"),
        "business": bool(raw.get("isBusiness")),
    }
