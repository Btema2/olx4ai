# olx4ai — Bug & Security Audit Report

**Date:** 2026-08-28
**Tester:** Hermes Agent
**Scope:** Live OLX.pl testing + offline code audit
**Tool:** olx4ai v0.1.0 (git: `https://github.com/Btema2/olx4ai`)

---

## Executive Summary

| Category | Count |
|---|---|
| Critical (breaks the tool) | 2 |
| High (silent data loss / misleading output) | 3 |
| Medium (edge-case crashes) | 4 |
| Low (minor UX / doc gaps) | 4 |
| Security / vulnerability | 3 |

**Headline:** The **git-repo (stock) version is 100% broken against live OLX** — every command
403s because Python's `urllib` speaks HTTP/1.1 and CloudFront rejects it. The **installed
(curl/HTTP-2) patch fixes the 403 but crashes on every 4xx response** due to a Python 3.13
`HTTPError` constructor bug. The two distributions are mutually exclusive: one is 403-blocked,
the other is 4xx-broken. Neither is shippable as-is.

---

## CRITICAL

### B1 — Stock repo 403s on every live OLX request
- **File:** `src/olx4ai/core/cache.py` (stock urllib path)
- **Reproduction:**
  ```bash
  pip install -e .
  olx4ai search "asus zenbook" --max 3
  # → HTTP 403 for https://www.olx.pl/api/v1/offers/?offset=0&limit=3&query=asus+zenbook...
  ```
- **Root cause:** `urllib.request.urlopen()` speaks HTTP/1.1 only. OLX's CloudFront edge
  rejects HTTP/1.1 with 403. `curl --http2` gets 200.
- **Impact:** 100% of commands fail against live OLX. The tool is unusable from the git repo.
- **Fix path:** Replace `_open()` with a `curl --http2` subprocess (see installed copy's
  `cache.py` / `/opt/data/olx4ai-http2-fix.patch`).

### B2 — Python 3.13 `HTTPError.__init__()` TypeError on all 4xx responses
- **File:** `src/olx4ai/core/cache.py` (installed curl path, `_open()` wrapper)
- **Reproduction:**
  ```bash
  olx4ai offer "https://www.olx.pl/d/oferta/this-does-not-exist-CID99-ID000000.html"
  # → network error for https://...: HTTPError.__init__() missing 1 required positional argument: 'fp'
  ```
- **Root cause:** The curl-path `_open()` constructs
  `urllib.error.HTTPError(url, status, "", io.BytesIO(body))` — 4 positional args.
  Python 3.13's `HTTPError.__init__` requires 5: `(url, code, msg, hdrs, fp)`.
  The `TypeError` is caught by `except Exception as e:` and masked as
  `"network error for {url}: {e}"`, hiding the real cause.
- **Impact:** Every 4xx (403, 404, 410) response crashes with a misleading "network error"
  message. The real HTTP status code is lost. Users cannot distinguish "not found" from
  "connection refused".
- **Fix path:** Pass `hdrs={}` as the 4th argument:
  `HTTPError(url, status, f"HTTP {status}", {}, io.BytesIO(body))`.

---

## HIGH

### B3 — `url` command silently ignores `--min` / `--max-price`
- **File:** `src/olx4ai/cli.py` `cmd_url()`, `src/olx4ai/core/html_client.py`
- **Reproduction:**
  ```bash
  olx4ai url "https://www.olx.pl/warszawa/q-asus-zenbook/" --min 1800 --max-price 2800 --json
  # → returns 50 zł chargers, 1750 zł listings — all below --min
  ```
- **Root cause:** `--min` / `--max-price` / `--condition` / `--sort` / `--exclude` /
  `--must` / `--dedupe` / `--no-promoted` are wired only to `api_client.api_search()`.
  `html_client.html_search()` receives only `url` and `use_cache`. The CLI accepts the flags
  but never passes them to the HTML path.
- **Impact:** Silent data loss. An agent filtering by price via `url` gets unfiltered results
  with no warning.
- **Fix path:** Either (a) pass `args` to `html_search()` and apply `filters.post_filter()`,
  or (b) document that `url` ignores these flags and emit a warning.

### B4 — Empty / whitespace query returns the entire OLX catalog
- **File:** `src/olx4ai/cli.py` `cmd_search()`, `src/olx4ai/core/api_client.py`
- **Reproduction:**
  ```bash
  olx4ai search "" --max 3
  # → returns 3 random offers from the entire OLX.pl catalog, exit 0
  olx4ai search "   " --max 3
  # → same
  ```
- **Root cause:** `args.query` is passed verbatim to the API as `query=`. OLX's API treats
  empty/whitespace query as "no filter" and returns the full catalog.
- **Impact:** An agent with a bug in its query construction silently gets the entire catalog
  instead of an error. Wastes context, misleads ranking.
- **Fix path:** Validate `query.strip()` is non-empty in `cmd_search()` before calling
  `api_search()`.

### B5 — `--fields` with whitespace silently drops fields
- **File:** `src/olx4ai/core/format.py` `emit()`
- **Reproduction:**
  ```bash
  olx4ai search "asus zenbook" --max 1 --json --fields "id,title, price"
  # → [{"id":..., "title":...}]  — price field silently missing
  ```
- **Root cause:** `args.fields.split(",")` does not `.strip()` each field. `" price"` ≠ `"price"`.
- **Impact:** Silent data loss in `--json` output. An agent filtering on `price` gets `None`.
- **Fix path:** `fields = [f.strip() for f in args.fields.split(",")]`.

---

## MEDIUM

### B6 — Negative `--min` sends malformed API param → 403 with misleading message
- **File:** `src/olx4ai/cli.py`, `src/olx4ai/core/api_client.py`
- **Reproduction:**
  ```bash
  olx4ai search "asus" --min -100 --max 1
  # → network error for https://www.olx.pl/api/v1/offers/?...filter_float_price%3Afrom=-100:
  #   HTTPError.__init__() missing 1 required positional argument: 'fp'
  ```
- **Root cause:** `args.min` is passed verbatim to `filter_float_price:from=-100`. CloudFront
  rejects the malformed param with 403, which hits B2's crash.
- **Fix path:** Validate `args.min >= 0` in `cmd_search()`.

### B7 — `--title-chars 0` produces off-by-one truncated titles
- **File:** `src/olx4ai/core/format.py` `fmt_line()`
- **Reproduction:**
  ```bash
  olx4ai search "asus zenbook" --max 1 --title-chars 0
  # → "Asus Zenbook UX434FAC…"  (title[:−1] + "…")
  ```
- **Root cause:** `title[:title_chars - 1]` with `title_chars=0` → `title[:-1]` (drops last char).
- **Fix path:** `if title_chars and len(title) > title_chars: title = title[:title_chars] + "…"`

### B8 — Malformed JSON response → unhandled `JSONDecodeError` traceback
- **File:** `src/olx4ai/cli.py` `main()`, `src/olx4ai/core/api_client.py`
- **Reproduction:** (simulated)
  ```python
  # Point cache.fetch at a non-JSON body:
  # → json.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2
  #   (unhandled traceback, no clean exit code)
  ```
- **Root cause:** `json.loads(cache.fetch(...))` in `api_client.api_search()` is not wrapped.
  `cache.fetch()` only catches `HTTPError` / network exceptions, not `JSONDecodeError`.
- **Impact:** Ugly traceback, no clean exit code, no user-friendly message.
- **Fix path:** Wrap `json.loads()` in try/except → `SystemExit("malformed JSON response")`.

### B9 — `clear-cache` does not clear `index.json`
- **File:** `src/olx4ai/cli.py` `cmd_clear_cache()`
- **Reproduction:**
  ```bash
  olx4ai clear-cache
  ls ~/.cache/olx4ai/index.json  # still present
  ```
- **Root cause:** `cmd_clear_cache()` only removes `*.cache` files. `index.json` (the id→url
  map, no TTL) is never cleaned.
- **Impact:** Confusing. `clear-cache` implies "clear all", but the index persists. An agent
  that expects a clean slate gets stale id→url mappings.
- **Fix path:** Also remove `index.json` in `cmd_clear_cache()`, or rename to `clear-cache-responses`
  and add a separate `clear-index` command.

---

## LOW

### B10 — `--param "nodash"` silently sends empty-value API param
- **File:** `src/olx4ai/core/api_client.py` line 46-47
- **Reproduction:**
  ```bash
  olx4ai search "asus" --param "bogus" --max 1
  # → sends ?bogus=&query=asus...  (empty value)
  ```
- **Root cause:** `k, _, v = kv.partition("=")` — if no `=`, `v=""`. `params[k]=""` is sent.
- **Fix path:** Skip params without `=` or raise a warning.

### B11 — `--max 0` silently returns "no results"
- **File:** `src/olx4ai/cli.py` `cmd_search()`
- **Reproduction:**
  ```bash
  olx4ai search "asus" --max 0
  # → "no results"  (not an error, just empty)
  ```
- **Root cause:** `while len(rows) < args.max:` with `args.max=0` → loop never executes.
- **Fix path:** Validate `args.max > 0`.

### B12 — `cache.fetch()` writes to disk before decoding; interrupted write leaves torn cache file
- **File:** `src/olx4ai/core/cache.py` `fetch()`
- **Root cause:** `with open(path, "w") as fh: fh.write(text)` — if the process is killed
  mid-write, the `.cache` file contains partial data. Next read returns the partial text,
  `json.loads()` fails → B8's traceback.
- **Fix path:** Write to `path + ".tmp"` then `os.replace(tmp, path)` (atomic rename).

### B13 — `index.json` grows unbounded across all searches
- **File:** `src/olx4ai/core/cache.py` `index_put()`
- **Root cause:** No size limit, no TTL, no pruning. Every `search` / `url` call adds entries.
- **Fix path:** Add a max-size or TTL-based prune, or document that `clear-cache` is the only
  reset mechanism.

---

## SECURITY / VULNERABILITIES

### V1 — SSRF: no host allowlist on `url` / `offer` commands
- **File:** `src/olx4ai/core/cache.py` `fetch()`, `src/olx4ai/cli.py` `cmd_offer()`, `cmd_url()`
- **Reproduction:**
  ```bash
  olx4ai offer "http://127.0.0.1:8080/admin"
  # → passes scheme check, attempts fetch (curl --http2 to localhost)
  olx4ai offer "http://169.254.169.254/latest/meta-data/"
  # → passes scheme check, attempts fetch (cloud metadata endpoint)
  ```
- **Root cause:** The only gate is `scheme in ("http", "https")`. No host allowlist.
  The `url` and `offer` commands accept arbitrary URLs. The MCP server exposes the same
  `offer` / `search_url` tools to any MCP client.
- **Impact:** An agent (or a malicious MCP client) can use the tool as an SSRF primitive
  against the local network, cloud metadata endpoints, or internal services.
- **Fix path:** Add a host allowlist (require `"olx"` in hostname) before the fetch.
  Block `127.0.0.0/8`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`.

### V2 — Plaintext HTTP accepted (no TLS enforcement)
- **File:** `src/olx4ai/core/cache.py` `fetch()`
- **Reproduction:**
  ```bash
  olx4ai url "http://olx.pl/12345"
  # → accepted (scheme check passes "http")
  ```
- **Root cause:** `scheme in ("http", "https")` accepts both.
- **Impact:** A MITM on an `http://` URL can inject `__PRERENDERED_STATE__` content.
  The tool's `Referer` header and UA are not secrets, but the fetched HTML is parsed
  and trusted as data.
- **Fix path:** Require `https` for `olx.*` domains, or at minimum warn on `http://`.

### V3 — `--param` allows injecting arbitrary API parameters
- **File:** `src/olx4ai/cli.py` `common()`, `src/olx4ai/core/api_client.py` line 45-47
- **Root cause:** `--param` is documented as "raw API param, repeatable". `k, _, v =
  kv.partition("=")` and `params[k] = v` — no validation of `k`.
- **Impact:** An agent can pass `--param "category_id=999"` or any OLX API param.
  This is by design but should be documented as a security boundary: the `--param` flag
  is an escape hatch that bypasses the tool's own filtering.
- **Fix path:** Document the security boundary. Optionally restrict `k` to a known set
  (`category_id`, `city_id`, `region_id`, `filter_enum_state[0]`, `filter_float_price:from/to`,
  `filter_enum_hdd_type[0]`, `sort_by`).

---

## NEW REVIEW FINDINGS (N1 - N4)

### N1 — `url` path ignores `--sort`
- **File:** `src/olx4ai/core/filters.py`, `src/olx4ai/mcp_server.py`
- **Root cause:** `post_filter` filtered on min/max/condition but did not implement in-memory sorting.
- **Fix:** Added `price-asc` and `price-desc` in-memory sorting to `post_filter` (preserving non-price sorts and putting unpriced offers at the end), wired `sort` in MCP `search_url`.

### N2 — `url` path fetches exactly ONE page → silent ~40-offer cap
- **File:** `src/olx4ai/core/html_client.py`, `src/olx4ai/cli.py`, `src/olx4ai/mcp_server.py`
- **Root cause:** `html_search()` fetched only the initial URL without `page=N` pagination.
- **Fix:** Added pagination loop `_build_page_url` to fetch sequential pages until `max_results` is reached or no new offer IDs are discovered.

### N3 — `olx4ai-mcp` ships broken without the [mcp] extra
- **File:** `src/olx4ai/mcp_server.py`
- **Root cause:** Top-level import `from mcp.server import MCPServer` raised unhandled `ModuleNotFoundError` when the optional extra wasn't installed.
- **Fix:** Handled missing `MCPServer` import gracefully with `_DummyMCP` and clear `SystemExit("olx4ai-mcp requires the mcp extra: pip install 'olx4ai[mcp]' or uv tool install 'olx4ai[mcp]'")`.

### N4 — 404 response dumps raw HTML head
- **File:** `src/olx4ai/core/cache.py`
- **Root cause:** `HTTPError` handler dumped the first 400 bytes of the response body, which for 404s was raw HTML boilerplate.
- **Fix:** Added `_format_http_error` to return clean `HTTP 404 for <url>` without body preview for 404 errors.

---

## Verification Checklist

| Bug | Status |
|---|---|
| B1 (stock 403) | **FIXED** — PR #1 (curl --http2 subprocess implementation) |
| B2 (py3.13 HTTPError) | **FIXED** — PR #1 (5-arg constructor + header parsing) |
| B3 (url ignores --min) | **FIXED** — in-memory post_filter handles min, max_price, and condition |
| B4 (empty query) | **FIXED** — rejects empty/whitespace query with SystemExit |
| B5 (--fields whitespace) | **FIXED** — whitespace stripped from field whitelist tokens |
| B6 (negative --min) | **FIXED** — rejects negative --min / --max-price with SystemExit |
| B7 (--title-chars 0) | **FIXED** — non-positive title-chars leaves title untruncated |
| B8 (malformed JSON) | **FIXED** — clean SystemExit error message on malformed JSON |
| B9 (clear-cache index) | **FIXED** — clear-cache removes both .cache files and index.json |
| B10 (--param nodash) | **FIXED** — skips invalid/empty param key or param without '=' |
| B11 (--max 0) | **FIXED** — validates --max > 0 with SystemExit |
| B12 (non-atomic cache write) | **FIXED** — PR #1 (atomic .tmp + os.replace write) |
| B13 (index unbounded) | **FIXED** — bounded index with MAX_INDEX_ENTRIES and FIFO/LRU pruning |
| V1 (SSRF) | **FIXED** — URL host allowlist and private/internal IP/domain blocking |
| V2 (plaintext HTTP) | **FIXED** — TLS enforced (refuses non-https URLs) |
| V3 (--param injection) | **FIXED** — documented security boundary (unvalidated escape hatch) |
| N1 (url ignores --sort) | **FIXED** — in-memory post_filter handles price-asc and price-desc sorting |
| N2 (url single page cap) | **FIXED** — html_search paginates sequential pages up to max_results |
| N3 (olx4ai-mcp without extra) | **FIXED** — clean SystemExit error message guiding user to install [mcp] extra |
| N4 (404 dumps raw HTML) | **FIXED** — clean HTTP 404 error message without noisy HTML body preview |

---

## Recommended Priority Order

1. **B1 + B2** (fix the two criticals — the tool is unusable without both)
2. **V1** (SSRF — security, easy fix: host allowlist)
3. **B3 + N1 + N2** (silent data loss in URL path)
4. **B4** (empty query — agents will hit this immediately)
5. **B5** (silent field drop — agents will hit this with --json)
6. **B8** (malformed JSON — crash without clean exit)
7. **V2** (plaintext HTTP)
8. **N3, N4, B6, B7, B9, B10, B11, B12, B13, V3** (low/medium priority)

