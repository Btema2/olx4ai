import email.message
import gzip
import io
import os
import subprocess
import time
import urllib.error
import urllib.request
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
        msg="HTTP " + str(code),
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
    with patch.object(cache, "_open", return_value=(b'{"ok": true}', "")) as mock_open:
        text = cache.fetch("https://example.com/x", json_mode=True)
        assert text == '{"ok": true}'
        assert mock_open.call_count == 1

        text2 = cache.fetch("https://example.com/x", json_mode=True)
        assert text2 == text
        assert mock_open.call_count == 1  # second call served from cache


def test_fetch_bypasses_stale_cache():
    path = cache._cache_path("https://example.com/stale")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("old")
    old_time = time.time() - cache.CACHE_TTL - 1
    os.utime(path, (old_time, old_time))

    with patch.object(cache, "_open", return_value=(b"fresh", "")):
        text = cache.fetch("https://example.com/stale", json_mode=True)
    assert text == "fresh"


def test_fetch_ignores_cache_when_use_cache_false():
    with patch.object(cache, "_open", return_value=(b"first", "")):
        cache.fetch("https://example.com/y", json_mode=True)

    with patch.object(cache, "_open", return_value=(b"second", "")):
        text = cache.fetch("https://example.com/y", json_mode=True, use_cache=False)
    assert text == "second"


def test_fetch_rejects_non_http_scheme():
    with patch.object(cache, "_open") as mock_open:
        with pytest.raises(SystemExit, match="refusing non-http\\(s\\) URL"):
            cache.fetch("file:///etc/passwd", json_mode=False)
        mock_open.assert_not_called()


def test_index_put_and_get_round_trip():
    cache.index_put([{"id": 42, "url": "https://example.com/42"}])
    assert cache.index_get("42") == "https://example.com/42"
    assert cache.index_get("999") is None


def test_fetch_retries_once_on_http_error_then_succeeds(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(
        cache, "_open", side_effect=[_http_error(), (b'{"ok": true}', "")]
    ) as mock_open:
        text = cache.fetch("https://example.com/retry-ok", json_mode=True)

    assert text == '{"ok": true}'
    assert mock_open.call_count == 2
    # the retry reuses the exact same Request (same URL/headers), not a new one
    assert mock_open.call_args_list[0].args[0] is mock_open.call_args_list[1].args[0]
    sleep.assert_called_once_with(cache.RETRY_DELAY)


def test_fetch_gives_up_after_one_retry_on_http_error(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(cache, "_open", side_effect=[_http_error(), _http_error()]) as mock_open:
        with pytest.raises(SystemExit, match="HTTP 403 for"):
            cache.fetch("https://example.com/retry-fail", json_mode=True)

    assert mock_open.call_count == 2  # exactly one retry, not a loop
    sleep.assert_called_once()


def test_fetch_retry_sleeps_for_retry_after_header_value(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(
        cache,
        "_open",
        side_effect=[_http_error(retry_after="7"), (b"ok", "")],
    ):
        cache.fetch("https://example.com/retry-after", json_mode=True)

    sleep.assert_called_once_with(7)


def test_fetch_retry_falls_back_to_default_when_retry_after_unparseable(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(
        cache,
        "_open",
        side_effect=[_http_error(retry_after="not-a-number"), (b"ok", "")],
    ):
        cache.fetch("https://example.com/retry-after-bad", json_mode=True)

    sleep.assert_called_once_with(cache.RETRY_DELAY)


def test_fetch_does_not_retry_non_retryable_http_error(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(cache, "_open", side_effect=[_http_error(code=404)]) as mock_open:
        with pytest.raises(SystemExit, match="HTTP 404 for"):
            cache.fetch("https://example.com/not-found", json_mode=True)

    assert mock_open.call_count == 1  # a 404 can never succeed on retry
    sleep.assert_not_called()


def test_fetch_retries_on_5xx_status(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(
        cache,
        "_open",
        side_effect=[_http_error(code=503), (b"ok", "")],
    ) as mock_open:
        text = cache.fetch("https://example.com/retry-5xx", json_mode=True)

    assert text == "ok"
    assert mock_open.call_count == 2
    sleep.assert_called_once_with(cache.RETRY_DELAY)


def test_retry_delay_clamps_negative_retry_after_to_zero():
    error = _http_error(retry_after="-5")
    assert cache._retry_delay(error) == 0


def test_retry_delay_clamps_large_retry_after_to_max():
    error = _http_error(retry_after="3600")
    assert cache._retry_delay(error) == cache.MAX_RETRY_DELAY


def test_fetch_retry_sleeps_clamped_delay_for_huge_retry_after(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(
        cache,
        "_open",
        side_effect=[_http_error(retry_after="99999"), (b"ok", "")],
    ):
        cache.fetch("https://example.com/retry-huge", json_mode=True)

    sleep.assert_called_once_with(cache.MAX_RETRY_DELAY)


def test_fetch_retry_sleeps_clamped_delay_for_negative_retry_after(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    with patch.object(
        cache,
        "_open",
        side_effect=[_http_error(retry_after="-1"), (b"ok", "")],
    ):
        cache.fetch("https://example.com/retry-negative", json_mode=True)

    sleep.assert_called_once_with(0)


def test_fetch_closes_first_http_error_before_retrying(monkeypatch):
    sleep = MagicMock()
    monkeypatch.setattr(cache.time, "sleep", sleep)

    first_error = _http_error()
    close_spy = MagicMock(wraps=first_error.close)
    monkeypatch.setattr(first_error, "close", close_spy)

    with patch.object(cache, "_open", side_effect=[first_error, (b"ok", "")]):
        cache.fetch("https://example.com/retry-closes", json_mode=True)

    close_spy.assert_called_once()


def test_index_get_missing_index_file_returns_none():
    assert cache.index_get("1") is None


def test_open_success_curl_command_and_headers():
    def fake_subprocess_run(cmd, capture_output=True):
        header_idx = cmd.index("-D") + 1
        header_path = cmd[header_idx]
        with open(header_path, "wb") as f:
            f.write(b"HTTP/1.1 200 OK\r\n\r\nHTTP/2 200\r\ncontent-encoding: gzip\r\n\r\n")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"raw body bytes", stderr=b""
        )

    req = urllib.request.Request("https://example.com/test", headers={"User-Agent": "test-agent"})
    with patch("subprocess.run", side_effect=fake_subprocess_run) as mock_run:
        raw, enc = cache._open(req)

    assert raw == b"raw body bytes"
    assert enc == "gzip"
    cmd_called = mock_run.call_args[0][0]
    assert cmd_called[:5] == ["curl", "--http2", "-s", "-S", "-L"]
    assert "-H" in cmd_called
    assert "User-agent: test-agent" in cmd_called
    assert cmd_called[-1] == "https://example.com/test"


def test_open_http_error_constructor_and_headers():
    def fake_subprocess_run(cmd, capture_output=True):
        header_idx = cmd.index("-D") + 1
        header_path = cmd[header_idx]
        with open(header_path, "wb") as f:
            f.write(b"HTTP/2 403\r\nRetry-After: 15\r\nContent-Type: text/plain\r\n\r\n")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"forbidden payload", stderr=b""
        )

    req = urllib.request.Request("https://example.com/forbidden")
    with patch("subprocess.run", side_effect=fake_subprocess_run):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            cache._open(req)

    err = exc_info.value
    assert err.code == 403
    assert err.msg == "HTTP 403"
    assert err.headers.get("Retry-After") == "15"
    assert err.read() == b"forbidden payload"


def test_open_http_error_decompresses_gzip_error_body():
    compressed_body = gzip.compress(b"human readable error message")

    def fake_subprocess_run(cmd, capture_output=True):
        header_idx = cmd.index("-D") + 1
        header_path = cmd[header_idx]
        with open(header_path, "wb") as f:
            f.write(b"HTTP/2 404\r\ncontent-encoding: gzip\r\n\r\n")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=compressed_body, stderr=b""
        )

    req = urllib.request.Request("https://example.com/not-found")
    with patch("subprocess.run", side_effect=fake_subprocess_run):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            cache._open(req)

    err = exc_info.value
    assert err.code == 404
    assert err.read() == b"human readable error message"


def test_open_curl_failure_raises_url_error():
    def fake_subprocess_run(cmd, capture_output=True):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=6,
            stdout=b"",
            stderr=b"curl: (6) Could not resolve host: example.com",
        )

    req = urllib.request.Request("https://example.com/bad-dns")
    with patch("subprocess.run", side_effect=fake_subprocess_run):
        with pytest.raises(urllib.error.URLError, match="Could not resolve host"):
            cache._open(req)


def test_open_curl_partial_transfer_failure_raises_url_error():
    def fake_subprocess_run(cmd, capture_output=True):
        header_idx = cmd.index("-D") + 1
        header_path = cmd[header_idx]
        with open(header_path, "wb") as f:
            f.write(b"HTTP/2 200\r\n\r\n")
        return subprocess.CompletedProcess(
            args=cmd, returncode=18, stdout=b"partial data", stderr=b"curl: (18) transfer closed"
        )

    req = urllib.request.Request("https://example.com/partial")
    with patch("subprocess.run", side_effect=fake_subprocess_run):
        with pytest.raises(urllib.error.URLError, match="transfer closed"):
            cache._open(req)
