# olx4ai Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the single-file `olx.py` script into an installable `olx4ai` package with a shared core, a CLI wrapper, and an MCP server wrapper — fixing two real bugs found in the HTML-scrape data path along the way.

**Architecture:** A `src/olx4ai/core/` package holds all business logic in small single-responsibility modules (cache/transport, `__PRERENDERED_STATE__` extraction, two fetch clients, two shape adapters, normalization, filtering, rendering). `cli.py` and `mcp_server.py` are thin wrappers that only wire arguments to `core/` calls — neither contains business logic. Every module is covered by fixture-based pytest tests with no live network calls.

**Tech Stack:** Python >=3.10, stdlib only for the core/CLI, official `mcp` Python SDK (`mcp[cli]`) as an optional extra for the MCP server, pytest + pytest-asyncio for tests, GitHub Actions for CI.

**Spec:** `docs/superpowers/specs/2026-08-25-olx4ai-rewrite-design.md`

## Global Constraints

- Python `>=3.10` (the source already uses `X | None` union syntax).
- Base package install has **zero** third-party dependencies. The `mcp` SDK is
  only pulled in via the `[mcp]` optional extra.
- **Live-value module access only, never `from ... import NAME`** for
  anything `cache.py` exposes that can change at runtime (`API`, `BASE`,
  `DOMAIN`, `CACHE_DIR`). Every consumer must `from olx4ai.core import cache`
  and reference `cache.API` / `cache.BASE` / `cache.fetch(...)` at call time.
  Binding a local name at import time (`from olx4ai.core.cache import API`)
  freezes a stale value before `cache.configure()` (the `--domain` flag) or
  test monkeypatching can take effect — this is a correctness requirement,
  not a style preference.
- Cache directory is `~/.cache/olx4ai/` (env var `OLX4AI_CACHE_DIR`, default
  TTL via `OLX4AI_CACHE_TTL`, default 600s) — renamed from the old
  `olx-agent`/`OLX_CACHE_*` names since nothing depends on the old names yet.
- Domain defaults to `olx.pl`, overridable via `--domain` (CLI) or
  `OLX4AI_DOMAIN` (env var, read at import time as the initial default).
  Only `olx.pl` is verified; other OLX Europe domains are unverified but
  structurally supported.
- No live network calls inside the pytest suite — every test uses fixtures
  under `tests/fixtures/` and monkeypatches `cache.fetch`.
- MCP server uses **stdio transport only** — no HTTP/SSE.
- GitHub-only distribution (no PyPI publish) — `pip install "olx4ai @
  git+https://github.com/Btema2/OLX4AI"` and `pip install "olx4ai[mcp] @
  git+https://github.com/Btema2/OLX4AI"`.
- MIT license, copyright holder `Btema2` (the GitHub account publishing this
  repo — see `gh auth status`), year 2026.
- Code follows PEP 8, and every function signature carries type annotations
  (the one deliberate exception: `args`/`**kwargs` parameters in
  `filters.post_filter`, `api_client.api_search`, and `mcp_server._Args`
  that intentionally duck-type across `argparse.Namespace` and the MCP
  server's own arg holder — a precise type there would need a `Protocol`
  for no real benefit). `black`, `isort`, and `ruff` (all in the `dev`
  extra, added in Task 1) are the formatting/lint standard; Task 12 runs
  them across the full tree once and wires the CI lint gate, rather than
  gating every intermediate task commit on it.

---

### Task 1: Project scaffolding + `core/cache.py`

**Files:**
- Create: `pyproject.toml`
- Create: `src/olx4ai/__init__.py`
- Create: `src/olx4ai/core/__init__.py`
- Create: `src/olx4ai/core/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `cache.DOMAIN: str`, `cache.BASE: str`, `cache.API: str`,
  `cache.CACHE_DIR: str`, `cache.CACHE_TTL: int`, `cache.UA: str`,
  `cache.configure(domain: str | None) -> None`,
  `cache.fetch(url: str, *, json_mode: bool, use_cache: bool = True, ttl: int = CACHE_TTL) -> str`,
  `cache.index_put(rows: list[dict]) -> None`,
  `cache.index_get(offer_id: str) -> str | None`.
  All later tasks consume these via `from olx4ai.core import cache`.

- [ ] **Step 1: Create the package skeleton and `pyproject.toml`**

```bash
mkdir -p src/olx4ai/core tests/fixtures
touch src/olx4ai/core/__init__.py
```

`src/olx4ai/__init__.py`:

```python
__version__ = "0.1.0"
```

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "olx4ai"
version = "0.1.0"
description = "Context-cheap OLX browser for AI agents — CLI and MCP server."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
dependencies = []

[project.optional-dependencies]
mcp = ["mcp[cli]>=1.12.4"]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "black>=24", "isort>=5"]

[project.scripts]
olx4ai = "olx4ai.cli:main"
olx4ai-mcp = "olx4ai.mcp_server:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.black]
line-length = 100
target-version = ["py310"]

[tool.isort]
profile = "black"
line_length = 100
```

- [ ] **Step 2: Write the failing test**

`tests/test_cache.py`:

```python
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from olx4ai.core import cache


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    yield
    cache.configure("olx.pl")  # reset domain for any later test module


def _fake_response(body: bytes, encoding: str = ""):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers.get.return_value = encoding
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_configure_overrides_domain_and_derived_urls():
    cache.configure("olx.ua")
    assert cache.DOMAIN == "olx.ua"
    assert cache.BASE == "https://www.olx.ua"
    assert cache.API == "https://www.olx.ua/api/v1/offers/"


def test_configure_with_no_domain_is_a_noop():
    cache.configure("olx.pl")
    before = (cache.DOMAIN, cache.BASE, cache.API)
    cache.configure(None)
    assert (cache.DOMAIN, cache.BASE, cache.API) == before


def test_fetch_writes_and_reads_from_cache():
    resp = _fake_response(b'{"ok": true}')
    with patch("urllib.request.urlopen", return_value=resp) as urlopen:
        text = cache.fetch("https://example.com/x", json_mode=True)
        assert text == '{"ok": true}'
        assert urlopen.call_count == 1

        text2 = cache.fetch("https://example.com/x", json_mode=True)
        assert text2 == text
        assert urlopen.call_count == 1  # second call served from cache


def test_fetch_bypasses_stale_cache():
    path = cache._cache_path("https://example.com/stale")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("old")
    old_time = time.time() - cache.CACHE_TTL - 1
    os.utime(path, (old_time, old_time))

    resp = _fake_response(b"fresh")
    with patch("urllib.request.urlopen", return_value=resp):
        text = cache.fetch("https://example.com/stale", json_mode=True)
    assert text == "fresh"


def test_fetch_ignores_cache_when_use_cache_false():
    resp1 = _fake_response(b"first")
    with patch("urllib.request.urlopen", return_value=resp1):
        cache.fetch("https://example.com/y", json_mode=True)

    resp2 = _fake_response(b"second")
    with patch("urllib.request.urlopen", return_value=resp2):
        text = cache.fetch("https://example.com/y", json_mode=True, use_cache=False)
    assert text == "second"


def test_index_put_and_get_round_trip():
    cache.index_put([{"id": 42, "url": "https://example.com/42"}])
    assert cache.index_get("42") == "https://example.com/42"
    assert cache.index_get("999") is None


def test_index_get_missing_index_file_returns_none():
    assert cache.index_get("1") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.cache'` (or `'olx4ai.core'` itself, since only `__init__.py` exists so far).

- [ ] **Step 4: Write the implementation**

`src/olx4ai/core/cache.py`:

```python
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

    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*" if json_mode
                  else "text/html,application/xhtml+xml",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": BASE + "/",
        "Connection": "close",
    })
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
```

- [ ] **Step 5: Install the project and run tests to verify they pass**

Run: `pip install -e ".[dev]" && pytest tests/test_cache.py -v`
Expected: 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/olx4ai/__init__.py src/olx4ai/core/__init__.py \
        src/olx4ai/core/cache.py tests/test_cache.py
git commit -m "feat: scaffold olx4ai package and add core/cache.py"
```

---

### Task 2: `core/prerendered.py` (Bug 2 fix)

**Files:**
- Create: `src/olx4ai/core/prerendered.py`
- Create: `tests/fixtures/html_listing_page.html`
- Create: `tests/conftest.py`
- Test: `tests/test_prerendered.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `prerendered.extract_prerendered(html: str) -> dict`,
  `prerendered.find_offers(node, best=None) -> list[dict]`. Consumed by
  Task 3 (`test_adapters.py`, via the `html_offer_raw` fixture), Task 8
  (`html_client.py`), Task 10 (`cli.py`'s `offer` HTML fallback), Task 11
  (`mcp_server.py`'s `offer` HTML fallback).
- Produces fixtures: `tests/conftest.py` fixtures `html_listing_html` (raw
  HTML text) and `html_offer_raw` (the first offer dict extracted from it,
  in HTML/`__PRERENDERED_STATE__` shape) — reused by Tasks 3, 4, 8, 10.

- [ ] **Step 1: Create the sanitized HTML listing fixture**

`tests/fixtures/html_listing_page.html`:

```html
<!doctype html>
<html><head><title>Test listing</title></head>
<body>
<script>
window.__PRERENDERED_STATE__ = {"listing":{"listing":{"data":[
{"id":2000000001,"title":"Test Phone Model A 128GB","description":"Excellent condition.<br />\nNo scratches.","url":"https://www.olx.pl/d/oferta/test-phone-a-ID2000000001.html","createdTime":"2026-08-22T12:00:00+02:00","lastRefreshTime":"2026-08-24T15:00:00+02:00","isBusiness":false,"delivery":{"rock":{"active":true,"mode":"AVAILABLE","offer_id":null}},"promotion":{"top_ad":false},"location":{"cityName":"Kraków","cityId":2,"cityNormalizedName":"krakow","regionName":"Małopolskie","regionId":6,"districtName":"Podgórze","districtId":20,"pathName":"Małopolskie, Kraków, Podgórze"},"price":{"budget":false,"free":false,"exchange":false,"displayValue":"900 zł","regularPrice":{"value":900,"currencyCode":"PLN","currencySymbol":"zł","negotiable":false}},"params":[{"key":"state","name":"Stan","type":"select","value":"Używane","normalizedValue":"used"},{"key":"storage_smartphones","name":"Pamięć wbudowana","type":"select","value":"128 GB","normalizedValue":"128gb"}],"user":{"name":"TestSeller2","created":"2020-05-10T09:00:00+02:00"},"photos":["https://example.com/photo3.jpg","https://example.com/photo4.jpg"]},
{"id":2000000002,"title":"Test Phone Model B 256GB","description":"Like new.","url":"https://www.olx.pl/d/oferta/test-phone-b-ID2000000002.html","createdTime":"2026-08-23T08:00:00+02:00","lastRefreshTime":"2026-08-23T08:00:00+02:00","isBusiness":true,"delivery":{"rock":{"active":false,"mode":"NotEligible","offer_id":null}},"promotion":{"top_ad":false},"location":{"cityName":"Gdańsk","cityId":3,"cityNormalizedName":"gdansk","regionName":"Pomorskie","regionId":5,"districtName":null,"districtId":null,"pathName":"Pomorskie, Gdańsk"},"price":{"budget":false,"free":false,"exchange":false,"displayValue":"1 800 zł","regularPrice":{"value":1800,"currencyCode":"PLN","currencySymbol":"zł","negotiable":false}},"params":[{"key":"state","name":"Stan","type":"select","value":"Nowe","normalizedValue":"new"}],"user":{"name":"TestSeller3","created":"2019-03-01T09:00:00+02:00"},"photos":["https://example.com/photo5.jpg"]}
]}}};
</script>
</body></html>
```

- [ ] **Step 2: Write the failing test**

`tests/test_prerendered.py`:

```python
from olx4ai.core.prerendered import extract_prerendered, find_offers

RAW_OBJECT_HTML = """<html><body><script>
window.__PRERENDERED_STATE__ = {"listing":{"listing":{"data":[
  {"id": 1, "title": "Offer A", "url": "https://example.com/a", "params": []},
  {"id": 2, "title": "Offer B", "url": "https://example.com/b", "params": []}
]}}};
</script></body></html>"""

JS_STRING_HTML = '''<html><body><script>
window.__PRERENDERED_STATE__ = "{\\"listing\\":{\\"listing\\":{\\"data\\":[{\\"id\\":1,\\"title\\":\\"Offer A\\",\\"url\\":\\"https://example.com/a\\",\\"params\\":[]}]}}}";
</script></body></html>'''

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
        "list": {"data": [
            {"id": 2, "title": "A", "url": "https://example.com/a"},
            {"id": 3, "title": "B", "url": "https://example.com/b"},
        ]},
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
```

`tests/conftest.py`:

```python
from pathlib import Path

import pytest

from olx4ai.core.prerendered import extract_prerendered, find_offers

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def html_listing_html() -> str:
    return (FIXTURES / "html_listing_page.html").read_text(encoding="utf-8")


@pytest.fixture
def html_offer_raw(html_listing_html) -> dict:
    state = extract_prerendered(html_listing_html)
    return find_offers(state)[0]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_prerendered.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.prerendered'`.

- [ ] **Step 4: Write the implementation**

`src/olx4ai/core/prerendered.py`:

```python
"""Extract __PRERENDERED_STATE__ from OLX HTML and locate offer-shaped dicts."""

from __future__ import annotations

import json
import urllib.parse


def _scan_js_string(s: str) -> str:
    """s starts at the opening quote. Return the raw literal including quotes."""
    quote, i, n = s[0], 1, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return s[: i + 1]
        i += 1
    raise ValueError("unterminated JS string")


def _scan_balanced(s: str) -> str:
    depth, i, in_str, esc = 0, 0, False, False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return s[: i + 1]
    raise ValueError("unbalanced JSON")


def extract_prerendered(html: str) -> dict:
    """Pull window.__PRERENDERED_STATE__ out of an OLX page, whatever its encoding."""
    idx = html.find("__PRERENDERED_STATE__")
    if idx == -1:
        raise SystemExit("no __PRERENDERED_STATE__ on this page (bot wall or layout change?)")
    rest = html[html.index("=", idx) + 1:].lstrip()

    if rest[0] in "\"'":
        literal = _scan_js_string(rest)
        if literal[0] == "'":  # normalise to a JSON-parsable double-quoted literal
            literal = '"' + literal[1:-1].replace('"', '\\"').replace("\\'", "'") + '"'
        inner = json.loads(literal)          # -> str
    else:
        inner = _scan_balanced(rest)

    if isinstance(inner, str):
        if inner.lstrip().startswith("%"):   # sometimes URI-encoded
            inner = urllib.parse.unquote(inner)
        return json.loads(inner)
    return inner


def _looks_like_offer(d: dict) -> bool:
    return "id" in d and "title" in d and ("url" in d or "params" in d or "price" in d)


def find_offers(node, best=None):
    """Structure-agnostic: find the offers, whether they sit in a list
    (search/listing pages) or as a single bare dict (offer-detail pages)."""
    best = best or []
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts and len(dicts) >= max(1, len(node) // 2):
            hits = sum(1 for d in dicts if _looks_like_offer(d))
            if hits >= max(1, len(dicts) // 2) and len(dicts) > len(best):
                best = dicts
        for x in node:
            best = find_offers(x, best)
    elif isinstance(node, dict):
        if not best and _looks_like_offer(node):
            best = [node]
        for v in node.values():
            best = find_offers(v, best)
    return best
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_prerendered.py -v`
Expected: 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/olx4ai/core/prerendered.py tests/fixtures/html_listing_page.html \
        tests/conftest.py tests/test_prerendered.py
git commit -m "feat: add core/prerendered.py, fix find_offers single-dict bug"
```

---

### Task 3: `core/adapters.py` (Bug 1 fix)

**Files:**
- Create: `src/olx4ai/core/adapters.py`
- Create: `tests/fixtures/api_search_response.json`
- Modify: `tests/conftest.py`
- Test: `tests/test_adapters.py`

**Interfaces:**
- Consumes: `html_offer_raw`, `html_listing_html` fixtures (Task 2).
- Produces: `adapters.adapt_api_offer(raw: dict) -> dict`,
  `adapters.adapt_html_offer(raw: dict) -> dict`. Consumed by Task 4
  (`test_normalize.py`), Task 10 (`cli.py`), Task 11 (`mcp_server.py`).
- Produces fixtures: `tests/conftest.py` fixtures `api_search_payload`
  (parsed JSON dict) and `api_offer` (its first offer dict, in JSON-API
  shape) — reused by Tasks 4, 7, 10, 11.

- [ ] **Step 1: Create the sanitized JSON API fixture**

`tests/fixtures/api_search_response.json`:

```json
{
  "data": [
    {
      "id": 1000000001,
      "title": "Test Laptop 14 inch 16GB RAM 512GB SSD",
      "description": "Great condition laptop.<br />\nBarely used, comes with charger.",
      "url": "https://www.olx.pl/d/oferta/test-laptop-ID1000000001.html",
      "created_time": "2026-08-20T10:00:00+02:00",
      "last_refresh_time": "2026-08-24T09:30:00+02:00",
      "business": false,
      "delivery": {"rock": {"active": true, "mode": "AVAILABLE", "offer_id": null}},
      "promotion": {"top_ad": true},
      "location": {
        "city": {"id": 1, "name": "Warszawa", "normalized_name": "warszawa"},
        "district": {"id": 10, "name": "Mokotów"},
        "region": {"id": 5, "name": "Mazowieckie", "normalized_name": "mazowieckie"}
      },
      "params": [
        {
          "key": "price",
          "name": "Cena",
          "type": "price",
          "value": {
            "value": 1500,
            "type": "price",
            "arranged": false,
            "budget": false,
            "currency": "PLN",
            "negotiable": true,
            "label": "1 500 zł"
          }
        },
        {
          "key": "state",
          "name": "Stan",
          "type": "select",
          "value": {"key": "used", "label": "Używane"}
        },
        {
          "key": "ram_memory_laptops",
          "name": "Pamięć RAM",
          "type": "select",
          "value": {"key": "16gb", "label": "16 GB"}
        }
      ],
      "user": {"name": "TestSeller", "created": "2021-01-15T08:00:00+02:00"},
      "photos": [{"url": "https://example.com/photo1.jpg"}, {"url": "https://example.com/photo2.jpg"}]
    }
  ],
  "metadata": {"total_elements": 1}
}
```

- [ ] **Step 2: Write the failing test**

Append to `tests/conftest.py`:

```python
import json


@pytest.fixture
def api_search_payload() -> dict:
    return json.loads((FIXTURES / "api_search_response.json").read_text(encoding="utf-8"))


@pytest.fixture
def api_offer(api_search_payload) -> dict:
    return api_search_payload["data"][0]
```

`tests/test_adapters.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_adapters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.adapters'`.

- [ ] **Step 4: Write the implementation**

`src/olx4ai/core/adapters.py`:

```python
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
            adapted_params.append({
                **p,
                "value": {"key": p.get("normalizedValue"), "label": p.get("value")},
            })
        else:
            adapted_params.append(p)
    adapted_params.append({
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
    })

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_adapters.py -v`
Expected: 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/olx4ai/core/adapters.py tests/fixtures/api_search_response.json \
        tests/conftest.py tests/test_adapters.py
git commit -m "feat: add core/adapters.py, fix normalize() shape-mismatch bug"
```

---

### Task 4: `core/normalize.py`

**Files:**
- Create: `src/olx4ai/core/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `adapters.adapt_api_offer`, `adapters.adapt_html_offer` (Task 3);
  `api_offer`, `html_offer_raw` fixtures (Tasks 2/3).
- Produces: `normalize.normalize(offer: dict) -> dict`,
  `normalize.normalize_detail(offer: dict, desc_chars: int) -> dict`.
  Consumed by Task 10 (`cli.py`), Task 11 (`mcp_server.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_normalize.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.normalize'`.

- [ ] **Step 3: Write the implementation**

`src/olx4ai/core/normalize.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalize.py -v`
Expected: 5 tests PASS. This is the direct proof both bugs are fixed.

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/normalize.py tests/test_normalize.py
git commit -m "feat: add core/normalize.py with regression tests for both bugs"
```

---

### Task 5: `core/filters.py`

**Files:**
- Create: `src/olx4ai/core/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure function over plain dicts).
- Produces: `filters.post_filter(rows: list[dict], args) -> list[dict]`.
  Consumed by Task 10 (`cli.py`), Task 11 (`mcp_server.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_filters.py`:

```python
from types import SimpleNamespace

from olx4ai.core.filters import post_filter


def row(title, price=100, promoted=False):
    return {"title": title, "price": price, "promoted": promoted}


def test_exclude_drops_matching_titles():
    rows = [row("Nice Case for iPhone"), row("iPhone 13 Pro")]
    args = SimpleNamespace(exclude="case", must=None, no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["iPhone 13 Pro"]


def test_must_keeps_only_matching_titles():
    rows = [row("iPhone 13 Pro"), row("Samsung Galaxy S21")]
    args = SimpleNamespace(exclude=None, must="iphone", no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["iPhone 13 Pro"]


def test_no_promoted_drops_promoted_rows():
    rows = [row("A", promoted=True), row("B", promoted=False)]
    args = SimpleNamespace(exclude=None, must=None, no_promoted=True, dedupe=False)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["B"]


def test_dedupe_drops_same_title_and_price():
    rows = [row("Same Title", price=100), row("Same Title", price=100), row("Same Title", price=200)]
    args = SimpleNamespace(exclude=None, must=None, no_promoted=False, dedupe=True)
    out = post_filter(rows, args)
    assert len(out) == 2
    assert [r["price"] for r in out] == [100, 200]


def test_filters_compose_together():
    rows = [row("iPhone Case", promoted=True), row("iPhone 13", promoted=True),
            row("iPhone 13", promoted=False)]
    args = SimpleNamespace(exclude="case", must="iphone", no_promoted=True, dedupe=True)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["iPhone 13"]


def test_missing_flags_default_to_no_op():
    rows = [row("Anything")]
    out = post_filter(rows, SimpleNamespace())
    assert out == rows
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.filters'`.

- [ ] **Step 3: Write the implementation**

`src/olx4ai/core/filters.py`:

```python
"""Client-side filtering the OLX API doesn't support natively."""

from __future__ import annotations


def post_filter(rows: list[dict], args) -> list[dict]:
    out = rows
    if getattr(args, "exclude", None):
        bad = [w.strip().lower() for w in args.exclude.split(",") if w.strip()]
        out = [r for r in out if not any(b in r["title"].lower() for b in bad)]
    if getattr(args, "must", None):
        good = [w.strip().lower() for w in args.must.split(",") if w.strip()]
        out = [r for r in out if all(g in r["title"].lower() for g in good)]
    if getattr(args, "no_promoted", False):
        out = [r for r in out if not r["promoted"]]
    if getattr(args, "dedupe", False):
        seen, ded = set(), []
        for r in out:
            k = (r["title"].lower().strip(), r["price"])
            if k in seen:
                continue
            seen.add(k)
            ded.append(r)
        out = ded
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_filters.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/filters.py tests/test_filters.py
git commit -m "feat: add core/filters.py"
```

---

### Task 6: `core/format.py`

**Files:**
- Create: `src/olx4ai/core/format.py`
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: `cache.index_put` (Task 1).
- Produces: `format.fmt_line(r, n, title_chars, show_url) -> str`,
  `format.compute_stats(rows: list[dict]) -> dict`,
  `format.print_stats(rows, label) -> None`,
  `format.emit(rows, args, label) -> None`. Consumed by Task 10 (`cli.py`),
  Task 11 (`mcp_server.py`'s `stats` tool uses `compute_stats` directly).

- [ ] **Step 1: Write the failing test**

`tests/test_format.py`:

```python
import json
from types import SimpleNamespace

from olx4ai.core import cache
from olx4ai.core import format as fmt


def offer_row(id=1, title="Test Offer", price=1000, price_label=None, neg=False,
              cond="used", city="Warszawa", district="Mokotów", age="1d",
              delivery=False, biz=False, promoted=False, url="https://example.com/1"):
    return dict(id=id, title=title, price=price, price_label=price_label, neg=neg,
                cond=cond, city=city, district=district, age=age, delivery=delivery,
                biz=biz, promoted=promoted, url=url)


def test_fmt_line_includes_price_flags_and_location():
    line = fmt.fmt_line(offer_row(delivery=True, promoted=True), 1, 80, False)
    assert "1000zł" in line
    assert "D" in line and "*" in line
    assert "Warszawa/Mokotów" in line


def test_fmt_line_truncates_long_titles():
    long_title = "x" * 200
    line = fmt.fmt_line(offer_row(title=long_title), 1, 20, False)
    expected = "x" * 19 + "…"
    assert expected in line
    assert "x" * 20 not in line


def test_fmt_line_appends_url_when_requested():
    line = fmt.fmt_line(offer_row(), 1, 80, True)
    assert "https://example.com/1" in line


def test_fmt_line_omits_url_by_default():
    line = fmt.fmt_line(offer_row(), 1, 80, False)
    assert "https://example.com/1" not in line


def test_compute_stats_basic_distribution():
    rows = [offer_row(id=i, price=p) for i, p in enumerate([100, 200, 300, 400, 500])]
    stats = fmt.compute_stats(rows)
    assert stats["count"] == 5
    assert stats["priced_count"] == 5
    assert stats["min"] == 100
    assert stats["max"] == 500
    assert stats["median"] == 300
    assert stats["cheapest_ids"]


def test_compute_stats_handles_no_priced_rows():
    rows = [offer_row(id=1, price=None)]
    stats = fmt.compute_stats(rows)
    assert stats == {"count": 1, "priced_count": 0}


def test_print_stats_matches_compute_stats(capsys):
    rows = [offer_row(id=i, price=p) for i, p in enumerate([100, 200, 300, 400, 500])]
    fmt.print_stats(rows, "test label")
    out = capsys.readouterr().out
    assert "test label: 5 offers, 5 with a numeric price" in out
    assert "min 100" in out
    assert "cheapest ids:" in out


def test_emit_json_mode_honors_field_whitelist(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    rows = [offer_row()]
    args = SimpleNamespace(json=True, fields="id,price", title_chars=80, urls=False)
    fmt.emit(rows, args, "label")
    out = json.loads(capsys.readouterr().out)
    assert out == [{"id": 1, "price": 1000}]


def test_emit_writes_to_index(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    rows = [offer_row(id=42, url="https://example.com/42")]
    args = SimpleNamespace(json=True, fields=None, title_chars=80, urls=False)
    fmt.emit(rows, args, "label")
    assert cache.index_get("42") == "https://example.com/42"


def test_emit_no_results_message(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    args = SimpleNamespace(json=False, fields=None, title_chars=80, urls=False)
    fmt.emit([], args, "label")
    assert "no results" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_format.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.format'`.

- [ ] **Step 3: Write the implementation**

`src/olx4ai/core/format.py`:

```python
"""Rendering: compact per-offer lines, price-distribution stats, and the
emit() dispatcher shared by search/url output."""

from __future__ import annotations

import json
import statistics

from olx4ai.core import cache


def fmt_line(r: dict, n: int, title_chars: int, show_url: bool) -> str:
    price = f"{r['price']}zł" if r["price"] is not None else (r["price_label"] or "-")
    flags = ("~" if r["neg"] else "") + ("D" if r["delivery"] else "") + \
            ("B" if r["biz"] else "") + ("*" if r["promoted"] else "")
    where = "/".join(x for x in (r["city"], r["district"]) if x) or "?"
    title = r["title"]
    if len(title) > title_chars:
        title = title[: title_chars - 1] + "…"
    line = (f"{n:>3}. [{r['id']}] {price:<9}{flags:<4} "
            f"{(r['cond'] or '?'):<8}{where:<26} {r['age']:>4}  {title}")
    if show_url and r.get("url"):
        line += "\n      " + r["url"]
    return line


def compute_stats(rows: list[dict]) -> dict:
    prices = sorted(r["price"] for r in rows if r["price"])
    result = {"count": len(rows), "priced_count": len(prices)}
    if not prices:
        return result
    q = statistics.quantiles(prices, n=4) if len(prices) > 3 else [prices[0]] * 3
    result.update({
        "min": prices[0], "p25": int(q[0]), "median": int(statistics.median(prices)),
        "p75": int(q[2]), "max": prices[-1],
    })
    lo, hi = prices[0], prices[-1]
    step = max(1, (hi - lo) // 8 or 1)
    histogram = []
    for b in range(lo, hi + 1, step):
        c = sum(1 for p in prices if b <= p < b + step)
        if c:
            histogram.append({"low": b, "high": b + step - 1, "count": c})
    result["histogram"] = histogram
    cheap = [r for r in rows if r["price"] and r["price"] <= q[0]]
    result["cheapest_ids"] = [r["id"] for r in sorted(cheap, key=lambda r: r["price"])[:5]]
    return result


def print_stats(rows: list[dict], label: str) -> None:
    stats = compute_stats(rows)
    print(f"{label}: {stats['count']} offers, {stats['priced_count']} with a numeric price")
    if stats["priced_count"] == 0:
        return
    print(f"  min {stats['min']}  p25 {stats['p25']}  median {stats['median']}  "
          f"p75 {stats['p75']}  max {stats['max']}")
    for b in stats["histogram"]:
        print(f"  {b['low']:>6}-{b['high']:<6} {'#' * min(b['count'], 40)} {b['count']}")
    if stats["cheapest_ids"]:
        print("  cheapest ids: " + " ".join(str(i) for i in stats["cheapest_ids"]))


def emit(rows: list[dict], args, label: str) -> None:
    cache.index_put(rows)
    if args.json:
        fields = args.fields.split(",") if args.fields else None
        data = [{k: v for k, v in r.items() if not fields or k in fields} for r in rows]
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        return
    if not rows:
        print("no results")
        return
    print(f"# {label} — {len(rows)} offers   (flags: ~negotiable D=delivery "
          f"B=business *=promoted)")
    for i, r in enumerate(rows, 1):
        print(fmt_line(r, i, args.title_chars, args.urls))
    prices = [r["price"] for r in rows if r["price"]]
    if prices:
        print(f"# price: min {min(prices)} / median {int(statistics.median(prices))} "
              f"/ max {max(prices)} zł")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_format.py -v`
Expected: 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/format.py tests/test_format.py
git commit -m "feat: add core/format.py with compute_stats/print_stats split"
```

---

### Task 7: `core/api_client.py`

**Files:**
- Create: `src/olx4ai/core/api_client.py`
- Test: `tests/test_api_client.py`

**Interfaces:**
- Consumes: `cache.API`, `cache.fetch`, `cache.configure` (Task 1);
  `api_search_payload` fixture (Task 3).
- Produces: `api_client.SORTS: dict[str, str | None]`,
  `api_client.api_search(args) -> list[dict]` (reads `args.offset`,
  `.max`, `.query`, `.min`, `.max_price`, `.category`, `.city_id`,
  `.region_id`, `.condition`, `.sort`, `.param`, `.no_cache`). Consumed by
  Task 10 (`cli.py`), Task 11 (`mcp_server.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_api_client.py`:

```python
import argparse
import json

from olx4ai.core import api_client, cache


def make_args(**overrides):
    defaults = dict(
        query="test laptop", max=40, offset=0, min=None, max_price=None,
        category=None, city_id=None, region_id=None, condition=None,
        sort="relevance", param=None, no_cache=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_api_search_returns_offers_from_single_page(monkeypatch, api_search_payload):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return json.dumps(api_search_payload)

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    rows = api_client.api_search(make_args(max=5))
    assert len(rows) == 1
    assert rows[0]["title"] == "Test Laptop 14 inch 16GB RAM 512GB SSD"
    assert len(calls) == 1
    assert "test+laptop" in calls[0] or "test%20laptop" in calls[0]


def test_api_search_respects_domain_configuration(monkeypatch, api_search_payload):
    captured = {}

    def fake_fetch(url, **kw):
        captured["url"] = url
        return json.dumps(api_search_payload)

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    cache.configure("olx.ua")
    try:
        api_client.api_search(make_args())
    finally:
        cache.configure("olx.pl")
    assert captured["url"].startswith("https://www.olx.ua/api/v1/offers/")


def test_api_search_stops_when_batch_empty(monkeypatch):
    monkeypatch.setattr(
        cache, "fetch",
        lambda url, **kw: json.dumps({"data": [], "metadata": {"total_elements": 0}}),
    )
    rows = api_client.api_search(make_args())
    assert rows == []


def test_api_search_includes_condition_and_price_range_params(monkeypatch, api_search_payload):
    captured = {}

    def fake_fetch(url, **kw):
        captured["url"] = url
        return json.dumps(api_search_payload)

    monkeypatch.setattr(cache, "fetch", fake_fetch)
    api_client.api_search(make_args(min=800, max_price=2000, condition="used"))
    assert "filter_float_price%3Afrom=800" in captured["url"]
    assert "filter_float_price%3Ato=2000" in captured["url"]
    assert "filter_enum_state%5B0%5D=used" in captured["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.api_client'`.

- [ ] **Step 3: Write the implementation**

`src/olx4ai/core/api_client.py`:

```python
"""JSON API search path -- https://www.olx.pl/api/v1/offers/ (or the
configured domain's equivalent)."""

from __future__ import annotations

import json
import time
import urllib.parse

from olx4ai.core import cache

SORTS = {
    "relevance": None,
    "newest": "created_at:desc",
    "price-asc": "filter_float_price:asc",
    "price-desc": "filter_float_price:desc",
}

SLEEP_BETWEEN_PAGES = 0.7


def api_search(args) -> list[dict]:
    rows, offset = [], args.offset
    while len(rows) < args.max:
        params = {
            "offset": offset,
            "limit": min(50, args.max - len(rows)),
            "query": args.query,
            "filter_refiners": "spell_checker",
        }
        if args.min is not None:
            params["filter_float_price:from"] = args.min
        if args.max_price is not None:
            params["filter_float_price:to"] = args.max_price
        if args.category:
            params["category_id"] = args.category
        if args.city_id:
            params["city_id"] = args.city_id
        if args.region_id:
            params["region_id"] = args.region_id
        if args.condition:
            params["filter_enum_state[0]"] = args.condition
        if SORTS.get(args.sort):
            params["sort_by"] = SORTS[args.sort]
        for kv in args.param or []:
            k, _, v = kv.partition("=")
            params[k] = v

        url = cache.API + "?" + urllib.parse.urlencode(params)
        payload = json.loads(cache.fetch(url, json_mode=True, use_cache=not args.no_cache))
        batch = payload.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        total = ((payload.get("metadata") or {}).get("total_elements"))
        if total is not None and offset >= total:
            break
        if len(rows) < args.max:
            time.sleep(SLEEP_BETWEEN_PAGES)
    return rows[: args.max]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_client.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/api_client.py tests/test_api_client.py
git commit -m "feat: add core/api_client.py"
```

---

### Task 8: `core/html_client.py`

**Files:**
- Create: `src/olx4ai/core/html_client.py`
- Test: `tests/test_html_client.py`

**Interfaces:**
- Consumes: `cache.fetch` (Task 1), `prerendered.extract_prerendered`,
  `prerendered.find_offers` (Task 2), `html_listing_html` fixture (Task 2).
- Produces: `html_client.html_search(url: str, use_cache: bool) -> list[dict]`.
  Consumed by Task 10 (`cli.py`), Task 11 (`mcp_server.py`).

- [ ] **Step 1: Write the failing test**

`tests/test_html_client.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_html_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.core.html_client'`.

- [ ] **Step 3: Write the implementation**

`src/olx4ai/core/html_client.py`:

```python
"""HTML-scrape search path -- reuses __PRERENDERED_STATE__ from any listing URL."""

from __future__ import annotations

from olx4ai.core import cache
from olx4ai.core.prerendered import extract_prerendered, find_offers


def html_search(url: str, use_cache: bool) -> list[dict]:
    state = extract_prerendered(cache.fetch(url, json_mode=False, use_cache=use_cache))
    return find_offers(state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_html_client.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/html_client.py tests/test_html_client.py
git commit -m "feat: add core/html_client.py"
```

---

### Task 9: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the full `tests/` suite built by Tasks 1-8 so far (and extended
  by Tasks 10-11 later — no changes to this file needed for that).

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[mcp,dev]"
      - run: pytest -v
```

- [ ] **Step 2: Verify the YAML is well-formed**

Run: `pip install pyyaml --quiet && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('valid YAML')"`
Expected: `valid YAML` printed, no exception.

- [ ] **Step 3: Verify the install+test command it runs locally succeeds**

Run: `pip install -e ".[mcp,dev]" && pytest -v`
Expected: all tests from Tasks 1-8 PASS (this also confirms the `[mcp]`
extra installs cleanly, ahead of Task 11 needing it).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow running pytest across Python 3.10-3.13"
```

---

### Task 10: `cli.py`

**Files:**
- Create: `src/olx4ai/cli.py`
- Create: `tests/fixtures/html_offer_page.html`
- Modify: `tests/conftest.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cache`, `adapters`, `api_client`, `html_client`, `filters`,
  `format` (all Tasks 1-8), `prerendered.extract_prerendered`,
  `prerendered.find_offers` (Task 2).
- Produces: `cli.CHEAT: str`, `cli.build_parser() -> argparse.ArgumentParser`,
  `cli.cmd_search/cmd_stats/cmd_url/cmd_offer/cmd_agent_help/cmd_clear_cache(args) -> None`,
  `cli.main() -> None` (the `olx4ai` console-script entry point).
- Produces fixtures: `tests/conftest.py` fixtures `html_offer_detail_html`
  and `html_offer_detail_raw` — reused by Task 11.

- [ ] **Step 1: Create the sanitized HTML offer-detail fixture**

`tests/fixtures/html_offer_page.html`:

```html
<!doctype html>
<html><head><title>Test offer detail</title></head>
<body>
<script>
window.__PRERENDERED_STATE__ = {"ad":{"ad":{"id":3000000001,"title":"Test Vacuum Cleaner Pro 3000W","description":"Barely used vacuum cleaner in great condition.<br />\nComes with all original attachments and the manual.<br />\nSmoke-free, pet-free home. Selling because I upgraded to a robot vacuum. Pickup preferred, can ship at buyer's cost via InPost.","url":"https://www.olx.pl/d/oferta/test-vacuum-ID3000000001.html","createdTime":"2026-08-15T11:00:00+02:00","lastRefreshTime":"2026-08-24T10:00:00+02:00","isBusiness":false,"delivery":{"rock":{"active":true,"mode":"AVAILABLE","offer_id":null}},"promotion":{"top_ad":false},"location":{"cityName":"Wrocław","cityId":4,"cityNormalizedName":"wroclaw","regionName":"Dolnośląskie","regionId":7,"districtName":"Krzyki","districtId":30,"pathName":"Dolnośląskie, Wrocław, Krzyki"},"price":{"budget":false,"free":false,"exchange":false,"displayValue":"250 zł","regularPrice":{"value":250,"currencyCode":"PLN","currencySymbol":"zł","negotiable":true}},"params":[{"key":"state","name":"Stan","type":"select","value":"Używane","normalizedValue":"used"},{"key":"brand_household_appliances","name":"Marka","type":"select","value":"TestBrand","normalizedValue":"testbrand"},{"key":"power_household_appliances","name":"Moc","type":"select","value":"3000W","normalizedValue":"3000w"}],"user":{"name":"TestSeller4","created":"2018-06-01T09:00:00+02:00"},"photos":["https://example.com/photo6.jpg","https://example.com/photo7.jpg","https://example.com/photo8.jpg"]}}};
</script>
</body></html>
```

- [ ] **Step 2: Write the failing test**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def html_offer_detail_html() -> str:
    return (FIXTURES / "html_offer_page.html").read_text(encoding="utf-8")


@pytest.fixture
def html_offer_detail_raw(html_offer_detail_html) -> dict:
    state = extract_prerendered(html_offer_detail_html)
    return find_offers(state)[0]
```

`tests/test_cli.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.cli'`.

- [ ] **Step 4: Write the implementation**

`src/olx4ai/cli.py`:

```python
"""Thin argparse CLI for olx4ai -- wiring only, all logic lives in core/."""

from __future__ import annotations

import argparse
import json
import os
import sys

from olx4ai.core import adapters, api_client, cache, filters, html_client
from olx4ai.core import format as fmt
from olx4ai.core import normalize as norm
from olx4ai.core.prerendered import extract_prerendered, find_offers

CHEAT = """olx4ai — context-cheap OLX browser. One short line per offer, no HTML.

  olx4ai stats  "asus vivobook 14"                  # price distribution first (~15 lines)
  olx4ai search "asus vivobook 14" --max 40
  olx4ai search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
  olx4ai search "iphone 13" --exclude "obudowa,etui,czesci" --dedupe --no-promoted
  olx4ai url    "<any olx.pl listing URL with filters already applied>"
  olx4ai offer  1023456789                          # description + specs for ONE id
  olx4ai search ... --json --fields id,title,price  # machine-readable, still pruned

Line format: N. [id] price flags condition city/district age title
Flags: ~ negotiable | D delivery | B business seller | * promoted
Tips: start with `stats`, then narrow with --min/--max-price. URLs are omitted by
default (they are long) — use `offer <id>`, or --urls if you really need them.
Responses are cached 10 min, so re-running a query costs nothing.
Use --domain to point at another OLX Europe site (untested outside olx.pl)."""


def cmd_search(args: argparse.Namespace) -> None:
    raw = api_client.api_search(args)
    rows = filters.post_filter([norm.normalize(adapters.adapt_api_offer(o)) for o in raw], args)
    fmt.emit(rows, args, f'search "{args.query}"')


def cmd_stats(args: argparse.Namespace) -> None:
    args.max = max(args.max, 100)
    raw = api_client.api_search(args)
    rows = filters.post_filter([norm.normalize(adapters.adapt_api_offer(o)) for o in raw], args)
    fmt.print_stats(rows, f'"{args.query}"')


def cmd_url(args: argparse.Namespace) -> None:
    raw = html_client.html_search(args.target, use_cache=not args.no_cache)
    rows = filters.post_filter(
        [norm.normalize(adapters.adapt_html_offer(o)) for o in raw], args
    )[: args.max]
    fmt.emit(rows, args, args.target)


def cmd_offer(args: argparse.Namespace) -> None:
    target = args.target
    offer = None
    adapt = adapters.adapt_api_offer
    if target.isdigit():
        try:
            payload = json.loads(cache.fetch(f"{cache.API}{target}/", json_mode=True,
                                              use_cache=not args.no_cache))
            offer = payload.get("data") or payload
        except SystemExit:
            offer = None
        if offer is None:
            url = cache.index_get(target)
            if not url:
                raise SystemExit(f"id {target} not in cache index — run a search first, "
                                  f"or pass the full offer URL")
            target = url
    if offer is None:
        state = extract_prerendered(cache.fetch(target, json_mode=False,
                                                  use_cache=not args.no_cache))
        cands = find_offers(state)
        offer = cands[0] if cands else None
        adapt = adapters.adapt_html_offer
        if offer is None:
            raise SystemExit("could not locate the offer object in the page state")

    d = norm.normalize_detail(adapt(offer), args.desc_chars)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        return
    price = f"{d['price']}zł" if d["price"] is not None else (d["price_label"] or "-")
    print(f"{d['title']}\n{price}{' (negotiable)' if d['neg'] else ''}  |  "
          f"{d['cond'] or '?'}  |  "
          f"{', '.join(x for x in (d['city'], d['district'], d['region']) if x)}  |  "
          f"{d['age']} old  |  {d['photos']} photos")
    if d["specs"]:
        print("specs: " + "; ".join(f"{k}={v}" for k, v in d["specs"].items()))
    if d["seller"]:
        print(f"seller: {d['seller']}{' (business)' if d['biz'] else ''}")
    if d["url"]:
        print(d["url"])
    if d["description"]:
        print("---\n" + d["description"])


def cmd_agent_help(args: argparse.Namespace) -> None:
    print(CHEAT)


def cmd_clear_cache(args: argparse.Namespace) -> None:
    n = 0
    if os.path.isdir(cache.CACHE_DIR):
        for f in os.listdir(cache.CACHE_DIR):
            if f.endswith(".cache"):
                os.remove(os.path.join(cache.CACHE_DIR, f))
                n += 1
    print(f"removed {n} cached responses")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="olx4ai", description="context-cheap OLX browser for AI agents")
    p.add_argument("--domain", default="olx.pl",
                    help="OLX domain to target (default olx.pl; other OLX Europe "
                         "domains untested)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_query=True):
        if with_query:
            sp.add_argument("query")
        sp.add_argument("--max", type=int, default=40, help="max offers (default 40)")
        sp.add_argument("--offset", type=int, default=0)
        sp.add_argument("--min", type=int, help="min price PLN")
        sp.add_argument("--max-price", type=int, help="max price PLN")
        sp.add_argument("--sort", choices=list(api_client.SORTS), default="relevance")
        sp.add_argument("--condition", choices=["new", "used", "damaged"])
        sp.add_argument("--used", dest="condition", action="store_const", const="used")
        sp.add_argument("--category", help="OLX category_id")
        sp.add_argument("--city-id")
        sp.add_argument("--region-id")
        sp.add_argument("--param", action="append",
                         help="raw API param, repeatable, e.g. --param filter_enum_hdd_type[0]=ssd")
        sp.add_argument("--exclude", help="comma-separated words to drop from titles")
        sp.add_argument("--must", help="comma-separated words the title must contain")
        sp.add_argument("--dedupe", action="store_true")
        sp.add_argument("--no-promoted", action="store_true")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--fields", help="json mode: comma-separated field whitelist")
        sp.add_argument("--urls", action="store_true", help="print offer URLs too")
        sp.add_argument("--title-chars", type=int, default=80)
        sp.add_argument("--no-cache", action="store_true")

    s = sub.add_parser("search", help="search offers (JSON API)")
    common(s)
    s.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="price distribution only")
    common(st)
    st.set_defaults(func=cmd_stats)

    u = sub.add_parser("url", help="scrape any OLX listing URL via __PRERENDERED_STATE__")
    u.add_argument("target")
    common(u, with_query=False)
    u.set_defaults(func=cmd_url)

    o = sub.add_parser("offer", help="details of one offer by id or URL")
    o.add_argument("target")
    o.add_argument("--desc-chars", type=int, default=1200, help="0 = full description")
    o.add_argument("--json", action="store_true")
    o.add_argument("--no-cache", action="store_true")
    o.set_defaults(func=cmd_offer)

    a = sub.add_parser("agent-help", help="short usage contract for an LLM")
    a.set_defaults(func=cmd_agent_help)

    c = sub.add_parser("clear-cache")
    c.set_defaults(func=cmd_clear_cache)
    return p


def main() -> None:
    args = build_parser().parse_args()
    cache.configure(args.domain)
    try:
        args.func(args)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pip install -e ".[dev]" && pytest tests/test_cli.py -v`
Expected: 7 tests PASS.

- [ ] **Step 6: Run the full suite so far**

Run: `pytest -v`
Expected: all tests from Tasks 1-8 and this task PASS (mcp_server.py from
Task 11 doesn't exist yet, so nothing references it).

- [ ] **Step 7: Commit**

```bash
git add src/olx4ai/cli.py tests/fixtures/html_offer_page.html \
        tests/conftest.py tests/test_cli.py
git commit -m "feat: add cli.py — olx4ai console-script entry point"
```

---

### Task 11: `mcp_server.py`

**Files:**
- Create: `src/olx4ai/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `cache`, `adapters`, `api_client`, `html_client`, `filters`,
  `format` (Tasks 1-8), `prerendered.extract_prerendered`,
  `prerendered.find_offers` (Task 2), `html_offer_detail_html` fixture
  (Task 10).
- Produces: `mcp_server.mcp: MCPServer`, `mcp_server.main() -> None` (the
  `olx4ai-mcp` console-script entry point). Terminal — no later task
  consumes this module.

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_server.py`:

```python
import json

import pytest

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402

from olx4ai.core import cache  # noqa: E402
from olx4ai.mcp_server import mcp  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.configure("olx.pl")
    yield


async def test_search_tool_returns_pruned_offers(monkeypatch, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"query": "test laptop", "max": 5})
    offers = result.structured_content["result"]
    assert offers[0]["price"] == 1500
    assert offers[0]["city"] == "Warszawa"


async def test_stats_tool_returns_structured_distribution(monkeypatch, api_search_payload):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: json.dumps(api_search_payload))
    async with Client(mcp) as client:
        result = await client.call_tool("stats", {"query": "test laptop"})
    assert result.structured_content["min"] == 1500
    assert result.structured_content["count"] == 1


async def test_search_url_tool_uses_html_adapter(monkeypatch, html_listing_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_listing_html)
    async with Client(mcp) as client:
        result = await client.call_tool("search_url", {"url": "https://www.olx.pl/oferty/q-test/"})
    offers = result.structured_content["result"]
    assert len(offers) == 2
    assert offers[0]["price"] == 900
    assert offers[0]["city"] == "Kraków"


async def test_offer_tool_from_html_fallback(monkeypatch, html_offer_detail_html):
    monkeypatch.setattr(cache, "fetch", lambda url, **kw: html_offer_detail_html)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "offer", {"target": "https://www.olx.pl/d/oferta/test-vacuum.html"})
    assert result.structured_content["price"] == 250
    assert result.structured_content["city"] == "Wrocław"


async def test_offer_tool_translates_fetch_errors_to_tool_errors(monkeypatch):
    def raise_system_exit(url, **kw):
        raise SystemExit("network error for https://example.com: boom")

    monkeypatch.setattr(cache, "fetch", raise_system_exit)
    async with Client(mcp) as client:
        result = await client.call_tool("offer", {"target": "https://example.com/x"})
    assert result.is_error is True


async def test_clear_cache_tool_reports_removed_count(tmp_path):
    (tmp_path / "abc.cache").write_text("x")
    async with Client(mcp) as client:
        result = await client.call_tool("clear_cache", {})
    assert result.structured_content["removed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pip install -e ".[mcp,dev]" && pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'olx4ai.mcp_server'`.

- [ ] **Step 3: Write the implementation**

`src/olx4ai/mcp_server.py`:

```python
"""MCP server exposing the core pipeline as tools over stdio."""

from __future__ import annotations

import functools
import json
import os

from mcp.server import MCPServer

from olx4ai.core import adapters, api_client, cache, filters, html_client
from olx4ai.core import format as fmt
from olx4ai.core import normalize as norm
from olx4ai.core.prerendered import extract_prerendered, find_offers

mcp = MCPServer("olx4ai")


class _Args:
    """Duck-types argparse.Namespace for api_client.api_search() and
    filters.post_filter(), which were written against CLI args."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _mcp_safe(fn):
    """cache.fetch() raises SystemExit on network/HTTP errors -- correct for
    a one-shot CLI process, fatal for a long-running server. Translate it
    into a normal exception so one failed fetch doesn't kill the server."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit as e:
            raise ValueError(str(e)) from e
    return wrapper


def _search_rows(query, max, min, max_price, condition, sort, city_id,
                  region_id, category, exclude, must, dedupe, no_promoted):
    args = _Args(query=query, max=max, offset=0, min=min, max_price=max_price,
                 category=category, city_id=city_id, region_id=region_id,
                 condition=condition, sort=sort, param=None, no_cache=False,
                 exclude=exclude, must=must, dedupe=dedupe, no_promoted=no_promoted)
    raw = api_client.api_search(args)
    rows = [norm.normalize(adapters.adapt_api_offer(o)) for o in raw]
    return filters.post_filter(rows, args)


@mcp.tool()
@_mcp_safe
def search(query: str, max: int = 40, min: int | None = None,
           max_price: int | None = None, condition: str | None = None,
           sort: str = "relevance", city_id: str | None = None,
           region_id: str | None = None, category: str | None = None,
           exclude: str | None = None, must: str | None = None,
           dedupe: bool = False, no_promoted: bool = False) -> list[dict]:
    """Search OLX offers via the JSON API. Returns pruned offer dicts, no raw HTML."""
    return _search_rows(query, max, min, max_price, condition, sort, city_id,
                         region_id, category, exclude, must, dedupe, no_promoted)


@mcp.tool()
@_mcp_safe
def stats(query: str, min: int | None = None, max_price: int | None = None,
          condition: str | None = None) -> dict:
    """Price distribution (min/p25/median/p75/max + histogram) for a query."""
    rows = _search_rows(query, 100, min, max_price, condition, "relevance",
                         None, None, None, None, None, False, False)
    return fmt.compute_stats(rows)


@mcp.tool()
@_mcp_safe
def search_url(url: str, max: int = 40) -> list[dict]:
    """Scrape any OLX listing URL (with OLX's own filters already applied)
    via __PRERENDERED_STATE__."""
    raw = html_client.html_search(url, use_cache=True)
    rows = [norm.normalize(adapters.adapt_html_offer(o)) for o in raw]
    return rows[:max]


@mcp.tool()
@_mcp_safe
def offer(target: str, desc_chars: int = 1200) -> dict:
    """Full details (description, specs, seller) for one offer by numeric id or URL."""
    offer_dict = None
    adapt = adapters.adapt_api_offer
    if target.isdigit():
        try:
            payload = json.loads(cache.fetch(f"{cache.API}{target}/", json_mode=True))
            offer_dict = payload.get("data") or payload
        except SystemExit:
            offer_dict = None
        if offer_dict is None:
            url = cache.index_get(target)
            if not url:
                raise ValueError(f"id {target} not in cache index — run search first, "
                                  f"or pass the full offer URL")
            target = url
    if offer_dict is None:
        state = extract_prerendered(cache.fetch(target, json_mode=False))
        cands = find_offers(state)
        offer_dict = cands[0] if cands else None
        adapt = adapters.adapt_html_offer
        if offer_dict is None:
            raise ValueError("could not locate the offer object in the page state")
    return norm.normalize_detail(adapt(offer_dict), desc_chars)


@mcp.tool()
def clear_cache() -> dict:
    """Remove all cached HTTP responses (does not clear the id-to-url index)."""
    n = 0
    if os.path.isdir(cache.CACHE_DIR):
        for f in os.listdir(cache.CACHE_DIR):
            if f.endswith(".cache"):
                os.remove(os.path.join(cache.CACHE_DIR, f))
                n += 1
    return {"removed": n}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: every test from Tasks 1-11 PASSes.

- [ ] **Step 6: Commit**

```bash
git add src/olx4ai/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add mcp_server.py — olx4ai-mcp stdio MCP server"
```

---

### Task 12: Docs, license, cleanup, and final verification

**Files:**
- Create: `README.md`
- Create: `LICENSE`
- Create: `scripts/live_smoke_test.sh`
- Modify: `CLAUDE.md`
- Modify: `.github/workflows/ci.yml`
- Delete: `olx.py`, `__pycache__/` (repo root)

**Interfaces:**
- Consumes: everything built in Tasks 1-11 (this task only writes docs,
  formats/lints the tree, adds the CI lint gate, and removes the retired
  script — no new production code).

- [ ] **Step 1: Write the LICENSE**

`LICENSE`:

```
MIT License

Copyright (c) 2026 Btema2

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Write the README**

`README.md`:

````markdown
# olx4ai

Context-cheap OLX browser for AI agents. A normal OLX page or API response
is ~30k tokens of markup/JSON; this tool prunes each offer down to ~8 fields
and prints one line per offer (~20 tokens), so 40 offers costs ~900 tokens
instead of ~30k.

Ships two ways to use it, sharing one core:

- **CLI** (`olx4ai`) — zero dependencies, works with any agent that has
  shell access.
- **MCP server** (`olx4ai-mcp`) — stdio transport, for Claude Desktop/Code,
  Cursor, or any other MCP-aware host.

## Install

CLI only, no dependencies:

```bash
pip install "olx4ai @ git+https://github.com/Btema2/OLX4AI"
```

CLI + MCP server:

```bash
pip install "olx4ai[mcp] @ git+https://github.com/Btema2/OLX4AI"
```

## CLI usage

```bash
olx4ai agent-help                                   # usage cheat sheet
olx4ai stats  "asus vivobook 14"                     # price distribution (~15 lines)
olx4ai search "asus vivobook 14" --max 40
olx4ai search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
olx4ai search "iphone 13" --exclude "obudowa,etui,czesci" --dedupe --no-promoted
olx4ai url    "https://www.olx.pl/oferty/q-asus-vivobook-14/?search%5Bfilter_float_price%3Ato%5D=1500"
olx4ai offer  1023456789                             # full details for one offer id
olx4ai offer  1023456789 --json
olx4ai search "iphone 13" --json --fields id,title,price
olx4ai clear-cache
```

## MCP server

Register `olx4ai-mcp` as an MCP server (stdio transport) with your client of
choice. Claude Code / Claude Desktop example config entry:

```json
{
  "mcpServers": {
    "olx4ai": {
      "command": "olx4ai-mcp"
    }
  }
}
```

Exposes five tools: `search`, `stats`, `search_url`, `offer`, `clear_cache`
— same underlying pipeline as the CLI, returning structured JSON instead of
printed text.

## Domain

Defaults to `olx.pl`. Override with `--domain olx.ua` (CLI) or
`OLX4AI_DOMAIN=olx.bg` (env var, also read by the MCP server). Only `olx.pl`
is verified — other OLX Europe sites (olx.ua, olx.bg, olx.ro, olx.kz,
olx.uz, ...) likely share the same platform and probably work, but OLX
brands outside Europe (Brazil, Pakistan, Nigeria, ...) have historically run
different infrastructure and are untested. If a domain's shape differs,
you'll see missing fields rather than a crash — please file an issue.

## Caching

Responses are cached on disk at `~/.cache/olx4ai/` for 10 minutes (override
via `OLX4AI_CACHE_DIR` / `OLX4AI_CACHE_TTL`), so re-running a query costs
nothing. `clear-cache` removes cached HTTP responses only, not the
id→URL index that lets `offer <id>` work after a `search` without ever
printing URLs.

## Development

```bash
pip install -e ".[mcp,dev]"
pytest -v
```

The whole suite runs offline against fixtures under `tests/fixtures/` — no
live network calls. To sanity-check against the real site (not run in CI):

```bash
pip install -e .
./scripts/live_smoke_test.sh
```

## License

MIT — see [LICENSE](LICENSE).
````

- [ ] **Step 3: Write the live smoke test script**

`scripts/live_smoke_test.sh`:

```bash
#!/usr/bin/env bash
# Opt-in live smoke test -- hits the real olx.pl site. Not run in CI.
set -euo pipefail

echo "== agent-help =="
olx4ai agent-help

echo "== stats =="
olx4ai stats "test query" --max 10

echo "== search =="
olx4ai search "test query" --max 5

echo "== url =="
olx4ai url "https://www.olx.pl/oferty/q-test/" --max 5

echo "== offer (HTML fallback via a listing URL) =="
FIRST_URL=$(olx4ai search "test query" --max 1 --urls --json --fields url \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['url'])")
olx4ai offer "$FIRST_URL"

echo "All live smoke checks completed."
```

```bash
chmod +x scripts/live_smoke_test.sh
```

- [ ] **Step 4: Rewrite CLAUDE.md**

`CLAUDE.md`:

````markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`olx4ai` is an installable Python package that lets an AI agent search and read OLX
listings without burning context on raw HTML/JSON. A normal OLX page or API response
is ~30k tokens of markup/JSON; this tool prunes each offer down to ~8 fields and prints
one line per offer (~20 tokens), so 40 offers costs ~900 tokens instead of ~30k.

It ships two ways to use the same core: a CLI (`olx4ai`, zero dependencies) and an MCP
server (`olx4ai-mcp`, stdio transport, requires the `[mcp]` extra). See
`docs/superpowers/specs/2026-08-25-olx4ai-rewrite-design.md` for the full design
rationale, including two real bugs found and fixed during the rewrite.

## Running it

```bash
pip install -e ".[mcp,dev]"     # editable install, CLI + MCP server + test deps

olx4ai agent-help                                   # print the usage cheat sheet
olx4ai stats  "asus vivobook 14"                     # price distribution (~15 lines)
olx4ai search "asus vivobook 14" --max 40
olx4ai search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
olx4ai search "iphone 13" --exclude "obudowa,etui,czesci" --dedupe --no-promoted
olx4ai url    "https://www.olx.pl/oferty/q-asus-vivobook-14/?search%5Bfilter_float_price%3Ato%5D=1500"
olx4ai offer  1023456789                             # full details for one offer id
olx4ai offer  1023456789 --json
olx4ai search "iphone 13" --json --fields id,title,price
olx4ai clear-cache
```

`agent-help` prints the `CHEAT` constant in `src/olx4ai/cli.py` — keep that string in
sync whenever CLI behavior changes, since that's the contract an agent actually reads
at runtime.

## Verifying changes

```bash
pytest -v                        # full suite, fixture-based, no network calls
ruff check .                     # lint
black --check .                  # format check
isort --check-only .             # import order check
python3 -m py_compile src/olx4ai/**/*.py src/olx4ai/*.py   # syntax check
```

The suite runs entirely offline against sanitized fixtures under `tests/fixtures/` —
never against the live site. To sanity-check against the real site (not part of CI):

```bash
pip install -e .
./scripts/live_smoke_test.sh
```

## Architecture

`src/olx4ai/core/` holds all business logic in single-responsibility modules; `cli.py`
and `mcp_server.py` are thin wrappers that only wire arguments to `core/` calls.

Two independent fetch paths converge on the same normalization pipeline:

1. **JSON API path** (`core/api_client.py` → `search`/`stats`) — paginates
   `https://www.<domain>/api/v1/offers/`.
2. **HTML scrape path** (`core/html_client.py`, `core/prerendered.py` → `url` command,
   and the fallback inside `offer <id>`) — pulls `window.__PRERENDERED_STATE__` out of
   a normal OLX page. This is the only way to reuse a listing URL that already has
   OLX's own filters/facets applied.

The two paths return **structurally different** raw offer shapes (see
`core/adapters.py`'s docstring and the design spec for the exact field mapping). Both
converge through:

```
fetch (disk-cached) → adapters.adapt_*_offer() → normalize()/normalize_detail() → post_filter() → emit()/print_stats()
```

- **`core/prerendered.find_offers(node)`** — structure-agnostic recursive search over
  the parsed `__PRERENDERED_STATE__` tree, finding either the longest list of
  offer-shaped dicts (search/listing pages) or a single bare offer-shaped dict
  (detail pages, where the offer sits at `state["ad"]["ad"]`). Don't replace with a
  fixed key path — OLX's prerendered state shape isn't documented/stable across page
  types.
- **`core/adapters.py`** — `adapt_api_offer()` (identity) and `adapt_html_offer()`
  reshape each source's raw offer into the one shape `normalize()` expects. Any new
  fetch path needs its own adapter here, not a change to `normalize()`.
- **`core/normalize.py`** — `normalize()` → the ~14 fields the CLI/MCP actually
  expose (price, condition, location, age, delivery/business/promoted flags, etc.).
  `normalize_detail()` extends this with parsed spec params, cleaned description
  text, seller info, and photo count, used only by `offer`.
- **`core/filters.py`** — client-side filtering the OLX API doesn't support natively:
  `--exclude`/`--must` (title keyword filters), `--no-promoted`, `--dedupe`.
- **`core/format.py`** — `compute_stats()` (pure data, used by both `print_stats()` and
  the MCP `stats` tool), `emit()` for compact human-readable lines or `--json`. Also
  calls `cache.index_put()` to persist an id→url map, since default output *omits*
  URLs — `offer <id>` and `--urls` are the only ways to recover them.

### Caching

- `core/cache.fetch()` caches raw HTTP response bodies on disk at
  `~/.cache/olx4ai/<sha1(url)>.cache` (override dir via `OLX4AI_CACHE_DIR`, TTL via
  `OLX4AI_CACHE_TTL`, default 600s). `clear-cache` only removes these files.
- Separately, `core/cache.index_put()`/`index_get()` maintain
  `~/.cache/olx4ai/index.json`, an id→url map with no TTL. `clear-cache` does **not**
  clear this file.

### Domain configuration

`core/cache.py`'s `DOMAIN`/`BASE`/`API` are set via `configure()`, called from
`--domain` (CLI) or read initially from `OLX4AI_DOMAIN` (env var). **Always access
these live through the `cache` module** (`from olx4ai.core import cache; cache.API`)
— never `from olx4ai.core.cache import API`, which freezes a stale value before
`configure()` runs.

### Adding a new CLI command

Add a `sub.add_parser(...)` in `cli.py`'s `build_parser()`, reuse `common(sp)` for the
shared search/filter/output flags where applicable, and set `func=cmd_x` pointing at a
new `cmd_x(args)` function. Keep any actual logic in `core/` — `cli.py` should only
wire arguments to core calls. Add the equivalent MCP tool in `mcp_server.py` if the
command has agent-facing value.
````

- [ ] **Step 5: Run black/isort/ruff across the whole tree and add the CI lint gate**

The `dev` extra (Task 1) already carries `black`/`isort`/`ruff`, and every
task since has been written by hand — this is the one point where the
whole tree gets normalized in one pass rather than gating each task's
commit individually.

Run:
```bash
pip install -e ".[mcp,dev]"
isort src tests
black src tests
ruff check --fix src tests
```
Expected: some files reformatted (whitespace/quote/import-order only — no
behavior change); if `ruff check` reports anything it can't auto-fix,
fix it by hand and re-run.

Append two lint steps to `.github/workflows/ci.yml`, right before the
existing `pytest -v` step:

```yaml
      - run: pip install -e ".[mcp,dev]"
      - run: ruff check .
      - run: black --check .
      - run: isort --check-only .
      - run: pytest -v
```

(This replaces the single `pip install` + `pytest -v` pair at the end of
the existing `steps:` list with the four lines above — same install line,
three new check lines ahead of the existing test line.)

- [ ] **Step 6: Remove the retired single-file script**

```bash
rm -f olx.py
rm -rf __pycache__
```

- [ ] **Step 7: Run the full verification suite**

Run:
```bash
pytest -v
ruff check .
black --check .
isort --check-only .
python3 -m py_compile src/olx4ai/*.py src/olx4ai/core/*.py
olx4ai agent-help
```
Expected: every test PASSes, lint/format checks report clean, no syntax
errors, `agent-help` prints the cheat sheet mentioning `olx4ai` (not
`olx.py`).

- [ ] **Step 8: Commit**

```bash
git add README.md LICENSE scripts/live_smoke_test.sh CLAUDE.md \
        .github/workflows/ci.yml src tests
git rm olx.py
git commit -m "docs: add README/LICENSE, rewrite CLAUDE.md, retire olx.py

Also normalizes the whole tree with black/isort/ruff and adds the
matching CI lint gate."
```
