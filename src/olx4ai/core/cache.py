"""HTTP fetch with on-disk caching, plus the id->url index."""

from __future__ import annotations

import email
import gzip
import hashlib
import io
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

OLX_DOMAIN_RE = re.compile(r"^olx\.[a-z]{2,3}(\.[a-z]{2})?$")
ALLOWED_SUBDOMAINS = {"", "www", "m", "api", "static", "pomoc", "jobs"}


def _is_valid_olx_domain(domain: str) -> bool:
    """Check if domain is a valid OLX root/base domain (e.g. olx.pl, olx.ua, olx.ro, olx.com.br)."""
    return bool(OLX_DOMAIN_RE.match(domain))


def _validate_domain(domain: str) -> None:
    """Validate target OLX domain, rejecting IPs, internal hosts, and non-OLX domains."""
    clean_host = domain.strip("[]")
    try:
        ip = ipaddress.ip_address(clean_host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SystemExit(f"refusing private/internal host in domain: {domain}")
        raise SystemExit(f"refusing non-OLX domain: {domain}")
    except ValueError:
        pass

    if domain in ("localhost", "localhost.localdomain") or domain.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise SystemExit(f"refusing private/internal host in domain: {domain}")

    if not _is_valid_olx_domain(domain):
        raise SystemExit(f"refusing non-OLX domain: {domain}")


def _is_valid_olx_host(hostname: str) -> bool:
    """Check if hostname belongs to an allowed OLX host/subdomain."""
    parts = hostname.split(".")
    for i in range(len(parts)):
        subdomain = ".".join(parts[:i])
        base_domain = ".".join(parts[i:])
        if _is_valid_olx_domain(base_domain) and subdomain in ALLOWED_SUBDOMAINS:
            return True
    return False


DOMAIN = os.environ.get("OLX4AI_DOMAIN", "olx.pl")
_validate_domain(DOMAIN)
BASE = f"https://www.{DOMAIN}"
API = f"{BASE}/api/v1/offers/"
CACHE_DIR = os.path.expanduser(os.environ.get("OLX4AI_CACHE_DIR", "~/.cache/olx4ai"))
CACHE_TTL = int(os.environ.get("OLX4AI_CACHE_TTL", "600"))
MAX_INDEX_ENTRIES = int(os.environ.get("OLX4AI_MAX_INDEX_ENTRIES", "5000"))
RETRY_DELAY = 2  # seconds; used when Retry-After is absent or unparseable

MAX_RETRY_DELAY = 60  # seconds; clamp for Retry-After-derived delays
RETRYABLE_HTTP_CODES = {403, 408, 429}  # plus any 5xx; see _is_retryable()
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def configure(domain: str | None = None) -> None:
    """Override the target OLX domain at runtime (e.g. from --domain)."""
    global DOMAIN, BASE, API
    if domain:
        clean = domain.strip().lower()
        _validate_domain(clean)
        DOMAIN = clean
        BASE = f"https://www.{DOMAIN}"
        API = f"{BASE}/api/v1/offers/"


def _cache_path(key: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".cache")


def _open(req: urllib.request.Request | str) -> tuple[bytes, str]:
    """Issue one HTTP request via curl with HTTP/2 support and return (raw body, content-encoding)."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    header_items = req.header_items() if isinstance(req, urllib.request.Request) else []

    with tempfile.NamedTemporaryFile(delete=False) as hf:
        header_path = hf.name
    try:
        cmd = ["curl", "--http2", "-s", "-S", "--max-time", "25", "-D", header_path]
        for k, v in header_items:
            cmd.extend(["-H", f"{k}: {v}"])
        cmd.append(url)

        res = subprocess.run(cmd, capture_output=True)

        headers_raw = ""
        if os.path.exists(header_path):
            with open(header_path, "rb") as f:
                headers_raw = f.read().decode("utf-8", "replace")

        blocks = [b.strip() for b in headers_raw.replace("\r\n", "\n").split("\n\n") if b.strip()]
        if not blocks:
            err_msg = (
                res.stderr.decode("utf-8", "replace").strip() or f"curl error {res.returncode}"
            )
            raise urllib.error.URLError(err_msg)

        last_block = blocks[-1]
        lines = last_block.split("\n")
        status_line = lines[0]
        status_parts = status_line.split()
        if len(status_parts) < 2 or not status_parts[1].isdigit():
            raise urllib.error.URLError(f"malformed HTTP status line: {status_line}")

        status_code = int(status_parts[1])
        hdrs = email.message_from_string("\n".join(lines[1:]))
        raw = res.stdout
        enc = (hdrs.get("Content-Encoding") or "").lower()

        if status_code >= 400:
            err_body = raw
            if enc == "gzip":
                try:
                    err_body = gzip.decompress(raw)
                except Exception:
                    pass
            elif enc == "deflate":
                try:
                    err_body = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    pass
            raise urllib.error.HTTPError(
                url=url,
                code=status_code,
                msg=f"HTTP {status_code}",
                hdrs=hdrs,
                fp=io.BytesIO(err_body),
            )

        if res.returncode != 0:
            err_msg = (
                res.stderr.decode("utf-8", "replace").strip() or f"curl error {res.returncode}"
            )
            raise urllib.error.URLError(err_msg)

        return raw, enc
    finally:
        if os.path.exists(header_path):
            try:
                os.remove(header_path)
            except OSError:
                pass


def _is_retryable(status_code: int) -> bool:
    """Whether an HTTPError status is transient and worth one retry."""
    return status_code in RETRYABLE_HTTP_CODES or status_code >= 500


def _retry_delay(error: urllib.error.HTTPError) -> int:
    """Seconds to wait before retrying, from Retry-After if present/valid,
    clamped to [0, MAX_RETRY_DELAY] to guard against negative or huge values."""
    retry_after = error.headers.get("Retry-After") if error.headers is not None else None
    if retry_after is None:
        delay = RETRY_DELAY
    else:
        try:
            delay = int(retry_after)
        except ValueError:
            delay = RETRY_DELAY
    return min(max(delay, 0), MAX_RETRY_DELAY)


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise SystemExit(f"refusing non-https URL: {url}")
    hostname = parsed.hostname
    if not hostname:
        raise SystemExit(f"refusing URL without host: {url}")
    hostname = hostname.lower()

    # Block IP addresses (including loopback, private, link-local metadata, etc.)
    clean_host = hostname.strip("[]")
    try:
        ip = ipaddress.ip_address(clean_host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SystemExit(f"refusing private/internal host in URL: {url}")
        raise SystemExit(f"refusing non-OLX host in URL: {url}")
    except ValueError:
        pass

    # Block localhost and local/internal domains
    if hostname in ("localhost", "localhost.localdomain") or hostname.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise SystemExit(f"refusing private/internal host in URL: {url}")

    # Check allowlist for OLX domains
    if not _is_valid_olx_host(hostname):
        raise SystemExit(f"refusing non-OLX host in URL: {url}")


def _format_http_error(e: urllib.error.HTTPError, url: str) -> str:
    if e.code == 404:
        return f"HTTP 404 for {url}"
    body = e.read()[:400].decode("utf-8", "replace").strip()
    if body:
        return f"HTTP {e.code} for {url}\n{body}"
    return f"HTTP {e.code} for {url}"


def _build_request(url: str, *, json_mode: bool, retry: bool = False) -> urllib.request.Request:
    if not retry:
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
    else:
        headers = {
            "User-Agent": UA,
            "Accept": (
                "application/json, text/plain, */*;q=0.8"
                if json_mode
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Accept-Encoding": "gzip, deflate",
            "Referer": BASE + "/",
        }
    return urllib.request.Request(url, headers=headers)


def fetch(url: str, *, json_mode: bool, use_cache: bool = True, ttl: int = CACHE_TTL) -> str:
    _validate_url(url)

    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

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
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"network error for {url}: {e}")

    try:
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"decompression error for {url}: {e}")
    text = raw.decode("utf-8", "replace")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return text


def index_put(rows: list[dict]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, "index.json")
    try:
        with open(p, encoding="utf-8") as fh:
            idx = json.load(fh)
        if not isinstance(idx, dict):
            idx = {}
    except Exception:  # noqa: BLE001
        idx = {}
    for r in rows:
        if r.get("id") and r.get("url"):
            key = str(r["id"])
            idx.pop(key, None)
            idx[key] = r["url"]
    if len(idx) > MAX_INDEX_ENTRIES:
        excess = len(idx) - MAX_INDEX_ENTRIES
        for k in list(idx.keys())[:excess]:
            del idx[k]
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh)
    os.replace(tmp, p)


def index_get(offer_id: str) -> str | None:
    try:
        with open(os.path.join(CACHE_DIR, "index.json"), encoding="utf-8") as fh:
            idx = json.load(fh)
        if not isinstance(idx, dict):
            return None
        return idx.get(str(offer_id))
    except Exception:  # noqa: BLE001
        return None


def clear_cache() -> int:
    """Remove all cached HTTP responses, atomic-write temporary files, and index files.

    Returns the count of .cache files removed.
    """
    count = 0
    if not os.path.isdir(CACHE_DIR):
        return 0
    for name in os.listdir(CACHE_DIR):
        full_path = os.path.join(CACHE_DIR, name)
        if not os.path.isfile(full_path):
            continue
        if name.endswith(".cache"):
            try:
                os.remove(full_path)
                count += 1
            except OSError:
                pass
        elif name.endswith(".tmp") or name == "index.json":
            try:
                os.remove(full_path)
            except OSError:
                pass
    return count

