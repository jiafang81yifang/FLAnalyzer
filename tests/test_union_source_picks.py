"""Pins the removal of pre-union single-source picks at the call sites that had them.

Each test fails on the pre-purge code and passes on the merged union logic, so the
`a or b` chains cannot creep back in. The bug class is the one the shipped build
(65943e7) fixed: the first non-empty copy of the 竞彩 score odds masks every legal
value in the second copy, those rows come out with an empty jc column, and the
31-table audit rejects the match as incomplete.

Hand-computed expectations are spelled out inline in each docstring.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _app():
    return importlib.import_module('app_v84_hermes')


def _quiet(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*args, **kwargs)


def test_heat_input_uses_the_union_of_market_average_copies() -> None:
    """The heat rebuild must see 5-0 from books_avg, not only page_avg's 1-0.

    Pre-purge the call site read `page_avg or books_avg`, which picks page_avg alone
    whenever it is non-empty: a books_avg-only option contributes nothing to the heat
    ranking. Hand-computed: union of {'1-0'} and {'5-0'} has 2 keys; the old
    expression has 1.
    """
    app = _app()
    rec = {
        'correct_score_page_avg': {'1-0': 6.0},
        'correct_score_books_avg': {'5-0': 60.0},
    }
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert "page = rec.get('correct_score_page_avg') or rec.get('correct_score_books_avg')" not in src, (
        'the pre-union single-source pick is back in the direction-lock heat path'
    )
    merged = app._merge_jc_odds_sources(
        rec.get('correct_score_page_avg'),
        rec.get('correct_score_books_avg'),
        rec.get('correct_score_site_avg'),
    )
    assert set(merged) == {'1-0', '5-0'}, merged
    assert len(rec.get('correct_score_page_avg') or rec.get('correct_score_books_avg')) == 1


def test_positive_mkt_gap_count_reads_both_market_average_copies() -> None:
    """`page_avg` is non-empty but missing 5-0; `books_avg` has only 5-0.

    Old chain `page_avg or books_avg` picked page_avg alone, so 5-0 could never
    contribute a positive gap. Hand-computed: page_avg 1-0=6.0 vs jc 5.0 -> +1 (counted);
    books_avg 5-0=60.0 vs jc 50.0 -> +10 (counted). Union count = 2, old count = 1.
    """
    app = _app()
    source = {
        'correct_score_page_avg': {'1-0': 6.0},
        'correct_score_books_avg': {'5-0': 60.0},
        'correct_score_jc_official': {'1-0': 5.0, '5-0': 50.0},
    }
    assert _quiet(app._positive_mkt_gap_count, source) == 2


def test_positive_mkt_gap_count_reads_both_jc_copies() -> None:
    """The 竞彩 side has the same rule: the official copy must not hide jc_score_odds.

    page_avg has 1-0 and 5-0. jc official only quotes 1-0; jc_score_odds only 5-0.
    Hand-computed: 1-0 6.0 vs 5.0 -> +1; 5-0 60.0 vs 50.0 -> +10. Union = 2.
    With the old `official = a or b`, 5-0 had no jc value and was skipped -> 1.
    """
    app = _app()
    source = {
        'correct_score_page_avg': {'1-0': 6.0, '5-0': 60.0},
        'correct_score_jc_official': {'1-0': 5.0},
        'jc_score_odds': {'5-0': 50.0},
    }
    assert _quiet(app._positive_mkt_gap_count, source) == 2


def test_pool31_rows_read_both_jc_copies_at_the_six_step_call_site() -> None:
    """The six-step fallback must hand `build_pool31_option_rows` the union of both copies.

    This pins the call site, not just the helper: on the pre-purge code the argument
    expression was `_rec.get('jc_score_odds') or _rec.get('correct_score_jc_official')`,
    so a non-empty first copy silently dropped the second copy's options.

    Hand-computed: jc_score_odds covers 1-0 only; correct_score_jc_official covers 5-0
    only. The union therefore has 2 options where the old expression had 1.
    """
    app = _app()
    rec = {
        'okooo_match_id': '1338958',
        'jc_score_odds': {'1-0': 9.0},
        'correct_score_jc_official': {'5-0': 51.0},
    }
    captured = {}
    real = app.build_pool31_option_rows

    def spy(rows, **kwargs):
        captured['jc_score_odds'] = kwargs.get('jc_score_odds')
        return real(rows, **kwargs)

    app.build_pool31_option_rows = spy
    try:
        # No theo_mkt pack, so the else-branch that had the `or` chain runs.
        _quiet(
            app._build_six_step_engine_payload,
            rec,
            {},
        ) if hasattr(app, '_build_six_step_engine_payload') else None
    finally:
        app.build_pool31_option_rows = real

    if not captured:
        # The call site lives inline in analyze_match; assert the expression there
        # instead of pretending to have exercised it.
        src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
        assert "_rec.get('jc_score_odds') or _rec.get('correct_score_jc_official')" not in src, (
            'the pre-union single-source pick is back at the six-step pool31 call site'
        )
        assert "_merge_jc_odds_sources(\n                    _rec.get('jc_score_odds')," in src
        return
    assert set(captured['jc_score_odds'] or {}) == {'1-0', '5-0'}, captured
