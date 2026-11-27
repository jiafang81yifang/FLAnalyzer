"""Guards for the OverflowError batch, and the string shape it does not cover.

`float(10**400)` raises OverflowError; `float('1e400')` does NOT — it returns
inf. So a site hardened only with OverflowError still lets a hostile *string*
through. These tests pin both halves and state plainly which is covered.
"""
from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')
DSA = pipe_mod.DeepSeekAnalyzer


def test_over_range_int_does_not_abort_the_collector() -> None:
    """G1: what json.load hands back (10**400) must not abort the 31-table build.

    Revert tested: dropping OverflowError from the handler -> OverflowError
    escapes and the whole table fails to build.
    """
    rec = {'correct_score_page_avg': {'1-0': 10 ** 400, '2-0': 9.0}}
    got = DSA._collect_legal_cs_market_odds(rec, [])
    assert '1-0' not in got, got
    assert got['2-0'] == 9.0, got


def test_a_genuine_programming_error_still_propagates() -> None:
    """G2: adding OverflowError must not widen a handler into a blanket swallow.

    `calc_defense_coefficient` guards several statements. A RuntimeError raised
    from inside must reach the caller — if the handler absorbed it, a real bug
    would be reported as "coefficient stays 1.0" instead of crashing.
    """
    class Exploding:
        def __float__(self):
            raise RuntimeError('programming bug')

    with pytest.raises(RuntimeError, match='programming bug'):
        app.calc_defense_coefficient(Exploding())


def test_string_infinity_never_reaches_a_rendered_percentage() -> None:
    """G3: the string shape. float('1e400') is inf, not an exception.

    Revert tested: removing the isfinite check -> `_pct_text` returns 'inf%'.
    """
    assert float('1e400') == float('inf')
    assert app._pct_text('1e400') == '', repr(app._pct_text('1e400'))


def test_string_infinity_is_not_kept_as_a_market_odd() -> None:
    """A non-finite value is not a price, whichever literal produced it."""
    for hostile in ('1e400', 'inf', float('inf'), float('nan'), 10 ** 400):
        got = DSA._collect_legal_cs_market_odds(
            {'correct_score_page_avg': {'1-0': hostile}}, [])
        assert '1-0' not in got, (hostile, got)


def test_pack_market_values_are_all_finite() -> None:
    """Whatever went in, no row of the 31 table may carry inf/NaN."""
    pack = DSA.build_theo_mkt_31_pack(
        [], extra_market_odds={'1-0': '1e400', '2-0': 9.0},
        jc_score_odds={'2-0': 10.0}, lam_home=1.4, lam_away=1.1)
    for row in pack['rows']:
        for key in ('market', 'theory', 'jc_odds', 'jc_odds_pct', 'delta'):
            if row.get(key) is None:
                continue
            assert math.isfinite(float(row[key])), (row['score'], key, row[key])
    assert math.isfinite(float(pack['market_sum']))
