"""HTTP fetch with on-disk caching, plus the id->url index."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import zlib

DOMAIN = os.environ.get("OLX4AI_DOMAIN", "olx.pl")
BASE = f"https://www.{DOMAIN}"
API = f"{BASE}/api/v1/offers/"
CACHE_DIR = os.path.expanduser(os.environ.get("OLX4AI_CACHE_DIR", "~/.cache/olx4ai"))
CACHE_TTL = int(os.environ.get("OLX4AI_CACHE_TTL", "600"))
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def configure(domain: str | None = None) -> None:
    """Override the target OLX domain at runtime (e.g. from --domain)."""
    global DOMAIN, BASE, API
    if domain:
        DOMAIN = domain
        BASE = f"https://www.{DOMAIN}"
        API = f"{BASE}/api/v1/offers/"


def _cache_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".cache")


def fetch(url: str, *, json_mode: bool, use_cache: bool = True, ttl: int = CACHE_TTL) -> str:
    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/json, text/plain, */*"
                if json_mode
                else "text/html,application/xhtml+xml"
            ),
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": BASE + "/",
            "Connection": "close",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} for {url}\n{e.read()[:400].decode('utf-8', 'replace')}")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"network error for {url}: {e}")

    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    text = raw.decode("utf-8", "replace")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def index_put(rows: list[dict]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, "index.json")
    try:
        with open(p, encoding="utf-8") as fh:
            idx = json.load(fh)
    except Exception:  # noqa: BLE001
        idx = {}
    for r in rows:
        if r.get("id") and r.get("url"):
            idx[str(r["id"])] = r["url"]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(idx, fh)


def index_get(offer_id: str) -> str | None:
    try:
        with open(os.path.join(CACHE_DIR, "index.json"), encoding="utf-8") as fh:
            return json.load(fh).get(str(offer_id))
    except Exception:  # noqa: BLE001
        return None
