# Fix Audit Findings (2026-08-28) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all bugs identified in `olx4ai-bug-report-2026-08-28.md`: BUG-NEW-1 (degraded SSR detection, retry, negative-cache skip, stderr warning), BUG-NEW-2 (`clear-cache` removing `.tmp` files), BUG-NEW-3 (request header variation on retry), and BUG-NEW-4 (robust single-quoted JS string unescaping).

**Architecture:**
- `prerendered.py`: Replace string `.replace()` hacks with a dedicated JS single-quoted string literal decoder (`_decode_js_single_quoted_string`) handling escapes (`\\`, `\'`, `\"`, `\n`, `\uXXXX`, etc.) without collision.
- `cache.py`: Centralize cache eviction and cleanup with `clear_cache()` removing `*.cache`, `*.tmp`, `*.cache.tmp`, `index.json`, `index.json.tmp`. Update `fetch()` to vary headers on retry (`_build_request(url, json_mode, retry)`), and support `write_cache` / `evict(url)`.
- `html_client.py`: In `html_search()`, detect when `find_offers()` yields 0 offers on the start page, evict cache, retry once with `use_cache=False`, prevent cache-poisoning of 0-offer responses, and emit a visible warning to `sys.stderr`.
- `cli.py` & `mcp_server.py`: Reuse `cache.clear_cache()` for cache cleanup.

**Tech Stack:** Python 3.10+, pytest, urllib, curl

## Global Constraints

- Zero external dependencies for core CLI (Python standard library + curl).
- All tests must pass with `.venv/bin/pytest`.
- Keep error and warning message formatting clean and consistent with existing patterns (`SystemExit("<msg>")` for fatal errors, `sys.stderr.write(...)` or `print(..., file=sys.stderr)` for warnings).
- Do not introduce breaking API changes.

---

### Task 1: BUG-NEW-4 — Single-Quoted JS String Normalization in `prerendered.py`

**Files:**
- Modify: `src/olx4ai/core/prerendered.py:55-70`
- Test: `tests/test_prerendered.py`

**Interfaces:**
- Consumes: `extract_prerendered(html: str) -> dict`
- Produces: Robust single-quoted JS string parsing without backslash-apostrophe collision.

- [ ] **Step 1: Write the failing tests**

In `tests/test_prerendered.py`:
```python
def test_extract_prerendered_single_quoted_backslash_quote_edge_case():
    # String literal with escaped backslash followed by escaped quote: \\\'
    html = """
    <html>
      <script>
        window.__PRERENDERED_STATE__ = '{"category": "test\\\\\\'s", "path": "C:\\\\\\\\test", "quote": "hello \\"world\\""}';
      </script>
    </html>
    """
    state = extract_prerendered(html)
    assert state["category"] == "test\\'s"
    assert state["path"] == "C:\\\\test"
    assert state["quote"] == 'hello "world"'


def test_extract_prerendered_single_quoted_escapes():
    html = """
    <script>
      window.__PRERENDERED_STATE__ = '{"name": "O\\\'Neil\\nCorp", "unicode": "\\u0041"}';
    </script>
    """
    state = extract_prerendered(html)
    assert state["name"] == "O'Neil\nCorp"
    assert state["unicode"] == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prerendered.py::test_extract_prerendered_single_quoted_backslash_quote_edge_case -v`
Expected: FAIL due to corrupted string replace

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/core/prerendered.py`:
Add helper function `_decode_js_single_quoted_string`:
```python
def _decode_js_single_quoted_string(s: str) -> str:
    """Decode a single-quoted JS string literal s (including outer quotes)."""
    content = s[1:-1]
    res = []
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        if c == "\\":
            i += 1
            if i >= n:
                break
            esc = content[i]
            if esc == "'":
                res.append("'")
            elif esc == "\\":
                res.append("\\")
            elif esc == '"':
                res.append('"')
            elif esc == "b":
                res.append("\b")
            elif esc == "f":
                res.append("\f")
            elif esc == "n":
                res.append("\n")
            elif esc == "r":
                res.append("\r")
            elif esc == "t":
                res.append("\t")
            elif esc == "v":
                res.append("\v")
            elif esc == "0":
                res.append("\0")
            elif esc == "x" and i + 2 < n:
                try:
                    res.append(chr(int(content[i + 1 : i + 3], 16)))
                    i += 2
                except ValueError:
                    res.append("\\x")
            elif esc == "u" and i + 4 < n:
                try:
                    res.append(chr(int(content[i + 1 : i + 5], 16)))
                    i += 4
                except ValueError:
                    res.append("\\u")
            else:
                res.append(esc)
        else:
            res.append(c)
        i += 1
    return "".join(res)
```

In `extract_prerendered(html: str)`:
```python
        if rest[0] in "\"'":
            literal = _scan_js_string(rest)
            if literal[0] == "'":
                inner = _decode_js_single_quoted_string(literal)
            else:
                inner = json.loads(literal)
        else:
            inner = _scan_balanced(rest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prerendered.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/prerendered.py tests/test_prerendered.py
git commit -m "fix(prerendered): resolve BUG-NEW-4 by safely decoding single-quoted JS strings"
```

---

### Task 2: BUG-NEW-2 — Atomic-Write `.tmp` Remnants Cleanup

**Files:**
- Modify: `src/olx4ai/core/cache.py`, `src/olx4ai/cli.py`, `src/olx4ai/mcp_server.py`
- Test: `tests/test_cache.py`, `tests/test_cli.py`, `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `cache.clear_cache() -> int`
- Produces: Cleanup of `*.cache`, `*.tmp`, `*.cache.tmp`, `index.json`, `index.json.tmp`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cache.py`:
```python
def test_clear_cache_removes_tmp_files_and_cache(isolated_cache_dir):
    cache_file = os.path.join(isolated_cache_dir, "test1.cache")
    tmp_file1 = os.path.join(isolated_cache_dir, "test1.cache.tmp")
    tmp_file2 = os.path.join(isolated_cache_dir, "other.tmp")
    index_file = os.path.join(isolated_cache_dir, "index.json")
    index_tmp = os.path.join(isolated_cache_dir, "index.json.tmp")

    for p in (cache_file, tmp_file1, tmp_file2, index_file, index_tmp):
        with open(p, "w") as f:
            f.write("content")

    removed = cache.clear_cache()
    assert removed == 1
    assert not os.path.exists(cache_file)
    assert not os.path.exists(tmp_file1)
    assert not os.path.exists(tmp_file2)
    assert not os.path.exists(index_file)
    assert not os.path.exists(index_tmp)
```

In `tests/test_cli.py` and `tests/test_mcp_server.py`:
Add tests asserting `clear-cache` CLI and MCP `clear_cache` remove `.tmp` files.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cache.py::test_clear_cache_removes_tmp_files_and_cache -v`
Expected: FAIL (`clear_cache` doesn't exist / does not remove `.tmp`)

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/core/cache.py`:
```python
def clear_cache() -> int:
    """Remove all cached HTTP responses, index files, and atomic-write .tmp remnants.
    Returns the count of .cache files removed."""
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            full_path = os.path.join(CACHE_DIR, f)
            if f.endswith(".cache"):
                try:
                    os.remove(full_path)
                    n += 1
                except OSError:
                    pass
            elif f.endswith(".tmp") or f.endswith(".cache.tmp") or f in ("index.json", "index.json.tmp"):
                try:
                    os.remove(full_path)
                except OSError:
                    pass
    return n
```

In `src/olx4ai/cli.py`:
```python
def cmd_clear_cache(args: argparse.Namespace) -> None:
    n = cache.clear_cache()
    print(f"removed {n} cached responses")
```

In `src/olx4ai/mcp_server.py`:
```python
@mcp.tool()
def clear_cache() -> dict[str, Any]:
    """Remove all cached HTTP responses and the id-to-url index."""
    n = cache.clear_cache()
    return {"removed": n}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cache.py tests/test_cli.py tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/cache.py src/olx4ai/cli.py src/olx4ai/mcp_server.py tests/test_cache.py tests/test_cli.py tests/test_mcp_server.py
git commit -m "fix(cache): resolve BUG-NEW-2 by cleaning up atomic-write .tmp remnants in clear_cache"
```

---

### Task 3: BUG-NEW-3 — Retry Path Header Variation in `cache.py`

**Files:**
- Modify: `src/olx4ai/core/cache.py:240-290`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `_build_request(url: str, json_mode: bool, retry: bool = False) -> urllib.request.Request`
- Produces: Varied header profile on retry.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cache.py`:
```python
def test_fetch_varies_headers_on_retry(isolated_cache_dir, monkeypatch):
    requests_made = []

    def mock_open(req):
        requests_made.append(req)
        if len(requests_made) == 1:
            raise _http_error(429, headers={"Retry-After": "0"})
        return b'{"ok": true}', ""

    monkeypatch.setattr(cache, "_open", mock_open)
    monkeypatch.setattr(cache, "_retry_delay", lambda e: 0)

    res = cache.fetch("https://www.olx.pl/api/v1/offers/123/", json_mode=True, use_cache=False)
    assert res == '{"ok": true}'
    assert len(requests_made) == 2

    first_headers = dict(requests_made[0].header_items())
    second_headers = dict(requests_made[1].header_items())

    # Harmless header variation on retry
    assert first_headers != second_headers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cache.py::test_fetch_varies_headers_on_retry -v`
Expected: FAIL (headers are currently identical)

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/core/cache.py`:
Define `_build_request`:
```python
def _build_request(url: str, *, json_mode: bool, retry: bool = False) -> urllib.request.Request:
    if retry:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
                if "Chrome" in UA
                else UA
            ),
            "Accept": (
                "application/json, text/plain, */*;q=0.8"
                if json_mode
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Accept-Encoding": "gzip, deflate",
            "Referer": BASE + "/",
        }
    else:
        headers = {
            "User-Agent": UA,
            "Accept": (
                "application/json, text/plain, */*"
                if json_mode
                else "text/html,application/xhtml+xml"
            ),
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": BASE + "/",
        }
    return urllib.request.Request(url, headers=headers)
```

In `fetch()`:
```python
    req = _build_request(url, json_mode=json_mode, retry=False)
    try:
        raw, enc = _open(req)
    except urllib.error.HTTPError as e:
        if not _is_retryable(e.code):
            raise SystemExit(_format_http_error(e, url))
        delay = _retry_delay(e)
        e.close()
        time.sleep(delay)
        try:
            req_retry = _build_request(url, json_mode=json_mode, retry=True)
            raw, enc = _open(req_retry)
        except urllib.error.HTTPError as e2:
            raise SystemExit(_format_http_error(e2, url))
        except Exception as e2:  # noqa: BLE001
            raise SystemExit(f"network error for {url}: {e2}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/cache.py tests/test_cache.py
git commit -m "fix(cache): resolve BUG-NEW-3 by varying request headers on retry"
```

---

### Task 4: BUG-NEW-1 — Degraded SSR / Bot-Wall Detection, Retry, Cache Eviction, and Warning

**Files:**
- Modify: `src/olx4ai/core/cache.py`, `src/olx4ai/core/html_client.py`
- Test: `tests/test_html_client.py`, `tests/test_cache.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `cache.evict(url: str) -> None`, `cache.fetch(..., write_cache: bool = True)`
- Produces: `html_search()` retry on empty initial batch, negative-lookup cache skip / eviction, and stderr warning.

- [ ] **Step 1: Write the failing tests**

In `tests/test_html_client.py`:
```python
def test_html_search_retries_and_warns_on_degraded_empty_state(monkeypatch, capsys):
    degraded_html = (
        '<html><script>window.__PRERENDERED_STATE__ = \'{"categories": {"1": "tech"}}\';</script></html>'
    )
    full_html = (
        '<html><script>window.__PRERENDERED_STATE__ = \'{"listing": {"listing": {"ads": ['
        '{"id": 999, "title": "Laptop", "url": "https://www.olx.pl/d/oferty/laptop-999.html"}'
        ']}}}\';</script></html>'
    )

    fetch_calls = []

    def mock_fetch(url, *, json_mode, use_cache=True, ttl=None, write_cache=True):
        fetch_calls.append({"url": url, "use_cache": use_cache, "write_cache": write_cache})
        if len(fetch_calls) == 1:
            return degraded_html
        return full_html

    monkeypatch.setattr(cache, "fetch", mock_fetch)

    offers = html_client.html_search("https://www.olx.pl/warszawa/q-laptop/", use_cache=True)
    assert len(offers) == 1
    assert offers[0]["id"] == 999
    assert len(fetch_calls) == 2
    assert fetch_calls[0]["use_cache"] is True
    assert fetch_calls[1]["use_cache"] is False


def test_html_search_warns_and_does_not_cache_persistent_empty_state(monkeypatch, capsys):
    degraded_html = (
        '<html><script>window.__PRERENDERED_STATE__ = \'{"categories": {"1": "tech"}}\';</script></html>'
    )
    evicted = []

    def mock_fetch(url, *, json_mode, use_cache=True, ttl=None, write_cache=True):
        return degraded_html

    monkeypatch.setattr(cache, "fetch", mock_fetch)
    monkeypatch.setattr(cache, "evict", lambda url: evicted.append(url))

    target_url = "https://www.olx.pl/warszawa/q-empty/"
    offers = html_client.html_search(target_url, use_cache=True)

    assert offers == []
    captured = capsys.readouterr()
    assert "WARNING: 0 offers parsed — possible bot-wall or layout variant" in captured.err
    assert target_url in evicted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_html_client.py::test_html_search_retries_and_warns_on_degraded_empty_state tests/test_html_client.py::test_html_search_warns_and_does_not_cache_persistent_empty_state -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/core/cache.py`:
Add `evict(url: str) -> None`:
```python
def evict(url: str) -> None:
    """Remove cached response for url if it exists."""
    p = _cache_path(url)
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass
```

Update `fetch()` signature and cache write:
```python
def fetch(
    url: str,
    *,
    json_mode: bool,
    use_cache: bool = True,
    ttl: int = CACHE_TTL,
    write_cache: bool = True,
) -> str:
    _validate_url(url)

    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    ...
    text = raw.decode("utf-8", "replace")

    if write_cache:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    return text
```

In `src/olx4ai/core/html_client.py`:
```python
import sys
...

def html_search(url: str, use_cache: bool, max_results: int | None = None) -> list[dict]:
    parsed = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    page_param = next((v for k, v in qs if k == "page"), None)
    try:
        start_page = int(page_param) if page_param else 1
    except ValueError:
        start_page = 1
    if start_page < 1:
        start_page = 1

    current_page = start_page
    all_offers: list[dict] = []
    seen_keys: set[Any] = set()

    while True:
        if current_page == 1 and not page_param:
            page_url = url
        elif current_page == start_page and page_param:
            page_url = url
        else:
            page_url = _build_page_url(url, current_page)

        try:
            raw_html = cache.fetch(page_url, json_mode=False, use_cache=use_cache)
            state = extract_prerendered(raw_html)
            batch = find_offers(state)

            if not batch and current_page == start_page:
                cache.evict(page_url)
                if use_cache:
                    # Retry once uncached
                    raw_html = cache.fetch(
                        page_url, json_mode=False, use_cache=False, write_cache=False
                    )
                    state = extract_prerendered(raw_html)
                    batch = find_offers(state)
                    if batch:
                        # Write valid result to cache
                        cache.fetch(page_url, json_mode=False, use_cache=False, write_cache=True)

                if not batch:
                    cache.evict(page_url)
                    sys.stderr.write(
                        "WARNING: 0 offers parsed — possible bot-wall or layout variant\n"
                    )
        except Exception:
            if all_offers:
                break
            raise

        if not batch:
            break

        new_count = 0
        for offer in batch:
            offer_id = offer.get("id")
            key = str(offer_id) if offer_id is not None else (offer.get("title"), offer.get("url"))
            if key not in seen_keys:
                seen_keys.add(key)
                all_offers.append(offer)
                new_count += 1

        if new_count == 0:
            break

        if max_results is not None and len(all_offers) >= max_results:
            break

        current_page += 1
        if max_results is not None and len(all_offers) < max_results:
            time.sleep(api_client.SLEEP_BETWEEN_PAGES)

    return all_offers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/cache.py src/olx4ai/core/html_client.py tests/test_html_client.py tests/test_cache.py
git commit -m "fix(html_client): resolve BUG-NEW-1 with degraded SSR retry, cache eviction, and warning"
```

---

### Task 5: Full Regression & Test Suite Verification

**Files:**
- Test: all tests in `tests/`

- [ ] **Step 1: Run full pytest suite**

Run: `.venv/bin/pytest -v`
Expected: All 160+ tests PASS

- [ ] **Step 2: Verify code formatting and linting**

Run: `ruff check .` (or `black --check . && isort --check-only .` if configured)

- [ ] **Step 3: Run whole-branch review and finish**
