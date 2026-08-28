from types import SimpleNamespace

from olx4ai.core.filters import post_filter


def row(title, price=100, promoted=False):
    return {"title": title, "price": price, "promoted": promoted}


def test_exclude_drops_matching_titles():
    rows = [row("Nice Case for iPhone"), row("iPhone 13 Pro")]
    args = SimpleNamespace(exclude="case", must=None, no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["iPhone 13 Pro"]


def test_must_keeps_only_matching_titles():
    rows = [row("iPhone 13 Pro"), row("Samsung Galaxy S21")]
    args = SimpleNamespace(exclude=None, must="iphone", no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["iPhone 13 Pro"]


def test_no_promoted_drops_promoted_rows():
    rows = [row("A", promoted=True), row("B", promoted=False)]
    args = SimpleNamespace(exclude=None, must=None, no_promoted=True, dedupe=False)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["B"]


def test_dedupe_drops_same_title_and_price():
    rows = [
        row("Same Title", price=100),
        row("Same Title", price=100),
        row("Same Title", price=200),
    ]
    args = SimpleNamespace(exclude=None, must=None, no_promoted=False, dedupe=True)
    out = post_filter(rows, args)
    assert len(out) == 2
    assert [r["price"] for r in out] == [100, 200]


def test_filters_compose_together():
    rows = [
        row("iPhone Case", promoted=True),
        row("iPhone 13", promoted=True),
        row("iPhone 13", promoted=False),
    ]
    args = SimpleNamespace(exclude="case", must="iphone", no_promoted=True, dedupe=True)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["iPhone 13"]


def test_missing_flags_default_to_no_op():
    rows = [row("Anything")]
    out = post_filter(rows, SimpleNamespace())
    assert out == rows


def test_exclude_does_not_match_substring_false_positive():
    """Regression test: 'pro' should not match 'PROMENADA' (substring false positive)."""
    rows = [row("iPhone 17 sklep PROMENADA"), row("iPhone 13 Pro")]
    args = SimpleNamespace(exclude="pro", must=None, no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    # Only "iPhone 13 Pro" should be excluded (matches whole word "Pro")
    # "iPhone 17 sklep PROMENADA" should be kept (pro is not a whole word)
    assert [r["title"] for r in out] == ["iPhone 17 sklep PROMENADA"]


def test_exclude_matches_whole_word_correctly():
    """Verify that whole-word matching still works: 'pro' matches 'Pro' in 'iPhone 17 Pro'."""
    rows = [row("iPhone 17 Pro"), row("iPhone 13")]
    args = SimpleNamespace(exclude="pro", must=None, no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    # "iPhone 17 Pro" should be excluded (matches whole word "Pro")
    # "iPhone 13" should be kept
    assert [r["title"] for r in out] == ["iPhone 13"]


def test_must_does_not_match_substring_false_positive():
    """Regression test: 'pro' should not match 'PROMENADA' with must filter."""
    rows = [row("iPhone 17 sklep PROMENADA"), row("iPhone 13 Pro")]
    args = SimpleNamespace(exclude=None, must="pro", no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    # Only "iPhone 13 Pro" should be kept (matches whole word "Pro")
    # "iPhone 17 sklep PROMENADA" should be excluded (pro is not a whole word)
    assert [r["title"] for r in out] == ["iPhone 13 Pro"]


def test_must_matches_whole_word_correctly():
    """Verify that whole-word matching still works with must: 'pro' matches 'Pro' in 'iPhone 17 Pro'."""
    rows = [row("iPhone 17 Pro"), row("iPhone 13")]
    args = SimpleNamespace(exclude=None, must="pro", no_promoted=False, dedupe=False)
    out = post_filter(rows, args)
    # "iPhone 17 Pro" should be kept (matches whole word "Pro")
    # "iPhone 13" should be excluded
    assert [r["title"] for r in out] == ["iPhone 17 Pro"]


def test_min_drops_rows_below_threshold_and_unpriced():
    rows = [
        {"title": "Cheap Item", "price": 50, "cond": "used", "promoted": False},
        {"title": "Unpriced Item", "price": None, "cond": "used", "promoted": False},
        {"title": "Expensive Item", "price": 1800, "cond": "used", "promoted": False},
    ]
    args = SimpleNamespace(min=1000)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["Expensive Item"]


def test_max_price_drops_rows_above_threshold_and_unpriced():
    rows = [
        {"title": "Cheap Item", "price": 50, "cond": "used", "promoted": False},
        {"title": "Unpriced Item", "price": None, "cond": "used", "promoted": False},
        {"title": "Expensive Item", "price": 1800, "cond": "used", "promoted": False},
    ]
    args = SimpleNamespace(max_price=1000)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["Cheap Item"]


def test_price_range_keeps_only_rows_within_bounds():
    rows = [
        {"title": "Too Cheap", "price": 50, "cond": "used", "promoted": False},
        {"title": "Just Right", "price": 1800, "cond": "used", "promoted": False},
        {"title": "Too Expensive", "price": 3500, "cond": "used", "promoted": False},
        {"title": "No Price", "price": None, "cond": "used", "promoted": False},
    ]
    args = SimpleNamespace(min=1000, max_price=2000)
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["Just Right"]


def test_condition_filter_keeps_only_matching_condition():
    rows = [
        {"title": "Used Phone", "price": 500, "cond": "used", "promoted": False},
        {"title": "New Phone", "price": 1500, "cond": "new", "promoted": False},
        {"title": "Damaged Phone", "price": 200, "cond": "damaged", "promoted": False},
        {"title": "Unknown Phone", "price": 300, "cond": None, "promoted": False},
    ]
    args = SimpleNamespace(condition="new")
    out = post_filter(rows, args)
    assert [r["title"] for r in out] == ["New Phone"]


def test_post_filter_rejects_negative_min():
    import pytest

    with pytest.raises(SystemExit, match="min price cannot be negative"):
        post_filter([], SimpleNamespace(min=-10))


def test_post_filter_rejects_negative_max_price():
    import pytest

    with pytest.raises(SystemExit, match="max price cannot be negative"):
        post_filter([], SimpleNamespace(max_price=-10))


def test_post_filter_sort_price_asc_orders_numeric_prices_and_puts_none_at_end():
    rows = [
        {"title": "B", "price": 2199},
        {"title": "A", "price": 750},
        {"title": "C", "price": None},
        {"title": "D", "price": 1900},
    ]
    out = post_filter(rows, SimpleNamespace(sort="price-asc"))
    assert [r["title"] for r in out] == ["A", "D", "B", "C"]
    assert [r["price"] for r in out] == [750, 1900, 2199, None]


def test_post_filter_sort_price_desc_orders_numeric_prices_and_puts_none_at_end():
    rows = [
        {"title": "A", "price": 750},
        {"title": "B", "price": 2199},
        {"title": "C", "price": None},
        {"title": "D", "price": 1900},
    ]
    out = post_filter(rows, SimpleNamespace(sort="price-desc"))
    assert [r["title"] for r in out] == ["B", "D", "A", "C"]
    assert [r["price"] for r in out] == [2199, 1900, 750, None]


def test_post_filter_sort_relevance_or_unrecognized_preserves_order():
    rows = [
        {"title": "B", "price": 2199},
        {"title": "A", "price": 750},
        {"title": "D", "price": 1900},
    ]
    out = post_filter(rows, SimpleNamespace(sort="relevance"))
    assert [r["title"] for r in out] == ["B", "A", "D"]


def test_filters_docstrings_document_semantics():
    import olx4ai.core.filters as filters_mod

    mod_doc = filters_mod.__doc__ or ""
    fn_doc = filters_mod.post_filter.__doc__ or ""

    for doc in (mod_doc, fn_doc):
        assert "exclude" in doc
        assert "must" in doc
        assert "dedupe" in doc
        assert "\\b" in doc or r"\b" in doc or "whole-word" in doc
        assert "title" in doc
        assert "price" in doc

