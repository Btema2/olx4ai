# Bug and Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all security vulnerabilities (V-NEW-1, V-NEW-2, V-NEW-3, V-NEW-4) and bug-class/documentation findings (B-NEW-5, B-NEW-6, B-NEW-7) from `olx4ai-bug-report (2).md`.

**Architecture:** Strengthen domain configuration and SSRF URL allowlisting in `cache.py`, disable redirect following in curl `_open()`, add build paths to `.gitignore`, validate `--title-chars >= 0` in `cli.py`, and document whole-word AND/NOT filter semantics and title+price deduplication in `CHEAT` / docstrings.

**Tech Stack:** Python 3.10+, pytest, urllib, curl

## Global Constraints

- Zero external dependencies for core CLI (standard library + curl).
- All tests must pass with `PYTHONPATH=src pytest`.
- Keep error message styles consistent (`SystemExit("<clear message>")`).
- Do not introduce breaking API changes for existing valid OLX domains (`olx.pl`, `olx.ro`, `olx.ua`, etc.).

---

### Task 1: V-NEW-1 & V-NEW-2 — Domain & URL Host SSRF Allowlist Hardening

**Files:**
- Modify: `src/olx4ai/core/cache.py:20-43`, `src/olx4ai/core/cache.py:143-189`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `cache.configure(domain: str | None)`, `cache._validate_url(url: str)`
- Produces: Hardened `configure()` rejecting non-OLX domains/IPs, and `_validate_url()` strictly validating against legitimate OLX hosts/subdomains.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cache.py`:
```python
def test_configure_rejects_non_olx_domain():
    with pytest.raises(SystemExit, match="refusing non-OLX domain"):
        cache.configure("evil.com")
    with pytest.raises(SystemExit, match="refusing private/internal host in domain"):
        cache.configure("169.254.169.254")
    with pytest.raises(SystemExit, match="refusing private/internal host in domain"):
        cache.configure("localhost")


def test_fetch_rejects_subdomain_and_tld_spoofs():
    with pytest.raises(SystemExit, match="refusing non-OLX host in URL"):
        cache.fetch("https://attacker.olx.pl/payload", json_mode=False)
    with pytest.raises(SystemExit, match="refusing non-OLX host in URL"):
        cache.fetch("https://foo.olx.attacker.com/bar", json_mode=False)
    with pytest.raises(SystemExit, match="refusing non-OLX host in URL"):
        cache.fetch("https://evil.olx.xyz/bar", json_mode=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_cache.py::test_configure_rejects_non_olx_domain tests/test_cache.py::test_fetch_rejects_subdomain_and_tld_spoofs -v`
Expected: FAIL (currently accepted)

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/core/cache.py`:
Implement strict host validation helper:
1. Reject IPs (loopback, private, link-local, reserved, multicast, unspecified) with `"refusing private/internal host in ..."` or `"refusing non-OLX domain..."`.
2. Reject localhost / internal domains (`.local`, `.internal`, `.localhost`).
3. Validate domain/host structure:
   - Must match valid OLX pattern: e.g. exact match for configured `DOMAIN` (and `www.{DOMAIN}`, `m.{DOMAIN}`, `api.{DOMAIN}`) or general OLX pattern `^([a-z0-9-]+\.)*olx\.[a-z]{2,3}(\.[a-z]{2})?$`.
   - Ensure subdomains are restricted (e.g. `attacker.olx.pl` vs allowed OLX hostnames: `olx.pl`, `www.olx.pl`, `m.olx.pl`, `api.olx.pl`, `static.olx.pl`, `pomoc.olx.pl`, `jobs.olx.pl` or `*.{DOMAIN}`).
4. Update `configure(domain)` to validate the provided domain before setting `DOMAIN`, `BASE`, `API`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_cache.py -v`
Expected: PASS (all tests pass)

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/cache.py tests/test_cache.py
git commit -m "fix(security): resolve V-NEW-1 and V-NEW-2 with strict OLX domain and URL validation"
```

---

### Task 2: V-NEW-3 — Drop `-L` (redirect following) in `_open()`

**Files:**
- Modify: `src/olx4ai/core/cache.py:50-65`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `_open(req)`
- Produces: Non-redirect-following curl invocation.

- [ ] **Step 1: Write/update the failing test**

In `tests/test_cache.py`, update `test_open_success_curl_command_and_headers` to verify `"-L"` is NOT in `cmd_called`, and `cmd_called[:4] == ["curl", "--http2", "-s", "-S"]`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_cache.py::test_open_success_curl_command_and_headers -v`
Expected: FAIL (due to `-L` still present)

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/core/cache.py` `_open()`:
Change `cmd = ["curl", "--http2", "-s", "-S", "-L", "--max-time", "25", "-D", header_path]`
to `cmd = ["curl", "--http2", "-s", "-S", "--max-time", "25", "-D", header_path]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/core/cache.py tests/test_cache.py
git commit -m "fix(security): resolve V-NEW-3 by dropping curl -L redirect following"
```

---

### Task 3: V-NEW-4 — Build Artifact Hygiene and `.gitignore`

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Produces: Git ignore rules for `build/` and `dist/`.

- [ ] **Step 1: Check .gitignore and add build/ and dist/**

Add `build/` and `dist/` to `.gitignore`.

- [ ] **Step 2: Verify git status is clean**

Run: `git status`

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: resolve V-NEW-4 by adding build/ and dist/ to .gitignore"
```

---

### Task 4: B-NEW-5 — Negative `--title-chars` Validation

**Files:**
- Modify: `src/olx4ai/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: CLI options `--title-chars`
- Produces: Validation rejecting negative values with `SystemExit("title chars cannot be negative")`.

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py`:
```python
def test_search_rejects_negative_title_chars():
    sys.argv = ["olx4ai", "search", "laptop", "--title-chars", "-5"]
    with pytest.raises(SystemExit, match="title chars cannot be negative"):
        main()


def test_url_rejects_negative_title_chars():
    sys.argv = ["olx4ai", "url", "https://www.olx.pl/oferty/q-test/", "--title-chars", "-1"]
    with pytest.raises(SystemExit, match="title chars cannot be negative"):
        main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_cli.py::test_search_rejects_negative_title_chars tests/test_cli.py::test_url_rejects_negative_title_chars -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `src/olx4ai/cli.py`:
In `cmd_search` and `cmd_url`:
```python
if getattr(args, "title_chars", None) is not None and args.title_chars < 0:
    raise SystemExit("title chars cannot be negative")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/cli.py tests/test_cli.py
git commit -m "fix(cli): resolve B-NEW-5 by validating that --title-chars is non-negative"
```

---

### Task 5: B-NEW-6 & B-NEW-7 — Document Filter Semantics and Deduplication Key

**Files:**
- Modify: `src/olx4ai/cli.py`, `src/olx4ai/core/filters.py`, `src/olx4ai/mcp_server.py`
- Test: `tests/test_cli.py`, `tests/test_filters.py`

**Interfaces:**
- Produces: Clear documentation of `--must` (all words match case-insensitive whole-word `\bword\b`), `--exclude` (no word matches case-insensitive whole-word `\bword\b`), and `--dedupe` (keyed on `(title.lower(), price)` only).

- [ ] **Step 1: Write test verifying CHEAT and docstrings contain the documented semantics**

In `tests/test_cli.py` and `tests/test_filters.py`, assert that `CHEAT` and `post_filter.__doc__` clearly describe the whole-word AND/NOT matching and title+price deduplication behavior.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_filters.py -v`
Expected: FAIL

- [ ] **Step 3: Update CHEAT, filters.py docstrings, mcp_server.py docstrings**

In `src/olx4ai/cli.py`:
Update `CHEAT` constant to include explanations for `--exclude`, `--must`, and `--dedupe`.
In `src/olx4ai/core/filters.py`:
Update module and function docstring for `post_filter`.
In `src/olx4ai/mcp_server.py`:
Update tool parameter docstrings for `exclude`, `must`, `dedupe`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest -v`
Expected: PASS (all tests in test suite pass)

- [ ] **Step 5: Commit**

```bash
git add src/olx4ai/cli.py src/olx4ai/core/filters.py src/olx4ai/mcp_server.py tests/test_cli.py tests/test_filters.py
git commit -m "docs: resolve B-NEW-6 and B-NEW-7 by documenting filter semantics and deduplication key"
```
