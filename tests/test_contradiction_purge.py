"""Contradiction-purge regression tests.

Every test here fails on the pre-purge code. Each one pins a structural removal
from `internal/2026-11-26-contradiction-ledger.md`:

  A  no recommendation spanning two different 1X2 outcomes
  B  no silent numeric fabrication on the analysis path
  C  a three-way tie is unresolved, never "draw"
  D  no hardcoded odds tables / mock candidate pools used as real data
  E  the complete 31-item tree never shrinks (aggregate buckets stay)
  F  no circular direction source

Constants in the expectations are hand-computed and spelled out inline.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import inspect
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_app():
    """Import the single-file app once per session (it is expensive)."""
    module = sys.modules.get('_purge_app')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location('_purge_app', ROOT / 'app_v84_hermes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_purge_app'] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    return module


def _module(name: str):
    return importlib.import_module(name)


# =====================================================================
# A. No recommendation spanning two opposing 1X2 directions
# =====================================================================

def test_deviation_prediction_never_locks_two_opposing_directions() -> None:
    """Hand-computed: `2-0` @ dev 300 is home, `1-1` @ dev 275 is draw.

    Old code took the top two by score, so it returned the pair
    (2-0 home, 1-1 draw) — a 主胜 + 平局 double pick. Same-direction locking
    means the second slot must also be a home win.
    """
    app = _load_app()
    engine = app.DeviationDrivenEngine
    # Hand-computed score = log1p(deviation) * ibi, dev must be > 200 to be TIER_1:
    #   4-0  dev 400, ibi 5.0 -> log1p(400)*5.0 = 29.98  (home)  <- highest
    #   1-1  dev 300, ibi 5.0 -> log1p(300)*5.0 = 28.52  (draw)  <- 2nd: the old pick
    #   3-0  dev 250, ibi 4.0 -> log1p(250)*4.0 = 22.11  (home)  <- 3rd: correct 2nd
    records = [
        {'option': '4-0', 'deviation': 400.0, 'ibi': 5.0, 'odds': 9.0},
        {'option': '1-1', 'deviation': 300.0, 'ibi': 5.0, 'odds': 6.8},
        {'option': '3-0', 'deviation': 250.0, 'ibi': 4.0, 'odds': 12.0},
    ]
    out = engine.build_prediction_output(records)
    assert out['available'] is True, out
    scores = [row['score'] for row in out['scores']]
    assert len(scores) == 2
    assert scores[0] != scores[1]

    def wdl(score: str) -> str:
        home, away = (int(p) for p in score.split('-', 1))
        return 'home' if home > away else ('away' if away > home else 'draw')

    assert wdl(scores[0]) == wdl(scores[1]), f'cross-direction lock: {scores}'
    assert scores == ['4-0', '3-0'], scores
    assert '1-1' not in scores


def test_deviation_prediction_refuses_when_one_direction_only_has_one() -> None:
    """Only one home candidate exists -> aborted, not padded with the draw bucket."""
    app = _load_app()
    records = [
        {'option': '2-0', 'deviation': 300.0, 'ibi': 3.0, 'odds': 7.0},
        {'option': '1-1', 'deviation': 275.0, 'ibi': 3.0, 'odds': 6.8},
    ]
    out = app.DeviationDrivenEngine.build_prediction_output(records)
    assert out['available'] is False
    assert out['scores'] == []
    assert out['reason'] == 'candidates_below_two_in_one_direction'


def test_ventropy_secondary_stays_in_the_primary_direction() -> None:
    """`2-0`(home) + `3-0`(home) both TIER_1; `1-1`(draw) ranks between them.

    Old code returned primary `2-0` / secondary `1-1` (home + draw).
    """
    app = _load_app()
    pool15 = [
        {'score': '2-0', 'theory': 5.0, 'market': 15.0},    # dev 1000, ibi 3.0
        {'score': '1-1', 'theory': 4.0, 'market': 12.0},    # dev 800,  ibi 3.0
        {'score': '3-0', 'theory': 3.0, 'market': 9.0},     # dev 600,  ibi 3.0
    ]
    ranked = [(p['score'], {'prob': p['theory']}) for p in pool15]
    with contextlib.redirect_stdout(io.StringIO()):
        result = app.run_ventropy_pipeline(ranked, pool15, 'TestLeague', {}, match_name='A vs B')
    assert result['primary'] == '2-0'
    assert result['secondary'] == '3-0', result
    # 本引擎不自报方向（方向只能来自 Step 1），只能断言两个比分的胜平负一致。
    assert result['primary'].split('-')[0] > result['primary'].split('-')[1]
    assert result['secondary'].split('-')[0] > result['secondary'].split('-')[1]


def test_ventropy_never_fills_secondary_from_another_tier_direction() -> None:
    """One home TIER_1 plus one draw TIER_2 -> secondary must be N/A."""
    app = _load_app()
    pool15 = [
        {'score': '2-0', 'theory': 5.0, 'market': 15.0},    # TIER_1 home
        {'score': '1-1', 'theory': 10.0, 'market': 5.0},    # dev -500, ibi 0.5 -> TIER_2 draw
    ]
    ranked = [(p['score'], {'prob': p['theory']}) for p in pool15]
    with contextlib.redirect_stdout(io.StringIO()):
        result = app.run_ventropy_pipeline(ranked, pool15, 'L', {}, match_name='A vs B')
    assert result['primary'] == '2-0'
    assert result['secondary'] == 'N/A', result


def test_comparison_report_is_a_risk_warning_with_no_pick_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruling 1: keep the two warning lines, but as pure risk statements.

    The output must still talk about the divergence (it must not go silent) and
    must not contain 首选 / 次选 / 双选 — no pick guidance of any kind.
    """
    app = _load_app()
    sent: list[str] = []
    monkeypatch.setattr(app, 'send_tg', lambda text, parse_mode=None: sent.append(str(text)))
    monkeypatch.setattr(app, 'load_json', lambda path: {
        'm1': {
            'status': 'pending', 'match': 'A vs B', 'predicted_score': '2-0',
            'second_score': '3-0', 'confidence': '中', 'dti': 0.1,
            'top15_scores': [['2-0', {'prob': 0.10}], ['3-0', {'prob': 0.095}]],
        },
        'm2': {
            'status': 'pending', 'match': 'C vs D', 'predicted_score': '1-1',
            'second_score': '0-0', 'confidence': '低', 'dti': 0.2,
            'top15_scores': [['1-1', {'prob': 0.09}], ['0-0', {'prob': 0.088}]],
        },
    })
    app.compare_matches()
    assert sent, 'report went silent — the risk warning must still be printed'
    body = sent[0]
    # still speaks about the risk, not silent
    assert '风险提示' in body
    assert '分歧' in body
    assert '不做推荐' in body
    # no pick guidance vocabulary at all
    for banned in ('首选', '次选', '双选', '防波堤', '保本', '对冲', '双边', '主次选'):
        assert banned not in body, f'{banned} still present in risk warning: {body}'


def test_comparison_report_never_offers_two_directions_for_one_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A match whose two scores straddle 1X2 must report the conflict, not offer both."""
    app = _load_app()
    sent: list[str] = []
    monkeypatch.setattr(app, 'send_tg', lambda text, parse_mode=None: sent.append(str(text)))
    monkeypatch.setattr(app, 'load_json', lambda path: {
        'm1': {
            'status': 'pending', 'match': 'A vs B', 'predicted_score': '2-0',
            'second_score': '1-1', 'confidence': '中', 'dti': 0.1,
            'top15_scores': [['2-0', {'prob': 0.10}], ['1-1', {'prob': 0.099}]],
        },
    })
    app.compare_matches()
    body = sent[0]
    assert '方向冲突' in body, body
    assert '不推荐' in body, body
    # neither direction may be presented as a pick
    assert '主胜: ' not in body and '平局: ' not in body


def test_comparison_report_single_direction_states_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _load_app()
    sent: list[str] = []
    monkeypatch.setattr(app, 'send_tg', lambda text, parse_mode=None: sent.append(str(text)))
    monkeypatch.setattr(app, 'load_json', lambda path: {
        'm1': {
            'status': 'pending', 'match': 'A vs B', 'predicted_score': '2-0',
            'second_score': '3-0', 'confidence': '中', 'dti': 0.1,
            'top15_scores': [['2-0', {'prob': 0.10}], ['3-0', {'prob': 0.095}]],
        },
    })
    app.compare_matches()
    body = sent[0]
    # the single direction is stated the same way in both warning blocks and
    # never paired with a second direction for the same match
    assert '方向冲突' not in body, body
    assert '平局' not in body and '客胜' not in body, body
    assert '主胜' in body


def test_market_defense_secondary_is_never_the_primary_key() -> None:
    """One surviving candidate must not be duplicated into both slots."""
    app = _load_app()
    analyzer = app.MarketDefenseInsightAnalyzer
    hot, theoretical, market_ranks = analyzer.build_market_inputs(
        ranked_scores=[('1-0', {'theory_rank': 1})],
        odds_correct_score={'1-0': 5.0},
    )
    result = analyzer().evaluate_trap_risk(2.5, 0.5, hot, theoretical, market_ranks, {})
    selection = result['adjusted_selection']
    assert selection['primary'] == '1-0'
    assert selection['secondary'] is None
    assert selection['secondary'] != selection['primary']


# =====================================================================
# B. No silent numeric fabrication
# =====================================================================

def test_convergence_to_float_has_no_default_parameter_at_all() -> None:
    """Ruling 2: the default is deleted, not merely changed to None.

    The signature must accept exactly one argument, so no caller can pass a
    fallback and silently resurrect the 2.5 behaviour.
    """
    arb = _module('convergence_arbitrator')
    assert list(inspect.signature(arb._to_float).parameters) == ['value']
    # missing value -> missing state, never a number
    assert arb._to_float(None) is None
    assert arb._to_float('not-a-number') is None
    assert arb._to_float('') is None
    assert arb._to_float(float('nan')) is None or arb._to_float(float('nan')) != 2.5
    # a real value still converts
    assert arb._to_float(2.75) == pytest.approx(2.75)
    # passing a fallback is a TypeError now, not a silent 2.5
    with pytest.raises(TypeError):
        arb._to_float(None, 2.5)


def test_ou_verdict_reports_missing_line_instead_of_assuming_2_5() -> None:
    """Hand-computed: (1+1, 2+1) averages 2.5 goals.
    Against an assumed 2.5 line that is a push ('走'); with no line at all the
    answer must be "line missing", never that push.
    """
    arb = _module('convergence_arbitrator')
    assert arb.ou_from_scores(['1-1', '2-1']) == ''
    missing = arb.ou_verdict(['1-1', '2-1'], None)
    assert missing['available'] is False
    assert 'ou_line' in missing['missing']
    assert missing['verdict'] == ''

    # with a real line the verdict is still produced
    assert arb.ou_verdict(['1-1', '2-1'], 2.5)['verdict'] == '走'
    assert arb.ou_verdict(['1-1', '2-1'], 2.0)['verdict'] == '大'
    assert arb.ou_verdict(['1-1', '2-1'], 3.0)['verdict'] == '小'


def test_missing_line_yields_a_missing_conclusion_not_a_2_5_verdict() -> None:
    """Ruling 2, the exact case named: no line must never produce 大/小/走.

    Hand-computed: (1-1) + (2-1) averages 2.5 goals. Against the old fabricated
    line of 2.5 that is a push, i.e. '走' — a settled verdict from an input that
    was never supplied. Every missing-line shape must instead report missing.
    """
    arb = _module('convergence_arbitrator')
    for missing_line in (None, ''):
        detail = arb.ou_verdict(['1-1', '2-1'], missing_line)
        assert detail['available'] is False, f'line={missing_line!r} produced a verdict'
        assert detail['verdict'] == '', detail
        assert 'ou_line' in detail['missing'], detail
        assert detail['line'] is None

    # the compatible entry point must not invent the verdict either
    assert arb.ou_from_scores(['1-1', '2-1']) == ''
    assert arb.ou_from_scores(['1-1', '2-1'], None) == ''
    # and never a 2.5-by-accident verdict through the extra-arg shape
    assert arb.ou_from_scores('1-1', '2-1', None) == ''


def test_resolve_cores_reports_missing_line_instead_of_assuming_2_5() -> None:
    """`resolve_cores` used to call `ou_from_scores(scores)` with no line at all,
    so its OU always came from the fabricated 2.5."""
    arb = _module('convergence_arbitrator')
    without_line = arb.resolve_cores(['1-1', '2-1'])
    assert without_line['ou'] == ''
    assert without_line['ou_available'] is False
    assert without_line['ou_missing'] == ['ou_line']
    assert without_line['ou_line'] is None

    with_line = arb.resolve_cores(['1-1', '2-1'], ou_line=2.5)
    assert with_line['ou'] == '走'
    assert with_line['ou_available'] is True
    assert with_line['ou_missing'] == []


def test_missing_value_yields_missing_state_not_2_5() -> None:
    """`_to_float` on absent/garbage values must return None — the old
    module-wide default returned 2.5 for all of them."""
    arb = _module('convergence_arbitrator')
    for missing in (None, '', 'abc', [], {}):
        assert arb._to_float(missing) is None, f'{missing!r} became a number'
    # the value that used to be indistinguishable from "missing"
    assert arb._to_float(2.5) == pytest.approx(2.5)


def test_ou_verdict_rejects_a_non_positive_line() -> None:
    arb = _module('convergence_arbitrator')
    zero = arb.ou_verdict(['1-1'], 0)
    assert zero['available'] is False and zero['verdict'] == ''
    assert 'ou_line' in zero['missing']


def test_ml_engine_refuses_to_invent_lambdas() -> None:
    """Poisson with no λ must be empty, not a normalized matrix around 1.2/0.9."""
    engine = _module('blade_ml_engine')
    assert engine.calc_bivariate_poisson_matrix(None, 'x') == {}
    assert engine.calc_bivariate_poisson_matrix(0, 0) == {}
    assert len(engine.calc_bivariate_poisson_matrix(1.6, 1.1, max_goals=4)) == 25


def test_ml_safe_float_reports_missing_rather_than_zero() -> None:
    engine = _module('blade_ml_engine')
    assert engine._safe_float(None) is None
    assert engine._safe_float('abc') is None
    assert engine._safe_float(float('nan')) is None
    assert engine._safe_float('1.75') == pytest.approx(1.75)


def test_ml_predict_reports_unavailable_without_lambda() -> None:
    engine = _module('blade_ml_engine')
    out = engine.predict_match_ml({})
    assert out['available'] is False
    assert out['used'] is False
    assert out['reason'] == 'lambda_not_formed'
    assert out['preferred_score'] == ''
    assert out['expected_total_goals'] is None


def test_ml_training_skips_rows_missing_market_inputs() -> None:
    """A history row without λ/OU/AH used to be trained as 1.2 / 0.9 / 2.5 / 0.0."""
    engine = _module('blade_ml_engine')
    bundle = engine.train_from_history({'a': {'actual_score': '2-1'}}, save=False)
    assert bundle['trained'] is False
    assert bundle['rows'] == 0
    assert 'home_mean' not in bundle and 'away_mean' not in bundle
    assert bundle['reason'] == 'no_usable_completed_scores'
    assert bundle['skipped_rows'][0]['missing'] == ['lam_h', 'lam_a', 'ou_line', 'ah_line']


def test_ml_training_keeps_rows_that_have_every_input() -> None:
    """Hand-computed means: one row 2-1 -> home_mean 2.0, away_mean 1.0."""
    engine = _module('blade_ml_engine')
    bundle = engine.train_from_history({'a': {
        'actual_score': '2-1', 'lam_h': 1.5, 'lam_a': 1.0,
        'ou_line': 2.5, 'asian_handicap': -0.5,
    }}, save=False)
    assert bundle['trained'] is True
    assert bundle['home_mean'] == pytest.approx(2.0)
    assert bundle['away_mean'] == pytest.approx(1.0)


def test_game_theory_does_not_invent_1x2_odds() -> None:
    """`_safe_odds(..., 3.0)` / `(..., 3.3)` fabricated a market on the core path.

    With no 1X2 market, the odds-driven game-theory models must be skipped and
    the gap must be an explicit None instead of |3.0 - 3.0| = 0.
    """
    app = _load_app()
    source = Path(app.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else '')
        if name != '_safe_odds':
            continue
        for arg in node.args[1:]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)) and not isinstance(arg.value, bool):
                offenders.append((node.lineno, arg.value))
    assert offenders == [], f'_safe_odds called with a fabricated default: {offenders}'


def test_market_matrix_renders_missing_as_dash_not_zero() -> None:
    pipe = _module('blade_deepseek_pipeline')
    lines = pipe.DeepSeekAnalyzer.format_mkt_hot_wdl_matrix_tg_lines(
        {'pos_rows': [{'score': '1-1', 'market': None, 'theory': None}], 'reason': ''}
    )
    row = next(line for line in lines if '1-1' in line)
    assert '市均-％' in row.replace('%', '％') or '市均-' in row
    assert '市均0.00' not in row and '理论0.00' not in row


def test_market_short_matrix_leaves_missing_gap_out_of_the_vector() -> None:
    """A missing Δ must not become a real 0.0 reading in the discrete vector."""
    pipe = _module('blade_deepseek_pipeline')
    matrix = pipe.DeepSeekAnalyzer.build_mkt_short_wdl_matrix(
        [{'score': '1-1'}, {'score': '2-0', 'delta': 0.02}]
    )
    assert '1-1' not in matrix['discrete_vector']
    assert matrix['discrete_vector']['2-0'] == pytest.approx(0.02)


# =====================================================================
# C. A tie is unresolved, never "draw"
# =====================================================================

def test_direction_vote_tie_is_unresolved_not_draw() -> None:
    """Hand-computed counts for ['1-1', '2-1']: 胜 1, 平 1, 负 0.

    The two leaders tie, so there is no unique direction. Old code took
    `max(counts, ...)` over a dict ordered 胜/平/负 and reported 平.
    """
    arb = _module('convergence_arbitrator')
    direction, resolution = arb.resolve_direction(['1-1', '2-1'])
    assert direction == ''
    assert resolution['tie'] is True
    assert set(resolution['tied_directions']) == {'胜', '平'}
    assert resolution['counts'] == {'胜': 1, '平': 1, '负': 0}


def test_three_way_tie_is_unresolved_not_draw() -> None:
    arb = _module('convergence_arbitrator')
    direction, resolution = arb.resolve_direction(['2-1', '1-1', '0-1'])
    assert direction == ''
    assert resolution['tie'] is True
    assert set(resolution['tied_directions']) == {'胜', '平', '负'}


def test_clear_majority_is_still_a_direction() -> None:
    arb = _module('convergence_arbitrator')
    direction, resolution = arb.resolve_direction(['2-1', '3-0', '1-1'])
    assert direction == '胜'
    assert resolution['tie'] is False


# =====================================================================
# D / E. No hardcoded odds tables; the 31-item tree never shrinks
# =====================================================================

MOCK_POOL_KEYS = frozenset(['3-2', '3-0', '1-0', '2-0'])


def test_no_mock_candidate_pool_is_ever_returned() -> None:
    """The banned shape is the mock 4-key per-channel pool from the V16.22 code.

    A 12-key enumeration of the JCZQ home-win options is not a mock pool, so the
    check requires the literal to be small (<= 8) as well as exactly these keys.
    """
    for name in ('app_v84_hermes.py', 'blade_deepseek_pipeline.py', 'blade_ml_engine.py',
                 'convergence_arbitrator.py', 'odds_fallback.py', 'sporttery_jc.py',
                 'odds_index_math.py', 'okooo_mobile_cs.py'):
        source = (ROOT / name).read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                continue
            values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            if not (4 <= len(values) <= 8) or not all(isinstance(v, str) for v in values):
                continue
            assert frozenset(values) != MOCK_POOL_KEYS, (
                f'{name}:{node.lineno} still holds the mock candidate pool {values}'
            )


def test_no_literal_31_item_odds_table_in_runtime_modules() -> None:
    """An odds table is a dict with >=25 score-shaped string keys and numeric values."""
    for name in ('blade_deepseek_pipeline.py', 'blade_ml_engine.py', 'convergence_arbitrator.py',
                 'odds_fallback.py', 'sporttery_jc.py', 'odds_index_math.py'):
        tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            score_keys = [
                k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
                and ('-' in k.value or '其它' in k.value or '其他' in k.value)
            ]
            numeric_values = [
                v for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) and not isinstance(v.value, bool)
            ]
            assert not (len(score_keys) >= 25 and len(numeric_values) >= 25), (
                f'{name}:{node.lineno} holds a literal {len(score_keys)}-item odds table'
            )


def test_market_defense_keeps_aggregate_buckets_in_the_tree() -> None:
    """`'其他' not in s` used to drop 胜其它/平其它/负其它 before ranking."""
    app = _load_app()
    analyzer = app.MarketDefenseInsightAnalyzer
    hot_list, theoretical, market_ranks = analyzer.build_market_inputs(
        ranked_scores=[('1-0', {'theory_rank': 1}), ('胜其它', {'theory_rank': 2}),
                       ('平其它', {'theory_rank': 3}), ('负其它', {'theory_rank': 4})],
        odds_correct_score={'1-0': 5.0, '胜其它': 20.0, '平其它': 30.0, '负其它': 40.0},
    )
    for bucket in ('胜其它', '平其它', '负其它'):
        assert bucket in hot_list, f'{bucket} dropped from the market hot list'
        assert bucket in market_ranks, f'{bucket} dropped from the market tree'
        assert bucket in theoretical, f'{bucket} dropped from the theory tree'


def test_jczq_all_options_still_has_exactly_31_items() -> None:
    """22 explicit + 9 jc-official stays frozen: 12+4+12 listed + 3 buckets."""
    pipe = _module('blade_deepseek_pipeline')
    assert len(pipe.JCZQ_ALL_OPTIONS) == 31
    assert len(set(pipe.JCZQ_ALL_OPTIONS)) == 31
    assert len(pipe.JCZQ_HOME_WIN_SCORES) == 12
    assert len(pipe.JCZQ_DRAW_SCORES) == 4
    assert len(pipe.JCZQ_AWAY_WIN_SCORES) == 12
    assert pipe.JCZQ_ALL_OPTIONS[-3:] == ['胜其它', '平其它', '负其它']


def test_cs31_extras_still_hold_the_three_extra_lines() -> None:
    """4-3 / 3-4 / 4-4 must stay in cs31_extras and out of the 31 rows."""
    pipe = _module('blade_deepseek_pipeline')
    pack = pipe.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [],
        extra_market_odds={'4-3': 50.0, '3-4': 60.0, '4-4': 80.0, '1-0': 5.0},
    )
    assert set(pack['cs31_extras']) == {'4-3', '3-4', '4-4'}
    assert pack['row_count'] == 31
    assert not ({'4-3', '3-4', '4-4'} & {row['score'] for row in pack['rows']})


# =====================================================================
# F. No circular direction source
# =====================================================================

def test_ventropy_direction_comes_from_the_picks_not_a_handicap_filter() -> None:
    """The direction reported by the router is the direction of its own primary pick,
    never a value derived by filtering candidate names through a handicap label
    and then being read back as the direction."""
    app = _load_app()
    pool15 = [
        {'score': '0-1', 'theory': 5.0, 'market': 15.0},   # away
        {'score': '0-2', 'theory': 3.0, 'market': 9.0},    # away
    ]
    ranked = [(p['score'], {'prob': p['theory']}) for p in pool15]
    with contextlib.redirect_stdout(io.StringIO()):
        result = app.run_ventropy_pipeline(ranked, pool15, 'L', {}, match_name='A vs B')
    assert result['primary'] == '0-1'
    assert result['secondary'] == '0-2'
    for pick in (result['primary'], result['secondary']):
        home, away = pick.split('-')
        assert away > home, f'{pick} is not an away win'


def test_deep_handicap_fallback_keeps_the_primary_direction() -> None:
    """Bait-fallback used to take `_clean_ranked[0]` regardless of side."""
    app = _load_app()
    pool15 = [
        {'score': '0-3', 'theory': 5.0, 'market': 15.0},   # away, will be marked bait
        {'score': '0-2', 'theory': 4.0, 'market': 12.0},   # away
        {'score': '3-0', 'theory': 3.0, 'market': 9.0},    # home (opposite side)
    ]
    ranked = [(p['score'], {'prob': p['theory']}) for p in pool15]
    rtm = {'ok': True, 'bait_scores': ['0-3'], 'anchor_scores': [], 'singularity_score_names': []}
    with contextlib.redirect_stdout(io.StringIO()):
        result = app.run_ventropy_pipeline(
            ranked, pool15, 'L', {}, match_name='A vs B', rtm_result=rtm, asian_handicap=-2.5,
        )
    assert result['primary'] == '0-2'
    if result['secondary'] != 'N/A':
        assert result['secondary'].split('-')[1] > result['secondary'].split('-')[0], result


# =====================================================================
# A (continued). Trap-warning block must not publish an opposing pick list
# =====================================================================

def test_trap_report_labels_the_bookie_side_as_non_recommendation() -> None:
    """The trap block used to print 「反向首选 / 反向次选」 — two bettable picks
    on the opposite side of the locked direction. It may only appear as a
    clearly-labelled risk note."""
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert '反向首选：' not in source
    assert '反向次选：' not in source
    assert '关注比分（庄家利益侧，非推荐方向）' in source


def test_allocation_output_has_no_money_fields() -> None:
    """`prioritize_allocation` / `evaluate_records` / `allocate_deviation_driven`
    used to emit bet_amount / allocation_pct / total_exposure for each pick."""
    app = _load_app()
    engine = app.DeviationDrivenEngine
    records = [
        {'option': '2-0', 'deviation': 300.0, 'ibi': 3.0, 'odds': 7.0},
        {'option': '3-0', 'deviation': 250.0, 'ibi': 4.0, 'odds': 12.0},
    ]
    decision = engine.evaluate_records(records, total_budget=1000.0)
    for banned in ('bet_amount', 'allocation_pct', 'allocation_weight'):
        for item in decision['allocations']:
            assert banned not in item, f'{banned} still emitted'
    for banned in ('total_budget', 'allocated_total'):
        assert banned not in decision, f'{banned} still emitted'

    ventropy = app.ShannonEntropyEngine.allocate_deviation_driven(
        [{'option': '2-0', 'theory': 5.0, 'market': 15.0}], total_budget=1000.0,
    )
    for banned in ('total_budget', 'allocated_total', 'total_exposure'):
        assert banned not in ventropy, f'{banned} still emitted by allocate_deviation_driven'
    for item in ventropy['allocations']:
        assert 'bet_amount' not in item
