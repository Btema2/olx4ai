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
