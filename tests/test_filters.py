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
