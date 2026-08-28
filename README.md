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
nothing. The id→URL index is bounded to 5000 entries (configurable via
`OLX4AI_MAX_INDEX_ENTRIES`). `clear-cache` removes both cached HTTP responses
and the id→URL index.

## Raw API Parameters (`--param`)

The `--param` flag (e.g. `--param filter_enum_hdd_type[0]=ssd`) is an escape hatch
for passing unvalidated raw query parameters directly to the OLX API.


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
