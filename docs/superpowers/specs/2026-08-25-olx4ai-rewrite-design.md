# olx4ai: package rewrite with CLI + MCP server

**Status:** approved for planning
**Date:** 2026-08-25

## Context

`olx.py` is a single-file, stdlib-only Python CLI that prunes OLX.pl listings
down to a handful of fields per offer so an AI agent can browse OLX without
burning context on raw HTML/JSON. It works well as a personal script but has
two problems that block making it a general-purpose, open-source agent tool:

1. It's only reachable by shelling out to a script — no protocol-level tool
   description, so any MCP-aware host (Claude Desktop/Code, Cursor, etc.)
   can't discover it as a native tool.
2. Live testing during this session found two real bugs in the HTML-scrape
   data path (`olx.py url`, and the HTML fallback inside `olx.py offer`) —
   see "Bugs found during testing" below. There is currently no automated
   test suite protecting against this class of regression.

## Goals

- Fix both bugs found during testing so every data-fetch path produces
  correct output.
- Split the single file into a small, testable package with one shared core.
- Ship two thin wrappers around that core: a CLI (drop-in equivalent of
  today's `olx.py`) and an MCP server (stdio transport).
- Add a fixture-based test suite that runs offline in CI — no dependency on
  hitting the live site to catch regressions.
- Publish as an open-source GitHub repo named `olx4ai`.
- Parameterize the target domain (default `olx.pl`) so the same code can
  point at another OLX Europe site without a code change — see "Domain
  configuration" below.

## Non-goals (explicitly out of scope for this rewrite)

- Publishing to PyPI (GitHub-only install for now; can be added later
  without breaking anything).
- An HTTP/SSE MCP transport or any hosted/remote deployment. This is a
  local subprocess tool; stdio is sufficient for every MCP host in scope.
- Generalizing beyond the OLX platform to other marketplaces.
- Verifying or officially supporting any specific OLX region other than
  olx.pl — see "Domain configuration" below for what *is* in scope.
- Any new user-facing features beyond what `olx.py` already does today
  (no new filters, no new commands). This is a structural rewrite + bugfix,
  not a feature expansion.

## Domain configuration

OLX is a brand used across many countries, but not all OLX-branded sites
run the same platform. OLX Europe sites (olx.pl, olx.ua, olx.bg, olx.ro,
olx.kz, olx.uz, ...) very likely share this exact tech stack — same
`/api/v1/offers/` JSON API, same `__PRERENDERED_STATE__` mechanism, just a
different domain/currency/locale. OLX-branded sites outside Europe (Brazil,
Pakistan, Nigeria, ...) have historically run on separate infrastructure
after various regional spin-offs, and there's no way to verify their shape
without live-testing each one.

Given that, `cache.py`'s `BASE`/`API` constants become a `--domain` CLI flag
/ `OLX4AI_DOMAIN` env var (default `olx.pl`) instead of hardcoded strings —
a single parameterization, no per-region field-mapping logic. This makes it
possible to point the tool at another OLX Europe domain without a code
change, but the README states plainly that only olx.pl is verified —
other domains are "try it, file a bug if the shape differs," not a
supported claim made by this rewrite.

## Bugs found during testing

Two independent, real bugs were found by exercising every subcommand live
against olx.pl during this session — both live in the HTML/`__PRERENDERED_STATE__`
path, `search`/`stats` (pure JSON API) were unaffected by either.

### Bug 1 — `normalize()` shape mismatch

The two fetch paths return **structurally different** raw offer shapes, and
`normalize()`/`normalize_detail()` were written only against the JSON API
shape:

| field | JSON API shape (`api_search`) | HTML/`__PRERENDERED_STATE__` shape (`html_search`) |
|---|---|---|
| city/district/region | `location.city.name` (nested dict) | `location.cityName` (flat string) |
| price | inside `params[]`: `{"key":"price","value":{"value":1499,...}}` | top-level `price.regularPrice.value` |
| condition | `params[].value` is a dict `{"key":"used","label":"Używane"}` | `params[].value` is a plain string `"Używane"`, with a sibling `normalizedValue:"used"` |
| timestamps | `created_time` / `last_refresh_time` (snake_case) | `createdTime` / `lastRefreshTime` (camelCase) |

Consequence: every offer reached via `olx.py url` or the HTML fallback in
`olx.py offer <id>` silently loses price, city, district, and age, and
prints an untranslated Polish condition string. Confirmed live against
`https://www.olx.pl/oferty/q-asus-vivobook-14/...` during this session.

**Fix:** introduce one small adapter per source that maps its raw field
names into a single common intermediate shape; `normalize()`/
`normalize_detail()` then operate on that one shape only, with no
shape-sniffing:

- `adapt_api_offer(raw) -> CommonOffer`
- `adapt_html_offer(raw) -> CommonOffer`

Rejected alternatives:
- **Fallback chains inside one `normalize()`** (try shape A's keys, then
  shape B's) — turns the shared function into an unreadable pile of
  "try/except field lookups" and gets worse with every future shape.
- **Re-fetch every HTML-discovered offer through the JSON API** — defeats
  the purpose of `olx.py url` (reusing OLX's own filter/facet state from a
  URL) and multiplies HTTP calls per listing page (an N+1 pattern for
  something that's supposed to be cheap).

### Bug 2 — `find_offers()` can't locate a single offer

`cmd_offer`'s HTML fallback (used whenever a URL is passed directly to
`olx.py offer`, or whenever the numeric-id JSON detail lookup fails and
falls back to a cached URL) calls `find_offers(state)` and takes
`cands[0]`. But on a real offer-*detail* page, the offer sits as a **bare
dict** at `state["ad"]["ad"]` — not inside any list. `find_offers()`'s
existing heuristic only ever inspects `list` nodes for offer-shaped dicts,
so it structurally cannot find a lone dict, no matter how it's nested.
Confirmed live: `find_offers()` returns `0` candidates on every real detail
page tested, so `olx.py offer <url>` currently fails outright with
`could not locate the offer object in the page state` — not degraded
output like Bug 1, a hard failure of the entire code path.

**Fix:** extend `find_offers()` so that, while recursing into a `dict`
node, it also treats that dict itself as a one-item candidate list when it
matches the existing offer-shape heuristic (`"id"` + `"title"` +
one of `"url"`/`"params"`/`"price"`) and no better (list-based) candidate
has been found yet:

```python
elif isinstance(node, dict):
    if not best and _looks_like_offer(node):
        best = [node]
    for v in node.values():
        best = find_offers(v, best)
```

Recursion continues afterward exactly as before, so a real list found
deeper in the tree (`len(dicts) > len(best)`) still overrides this
single-dict fallback — verified against both a real listing page (still
returns all 46 offers, unchanged) and a real detail page (now returns the
1 correct offer, id matching the page) during this session.

## Architecture

```
OLX4AI/
├── src/olx4ai/
│   ├── core/
│   │   ├── cache.py        # fetch() + disk cache + id→url index
│   │   ├── prerendered.py  # extract __PRERENDERED_STATE__, find_offers() (Bug 2 fix)
│   │   ├── api_client.py   # JSON API search (api_search)
│   │   ├── html_client.py  # HTML scrape search (html_search)
│   │   ├── adapters.py     # NEW — adapt_api_offer() / adapt_html_offer() (Bug 1 fix)
│   │   ├── normalize.py    # normalize()/normalize_detail(), common-shape only
│   │   ├── filters.py      # post_filter (--exclude/--must/--dedupe/--no-promoted)
│   │   └── format.py       # fmt_line, compute_stats, print_stats, emit
│   ├── cli.py               # argparse wrapper — thin, delegates to core
│   └── mcp_server.py        # MCP server — thin, delegates to core
├── tests/
│   ├── fixtures/            # sanitized real API + HTML responses
│   └── test_*.py
├── pyproject.toml
├── README.md
├── LICENSE (MIT)
└── .github/workflows/ci.yml
```

This is a relocation of existing logic (one module per current concern) plus
one new module (`adapters.py`) that fixes Bug 1, and a small addition inside
`prerendered.py`'s existing `find_offers()` that fixes Bug 2. No other
behavior changes are intended in `cache.py`, `api_client.py`,
`html_client.py`, or `filters.py` beyond the rename below. `format.py` gains
one refactor: `print_stats()`'s histogram/quantile math is extracted into a
`compute_stats(rows) -> dict` helper so the MCP server's `stats` tool can
return the same numbers as structured data instead of printed text (see
"MCP server" below) — `print_stats()` itself keeps printing byte-identical
output by calling `compute_stats()` internally.

### Data flow

```
api_client.api_search()  ─┐
                           ├─> adapters.adapt_*_offer() ─> normalize.normalize()/normalize_detail() ─> filters.post_filter() ─> format.emit()/print_stats()
html_client.html_search() ┘
```

### Caching rename

`~/.cache/olx-agent/` → `~/.cache/olx4ai/`. Env var overrides rename to
match: `OLX_CACHE_DIR` → `OLX4AI_CACHE_DIR`, `OLX_CACHE_TTL` →
`OLX4AI_CACHE_TTL`. No migration path for old cache dirs — it's just a
disk cache, it'll repopulate on first run.

## CLI (`olx4ai` command)

Same six subcommands as today (`search`, `stats`, `url`, `offer`,
`agent-help`, `clear-cache`), same flags, same output format, plus one new
top-level `--domain` flag (see "Domain configuration") read before
dispatching to any subcommand. `cli.py` contains only argparse wiring and
the `CHEAT` usage text; all business logic lives in `core/`. Zero
third-party dependencies — stays stdlib-only.

## MCP server (`olx4ai-mcp` command)

Built on the official `mcp` Python SDK (`pip install "mcp[cli]"`,
`mcp.server.MCPServer`), **stdio transport only** (`mcp.run()` defaults to
it). Exposes one tool per CLI subcommand that has agent-facing value:
`search`, `stats`, `search_url`, `offer`, `clear_cache` — each a plain
type-hinted function decorated with `@mcp.tool()`, calling straight into
`core/` and returning the same pruned fields as structured output (list/dict
return types are auto-published as the tool's output schema — no manual
JSON Schema or Pydantic models needed). `stats` returns `compute_stats()`'s
dict directly instead of printed text. No `agent-help` equivalent is needed
here — MCP tools self-describe via schema, which is the point. Tests use the
SDK's documented in-memory pattern — `mcp.client.Client(server)` calling
`call_tool()` directly against the `MCPServer` instance, no subprocess or
real stdio involved.

## Packaging

`pyproject.toml`, src-layout. Base install (`pip install
"olx4ai @ git+https://github.com/Btema2/OLX4AI"`) pulls zero dependencies and
installs the `olx4ai` console script. The MCP server is an optional extra:
`pip install "olx4ai[mcp] @ git+https://github.com/Btema2/OLX4AI"` (or `uvx
--from git+https://github.com/Btema2/OLX4AI olx4ai-mcp`) adds the `mcp` SDK
and the `olx4ai-mcp` console script. GitHub-only distribution for now; PyPI
can be added later without breaking either install path.

## Testing

pytest, entirely fixture-based — no live network calls in the suite itself.

- `tests/fixtures/` holds sanitized real responses captured during this
  session: one JSON-API search response, one HTML listing page, one HTML
  offer-detail page. **Sanitization step before committing:** replace
  seller name(s) and any other identifying text with placeholders; numeric
  fields (price, ids structurally, photo counts) and structural shape are
  preserved as-is since those are what the tests actually verify.
- `test_adapters.py` — the highest-value test given what just broke: feeds
  each fixture through `adapt_api_offer`/`adapt_html_offer` and asserts
  price/city/district/age/condition all come out correctly for both
  sources.
- `test_normalize.py`, `test_prerendered.py`, `test_filters.py` — unit
  tests for the surrounding pipeline stages.
- `test_cli.py` — argparse wiring smoke tests (invokes `cli.main()` against
  fixture-backed core functions via monkeypatched `fetch()`).
- `test_mcp_server.py` — tool registration and one call-through test per
  tool, same fixture-backed approach.
- One separate, opt-in live smoke-test script (not part of the pytest
  suite/CI) for manually verifying against the real site, replacing the
  manual verification steps currently documented in `CLAUDE.md`.

CI: GitHub Actions workflow running `pytest` on push/PR.

## Migration

The root `olx.py` script is retired entirely in favor of the package — this
project isn't published yet, so there's no back-compat surface to preserve.
`CLAUDE.md` gets updated to describe the new layout, install/run commands,
and test commands (replacing the current "no test suite, hit the live site"
guidance).
