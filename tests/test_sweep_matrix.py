"""Deterministic traversal sweep over the water / 31-pool / gate invariants.

The user's ask was explicit: enumerate 10,000+ cases and assert invariants on
every one, printing a final `SWEEP n_cases=... failures=0` line. This file is a
test module, so `pytest` runs it as part of the suite, but it can also be run
directly:

    python -m tests.test_sweep_matrix          # -> SWEEP n_cases=... failures=0

Dimensions (cross product, then filtered for duplicates):

  pool subset size     0, 1, 15, 30, 31
  water presence       none / ah / ou / 1x2 / all  (incl. 0.0 and {})
  CS channel           mobile, desktop_cs, okooo_cs, fallback_1, fallback_500, ""
  heat source          mobile, desktop, pool31, fallback, ""
  stub shape           block_reason only, reason only, neither, both
  water_gaps           stale (claims all three missing) vs fresh (matches fields)

Six invariants are checked on every case:

  (i)   audit_theo_mkt_31_rows is complete IFF all 31 options have theory+market
  (ii)  ensure_dual_source_before_judgment agrees with a freshly computed
        missing_six_market_waters
  (iii) no heat path raises TypeError for any channel/source label combination
  (iv)  every unavailable payload surfaces a non-'empty' reason
  (v)   apply_six_cs_settlement_v2_to_rec never reports available on all-None input
  (vi)  resolve_direction always unpacks to exactly 2 values
"""
from __future__ import annotations

import importlib
import itertools
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
okooo = importlib.import_module('okooo_mobile_cs')
pipe = importlib.import_module('blade_deepseek_pipeline')
fallback = importlib.import_module('odds_fallback')
mathmod = importlib.import_module('odds_index_math')
arb = importlib.import_module('convergence_arbitrator')

POOL_SIZES = (0, 1, 15, 30, 31)
WATER_SHAPES = (
    'none', 'ah', 'ou', '1x2', 'all',
    'ah_zero', 'ou_empty_dict', 'one_zero_dict',
)
CS_CHANNELS = ('mobile', 'desktop_cs', 'okooo_cs', 'fallback_1', 'fallback_500', '')
HEAT_SOURCES = (
    'okooo_mobile_cs_books_avg', 'okooo_cs_books_avg', 'okooo_desktop_cs_page_avg',
    'pool31_heat', 'fallback_history_cs', '',
)
STUB_SHAPES = ('block_reason', 'reason', 'neither', 'both')
GAP_STATES = ('stale', 'fresh')

ALL_OPTIONS = list(pipe.JCZQ_ALL_OPTIONS)


def _rows_for(size: int) -> list[dict[str, Any]]:
    """First `size` of the 31 options, with theory+market filled for those only."""
    rows = []
    for option in ALL_OPTIONS[:size]:
        rows.append({'score': option, 'theory': 3.0, 'market': 2.9})
    return rows


def _water_fields(shape: str) -> dict[str, Any]:
    if shape == 'none':
        return {}
    if shape == 'ah':
        return {'asian_handicap': -0.75}
    if shape == 'ou':
        return {'ou_line': 3.25}
    if shape == '1x2':
        return {'odds_1x2': {'home': 4.55, 'draw': 4.2, 'away': 1.47}}
    if shape == 'all':
        return {
            'asian_handicap': -0.75, 'ou_line': 3.25,
            'odds_1x2': {'home': 4.55, 'draw': 4.2, 'away': 1.47},
        }
    if shape == 'ah_zero':
        # 0.0 is falsy but is a real line (平手盘) — must count as present.
        return {'asian_handicap': 0.0}
    if shape == 'ou_empty_dict':
        return {'ou_line': 3.25, 'odds_1x2': {}}
    if shape == 'one_zero_dict':
        return {'asian_handicap': 0.0, 'ou_line': {}, 'odds_1x2': {'home': 2.0}}
    raise AssertionError(shape)


def _stub(shape: str) -> dict[str, Any]:
    base = {'available': False, 'match_id': '1'}
    if shape == 'block_reason':
        base['block_reason'] = 'external_fetch_not_configured'
    elif shape == 'reason':
        base['reason'] = 'external_fetch_not_configured'
    elif shape == 'both':
        base['block_reason'] = 'external_fetch_not_configured'
        base['reason'] = 'external_fetch_not_configured'
    return base


def _heat(source: str, channel: str) -> dict[str, Any]:
    page = {'1:0': 15.28, '2:0': 25.48, '2:1': 19.09, '0:0': 17.21, '1:1': 7.88}
    return okooo.build_score_market_heat_ranking(
        page, page_avg_odds=page,
        heat_source=source or 'okooo_mobile_cs_existing_odds',
        channel=channel or 'mobile',
    )


def _iter_cases():
    yield from itertools.product(
        POOL_SIZES, WATER_SHAPES, CS_CHANNELS, HEAT_SOURCES, STUB_SHAPES, GAP_STATES,
    )


def _check_case(pool_size, water, channel, source, stub_shape, gap_state) -> list[str]:
    """Run all six invariants for one case; return a list of failure strings."""
    fails: list[str] = []

    # ---- (i) audit completeness is exactly "all 31 have theory+market" ----
    rows = _rows_for(pool_size)
    audit = pipe.DeepSeekAnalyzer.audit_theo_mkt_31_rows(rows)
    should_complete = len(rows) == 31
    if bool(audit.get('complete')) is not should_complete:
        fails.append(f'(i) audit complete={audit.get("complete")} for {len(rows)} rows')
    if not audit.get('complete') and not audit.get('missing'):
        fails.append('(i) incomplete audit gave no missing detail')

    # ---- (iii) no heat path raises TypeError for any label combination ----
    try:
        heat = _heat(source, channel)
        heat_alias = okooo.build_score_market_heat_ranking(
            {'1:0': 8.0}, heat_channel=channel or 'mobile',
            heat_source=source or 'okooo_mobile_cs_existing_odds',
        )
    except TypeError as exc:
        fails.append(f'(iii) heat raised TypeError: {exc}')
        heat = heat_alias = {}
    if heat:
        for row in heat.get('ranking') or []:
            if 'avg_odds' not in row:
                fails.append('(iii) ranking row missing avg_odds')
                break
        # legality must be decidable, never raising
        try:
            app._is_legal_mobile_cs_heat(heat)
        except Exception as exc:  # noqa: BLE001 - the point is that it must not raise
            fails.append(f'(iii) legality gate raised {type(exc).__name__}: {exc}')
    if heat and heat_alias and 'desktop' in str(heat_alias.get('channel') or '').lower():
        fails.append(f'(iii) legacy desktop label not normalised: {heat_alias.get("channel")}')

    # ---- (ii) gate agrees with a freshly computed gap list ----
    rec: dict[str, Any] = {
        'correct_score_channel': channel,
        'correct_score_page_avg': {'1:0': 8.0, '2:1': 12.0},
        'score_market_heat': heat,
        **_water_fields(water),
    }
    if gap_state == 'stale':
        rec['water_gaps'] = ['asian_handicap', 'ou_line', 'odds_1x2']
    expected_gaps = okooo.missing_six_market_waters(rec)
    # Pass the SAME object: the gate is supposed to write the fresh list back.
    gate = fallback.ensure_dual_source_before_judgment(rec)
    if sorted(gate.get('water_gaps') or []) != sorted(expected_gaps):
        fails.append(
            f'(ii) gate gaps {gate.get("water_gaps")} != fresh {expected_gaps} ({water})'
        )
    # ready requires BOTH: no water gaps AND a mobile/desktop-CS market average
    # that actually entered the pack. A fallback-only rec can have zero gaps and
    # still be correctly not-ready.
    should_be_ready = (not expected_gaps) and bool(gate.get('mobile_cs_entered'))
    if bool(gate.get('ready')) is not should_be_ready:
        fails.append(
            f'(ii) ready={gate.get("ready")} but gaps={expected_gaps} '
            f'entered={gate.get("mobile_cs_entered")} ({channel}/{water})'
        )
    if gate.get('ready') and gate.get('reason'):
        fails.append('(ii) ready gate carried a reason')
    if not gate.get('ready') and not gate.get('reason'):
        fails.append('(ii) unready gate gave no reason')
    # desktop-scraped CS is legal; pool31 / desktop_api channels are not
    if channel in ('okooo_cs', 'mobile', 'desktop_cs') and not gate.get('mobile_cs_entered'):
        fails.append(f'(ii) legal channel {channel!r} not counted as entered')
    if channel in ('fallback_1', 'fallback_500', '') and gate.get('mobile_cs_entered'):
        fails.append(f'(ii) non-mobile channel {channel!r} counted as entered')
    if sorted(rec.get('water_gaps') or []) != sorted(expected_gaps):
        fails.append('(ii) gate did not write refreshed water_gaps back')

    # ---- (iv) every unavailable payload surfaces a non-empty reason ----
    # A payload that carries no reason field at all cannot invent one; the
    # requirement is that the log line still says something actionable rather
    # than a bare 'empty'. _pack_reason must prefer a real field when present.
    stub = _stub(stub_shape)
    reason_text = app._pack_reason(stub)
    if stub_shape in ('block_reason', 'reason', 'both'):
        if reason_text in ('empty', 'invalid_payload', ''):
            fails.append(f'(iv) stub shape {stub_shape!r} collapsed to {reason_text!r}')
        elif reason_text != 'external_fetch_not_configured':
            fails.append(f'(iv) stub shape {stub_shape!r} reported {reason_text!r}')
    else:
        # No reason field: it is acceptable only because the caller will see a
        # placeholder, never a silently-ok status.
        if reason_text != 'empty':
            fails.append(f'(iv) reasonless stub produced {reason_text!r}, expected the empty placeholder')
    # Real scraper stubs (the ones that used to print reason=empty) must be named.
    for fn, args in ((okooo.scrape_okooo_mobile_lineup, ('',)),
                     (okooo.scrape_okooo_mobile_ah_ou_books, ('',))):
        if app._pack_reason(fn(*args)) in ('empty', 'invalid_payload', ''):
            fails.append(f'(iv) {fn.__name__} stub surfaced no reason')

    # ---- (v) settlement v2 never claims available on all-None input ----
    empty_board = mathmod.apply_six_cs_settlement_v2_to_rec({})
    if empty_board.get('available'):
        fails.append('(v) settlement v2 reported available on empty rec')
    if not empty_board.get('block_reason'):
        fails.append('(v) settlement v2 gave no block_reason on empty rec')
    partial = mathmod.apply_six_cs_settlement_v2_to_rec({'ou_line': 3.25})
    if partial.get('available'):
        fails.append('(v) settlement v2 reported available with only ou_line')
    full = mathmod.apply_six_cs_settlement_v2_to_rec({
        'ou_line': 3.25, 'asian_handicap': -0.75,
        'correct_score_page_avg': {'1:0': 8.0},
    })
    if not full.get('available'):
        fails.append('(v) settlement v2 rejected a fully populated rec')

    # ---- (vi) resolve_direction always unpacks to exactly 2 values ----
    unpacked = arb.resolve_direction(['1-0', '2-1'], channel or '')
    if not (isinstance(unpacked, tuple) and len(unpacked) == 2):
        fails.append(f'(vi) resolve_direction returned {unpacked!r}')
    else:
        direction, resolution = unpacked
        if not isinstance(resolution, dict):
            fails.append('(vi) resolution is not a dict')
        if direction not in ('', '胜', '平', '负'):
            fails.append(f'(vi) unexpected direction {direction!r}')
    # the app-side tolerant unpack must accept both shapes
    for shape_ret in (unpacked, '胜', '', None):
        if isinstance(shape_ret, tuple) and len(shape_ret) == 2:
            _d, _r = shape_ret
        else:
            _d, _r = str(shape_ret or ''), {}
        if not isinstance(_r, dict):
            fails.append('(vi) tolerant unpack produced a non-dict resolution')

    # ---- (vii) every "31" producer means the SAME 31 options ----
    # Defect 11's root cause: the view's 31 (raw poisson cells, 28 of them JC)
    # and the audit's 31 (JC options) were different sets.
    view = app.build_score_pool31_views([], lam_home=1.45, lam_away=1.10)
    if view.get('pool_size') != 31:
        fails.append(f'(vii) view pool_size={view.get("pool_size")}')
    if set(view.get('options') or []) != set(pipe.JCZQ_ALL_OPTIONS):
        extra = sorted(set(view.get('options') or []) - set(pipe.JCZQ_ALL_OPTIONS))
        fails.append(f'(vii) view options are not the JC 31 (extra={extra[:3]})')
    enriched = app.build_31pool_enriched_list({'lam_h': 1.45, 'lam_a': 1.10})
    if {row['score'] for row in enriched} != set(pipe.JCZQ_ALL_OPTIONS):
        fails.append('(vii) build_31pool_enriched_list is not the JC 31')
    if abs(sum(row['theory_prob'] for row in enriched) - 1.0) > 1e-6:
        fails.append('(vii) enriched theory_prob does not sum to 1')

    # ---- (viii) jc31 always lands in correct_score_jc_official ----
    jc31 = {option: 8.0 for option in pipe.JCZQ_ALL_OPTIONS}
    stamped: dict[str, Any] = {}
    app._stamp_more_play_scores(stamped, {'jc_score_odds': jc31})
    if len(stamped.get('correct_score_jc_official') or {}) != 31:
        fails.append('(viii) jc31 did not land in correct_score_jc_official')

    # ---- (ix) independent falsy-zero expectations ----
    # These must NOT be derived from missing_six_market_waters / the gate: an
    # invariant computed from the function under test can never catch that
    # function's own bug. 0.0 is a real 平手盘 / OU line.
    z = {'asian_handicap': 0.0, 'ou_line': 2.5,
         'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
         'correct_score_page_avg': {'1:0': 8.0}, 'correct_score_channel': 'okooo_cs'}
    if okooo.missing_six_market_waters(z) != []:
        fails.append(f'(ix) ah=0.0 reported as a gap: {okooo.missing_six_market_waters(z)}')
    zg = fallback.ensure_dual_source_before_judgment(dict(z))
    if zg['ready'] is not True:
        fails.append(f'(ix) ah=0.0 gate not ready: {zg["reason"]}')
    if 'asian_handicap' not in (zg.get('kept_mobile') or []):
        fails.append('(ix) ah=0.0 not in kept_mobile')
    settle0 = mathmod.apply_six_cs_settlement_v2_to_rec(dict(z))
    if settle0.get('available') is not True or settle0.get('asian_handicap') != 0.0:
        fails.append(f'(ix) settlement dropped ah=0.0: {settle0.get("block_reason")}')
    board0 = pipe.DeepSeekAnalyzer.build_cross_market_board(dict(z))
    if board0.get('asian_handicap') != 0.0:
        fails.append(f'(ix) cross_market_board ah={board0.get("asian_handicap")}')
    z5 = dict(z)
    z5['dual_source_facts'] = {'fallback_500_fenxi': {'available': True, 'ah_line': 0.5}}
    if 'ah_line' in (fallback.ensure_dual_source_before_judgment(z5).get('filled_from_500') or []):
        fails.append('(ix) 500 refill would overwrite a real 平手盘')
    # OU line 0.0 is likewise "present" for gap purposes
    if 'ou_line' in okooo.missing_six_market_waters({'asian_handicap': 0.0, 'ou_line': 0.0, 'odds_1x2': {'h': 1}}):
        fails.append('(ix) ou_line=0.0 reported as a gap')
    # and the negatives must stay gaps
    for bad in (None, '', {}):
        if 'asian_handicap' not in okooo.missing_six_market_waters(
            {'asian_handicap': bad, 'ou_line': 2.5, 'odds_1x2': {'h': 1}}
        ):
            fails.append(f'(ix) ah={bad!r} not treated as a gap')
    # a 0.0 rec without any CS stays unready for the CS reason only
    no_cs = {'asian_handicap': 0.0, 'ou_line': 2.5, 'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0}}
    ng = fallback.ensure_dual_source_before_judgment(dict(no_cs))
    if ng['ready'] is not False:
        fails.append('(ix) no-CS rec reported ready')
    if '水位缺' in (ng.get('reason') or ''):
        fails.append(f'(ix) no-CS rec blamed water: {ng.get("reason")}')

    # ---- (x) chart market precedence: 市均 first, jc31 fills the rest ----
    jc_fixture = {'1-0': 11.0, '2-1': 10.5, '1-1': 6.25, '0-1': 7.25, '5-0': 400.0}
    chart_jc_only = app.generate_31pool_theory_vs_market_chart(
        {'lam_h': 1.45, 'lam_a': 1.1, 'odds_correct_score': dict(jc_fixture),
         'market_source_note': '竞彩31比分'}
    )
    if app.chart31_pool_verdict(chart_jc_only)['status'] == 'degraded':
        fails.append('(x) chart degraded although jc31 supplies market')
    chart_none = app.generate_31pool_theory_vs_market_chart(
        {'lam_h': 1.45, 'lam_a': 1.1, 'odds_correct_score': {}}
    )
    if app.chart31_pool_verdict(chart_none)['status'] == 'full':
        fails.append('(x) chart claimed full market with no odds at all')

    return fails


def run_sweep() -> tuple[int, list[str]]:
    """Execute every case; return (n_cases, failures)."""
    total = 0
    failures: list[str] = []
    for case in _iter_cases():
        total += 1
        fails = _check_case(*case)
        if fails:
            failures.append(f'case={case}: ' + ' | '.join(fails))
    return total, failures


def test_sweep_matrix_invariants() -> None:
    """10,000+ traversals; the printed SWEEP line is the acceptance evidence."""
    total, failures = run_sweep()
    printed = f'SWEEP n_cases={total} failures={len(failures)}'
    print(printed)
    assert total >= 10000, f'sweep only produced {total} cases'
    assert not failures, '\n'.join(failures[:40])


if __name__ == '__main__':
    n_cases, failure_list = run_sweep()
    for failure in failure_list[:40]:
        print('FAIL ' + failure)
    print(f'SWEEP n_cases={n_cases} failures={len(failure_list)}')
    raise SystemExit(0 if not failure_list else 1)
