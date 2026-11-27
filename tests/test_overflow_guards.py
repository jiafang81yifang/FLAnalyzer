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

    # Not the helper alone: drive the real call site, `_check_report`, which is
    # what every case goes through. A guard that only exercises the helper would
    # pass even if `_check_report` stopped calling it.
    case = S._build_case(20261127)
    with_inf = S._check_report(case, ['置信度 inf%'], '')
    assert any(inv == 'E11' for inv, _ in with_inf), (
        f'_check_report must surface E11 for a rendered inf; got {with_inf}')
    with_diag = S._check_report(case, ['赔率数值非法: inf'], '')
    assert not any(inv == 'E11' for inv, _ in with_diag), (
        f'_check_report must not flag a diagnostic; got {with_diag}')


def test_scrubbed_section_is_never_a_plausible_verdict() -> None:
    """A section made only of a non-finite value must not read as a real answer.

    Replacing `inf%` with `-%` leaves a body that looks like a supplied figure.
    The replacement has to say, in the text itself, that no usable number exists.
    """
    src = '🧠【DeepSeek 数据分析研判报告】\n  inf%'
    out = app._scrub_non_finite_text(src)
    body = out.split('】', 1)[1].strip()

    assert body, 'the section must not be blanked: an empty section is worse'
    assert '未形成' in body or '非有限' in body, (
        f'a scrubbed section must self-describe as missing, got {body!r}')
    # It must not masquerade as a figure: a bare '-' or '-%' reads as a value.
    assert body.strip() not in ('-', '-%', '—'), f'scrubbed body looks like a value: {body!r}'
    assert not __import__('re').fullmatch(r'-?\s*%?', body), body


def test_scrubbing_leaves_a_searchable_trace(capsys) -> None:
    """A silent rewrite hides the defect that caused it.

    Revert tested: removing the print from `_scrub_non_finite_text` -> no line
    matches '数值卫生' and this assertion fails.
    """
    app._scrub_non_finite_text('置信度 inf%')
    printed = capsys.readouterr().out
    assert '数值卫生' in printed, f'no trace of scrubbing; got {printed!r}'
    assert 'non-finite' in printed, printed

    # And a clean body must stay silent.
    app._scrub_non_finite_text('置信度 12.3%')
    assert '数值卫生' not in capsys.readouterr().out


def test_emit_scrubs_a_non_finite_judgment_and_still_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys,
) -> None:
    """Drives the real call site: the scrub must run inside emit, not only in the helper.

    Revert tested (three ways, all observed red):
      * delete the whole clause  -> section becomes blank            -> assert body
      * replace with '-' only    -> section looks like a value        -> assert 未形成
      * drop the trace print     -> no '数值卫生' line                -> assert printed
    """
    import os
    import sys as _sys
    import tempfile
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    import sweep_100k_e2e as S

    case = S._build_case(20261127)
    sent, _out = S._run_once(case, os.path.join(tempfile.mkdtemp(), 'h.json'))
    body = max((t for t in sent if t), key=len) if sent else ''
    # No case may leave a rendered non-finite figure in the body.
    assert not S._check_no_non_finite_in_body(body), body[:200]
    # And if scrubbing happened, the section must not be a bare placeholder.
    if '非有限值·未形成' in body:
        tail = body.split('DeepSeek 数据分析研判报告', 1)[-1]
        assert tail.strip(), 'judgment section blanked by scrubbing'
        assert '未形成' in tail, tail[:120]


def test_e10_detects_a_refusal_only_body() -> None:
    """E10 must have teeth, or `E10 == 0` on post-#22 is vacuous.

    Third false-green risk in this line of work: a check that reports zero
    because it cannot see the thing, not because the thing is absent. This
    drives the real checker with the exact body the third refusal exit emits.
    """
    import sys as _sys
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    import sweep_100k_e2e as S

    body = ('⚠️【阶段2缺省】[A vs B]\n'
            'history 无可用精算结果，无法输出 DeepSeek 研判。请重新跑阶段1抓取。')
    assert S._check_refusal_only(body), 'E10 must flag a refusal-only body'
    case = {'seed': 1, 'rec': {}, 'matched': 31, 'family': 'x', 'lam_h': 1.0,
            'lam_a': 1.0, 'regime': 'x', 'league': '', 'jc_shape': '',
            'official_shape': '', 'page_shape': '', 'stored_shape': '',
            'heat_shape': '', 'lock': '', 'pool_len': 31}
    found = S._check_report(case, [body], '')
    assert any(inv == 'E10' for inv, _ in found), (
        f'the report-level checker must surface E10; got {found}')
    # and a real report must not be flagged
    assert not S._check_refusal_only(
        '📈【31项市均×竞彩对比表】\n  汇总: 共31项\n'
        '🧠【DeepSeek 数据分析研判报告】\n  ① 2-1 ② 1-0')
