"""Hostile odds values and split jc copies must not cost a match its 31 table.

Three defects found by the 100k sweep harness describe behaviour that was never
fixed on master:

  * `_collect_legal_cs_market_odds` caught only TypeError/ValueError, but
    `json.load` really does hand over `10**400` as an int and `float()` raises
    OverflowError on it. One hostile value aborted the collector, so the match
    could not build a 31 table at all.
  * `inf > 1` is true, so a non-finite value won `setdefault` and masked a legal
    copy of the same score in a later source.
  * Call sites passed `jc_score_odds or correct_score_jc_official or {}`, so a
    partial first copy hid every legal value in the second. Those rows rendered
    with an empty 竞彩 column and the audit then refused the match as
    「31全表未齐」.

Each test fails against the pre-fix code.
"""
from __future__ import annotations

import importlib
import io
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot), contextlib.redirect_stderr(_boot):
    app = importlib.import_module('app_v84_hermes')
    pipe = importlib.import_module('blade_deepseek_pipeline')

DSA = pipe.DeepSeekAnalyzer


def test_over_range_int_does_not_abort_the_collector() -> None:
    """`float(10**400)` raises OverflowError; the collector must absorb it."""
    got = DSA._collect_legal_cs_market_odds(
        {'correct_score_page_avg': {'1-0': 10 ** 400, '2-0': 9.0}}, [],
    )
    assert got.get('2-0') == 9.0, got
    assert got.get('1-0') is None, got


def test_non_finite_does_not_mask_a_legal_copy_from_another_source() -> None:
    """Restoring the plain `f > 1` check makes 1-0 stay inf and fails here."""
    got = DSA._collect_legal_cs_market_odds(
        {
            'correct_score_page_avg': {'1-0': float('inf'), '2-0': float('nan')},
            'correct_score_site_avg': {'1-0': 647.6292, '2-0': 174.1624},
        },
        [],
    )
    assert got.get('1-0') == 647.6292, got
    assert got.get('2-0') == 174.1624, got


def test_a_partial_jc_copy_does_not_hide_the_official_copy() -> None:
    merged = app._merge_jc_odds_sources(
        {'1-0': 13.6673, '2-0': 'x'},
        {'1-0': 13.0, '2-0': 26.0828, '3-1': float('inf')},
    )
    assert merged['1-0'] == 13.6673, merged
    assert merged['2-0'] == 26.0828, merged
    assert '3-1' not in merged, merged


def test_union_keeps_the_first_source_winning() -> None:
    merged = app._merge_jc_odds_sources({'1-0': 9.0}, {'1-0': 8.0, '2-0': 9.5})
    assert merged == {'1-0': 9.0, '2-0': 9.5}, merged


def test_union_accepts_dict_shaped_odds_and_skips_junk() -> None:
    merged = app._merge_jc_odds_sources(
        {'1-0': {'odds': 7.5}, '2-0': None, '3-0': 'n/a', '4-0': 0.5},
        {'5-0': {'avg': 120.0}},
        None,
        'not a dict',
    )
    assert merged == {'1-0': 7.5, '5-0': 120.0}, merged


def test_union_is_empty_for_no_usable_input() -> None:
    assert app._merge_jc_odds_sources() == {}
    assert app._merge_jc_odds_sources(None, {}, {'1-0': 0}) == {}
