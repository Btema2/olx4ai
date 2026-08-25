# Fix v1 Field-Report Issues Plan

Spec: none formally written. Scope was determined in a review-triage conversation
with the user: a field report on the v1 script (`olx.py`) surfaced 8 issues; after
the `olx4ai` rewrite, 2 were verified fixed (HTML/API shape-mismatch adapters,
`offer <id>` no longer requiring the tool's own cache index) and 5 remained open.
The user was asked which of the 5 were worth fixing and approved 4: substring
trap in title filters (#7), `--desc-chars` default (#8), no retry/backoff on
rate-limiting (#6), and missing city-slug URL docs (#4). Two were explicitly
deferred as not worth it: city-id lookup (#3 — no reliable source of truth,
upstream locations API deprecated) and `url`-mode pagination (#5 — real gap but
non-trivial and would just hit the rate limit sooner).

## Context

`olx4ai` is a Python CLI/MCP tool (`src/olx4ai/`) for searching OLX.pl listings
without burning context on raw HTML/JSON. See `/home/btema2/smart-things/code/olxNav/CLAUDE.md`
for full architecture. `core/` holds all business logic; `cli.py` and
`mcp_server.py` are thin wrappers only.

## Global Constraints

- Core logic lives in `core/`; `cli.py`/`mcp_server.py` stay thin wrappers — no
  new logic added to them beyond what each task specifies.
- Tests run offline via `pytest -v` against fixtures in `tests/fixtures/` — no
  network calls, ever, in tests. Any new test involving HTTP must mock/monkeypatch
  the network call.
- Style must pass clean: `black --check .`, `isort --check-only .`,
  `ruff check .`.
- Type annotations required on all `src/` function signatures (not required in
  `tests/`).
- No behavior changes beyond what each task specifies — do not refactor
  unrelated code, do not touch files outside each task's stated scope.
- Commit each task's changes with a conventional-commit message.
- Never commit directly to `main` — this plan runs inside an isolated worktree.

## Task 1: Word-boundary matching in --exclude/--must filters

`core/filters.py`'s `post_filter()` currently uses substring containment
(`b in r["title"].lower()`) to implement `--exclude`/`--must` title filtering
(around lines 10 and 13). This causes false positives: `--exclude "pro"`
wrongly matches a title containing "PROMENADA" because `"pro"` is a substring
of `"promenada"`, even though the user meant to exclude the whole word "Pro".

**Fix:** change both the `--exclude` and `--must` matching in `post_filter()`
(`core/filters.py`) to match whole words only, using regex word-boundary
matching (e.g. `re.search(r"\b" + re.escape(term) + r"\b", title, re.IGNORECASE)`)
instead of plain substring `in` containment. Keep the existing case-insensitive
behavior. Keep the comma-splitting/stripping behavior for both `--exclude` and
`--must` exactly as it is today.

**Tests (`tests/test_filters.py`):**
- Keep all existing tests passing as-is (they already use whole-word-safe
  examples — `"case"` against "Nice Case for iPhone", `"iphone"` against
  "iPhone 13 Pro" — both are real whole-word matches, so no assertion changes
  should be needed, only re-verify after the fix).
- Add a regression test proving the fix: a title containing "PROMENADA" (e.g.
  "iPhone 17 sklep PROMENADA") must NOT be dropped by `--exclude="pro"`, and
  must NOT be kept by `--must="pro"` (i.e. `--must="pro"` filters it OUT,
  since "pro" is not a whole word in that title).
- Add a test proving a real whole-word match still works: a title containing
  the standalone word "Pro" (e.g. "iPhone 17 Pro") must still be excluded when
  `--exclude="pro"` is set, and must still be kept by `--must="pro"`.

**Verify:** `pytest tests/test_filters.py -v` all pass;
`ruff check core/filters.py tests/test_filters.py`;
`black --check core/filters.py tests/test_filters.py`;
`isort --check-only core/filters.py tests/test_filters.py`.

**Commit:** `fix: match whole words, not substrings, in --exclude/--must filters`

## Task 2: Retry with backoff on cache.fetch() HTTP errors

`core/cache.py`'s `fetch()` (around lines 40-81) makes a single HTTP request
and immediately raises `SystemExit` on any `urllib.error.HTTPError`, with no
retry. OLX's CloudFront occasionally rate-limits with transient 403s, and a
single retry with a short backoff often succeeds where the first attempt
didn't.

**Fix:** in `fetch()`, on `urllib.error.HTTPError`, retry the request exactly
once before giving up:
- If the `HTTPError` response has a `Retry-After` header, sleep for that many
  seconds (parse as int; if unparseable, fall back to a fixed default of 2
  seconds).
- If there's no `Retry-After` header, sleep for a fixed short backoff (2
  seconds).
- Make exactly one retry attempt — not a loop, not configurable, not an
  elaborate retry framework. One extra attempt, then raise `SystemExit` as
  before (same error-message format as today) if the retry also fails.
- The retry must reuse the same request (same URL, same headers) as the first
  attempt.
- Do not change behavior for the existing generic `except Exception` branch —
  no retry there, only for `HTTPError`.
- Do not change caching behavior — the retry only applies to the network
  fetch on a cache miss; the existing cache-hit short-circuit at the top of
  `fetch()` is untouched.

**Tests (`tests/test_cache.py`):**
- Add a test that mocks/monkeypatches `urllib.request.urlopen` to raise an
  `HTTPError` on the first call and return a successful response on the
  second call, asserting `fetch()` returns the successful result (proves the
  retry happened).
- Add a test where `urllib.request.urlopen` raises `HTTPError` on both calls,
  asserting `fetch()` still raises `SystemExit` (proves it gives up after
  exactly one retry, not an infinite loop).
- Monkeypatch `time.sleep` to a no-op in both new tests so the suite doesn't
  actually sleep.

**Verify:** `pytest tests/test_cache.py -v` all pass; `pytest -v` (full suite)
all pass; `ruff check core/cache.py tests/test_cache.py`;
`black --check core/cache.py tests/test_cache.py`;
`isort --check-only core/cache.py tests/test_cache.py`.

**Commit:** `fix: retry once with backoff on transient HTTP errors in cache.fetch()`

## Task 3: CLI defaults and docs — desc-chars default, city-slug URL pattern

Two small, independent, single-file (`cli.py`) fixes — a default-value bump
and a docs addition:

**3a.** `cli.py`'s `offer` subcommand defines `--desc-chars` with
`default=1200` (around line 175). This truncates useful info out of offer
descriptions (e.g. battery-condition percentages sit past 1200 chars in
longer listings). Change the default to `4000`. Leave `type=int` and
`help="0 = full description"` as-is — only change the numeric default value.

**3b.** The `CHEAT` constant in `cli.py` (the multi-line string printed by
`agent-help`, near the top of the file) does not document the correct OLX
city-scoped listing URL pattern for use with the `url` subcommand. Add one
short line/example to `CHEAT` showing the correct pattern is
`https://www.olx.pl/<city-slug>/q-<query>/` (e.g.
`https://www.olx.pl/warszawa/q-iphone-17-pro/`), and note that
`/oferty/w-<city-slug>/q-<query>/` is NOT valid (it 404s). Keep the addition
concise (1-2 lines), consistent with `CHEAT`'s existing terse, example-driven
style, placed near the existing `olx4ai url` example line so it reads
naturally as a tip about that command.

**Tests:** check `tests/test_cli.py` for any test asserting the exact default
value of `desc_chars` or the exact contents of `CHEAT`; update any such
assertion to match the new values. No new tests are required for either 3a or
3b beyond keeping existing assertions accurate.

**Verify:** `pytest tests/test_cli.py -v` all pass; `pytest -v` (full suite)
all pass; `ruff check cli.py`; `black --check cli.py`;
`isort --check-only cli.py`.

**Commit:** `fix: raise default --desc-chars to 4000; docs: document city-slug URL pattern in agent-help`
