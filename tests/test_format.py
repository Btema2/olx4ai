import json
from types import SimpleNamespace

from olx4ai.core import cache
from olx4ai.core import format as fmt


def offer_row(id=1, title="Test Offer", price=1000, price_label=None, neg=False,
              cond="used", city="Warszawa", district="Mokotów", age="1d",
              delivery=False, biz=False, promoted=False, url="https://example.com/1"):
    return dict(id=id, title=title, price=price, price_label=price_label, neg=neg,
                cond=cond, city=city, district=district, age=age, delivery=delivery,
                biz=biz, promoted=promoted, url=url)


def test_fmt_line_includes_price_flags_and_location():
    line = fmt.fmt_line(offer_row(delivery=True, promoted=True), 1, 80, False)
    assert "1000zł" in line
    assert "D" in line and "*" in line
    assert "Warszawa/Mokotów" in line


def test_fmt_line_truncates_long_titles():
    long_title = "x" * 200
    line = fmt.fmt_line(offer_row(title=long_title), 1, 20, False)
    expected = "x" * 19 + "…"
    assert expected in line
    assert "x" * 20 not in line


def test_fmt_line_appends_url_when_requested():
    line = fmt.fmt_line(offer_row(), 1, 80, True)
    assert "https://example.com/1" in line


def test_fmt_line_omits_url_by_default():
    line = fmt.fmt_line(offer_row(), 1, 80, False)
    assert "https://example.com/1" not in line


def test_compute_stats_basic_distribution():
    rows = [offer_row(id=i, price=p) for i, p in enumerate([100, 200, 300, 400, 500])]
    stats = fmt.compute_stats(rows)
    assert stats["count"] == 5
    assert stats["priced_count"] == 5
    assert stats["min"] == 100
    assert stats["max"] == 500
    assert stats["median"] == 300
    assert stats["cheapest_ids"]


def test_compute_stats_handles_no_priced_rows():
    rows = [offer_row(id=1, price=None)]
    stats = fmt.compute_stats(rows)
    assert stats == {"count": 1, "priced_count": 0}


def test_print_stats_matches_compute_stats(capsys):
    rows = [offer_row(id=i, price=p) for i, p in enumerate([100, 200, 300, 400, 500])]
    fmt.print_stats(rows, "test label")
    out = capsys.readouterr().out
    assert "test label: 5 offers, 5 with a numeric price" in out
    assert "min 100" in out
    assert "cheapest ids:" in out


def test_emit_json_mode_honors_field_whitelist(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    rows = [offer_row()]
    args = SimpleNamespace(json=True, fields="id,price", title_chars=80, urls=False)
    fmt.emit(rows, args, "label")
    out = json.loads(capsys.readouterr().out)
    assert out == [{"id": 1, "price": 1000}]


def test_emit_writes_to_index(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    rows = [offer_row(id=42, url="https://example.com/42")]
    args = SimpleNamespace(json=True, fields=None, title_chars=80, urls=False)
    fmt.emit(rows, args, "label")
    assert cache.index_get("42") == "https://example.com/42"


def test_emit_no_results_message(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    args = SimpleNamespace(json=False, fields=None, title_chars=80, urls=False)
    fmt.emit([], args, "label")
    assert "no results" in capsys.readouterr().out
