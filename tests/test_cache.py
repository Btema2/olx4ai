import os
import time
from unittest.mock import MagicMock, patch

import pytest

from olx4ai.core import cache


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    yield
    cache.configure("olx.pl")  # reset domain for any later test module


def _fake_response(body: bytes, encoding: str = ""):
    resp = MagicMock()
    resp.read.return_value = body
    resp.headers.get.return_value = encoding
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_configure_overrides_domain_and_derived_urls():
    cache.configure("olx.ua")
    assert cache.DOMAIN == "olx.ua"
    assert cache.BASE == "https://www.olx.ua"
    assert cache.API == "https://www.olx.ua/api/v1/offers/"


def test_configure_with_no_domain_is_a_noop():
    cache.configure("olx.pl")
    before = (cache.DOMAIN, cache.BASE, cache.API)
    cache.configure(None)
    assert (cache.DOMAIN, cache.BASE, cache.API) == before


def test_fetch_writes_and_reads_from_cache():
    resp = _fake_response(b'{"ok": true}')
    with patch("urllib.request.urlopen", return_value=resp) as urlopen:
        text = cache.fetch("https://example.com/x", json_mode=True)
        assert text == '{"ok": true}'
        assert urlopen.call_count == 1

        text2 = cache.fetch("https://example.com/x", json_mode=True)
        assert text2 == text
        assert urlopen.call_count == 1  # second call served from cache


def test_fetch_bypasses_stale_cache():
    path = cache._cache_path("https://example.com/stale")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("old")
    old_time = time.time() - cache.CACHE_TTL - 1
    os.utime(path, (old_time, old_time))

    resp = _fake_response(b"fresh")
    with patch("urllib.request.urlopen", return_value=resp):
        text = cache.fetch("https://example.com/stale", json_mode=True)
    assert text == "fresh"


def test_fetch_ignores_cache_when_use_cache_false():
    resp1 = _fake_response(b"first")
    with patch("urllib.request.urlopen", return_value=resp1):
        cache.fetch("https://example.com/y", json_mode=True)

    resp2 = _fake_response(b"second")
    with patch("urllib.request.urlopen", return_value=resp2):
        text = cache.fetch("https://example.com/y", json_mode=True, use_cache=False)
    assert text == "second"


def test_index_put_and_get_round_trip():
    cache.index_put([{"id": 42, "url": "https://example.com/42"}])
    assert cache.index_get("42") == "https://example.com/42"
    assert cache.index_get("999") is None


def test_index_get_missing_index_file_returns_none():
    assert cache.index_get("1") is None
