import email.message
import io
import os
import time
import urllib.error
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


def _http_error(code: int = 403, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        url="https://example.com/x",
        code=code,
        msg="Forbidden",
        hdrs=hdrs,
        fp=io.BytesIO(b"blocked"),
    )


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


def test_fetch_rejects_non_http_scheme():
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(SystemExit, match="refusing non-http\\(s\\) URL"):
            cache.fetch("file:///etc/passwd", json_mode=False)
        urlopen.assert_not_called()


def test_index_put_and_get_round_trip():
    cache.index_put([{"id": 42, "url": "https://example.com/42"}])
    assert cache.index_get("42") == "https://example.com/42"
    assert cache.index_get("999") is None


def test_fetch_retries_once_on_http_error_then_succeeds(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    success = _fake_response(b'{"ok": true}')
    with patch("urllib.request.urlopen", side_effect=[_http_error(), success]) as urlopen:
        text = cache.fetch("https://example.com/retry-ok", json_mode=True)

    assert text == '{"ok": true}'
    assert urlopen.call_count == 2
    # the retry reuses the exact same Request (same URL/headers), not a new one
    assert urlopen.call_args_list[0].args[0] is urlopen.call_args_list[1].args[0]
    sleep.assert_called_once_with(cache.RETRY_DELAY)


def test_fetch_gives_up_after_one_retry_on_http_error(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch("urllib.request.urlopen", side_effect=[_http_error(), _http_error()]) as urlopen:
        with pytest.raises(SystemExit, match="HTTP 403 for"):
            cache.fetch("https://example.com/retry-fail", json_mode=True)

    assert urlopen.call_count == 2  # exactly one retry, not a loop
    sleep.assert_called_once()


def test_fetch_retry_sleeps_for_retry_after_header_value(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    success = _fake_response(b"ok")
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_error(retry_after="7"), success],
    ):
        cache.fetch("https://example.com/retry-after", json_mode=True)

    sleep.assert_called_once_with(7)


def test_fetch_retry_falls_back_to_default_when_retry_after_unparseable(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    success = _fake_response(b"ok")
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_error(retry_after="not-a-number"), success],
    ):
        cache.fetch("https://example.com/retry-after-bad", json_mode=True)

    sleep.assert_called_once_with(cache.RETRY_DELAY)


def test_fetch_does_not_retry_non_retryable_http_error(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch("urllib.request.urlopen", side_effect=[_http_error(code=404)]) as urlopen:
        with pytest.raises(SystemExit, match="HTTP 404 for"):
            cache.fetch("https://example.com/not-found", json_mode=True)

    assert urlopen.call_count == 1  # a 404 can never succeed on retry
    sleep.assert_not_called()


def test_fetch_retries_on_5xx_status(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    success = _fake_response(b"ok")
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_error(code=503), success],
    ) as urlopen:
        text = cache.fetch("https://example.com/retry-5xx", json_mode=True)

    assert text == "ok"
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(cache.RETRY_DELAY)


def test_retry_delay_clamps_negative_retry_after_to_zero():
    error = _http_error(retry_after="-5")
    try:
        assert cache._retry_delay(error) == 0
    finally:
        error.close()


def test_retry_delay_clamps_large_retry_after_to_max():
    error = _http_error(retry_after="3600")
    try:
        assert cache._retry_delay(error) == cache.MAX_RETRY_DELAY
    finally:
        error.close()


def test_fetch_retry_sleeps_clamped_delay_for_huge_retry_after(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    success = _fake_response(b"ok")
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_error(retry_after="99999"), success],
    ):
        cache.fetch("https://example.com/retry-huge", json_mode=True)

    sleep.assert_called_once_with(cache.MAX_RETRY_DELAY)


def test_fetch_retry_sleeps_clamped_delay_for_negative_retry_after(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    success = _fake_response(b"ok")
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_error(retry_after="-1"), success],
    ):
        cache.fetch("https://example.com/retry-negative", json_mode=True)

    sleep.assert_called_once_with(0)


def test_fetch_closes_first_http_error_before_retrying(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    first_error = _http_error()
    close_spy = MagicMock(wraps=first_error.close)
    monkeypatch.setattr(first_error, "close", close_spy)

    success = _fake_response(b"ok")
    with patch("urllib.request.urlopen", side_effect=[first_error, success]):
        cache.fetch("https://example.com/retry-closes", json_mode=True)

    close_spy.assert_called_once()


def test_index_get_missing_index_file_returns_none():
    assert cache.index_get("1") is None
