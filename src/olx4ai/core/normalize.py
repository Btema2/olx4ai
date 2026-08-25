"""Offer dicts -> the ~14 fields the CLI/MCP actually expose. Operates ONLY
on the JSON-API shape -- HTML-sourced offers must go through
adapters.adapt_html_offer() first."""

from __future__ import annotations

import re
from datetime import datetime, timezone

CONDITION = {"used": "used", "new": "new", "damaged": "damaged",
             "uzywane": "used", "nowe": "new", "uszkodzone": "damaged"}


def _param_map(offer: dict) -> dict:
    out = {}
    for p in offer.get("params") or []:
        if not isinstance(p, dict):
            continue
        v = p.get("value")
        if isinstance(v, dict):
            out[p.get("key")] = v
        else:
            out[p.get("key")] = {"label": v}
    return out


def _rel_age(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    if mins < 60:
        return f"{int(mins)}m"
    if mins < 1440:
        return f"{int(mins // 60)}h"
    return f"{int(mins // 1440)}d"


def normalize(offer: dict) -> dict:
    pm = _param_map(offer)
    price = pm.get("price", {})
    loc = offer.get("location") or {}
    city = (loc.get("city") or {}).get("name")
    district = (loc.get("district") or {}).get("name")
    region = (loc.get("region") or {}).get("name")
    state = (pm.get("state") or {}).get("key") or (pm.get("state") or {}).get("label")

    val = price.get("value")
    if isinstance(val, (int, float)):
        val = int(val)
    else:
        val = None

    return {
        "id": offer.get("id"),
        "title": (offer.get("title") or "").strip(),
        "price": val,
        "price_label": price.get("label") or ("Zamienię" if price.get("arranged") else None),
        "currency": price.get("currency") or "PLN",
        "neg": bool(price.get("negotiable")),
        "cond": CONDITION.get(str(state).lower(), state),
        "city": city,
        "district": district,
        "region": region,
        "age": _rel_age(offer.get("last_refresh_time") or offer.get("created_time")),
        "delivery": bool(((offer.get("delivery") or {}).get("rock") or {}).get("mode")),
        "biz": bool(offer.get("business")),
        "url": offer.get("url"),
        "promoted": bool((offer.get("promotion") or {}).get("top_ad")),
    }


def normalize_detail(offer: dict, desc_chars: int) -> dict:
    d = normalize(offer)
    specs = {}
    for p in offer.get("params") or []:
        if not isinstance(p, dict) or p.get("key") == "price":
            continue
        v = p.get("value")
        label = v.get("label") if isinstance(v, dict) else v
        if label:
            specs[p.get("name") or p.get("key")] = label
    desc = re.sub(r"<[^>]+>", " ", offer.get("description") or "")
    desc = re.sub(r"[ \t]+", " ", desc).strip()
    if desc_chars and len(desc) > desc_chars:
        desc = desc[:desc_chars].rsplit(" ", 1)[0] + " […]"
    d.update({
        "specs": specs,
        "description": desc,
        "seller": (offer.get("user") or {}).get("name"),
        "seller_since": (offer.get("user") or {}).get("created"),
        "photos": len(offer.get("photos") or []),
        "created": offer.get("created_time"),
    })
    return d
