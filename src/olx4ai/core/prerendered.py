"""Extract __PRERENDERED_STATE__ from OLX HTML and locate offer-shaped dicts."""

from __future__ import annotations

import json
import urllib.parse


def _scan_js_string(s: str) -> str:
    """s starts at the opening quote. Return the raw literal including quotes."""
    quote, i, n = s[0], 1, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return s[: i + 1]
        i += 1
    raise ValueError("unterminated JS string")


def _scan_balanced(s: str) -> str:
    depth, i, in_str, esc = 0, 0, False, False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return s[: i + 1]
    raise ValueError("unbalanced JSON")


def extract_prerendered(html: str) -> dict:
    """Pull window.__PRERENDERED_STATE__ out of an OLX page, whatever its encoding."""
    idx = html.find("__PRERENDERED_STATE__")
    if idx == -1:
        raise SystemExit("no __PRERENDERED_STATE__ on this page (bot wall or layout change?)")
    rest = html[html.index("=", idx) + 1:].lstrip()

    if rest[0] in "\"'":
        literal = _scan_js_string(rest)
        if literal[0] == "'":  # normalise to a JSON-parsable double-quoted literal
            literal = '"' + literal[1:-1].replace('"', '\\"').replace("\\'", "'") + '"'
        inner = json.loads(literal)          # -> str
    else:
        inner = _scan_balanced(rest)

    if isinstance(inner, str):
        if inner.lstrip().startswith("%"):   # sometimes URI-encoded
            inner = urllib.parse.unquote(inner)
        return json.loads(inner)
    return inner


def _looks_like_offer(d: dict) -> bool:
    return "id" in d and "title" in d and ("url" in d or "params" in d or "price" in d)


def find_offers(node: object, best: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
    """Structure-agnostic: find the offers, whether they sit in a list
    (search/listing pages) or as a single bare dict (offer-detail pages)."""
    best = best or []
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts and len(dicts) >= max(1, len(node) // 2):
            hits = sum(1 for d in dicts if _looks_like_offer(d))
            if hits >= max(1, len(dicts) // 2) and len(dicts) > len(best):
                best = dicts
        for x in node:
            best = find_offers(x, best)
    elif isinstance(node, dict):
        if not best and _looks_like_offer(node):
            best = [node]
        for v in node.values():
            best = find_offers(v, best)
    return best
