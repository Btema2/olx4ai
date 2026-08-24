# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`olx.py` is a single-file, stdlib-only Python CLI that lets an AI agent search and read OLX.pl
(a Polish classifieds site) without burning context on raw HTML/JSON. A normal OLX page or API
response is ~30k tokens of markup/JSON; this tool prunes each offer down to ~8 fields and prints
one line per offer (~20 tokens), so 40 offers costs ~900 tokens instead of ~30k.

The project is currently a single script (`olx.py`) with no package structure, build step, or
test suite. It is not (yet) a git repository. Treat any polishing work as editing this one file
unless the user asks to split it up.

## Running it

No dependencies to install — stdlib only, no venv/pip needed.

```bash
./olx.py agent-help                                   # print the usage cheat sheet
./olx.py stats  "asus vivobook 14"                     # price distribution (~15 lines)
./olx.py search "asus vivobook 14" --max 40
./olx.py search "asus vivobook 14" --min 800 --max-price 2000 --used --sort price-asc
./olx.py search "iphone 13" --exclude "obudowa,etui,czesci" --dedupe --no-promoted
./olx.py url    "https://www.olx.pl/oferty/q-asus-vivobook-14/?search%5Bfilter_float_price%3Ato%5D=1500"
./olx.py offer  1023456789                             # full details for one offer id
./olx.py offer  1023456789 --json
./olx.py search "iphone 13" --json --fields id,title,price
./olx.py clear-cache
```

`agent-help` prints the `CHEAT` constant near the top of the file — keep that string and the
module docstring in sync whenever CLI behavior changes, since that's the contract an agent
actually reads at runtime.

## Verifying changes

There is no test suite, linter, or formatter configured in this repo. To sanity-check a change:

```bash
python3 -m py_compile olx.py     # syntax check
./olx.py agent-help               # quick smoke test, no network call
./olx.py stats "test query" --max 10   # exercises the live API path
```

Since there's no mock/fixture layer, exercising `search`/`stats`/`url`/`offer` against the real
site is the only way to validate the fetch → extract → normalize → filter → emit pipeline
end-to-end. Be mindful this hits olx.pl for real (though responses are cached 10 min).

## Architecture

The script has two independent data-fetching paths that both feed the same normalization layer:

1. **JSON API path** (`api_search` → `search`/`stats` commands) — paginates
   `https://www.olx.pl/api/v1/offers/`, building query params from CLI flags (price range,
   category, city/region id, condition, sort, arbitrary `--param key=value` passthrough).
2. **HTML scrape path** (`html_search`/`cmd_offer` → `url`/`offer` commands) — fetches a normal
   OLX page and pulls `window.__PRERENDERED_STATE__` out of the raw HTML (`extract_prerendered`),
   handling both JS-string-encoded and raw-object-encoded variants. This is the only way to reuse
   a listing URL that already has OLX's own filters/facets applied, and it's the fallback used by
   `offer <id>` when the JSON detail endpoint doesn't have the id cached.

Both paths converge on a common pipeline:

```
fetch (disk-cached) → normalize()/normalize_detail() → post_filter() → emit()/print_stats()
```

- **`find_offers(node)`** — structure-agnostic recursive search over the parsed
  `__PRERENDERED_STATE__` tree that finds "the longest list of dicts that look like offers"
  (has `id`+`title`+one of `url`/`params`/`price`). This exists because OLX's prerendered state
  shape isn't documented/stable across page types — don't replace it with a fixed key path.
- **`normalize()`** — offer dict → the ~14 fields the CLI actually prints (price, condition,
  location, age, delivery/business/promoted flags, etc). `normalize_detail()` extends this with
  parsed spec params, cleaned description text, seller info, and photo count, used only by
  `offer`.
- **`post_filter()`** — client-side filtering that the OLX API doesn't support natively:
  `--exclude`/`--must` (title keyword filters), `--no-promoted`, `--dedupe` (by title+price).
- **`emit()`** — either compact human-readable lines (`fmt_line`) or `--json` with an optional
  `--fields` whitelist. Also calls `index_put()` to persist an id→url map, since default output
  *omits* URLs (they're long); `offer <id>` and `--urls` are the only ways to recover them.

### Caching

- `fetch()` caches raw HTTP response bodies on disk at `~/.cache/olx-agent/<sha1(url)>.cache`
  (override dir via `OLX_CACHE_DIR`, TTL via `OLX_CACHE_TTL`, default 600s). Cache key is the
  full request URL, so different filter/pagination params never collide.
  `clear-cache` only removes these files.
- Separately, `index_put`/`index_get` maintain `~/.cache/olx-agent/index.json`, an id→url map with
  no TTL — this is what lets `offer <id>` work after a `search` without ever printing URLs.
  `clear-cache` does **not** clear this file.

### Adding a new CLI command

Follow the existing subparser pattern in `build_parser()`: add a `sub.add_parser(...)`, reuse
`common(sp)` for the shared search/filter/output flags where applicable, and set `func=cmd_x`
pointing at a new `cmd_x(args)` function alongside the existing `cmd_search`/`cmd_stats`/etc.
