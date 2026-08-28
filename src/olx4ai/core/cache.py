"""HTTP fetch with on-disk caching, plus the id->url index."""

from __future__ import annotations

import email
import gzip
import hashlib
import io
import ipaddress
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

DOMAIN = os.environ.get("OLX4AI_DOMAIN", "olx.pl")
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
        DOMAIN = domain
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
        cmd = ["curl", "--http2", "-s", "-S", "-L", "--max-time", "25", "-D", header_path]
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
    parts = hostname.split(".")
    is_olx = False
    if len(parts) >= 2:
        if parts[-2] == "olx":
            is_olx = True
        elif len(parts) >= 3 and parts[-3] == "olx" and parts[-2] in ("com", "co", "org", "net"):
            is_olx = True
        elif DOMAIN and (hostname == DOMAIN or hostname.endswith("." + DOMAIN)):
            is_olx = True

    if not is_olx:
        raise SystemExit(f"refusing non-OLX host in URL: {url}")


def fetch(url: str, *, json_mode: bool, use_cache: bool = True, ttl: int = CACHE_TTL) -> str:
    _validate_url(url)

    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/json, text/plain, */*"
                if json_mode
                else "text/html,application/xhtml+xml"
            ),
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": BASE + "/",
        },
    )
    try:
        raw, enc = _open(req)
    except urllib.error.HTTPError as e:
        if not _is_retryable(e.code):
            raise SystemExit(
                f"HTTP {e.code} for {url}\n{e.read()[:400].decode('utf-8', 'replace')}"
            )
        delay = _retry_delay(e)
        e.close()
        time.sleep(delay)
        try:
            raw, enc = _open(req)
        except urllib.error.HTTPError as e2:
            raise SystemExit(
                f"HTTP {e2.code} for {url}\n{e2.read()[:400].decode('utf-8', 'replace')}"
            )
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
            return json.load(fh).get(str(offer_id))
    except Exception:  # noqa: BLE001
        return None
