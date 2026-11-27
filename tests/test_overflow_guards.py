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


def test_no_display_formatter_renders_a_non_finite_value() -> None:
    """The whole class, not the three sites reached first.

    Every display entry point must render inf/NaN as a placeholder. This walks
    the shared formatter over the hostile shapes, so a regression anywhere it is
    used fails here.
    """
    import number_text as nt

    for hostile in ('1e400', 'inf', '-1e400', 'nan', float('inf'),
                    float('-inf'), float('nan'), 10 ** 400):
        assert nt.num_text(hostile, '.2f') == '-', hostile
        assert nt.pct_text(hostile) == '-', hostile
    # and the value that is fine still formats
    assert nt.num_text(1.234, '.2f') == '1.23'
    assert nt.pct_text(12.345) == '12.3%'


def test_a_rendered_body_can_never_carry_a_non_finite_number() -> None:
    """The boundary guard: whatever produced the number, the body is clean.

    Revert tested: removing `_scrub_non_finite_text` -> the body keeps 'inf%'.
    """
    import number_text as nt

    with pytest.raises(AssertionError):
        nt.assert_finite_text('置信度 inf%')
    with pytest.raises(AssertionError):
        nt.assert_finite_text('方向 nan')
    nt.assert_finite_text('置信度 12.3%')          # ordinary body passes
    scrubbed = app._scrub_non_finite_text('置信度 inf% 方向 nan')
    assert 'inf' not in scrubbed and 'nan' not in scrubbed, scrubbed
    assert '12.3%' in app._scrub_non_finite_text('置信度 12.3%')
    # and the emit path must actually call the scrubber: a body with a
    # non-finite figure may not reach send_tg unchanged.
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert '_scrub_non_finite_text(tg_report)' in src, (
        'the emit path must scrub the report before sending it')


def test_emit_path_never_puts_inf_in_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End to end: a record whose odds are the string shape yields no `inf` cell."""
    import os
    import tempfile

    import sys as _sys
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    import sweep_100k_e2e as S

    case = S._build_case(20261127)
    rec = case['rec']
    # plant the string shape where the 31 table reads market odds
    rec['correct_score_page_avg'] = {k: '1e400' for k in list(rec['correct_score_page_avg'])[:5]}
    case = dict(case, rec=rec)
    d = tempfile.mkdtemp()
    sent, _out = S._run_once(case, os.path.join(d, 'h.json'))
    for text in (t for t in sent if t):
        lowered = text.lower()
        assert 'inf%' not in lowered, text[:200]
        assert 'nan%' not in lowered, text[:200]


def test_body_checker_separates_a_rendered_inf_from_a_diagnostic() -> None:
    """The boundary check must fire on a rendered figure, not on a diagnostic.

    Revert tested: dropping the DIAGNOSTIC exclusion -> the checker flags
    「赔率数值非法: inf」, which is a correct message, and the sweep would report a
    false defect on every hostile-value case.
    """
    import sys as _sys
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    import sweep_100k_e2e as S

    assert S._check_no_non_finite_in_body('置信度 inf%'), 'a rendered inf must be caught'
    assert S._check_no_non_finite_in_body('Δ -inf pp'), 'a rendered -inf must be caught'
    assert not S._check_no_non_finite_in_body('赔率数值非法: inf'), (
        'naming the bad value is a correct diagnostic')
    assert not S._check_no_non_finite_in_body('置信度 12.3%')
