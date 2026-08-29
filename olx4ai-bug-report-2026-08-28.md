# olx4ai — Bug & Vulnerability Audit Report

**Date:** 2026-08-28 (UTC)
**Build:** git `918ca9c` (branch main, post-audit-fix PR #6) — `a59767e..918ca9c` diff reviewed
**Baseline:** previous re-verification report (2026-08-28): 17/17 items resolved at `a59767e`
**Method:** offline code audit of all 14 source files (~1.5k LOC) + live OLX.pl probing

---

## Executive Summary

| Category | New findings | Status |
|---|---|---|
| Medium | 1 (silent zero-offer result on degraded SSR variant) | **NEW — reproducible live** |
| Low | 3 | New |
| Security / vulnerability | 0 new (V-NEW-1/2/3 remain fixed — re-verified live) | — |
| Regression checks | 17/17 previously fixed items still pass | Verified |

No critical or high-severity defects. The tool is shippable; the one medium
finding is a live-OLX interaction issue, not a code-logic bug.

---

## NEW FINDINGS

### BUG-NEW-1 (MEDIUM) — Silent `[]` on OLX degraded-SSR / bot-wall variant

- **File:** `src/olx4ai/core/html_client.py` + `core/prerendered.py` + `core/cache.py`
- **Reproduction (live, 2026-08-28):**
  ```bash
  olx4ai url "https://www.olx.pl/warszawa/q-elitebook-845/" --max 60 --json
  # -> []  (exit 0, no warning)
  # cached response: 1,632,078 bytes, __PRERENDERED_STATE__ present but
  # state == {"categories": {...}}  — no listing, no ads, no offers.

  curl --http2 -s "https://www.olx.pl/warszawa/q-elitebook-845/"   # plain curl UA
  # -> 3,572,357 bytes, full state: listing.listing.ads == 40 offers.

  olx4ai url "https://www.olx.pl/warszawa/q-elitebook-845/" --max 5   # after cache cleared
  # -> 5 real offers, exit 0
  ```
- **Root cause:** OLX's CDN/edge served the CLI's request profile
  (UA `Mozilla/5.0 ... Chrome/126.0`, `Accept: text/html`, pl-PL headers) a
  degraded SSR page whose `__PRERENDERED_STATE__` contains only `categories`
  (bot-wall / A-B variant). `find_offers()` correctly returns `[]` on that
  tree, the pipeline exits 0, and the degraded body is then **cached for the
  full 600 s TTL**, so every call in the window silently reports "no results".
  The page itself is a valid search with 40+ offers.
- **Impact:** an agent concludes "zero offers" for a query that has hundreds;
  the result is also cache-poisoned.
- **Recommendation:** in `html_search()`, detect "marker present, zero offers"
  and (a) print a visible `WARNING: 0 offers parsed — possible bot-wall or layout variant`
  line, (b) retry once with `use_cache=False` and/or a perturbed UA, (c) skip
  `cache.fetch()` caching for zero-offer responses (negative-lookup skip).
- **Severity:** MEDIUM — silent wrong data, not a crash.

### BUG-NEW-2 (LOW) — `clear-cache` leaves atomic-write `.tmp` remnants

- **File:** `src/olx4ai/core/cache.py` (`fetch` line 288-291, `cmd_clear_cache` in `cli.py`)
- Cache writes use `path + ".tmp"` then `os.replace`. A killed process leaves
  `*.cache.tmp` (and `index.json.tmp`) behind; `clear-cache` removes only
  `*.cache` and `index.json`, so stale `.tmp` files accumulate and count as
  disk cruft. Same for `index_put` (line 314).
- **Recommendation:** glob `*.cache.tmp` / `*.tmp` in `clear-cache`.

### BUG-NEW-3 (LOW) — Retry path reuses the exact failing request

- **File:** `src/olx4ai/core/cache.py` (`fetch`, lines 264-275)
- On 403/408/429/5xx the tool sleeps (Retry-After, clamped 0-60 s) and retries
  with the **same** `Request` object — same UA, headers, URL. If the 403/429
  is caused by fingerprinting (see BUG-NEW-1), the retry deterministically
  repeats the failure; the clamp is the only guard against pathological
  `Retry-After`.
- **Recommendation:** vary a harmless header (e.g. jittered `Accept` order) on
  retry, or fold retry into the BUG-NEW-1 negative-response path.

### BUG-NEW-4 (LOW) — Single-quoted JS string normalization edge case

- **File:** `src/olx4ai/core/prerendered.py` (line 55-58)
  ```python
  literal = '"' + literal[1:-1].replace('"', '\\"').replace("\\'", "'") + '"'
  ```
- The two replaces interact badly when a single-quoted JS literal contains a
  literal backslash immediately before an apostrophe (`\\'` sequence): the
  second replace consumes one backslash of the pair, corrupting the JSON
  escape. Practically unreachable for OLX's JSON-embedded state (the inner
  payload is double-quote JSON), so impact is theoretical — noted for the
  record since the function claims general JS-string handling.

### INFO-5 — Offset pagination drift (API path)

- **File:** `src/olx4ai/core/html_client.py` N/A — `core/api_client.py` line 36-90
- `search` pages via `offset += len(batch)` on a live-changing catalog: offers
  created between pages shift results and rows can be skipped or repeated.
  `--dedupe` masks the duplicate side; the skip side is invisible. Documented
  behavior, no action required, flagged so it isn't mistaken for a bug later.

### Vulnerability re-verification (no new findings)

Re-ran all prior probes on `918ca9c` live — all still clean:

| Probe | Result |
|---|---|
| `offer http://127.0.0.1:8080/admin` | `refusing non-https URL` exit 1 |
| `url https://3.4.5.6/abc` (raw IP) | `refusing non-OLX host in URL` exit 1 |
| `offer https://169.254.169.254/latest/meta-data/` | `refusing private/internal host in URL` exit 1 |
| `url https://example.com/abc` | `refusing non-OLX host in URL` exit 1 |
| `--domain evil.com` / `amazon.com` / `10.0.0.1` | `refusing non-OLX domain` / `refusing private/internal host in domain` exit 1 |
| `search ""` / `"   "` | `search query cannot be empty` exit 1 |
| `--min -1`, `--max 0` | clean validation errors exit 1 |
| 404 URL | clean `HTTP 404 for <url>` exit 1 |
| `offer <unknown numeric id>` | clean index-miss message exit 1 |
| `--param` without `=` | skipped (documented escape hatch) |

`--param` remains an intentional, documented unvalidated escape hatch
(V-NEW-3). No new SSRF, plaintext-HTTP, or injection paths found.

---

## Regression & Health Checks (all pass)

- `pytest -v`: **158/158 passed** (offline fixtures).
- `scripts/live_smoke_test.sh`: **All live smoke checks completed** (live OLX).
- Live CLI paths exercised: `search`, `stats`, `url` (incl. `?page=2`),
  `offer <id>`, `offer <url>`, `--json --fields`, `--dedupe`, `clear-cache`
  (index.json + `.cache` removed), `agent-help`.
- Exit codes: 0 on success, 1 on all error paths (verified per case above).

---

## Conclusion

`918ca9c` is shippable. One medium live-OLX interaction issue
(BUG-NEW-1: silent empty result on degraded SSR variant + TTL cache poisoning)
should be fixed before the next release; three low hygiene items are queued.
No vulnerabilities remain.
