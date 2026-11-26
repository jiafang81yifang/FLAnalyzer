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
import re
import sys
from pathlib import Path
from unittest import mock

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
# D7: `compare_matches` is a report surface and must obey direction uniqueness
# --------------------------------------------------------------------------
def _compare_output(history: dict[str, dict[str, object]]) -> str:
    """Run `compare_matches` against a fixed history and return the TG text."""
    sent: list[str] = []
    with mock.patch.object(app, 'load_json', return_value=history), \
         mock.patch.object(app, 'send_tg', side_effect=lambda t, *a, **k: sent.append(t)):
        app.compare_matches()
    assert len(sent) == 1, f'compare_matches sent {len(sent)} messages, expected 1'
    return sent[0]


def _compare_record(name: str, first: str, second: str, p1: float, p2: float) -> dict[str, object]:
    return {
        'status': 'pending', 'match': name,
        'predicted_score': first, 'second_score': second,
        'confidence': 'high', 'dti': 0.5, 'cup_mode': '常规',
        'top15_scores': [(first, {'prob': p1}), (second, {'prob': p2})],
    }


def test_compare_matches_cross_direction_gives_no_single_pick() -> None:
    """When the two candidates differ in direction, no single candidate may be named.

    The original text named one anyway ("强推首选：... → 1-0") while its own
    second candidate was 0-1 — a 主胜 pick backed by a 客胜 runner-up.
    """
    history = {
        'm1': _compare_record('A vs B', '1-0', '0-1', 0.20, 0.10),
        'm2': _compare_record('C vs D', '2-0', '1-2', 0.18, 0.12),
    }
    text = _compare_output(history)

    for word in ('首选', '次选', '双选', '强推', '推荐'):
        assert word not in text, f'compare output carries {word!r}:\n{text}'

    # the fact must be stated, not silently dropped
    assert '方向不一致' in text, f'cross-direction fact missing:\n{text}'
    assert '不给出单一候选' in text, f'the no-single-pick statement is missing:\n{text}'
    # and no sentence may present one candidate as *the* answer
    for line in text.splitlines():
        stripped = line.strip()
        if '→' in stripped:
            raise AssertionError(f'a single-pick arrow survived: {stripped!r}')


def test_compare_matches_same_direction_reports_consistency() -> None:
    """Both candidates in one direction: the numbers and the fact are reported."""
    history = {
        'm1': _compare_record('A vs B', '2-1', '1-0', 0.20, 0.10),
        'm2': _compare_record('C vs D', '3-1', '2-0', 0.18, 0.12),
    }
    text = _compare_output(history)

    for word in ('首选', '次选', '双选', '强推', '推荐'):
        assert word not in text, f'compare output carries {word!r}:\n{text}'
    assert '方向一致' in text, f'same-direction fact missing:\n{text}'
    assert '2场中 2场两候选同向' in text, f'consistency count wrong:\n{text}'
    assert '10.0个百分点' in text, f'the probability gap is missing:\n{text}'
    assert '⚠️ 两候选方向不一致' not in text, f'false conflict reported:\n{text}'


def test_compare_command_reply_is_direction_neutral() -> None:
    """The `/比较` acknowledgement and the skill labels are part of the surface.

    The embedded runtime payload (one giant JSON line near the top) carries the
    whole sibling-module source, so it is stripped before scanning: what matters
    is the app's own compare surface, not a copy of another module.
    """
    source = Path(str(app.__file__)).read_text(encoding='utf-8')
    start = source.index('# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>')
    end = source.index('# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<')
    own_source = source[:start] + source[end:]

    checked = 0
    for line_no, line in enumerate(own_source.splitlines(), 1):
        if '比较' not in line and 'compare' not in line:
            continue
        checked += 1
        for word in ('首选', '次选', '双选', '强推', '推荐'):
            assert word not in line, (
                f'compare surface carries {word!r} at L{line_no}: {line.strip()[:120]}'
            )
    assert checked >= 4, f'only scanned {checked} compare lines; the scan went vacuous'
    assert '比分方向一致性对比' in own_source


def test_every_report_surface_is_free_of_direction_wording() -> None:
    """Scan every rendered surface, `/比较` included, for banned wording.

    The scan drives the real report builders and inspects their output, so it
    catches wording that a source-text grep would miss (f-strings, assembled
    lines) and does not false-positive on comments or LLM prompts.
    """
    scan = importlib.import_module('scripts.scan_report_surfaces')
    results = scan.scan()
    dirty = {name: hits for name, hits in results if hits}
    assert not dirty, f'report surfaces carry direction wording: {dirty}'
    assert len(results) >= 9, f'only scanned {len(results)} surfaces'
    names = {name for name, _ in results}
    for required in ('compare[cross]', 'compare[same]', 'game_theory_15to2',
                     'battle_review', 'unified_report', 'theo_mkt_31_tg'):
        assert required in names, f'{required} missing from the scan: {sorted(names)}'


# --------------------------------------------------------------------------
# D8: an over-range integer literal must be handled, not crash
# --------------------------------------------------------------------------
OVER = 10 ** 400  # what `json.loads` produces from a 400-digit scrape field


def test_heat_ranking_survives_an_over_range_int() -> None:
    """`float(10**400)` raises OverflowError, which the parser did not catch."""
    heat = okooo.build_score_market_heat_ranking({'1:0': OVER, '0:0': OVER})
    rows = heat['ranking']
    assert rows, 'an over-range int must still yield a row, not an empty ranking'
    for row in rows:
        for key in ('odds', 'avg_odds', 'heat'):
            value = row.get(key)
            assert isinstance(value, float) and math.isfinite(value), (
                f'{key} carried {value!r} for an over-range int'
            )
    assert any(r.get('odds_rejected') == 'odds_not_finite' for r in rows), (
        f'an over-range int must record why it was rejected: {rows}'
    )


def test_theo_mkt_pack_survives_an_over_range_int() -> None:
    pack = pipe.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=1.4, lam_away=1.1, jc_score_odds={'1-0': OVER})
    assert pack['row_count'] == 31
    for key in ('market_sum', 'jc_pct_sum'):
        value = pack[key]
        assert isinstance(value, (int, float)) and math.isfinite(float(value)), (
            f'{key} carried {value!r} for an over-range int'
        )
    assert pack['market_sum'] == 0, (
        f'an unrepresentable price must not contribute market mass: {pack["market_sum"]}'
    )


def test_pool31_rows_survive_an_over_range_int() -> None:
    for kwargs in ({'odds_correct_score': {'1-0': OVER}},
                   {'jc_score_odds': {'1-0': OVER}}):
        rows = app.build_pool31_option_rows([], lam_home=1.5, lam_away=1.1, **kwargs)
        assert len(rows) == 31
        row = next(r for r in rows if r['score'] == '1-0')
        assert row['market'] is None or math.isfinite(float(row['market'])), (
            f'market carried {row["market"]!r} for {list(kwargs)}'
        )


def test_over_range_int_string_form_is_also_handled() -> None:
    """`str(10**400)` parses to inf via float(), a different path from the int."""
    heat = okooo.build_score_market_heat_ranking({'1:0': str(OVER)})
    row = heat['ranking'][0]
    assert row['odds'] == 0.0
    assert row['odds_rejected'] == 'odds_not_finite'


def test_negative_over_range_int_is_handled() -> None:
    heat = okooo.build_score_market_heat_ranking({'1:0': -OVER})
    row = heat['ranking'][0]
    assert row['odds'] == 0.0
    assert row['odds_rejected'] == 'odds_not_finite'


# --------------------------------------------------------------------------
# D9: no surface may store or render a pair of opposite directions
# --------------------------------------------------------------------------
def test_extract_dual_rejects_a_cross_direction_pair() -> None:
    """`extract_dual` used to regex out the first two scores with no direction check."""
    got = pipe.DeepSeekAnalyzer.extract_dual_scores_from_judgment_text('①2-0 ②1-2')
    assert got['first'] == '2-0'
    assert got['second'] == '', f'a cross-direction second must be dropped, got {got["second"]!r}'
    assert got['second_reason'] == 'direction_mismatch', got


def test_extract_dual_snaps_to_a_locked_spare_when_one_exists() -> None:
    """When the lock carries a same-direction alternative, use it — never invent one."""
    lock = {'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-0', '3-0']}}
    got = pipe.DeepSeekAnalyzer.extract_dual_scores_from_judgment_text('①2-0 ②1-2', lock)
    assert got['first'] == '2-0'
    assert got['second'] == '3-0', f'expected the locked spare, got {got["second"]!r}'
    assert got['second_reason'] == 'direction_mismatch_snapped_to_lock'


def test_extract_dual_ignores_a_lock_that_points_the_other_way() -> None:
    lock = {'direction_lock': {'locked': True, 'direction': '负', 'scores': ['0-1']}}
    got = pipe.DeepSeekAnalyzer.extract_dual_scores_from_judgment_text('①2-0 ②1-2', lock)
    assert got['second'] == '', 'a lock pointing away from the first candidate offers no spare'
    assert got['second_reason'] == 'direction_mismatch'


def test_extract_dual_keeps_a_same_direction_pair() -> None:
    got = pipe.DeepSeekAnalyzer.extract_dual_scores_from_judgment_text('①2-1 ②1-0')
    assert (got['first'], got['second']) == ('2-1', '1-0')
    assert got['second_reason'] == '', 'a valid pair must not be marked'


def _verdict(data: dict[str, object]) -> str:
    report = app.build_unified_analysis_report(data, source='test')
    return str(next(s.get('verdict') for s in report['sections'] if s['key'] == 'verdict'))


COMMON = {'match': 'A vs B', 'lam_h': 1.45, 'lam_a': 1.1,
          'correct_score_available': True, 'mobile_market_ready': True}


def test_verdict_states_a_conflict_instead_of_naming_a_direction() -> None:
    """It used to print `候选A 2-0｜候选B 1-2｜方向 未形成` — a pair with no verdict."""
    text = _verdict({**COMMON, 'predicted_score': '2-0', 'second_score': '1-2'})
    assert '方向不一致' in text, text
    assert '不给出单一候选' in text, text
    assert not re.search(r'方向\s*[胜平负]', text), (
        f'a contradictory pair must not state a direction: {text}'
    )


def test_verdict_does_not_let_a_lock_contradict_the_candidates() -> None:
    """The lock used to overwrite `direction`, yielding `候选A 2-0｜方向 负`."""
    text = _verdict({**COMMON, 'predicted_score': '2-0', 'second_score': '3-0',
                     'direction_lock': {'locked': True, 'direction': '负'}})
    stated = re.search(r'方向\s*([胜平负])', text)
    assert stated, f'the verdict should still state the candidates’ own direction: {text}'
    assert stated.group(1) == '胜', (
        f'a 主胜 pair must not be labelled 方向{stated.group(1)}: {text}'
    )
    assert '与候选方向' in text and '不一致' in text, (
        f'the lock conflict must be stated explicitly: {text}'
    )


def test_verdict_reports_a_cross_pair_even_with_a_agreeing_lock() -> None:
    text = _verdict({**COMMON, 'predicted_score': '2-0', 'second_score': '1-2',
                     'direction_lock': {'locked': True, 'direction': '胜'}})
    assert '方向不一致' in text, text
    assert '不给出单一候选' in text, text
    assert not re.search(r'方向\s*[胜平负]', text), text


def test_verdict_explains_an_absent_second_candidate() -> None:
    text = _verdict({**COMMON, 'predicted_score': '2-1', 'second_score': '',
                     'second_score_absent_reason': 'direction_mismatch'})
    assert '候选B 未形成' in text, text
    assert '方向不一致' in text, text


def test_report_surface_scan_catches_direction_contradictions() -> None:
    """The scanner must judge direction, not just wording.

    Feeding it a surface that is wording-clean but renders a contradictory pair
    has to come back dirty — that is the gap the wording-only scan left.
    """
    scan = importlib.import_module('scripts.scan_report_surfaces')
    fake: list[tuple[str, dict[str, int]]] = []
    scan._record('probe',
                 '候选A 2-0｜候选B 1-2｜方向 未形成｜置信度 中偏高（70.0%）', fake)
    assert fake and fake[0][1], 'a cross-direction pair with no conflict statement must be dirty'
    assert 'pair_cross_direction' in fake[0][1], fake

    fake2: list[tuple[str, dict[str, int]]] = []
    scan._record('probe2', '候选A 2-0｜候选B 3-0｜方向 负｜置信度 中偏高（70.0%）', fake2)
    assert 'stated_direction_contradicts_pair' in fake2[0][1], fake2

    fake3: list[tuple[str, dict[str, int]]] = []
    scan._record('probe3',
                 '候选A 2-0｜候选B 1-2｜两候选方向不一致（2-0为胜，1-2为负），'
                 '不能作为同一注的两个候选；此处不给出单一候选', fake3)
    assert not fake3[0][1], f'a stated conflict is clean: {fake3}'


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
        # the `/比较` surface must be exercised in every case, and both its
        # cross-direction and same-direction branches must actually appear
        ('compare_surface_checked', 400),
        ('compare_reported_cross_direction', 20),
        ('compare_reported_same_direction', 20),
        # the over-range-int probe runs once per case
        ('overflow_probe_checked', 400),
        ('overflow_ok', 1200),
        # the landing path (`extract_dual` -> record -> report) runs per case,
        # and its same-direction branch must actually be exercised
        ('landing_path_checked', 400),
        ('landing_same_direction', 1200),
    ):
        assert coverage.counts[name] >= floor, (
            f'{name}={coverage.counts[name]} < {floor}: the sweep went vacuous. '
            f'coverage={coverage.report()}'
        )
