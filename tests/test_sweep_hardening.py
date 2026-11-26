"""Regression tests for the defects the 10,000-case sweep found.

Each test is a minimal reproduction of one root cause. They are written so that
reverting the corresponding fix makes them fail for the documented reason, not by
some incidental error — see the revert proofs in
`/cursor/stores/.../internal/2026-11-26-10000-sweep-build.md`.

Defects covered:

  D1  `DeepSeekAnalyzer._classify_score_wdl` returned a bare string while every
      call site read `[1]`, so direction filtering silently did nothing.
  D2  `DeepSeekAnalyzer.snap_score_pair` accepted two positional args while 14
      call sites passed the lock payload as a third, so the lock never applied.
  D3  `TensorGravityReversalEngine.apply_gravity_reversal` dropped `其它` rows
      (and every row of a colon-format pool) via a bare `continue`.
  D4  `run_ventropy_pipeline` emitted a first/second pair in different
      directions because the two tiers were picked independently.
  D5  `build_score_market_heat_ranking` let NaN / inf / negative odds into the
      numeric fields of the heat ranking.
"""
from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe = importlib.import_module('blade_deepseek_pipeline')
okooo = importlib.import_module('okooo_mobile_cs')

JC = list(pipe.JCZQ_ALL_OPTIONS)
FISSURE_ON = {'triggered': True, 'macro_direction': 'home', 'counter_direction': 'away'}


def _jc_pool() -> list[dict[str, object]]:
    """The 31 JC options as an enriched pool, the shape gravity must preserve."""
    return [{'score': option, 'theory': 100.0 / 31, 'market': 100.0 / 31,
             'ibi': 1.0, 'delta': 0.0} for option in JC]


# --------------------------------------------------------------------------
# D1: `_classify_score_wdl` must return a (score, direction) pair
# --------------------------------------------------------------------------
def test_classify_score_wdl_returns_direction_at_index_one() -> None:
    """Every call site reads `[1]`; a bare string raised IndexError inside `except`."""
    result = pipe.DeepSeekAnalyzer._classify_score_wdl('1-0')
    assert isinstance(result, tuple), (
        f'_classify_score_wdl must return a pair, got {type(result).__name__}: {result!r}'
    )
    assert len(result) == 2, f'expected a 2-tuple, got {result!r}'
    assert result[1] == '胜'

    # the direction slot must be the label for all three outcomes plus 其它
    assert pipe.DeepSeekAnalyzer._classify_score_wdl('0-0')[1] == '平'
    assert pipe.DeepSeekAnalyzer._classify_score_wdl('0-1')[1] == '负'
    assert pipe.DeepSeekAnalyzer._classify_score_wdl('胜其它')[1] == '胜'
    assert pipe.DeepSeekAnalyzer._classify_score_wdl('负其它')[1] == '负'
    assert pipe.DeepSeekAnalyzer._classify_score_wdl('平其它')[1] == '平'
    # an unclassifiable score yields an empty direction, never an exception
    assert pipe.DeepSeekAnalyzer._classify_score_wdl('')[1] == ''


def test_classify_score_wdl_lets_the_lock_filter_do_its_job() -> None:
    """The filter is the production consumer: it must drop off-direction scores."""
    lock = {'direction_lock': {'locked': True, 'direction': '负',
                               'scores': ['0-1', '0-2']}}
    kept = app.filter_scores_to_direction_lock(['1-0', '2-0', '0-1', '0-2'], lock)
    assert kept == ['0-1', '0-2'], (
        f'a 负 lock must keep only away scores, kept={kept}'
    )


# --------------------------------------------------------------------------
# D2: `snap_score_pair` must accept the lock payload as a third argument
# --------------------------------------------------------------------------
def test_snap_score_pair_accepts_the_lock_payload() -> None:
    """14 call sites pass the payload third; the 2-arg signature raised TypeError."""
    lock = {'direction_lock': {'locked': True, 'direction': '负',
                               'scores': ['0-1', '0-2']}}
    pair = pipe.DeepSeekAnalyzer.snap_score_pair('1-0', '2-0', lock)
    assert isinstance(pair, tuple) and len(pair) == 2, pair
    assert pair == ('0-1', '0-2'), (
        f'a 负 lock must snap a 胜 pair onto the locked scores, got {pair}'
    )


def test_snap_score_pair_without_lock_only_normalises() -> None:
    pair = pipe.DeepSeekAnalyzer.snap_score_pair('1:0', '2:0')
    assert pair == ('1-0', '2-0'), pair


def test_snap_score_pair_drops_a_contradicting_score_when_lock_has_no_spare() -> None:
    lock = {'direction_lock': {'locked': True, 'direction': '平', 'scores': []}}
    pair = pipe.DeepSeekAnalyzer.snap_score_pair('1-0', '1-1', lock)
    assert pair == ('1-1', ''), (
        f'without a spare the off-direction score must be removed, got {pair}'
    )


# --------------------------------------------------------------------------
# D3: gravity reversal must not shorten the pool
# --------------------------------------------------------------------------
def test_gravity_reversal_keeps_the_other_buckets() -> None:
    pool = _jc_pool()
    out = app.TensorGravityReversalEngine().apply_gravity_reversal(pool, FISSURE_ON)
    loss = {str(r['score']) for r in pool} - {str(r['score']) for r in out}
    assert len(out) == len(pool), (
        f'pool must not shrink: in={len(pool)} out={len(out)} '
        f'lost={sorted(loss)}'
    )
    survivors = {row['score'] for row in out}
    for other in ('胜其它', '平其它', '负其它'):
        assert other in survivors, f'{other} was dropped from the recalibrated pool'


def test_gravity_reversal_keeps_colon_format_scores() -> None:
    """okooo's native score format is `1:0`; a parse that only knew `-` lost everything."""
    pool = [{'score': '1:0', 'theory': 3.2, 'market': 3.2, 'ibi': 1.0, 'delta': 0.0},
            {'score': '0:1', 'theory': 2.8, 'market': 2.6, 'ibi': 0.9, 'delta': -0.2}]
    out = app.TensorGravityReversalEngine().apply_gravity_reversal(pool, FISSURE_ON)
    assert len(out) == 2, f'colon-format pool shrank to {len(out)}: {out}'
    assert {row['score'] for row in out} == {'1:0', '0:1'}


def test_gravity_reversal_marks_rows_it_cannot_score() -> None:
    """Skipped rows must say why, not carry a None in the numeric field."""
    pool = _jc_pool()
    out = app.TensorGravityReversalEngine().apply_gravity_reversal(pool, FISSURE_ON)
    for row in out:
        if '其它' in row['score']:
            assert 'gravity_skipped' in row, f'{row["score"]} lost its skip reason'
            assert 'tactical_gravity_score' not in row, (
                f'{row["score"]} carries a tactical_gravity_score it cannot compute'
            )
        else:
            assert isinstance(row.get('tactical_gravity_score'), float)


# --------------------------------------------------------------------------
# D4: the first/second pair must share one direction
# --------------------------------------------------------------------------
def _pool_with_two_defense_tiers() -> list[dict[str, object]]:
    """One home and one away score both land in TIER_2 (deviation<0, ibi<1)."""
    pool = []
    for option in JC:
        theory = 100.0 / 31
        pool.append({'score': option, 'theory': theory, 'market': theory,
                     'ibi': 1.0, 'delta': 0.0})
    home = next(row for row in pool if row['score'] == '1-0')
    home['theory'], home['market'] = 50.0, 1.0
    away = next(row for row in pool if row['score'] == '0-1')
    away['theory'], away['market'] = 40.0, 1.0
    return pool


def test_ventropy_pair_stays_in_one_direction() -> None:
    result = app.run_ventropy_pipeline([], _pool_with_two_defense_tiers(), '英超', {})
    assert result is not None, 'ventropy returned None on a valid pool'
    primary = str(result['primary'])
    secondary = str(result['secondary'])
    assert primary == '1-0', f'expected the strongest tier as primary, got {primary}'
    if secondary != 'N/A':
        assert pipe._score_wdl(secondary) == pipe._score_wdl(primary), (
            f'first/second must share a direction: {primary} vs {secondary}'
        )


def test_ventropy_log_carries_no_direction_choice_wording() -> None:
    """The lock is direction-unique, so no 首选/次选/双选/强推/推荐 may appear."""
    result = app.run_ventropy_pipeline([], _pool_with_two_defense_tiers(), '英超', {})
    log = str((result or {}).get('log') or '')
    for word in ('首选', '次选', '双选', '强推', '推荐'):
        assert word not in log, f'log carries {word!r}: {log}'


# --------------------------------------------------------------------------
# D6: `matrix_direction_lock` must read the lock the app writes
# --------------------------------------------------------------------------
def test_matrix_direction_lock_reads_an_explicit_lock() -> None:
    """The stub returned `locked=False` for every payload, so all 15 filters were inert."""
    payload = {'direction_lock': {'locked': True, 'direction': '负',
                                  'scores': ['0-1', '0-2', '1-0']}}
    lock = pipe.DeepSeekAnalyzer.matrix_direction_lock(payload)
    assert 'locked' in lock, f'a locked payload must report a locked flag, got {lock}'
    assert lock['locked'] is True, f'a locked payload must report locked, got {lock}'
    assert lock['direction'] == '负'
    # off-direction scores inside the lock's own list are discarded
    assert lock['scores'] == ['0-1', '0-2'], lock['scores']


def test_matrix_direction_lock_reports_why_it_cannot_derive_one() -> None:
    """A 市短 matrix with no explicit lock has no documented rule; say so."""
    lock = pipe.DeepSeekAnalyzer.matrix_direction_lock(
        {'wdl_discrete_vector': {'1-0': 0.4}, 'mkt_short_wdl_matrix': {'counts': {}}},
    )
    assert lock.get('locked') is False, f'no rule exists, so nothing may lock: {lock}'
    assert lock.get('reason') == 'no_derivation_rule', lock


def test_matrix_direction_lock_ignores_a_non_wdl_direction() -> None:
    lock = pipe.DeepSeekAnalyzer.matrix_direction_lock(
        {'direction_lock': {'locked': True, 'direction': 'away'}},
    )
    assert lock.get('locked') is False, f'a non-WDL direction must not lock: {lock}'
    assert str(lock.get('reason') or '').startswith('direction_not_wdl'), lock


# --------------------------------------------------------------------------
# D5: heat ranking must not carry non-finite or negative odds
# --------------------------------------------------------------------------
def test_heat_ranking_rejects_non_finite_odds() -> None:
    for bad in (float('nan'), float('inf'), float('-inf')):
        heat = okooo.build_score_market_heat_ranking({'1-0': bad, '0:0': 17.21})
        for row in heat['ranking']:
            for key in ('odds', 'avg_odds', 'heat'):
                value = row.get(key)
                assert isinstance(value, float) and math.isfinite(value), (
                    f'{key} carried {value!r} for input {bad!r}'
                )
        flagged = [row for row in heat['ranking'] if row.get('odds_rejected')]
        assert flagged, f'no row recorded why {bad!r} was rejected'
        assert flagged[0]['odds_rejected'] == 'odds_not_finite'


def test_heat_ranking_rejects_negative_odds() -> None:
    heat = okooo.build_score_market_heat_ranking({'1-0': -5.0, '0:0': 17.21})
    rows = {row['score']: row for row in heat['ranking']}
    assert rows['1-0']['odds'] == 0.0
    assert rows['1-0']['odds_rejected'] == 'odds_negative'
    assert rows['0-0']['odds'] == 17.21, 'a healthy row must survive a bad sibling'


# --------------------------------------------------------------------------
# The sweep itself must be green and must not be vacuous
# --------------------------------------------------------------------------
def test_sweep_is_green_and_not_vacuous() -> None:
    """Run a reduced sweep inside pytest so a regression cannot rot silently.

    400 cases keeps the suite fast; `scripts/sweep_10000.py` runs the full 10,000
    on demand. The coverage floor is what makes this more than "nothing raised":
    each counter below has been observed non-zero on real data, and a zero means
    the check stopped having anything to test.
    """
    sweep = importlib.import_module('scripts.sweep_10000')
    failures, total, coverage = sweep.run_sweep(400)
    assert total == 400
    detail = f'n_failures={len(failures)} clusters={sweep._cluster(failures)!r}'
    assert not failures, f'the sweep found defects: {detail}\n' + '\n'.join(
        f'{f["invariant"]}: {f["detail"]} <- {f["case"]}' for f in failures[:5]
    )
    for name, floor in (
        ('conservation_checked', 400),
        ('determinism_checked', 400),
        ('pool_nonempty', 200),
        ('lock_matches_pool', 50),
        ('pair_both_directions', 50),
        ('pool_with_other_bucket', 20),
    ):
        assert coverage.counts[name] >= floor, (
            f'{name}={coverage.counts[name]} < {floor}: the sweep went vacuous. '
            f'coverage={coverage.report()}'
        )
