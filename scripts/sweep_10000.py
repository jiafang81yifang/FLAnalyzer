"""10,000-case randomized sweep over the production analysis shapes.

Every case is built from the *real* producer chain, never from a hand-written
fixture:

  prob_dict  = calc_poisson_prob(lam_h, lam_a, max_goals=8)   # 36 or fewer items
  ranked     = build_probability_rankings(prob_dict)          # full matrix
  pool31     = ranked[:31]                                    # `_pool15_enriched`

Dimensions (sampled independently per case, so the sample is not a grid):

  lambda home/away   0.15 .. 4.5 continuous, five regimes (balanced / home-heavy /
                     away-heavy / both-high / both-low)
  odds               normal / stretched / compressed / missing keys /
                     0 / negative / huge / non-numeric / NaN / inf
  league             known / unknown / '' / None / wrong type
  direction lock     none / 胜 / 平 / 负 / locked to a direction absent from pool
  heat ranking       normal / empty / duplicate / unknown / NaN / negative /
                     zero / non-numeric
  pool length        31 / 15 / 0 / 1 / duplicates / the JC-31 producer

Invariants asserted per case (each violation is recorded as one defect):

  I1  no uncaught exception, and no exception swallowed into a degraded answer
  I2  no NaN / inf / None leaking into numeric fields
  I3  pool length is not shortened; `evicted` count == real lost count
  I4  *injected* options are a subset of the 31 JC options
  I5  direction uniqueness: no cross-direction pair, zero banned words
  I6  probability conservation against the row's declared theory source
  I7  determinism: two runs of the same input are byte-identical
  I8  a locked direction that exists in the pool is actually honoured

Tolerances: percentages are carried through `round(x, 4)`, i.e. one half-step is
5e-5 pp per row. `CONSERVATION_TOL_PCT` is 31 half-steps (~3.1e-3 pp), derived
from that rounding, not tuned to whatever the run happened to produce.

`COVERAGE` in the output proves the run was not vacuous: it counts how often
each invariant actually had data that could have failed it.

Usage:
    python scripts/sweep_10000.py                 # 10000 cases, clustered report
    python scripts/sweep_10000.py --cases 2000    # shorter run
    python scripts/sweep_10000.py --json out.json # machine-readable evidence

Exit code 0 only when zero defects were found.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The app prints a boot banner and a monitor line on import; keep the sweep
# output readable by swallowing only the import-time chatter.
_boot = io.StringIO()
with contextlib.redirect_stdout(_boot):
    app = importlib.import_module('app_v84_hermes')
    pipe = importlib.import_module('blade_deepseek_pipeline')
    okooo = importlib.import_module('okooo_mobile_cs')

JC = list(pipe.JCZQ_ALL_OPTIONS)
JC_SET = set(JC)
BANNED_WORDS = ('首选', '次选', '双选', '强推', '推荐')
# `不做推荐` is the only permitted use of a banned word.
BANNED_ALLOWED = ('不做推荐',)

# Probability sums are floats carried through `round(x, 4)` on percentages:
# 31 rows x 5e-5 (half a rounding step) = 1.55e-3 percentage points worst case.
# The tolerance must be derived from that, never tuned until the run goes green.
ROUNDING_STEP_PCT = 5e-5  # half of the 4th decimal, in percent
CONSERVATION_TOL_PCT = 31 * ROUNDING_STEP_PCT * 2  # 3.1e-3 pp
# Per-direction shares accumulate the same per-row rounding, so the bound is the
# same; it is stated separately because the two checks mean different things.
DIRECTION_TOL_PCT = 31 * ROUNDING_STEP_PCT * 2  # 3.1e-3 pp

KNOWN_LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲', '欧冠', '日职联')
ODD_SHAPES = (
    'normal', 'tail_long', 'tail_short', 'missing', 'zero', 'negative',
    'huge', 'text', 'nan', 'inf',
)
LOCK_SHAPES = ('none', 'home', 'draw', 'away', 'absent_direction')
HEAT_SHAPES = ('normal', 'empty', 'duplicate', 'unknown',
               'nan', 'negative', 'zero', 'text')
POOL_SHAPES = ('31', '15', '0', '1', 'duplicate', 'jc31')


# --------------------------------------------------------------------------
# input construction
# --------------------------------------------------------------------------
def _sample_lambda(rng: random.Random) -> tuple[float, float, str]:
    """Sample a lambda pair from one of five regimes, all inside 0.15..4.5."""
    regime = rng.choice(('balanced', 'home_heavy', 'away_heavy', 'both_high', 'both_low'))
    if regime == 'balanced':
        h = rng.uniform(0.6, 2.6)
        a = h + rng.uniform(-0.7, 0.7)
    elif regime == 'home_heavy':
        h = rng.uniform(1.6, 4.5)
        a = rng.uniform(0.15, 0.9)
    elif regime == 'away_heavy':
        h = rng.uniform(0.15, 0.9)
        a = rng.uniform(1.6, 4.5)
    elif regime == 'both_high':
        h = rng.uniform(2.8, 4.5)
        a = rng.uniform(2.8, 4.5)
    else:
        h = rng.uniform(0.15, 0.5)
        a = rng.uniform(0.15, 0.5)
    return round(min(4.5, max(0.15, h)), 4), round(min(4.5, max(0.15, a)), 4), regime


def _build_odds(prob_dict: dict[str, float], shape: str, rng: random.Random) -> dict[str, Any]:
    """Derive a JC-style correct-score odds map from the real probability table."""
    odds: dict[str, Any] = {}
    for score, prob in prob_dict.items():
        if score not in JC_SET:
            continue
        if prob > 0:
            odds[score] = round(1.0 / prob, 4)
    if shape == 'normal':
        return odds
    if shape == 'tail_long':
        for score in ('0-1', '0-2', '0-3', '1-2', '1-3', '2-3', '0-4'):
            if score in odds:
                odds[score] = round(odds[score] * 1.9, 4)
        return odds
    if shape == 'tail_short':
        for score in ('0-1', '0-2', '0-3', '1-2', '1-3', '2-3', '0-4'):
            if score in odds:
                odds[score] = round(odds[score] / 2.4, 4)
        return odds
    if shape == 'missing':
        drop = rng.sample(sorted(odds), k=max(1, len(odds) // 3))
        for score in drop:
            odds.pop(score, None)
        return odds
    if shape == 'zero':
        for score in rng.sample(sorted(odds), k=max(1, len(odds) // 4)):
            odds[score] = 0
        return odds
    if shape == 'negative':
        for score in rng.sample(sorted(odds), k=max(1, len(odds) // 4)):
            odds[score] = -abs(odds[score])
        return odds
    if shape == 'huge':
        for score in rng.sample(sorted(odds), k=max(1, len(odds) // 4)):
            odds[score] = rng.choice((1e6, 1e9, 1e12))
        return odds
    if shape == 'text':
        for score in rng.sample(sorted(odds), k=max(1, len(odds) // 4)):
            odds[score] = rng.choice(('abc', '', '  ', '8.5倍', None, []))
        return odds
    if shape == 'nan':
        for score in rng.sample(sorted(odds), k=max(1, len(odds) // 4)):
            odds[score] = float('nan')
        return odds
    if shape == 'inf':
        for score in rng.sample(sorted(odds), k=max(1, len(odds) // 4)):
            odds[score] = float('inf')
        return odds
    raise AssertionError(shape)


def _build_heat(shape: str, rng: random.Random) -> Any:
    """Heat input for `build_score_market_heat_ranking`.

    Most shapes are the dict form production passes; `duplicate` uses the list
    form because a dict cannot carry a repeated key.
    """
    page = {'1:0': 15.28, '2:0': 25.48, '2:1': 19.09, '0:0': 17.21, '1:1': 7.88}
    if shape == 'normal':
        return page
    if shape == 'empty':
        return {}
    if shape == 'duplicate':
        # The dict form cannot hold a repeated key, so duplicates are injected
        # through `correct_score_rows`, which is the list shape production uses.
        return [{'score': '1:0', 'odds': 15.28}, {'score': '1-0', 'odds': 9.9},
                {'score': '0:0', 'odds': 17.21}]
    if shape == 'unknown':
        return {'9:9': 12.0, 'foo': 3.0, '1-0': 15.28}
    if shape == 'nan':
        return {'1:0': float('nan'), '0:0': 17.21}
    if shape == 'negative':
        return {'1:0': -15.28, '0:0': 17.21}
    if shape == 'zero':
        return {'1:0': 0.0, '0:0': 17.21}
    if shape == 'text':
        return {'1:0': 'abc', '0:0': 17.21}
    raise AssertionError(shape)


def _build_lock(shape: str, pool_scores: list[str]) -> dict[str, Any]:
    if shape == 'none':
        return {}
    direction = {'home': '胜', 'draw': '平', 'away': '负'}.get(shape, '')
    if shape == 'absent_direction':
        present = {pipe._score_wdl(s) for s in pool_scores}
        missing = [d for d in ('胜', '平', '负') if d not in present]
        direction = missing[0] if missing else '胜'
    return {'direction_lock': {'locked': True, 'direction': direction,
                               'scores': [s for s in pool_scores
                                          if pipe._score_wdl(s) == direction][:3]}}


def _enrich_pool(ranked: list[tuple[str, dict[str, Any]]],
                 odds: dict[str, Any], limit: int | None) -> list[dict[str, Any]]:
    """Exactly the `_pool15_enriched` construction from `analyze_match`."""
    usable = [v for v in odds.values() if isinstance(v, (int, float)) and v > 0
              and math.isfinite(float(v))]
    total_inv = sum(1.0 / float(v) for v in usable)
    rows = []
    for score, info in (ranked[:limit] if limit is not None else ranked):
        theory = float(info['prob']) * 100.0
        market = 0.0
        odd = odds.get(score)
        if isinstance(odd, (int, float)) and float(odd) > 0 and total_inv > 0:
            market = (1.0 / float(odd)) / total_inv * 100.0
        rows.append({
            'score': score, 'theory': theory, 'market': market,
            'theory_prob': theory, 'market_prob': market,
            'ibi': (market / theory) if theory > 0.01 else 0,
            'weighted': theory, 'delta': market - theory,
        })
    return rows


def _pool_for(shape: str, ranked: list[tuple[str, dict[str, Any]]],
              odds: dict[str, Any]) -> list[dict[str, Any]]:
    if shape == '31':
        return _enrich_pool(ranked, odds, 31)
    if shape == '15':
        return _enrich_pool(ranked, odds, 15)
    if shape == '0':
        return []
    if shape == '1':
        return _enrich_pool(ranked, odds, 1)
    if shape == 'duplicate':
        base = _enrich_pool(ranked, odds, 5)
        return base + [dict(row) for row in base[:2]]
    if shape == 'jc31':
        # The other real producer: `build_31pool_enriched_list` is guaranteed to
        # be exactly the 31 JC options (three 其它 included), unlike
        # `_pool15_enriched`, whose 31 rows come from a 36/64-cell matrix.
        return app.build_31pool_enriched_list({'lam_h': 1.45, 'lam_a': 1.1})
    raise AssertionError(shape)


# --------------------------------------------------------------------------
# numeric hygiene
# --------------------------------------------------------------------------
# Keys where `None` is the documented "missing, do not invent" marker rather
# than a leak. Everything else must carry a finite number.
#   rows / pack : `market`/`theory`/`jc_odds` are None when that source is absent
#   pool        : `build_31pool_enriched_list` defers market to the caller
_DOCUMENTED_MISSING = {
    'rows': ('market', 'theory', 'jc_odds', 'jc_odds_pct', 'delta'),
    'pack': ('market', 'theory', 'jc_odds', 'jc_odds_pct', 'delta',
             'market_sum', 'theory_sum', 'jc_pct_sum'),
    'pool': ('market', 'market_prob'),
}
# `gravity` copies the pool rows, so it inherits the same documented gap.
_DOCUMENTED_MISSING['gravity'] = _DOCUMENTED_MISSING['pool']

_NUMERIC_KEYS = (
    'theory', 'market', 'theory_prob', 'market_prob', 'ibi', 'weighted', 'delta',
    'tactical_gravity_score', 'deviation', 'score_weight', 'market_pct', 'jc_pct',
    'odds', 'heat', 'avg_odds', 'jc_odds',
)


def _numeric_leaks(value: Any, allow_none: tuple[str, ...] = (),
                   path: str = '') -> list[str]:
    """Return every non-finite numeric value, and every None outside `allow_none`."""
    out: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            sub = f'{path}.{key}' if path else str(key)
            if key in _NUMERIC_KEYS:
                if item is None:
                    if key not in allow_none:
                        out.append(f'{sub}=None')
                elif isinstance(item, bool):
                    out.append(f'{sub}=bool:{item}')
                elif isinstance(item, (int, float)) and not math.isfinite(float(item)):
                    out.append(f'{sub}={item}')
            out.extend(_numeric_leaks(item, allow_none, sub))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            out.extend(_numeric_leaks(item, allow_none, f'{path}[{index}]'))
    return out


def _direction_totals(prob_map: dict[str, float]) -> dict[str, float]:
    totals = {'胜': 0.0, '平': 0.0, '负': 0.0}
    for score, prob in prob_map.items():
        label = pipe._score_wdl(score)
        if label:
            totals[label] += prob
    return totals


# --------------------------------------------------------------------------
# per-case evaluation
# --------------------------------------------------------------------------
class CaseFailure:
    __slots__ = ('detail', 'invariant')

    def __init__(self, invariant: str, detail: str) -> None:
        self.invariant = invariant
        self.detail = detail

    def __repr__(self) -> str:
        return f'{self.invariant}: {self.detail}'


class Coverage:
    """How often each invariant actually had something to check.

    A green sweep is only meaningful if the checks ran against data that could
    have failed them. These counters are printed with the summary so a vacuous
    run (e.g. every pool empty) is visible instead of looking like a pass.
    """

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def hit(self, name: str, times: int = 1) -> None:
        self.counts[name] += times

    def report(self) -> str:
        return ', '.join(f'{k}={v}' for k, v in sorted(self.counts.items()))


def _check_case(case: dict[str, Any], coverage: Coverage) -> list[CaseFailure]:
    failures: list[CaseFailure] = []
    lam_h, lam_a = case['lam_h'], case['lam_a']
    chatter = io.StringIO()

    def fail(invariant: str, detail: str) -> None:
        failures.append(CaseFailure(invariant, detail))

    # ---- producers, exactly as analyze_match builds them ----
    with contextlib.redirect_stdout(chatter):
        prob_dict = app.calc_poisson_prob(lam_h, lam_a, max_goals=8)
        ranked = app.build_probability_rankings(prob_dict)
        odds = _build_odds(prob_dict, case['odds'], case['rng'])
        pool = _pool_for(case['pool'], ranked, odds)
        jc_odds = odds if case['odds'] in ('normal', 'tail_long', 'tail_short') else None
        rows = app.build_pool31_option_rows(
            ranked, prob_dict=prob_dict, odds_correct_score=odds,
            jc_score_odds=jc_odds, lam_home=lam_h, lam_away=lam_a,
        )
        pack = pipe.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            [], lam_home=lam_h, lam_away=lam_a, jc_score_odds=jc_odds,
        )
        heat = okooo.build_score_market_heat_ranking(_build_heat(case['heat'], case['rng']))
        lock = _build_lock(case['lock'], [row['score'] for row in pool])
        ventropy = app.run_ventropy_pipeline(
            ranked, pool, case['league'], {}, direction_lock_payload=lock,
        )
        grav_payload = {'triggered': True,
                        'macro_direction': 'home', 'counter_direction': 'away'}
        gravity = app.TensorGravityReversalEngine().apply_gravity_reversal(pool, grav_payload)
        pack_two = pipe.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            [], lam_home=lam_h, lam_away=lam_a, jc_score_odds=jc_odds,
        )
        ventropy_two = app.run_ventropy_pipeline(
            ranked, pool, case['league'], {}, direction_lock_payload=lock,
        )
        gravity_two = app.TensorGravityReversalEngine().apply_gravity_reversal(
            pool, grav_payload,
        )

    # ---- I1: no uncaught exception (any raise above would have escaped) ----
    coverage.hit('cases')
    # A swallowed `except` is just as much a defect as a raise: the caller sees a
    # silently degraded answer instead of the real one. Any `⚠️`/`异常` line the
    # producers printed under our redirect means a path failed and was hidden.
    swallowed = [line for line in chatter.getvalue().splitlines()
                 if '⚠️' in line or '异常' in line]
    if swallowed:
        fail('I1-swallowed-exception', swallowed[0].strip()[:160])
    # ---- I2: numeric hygiene ----
    # The pool row schema is inherited from `_pool15_enriched`, where `market`
    # is always a float. `build_31pool_enriched_list` documents `market=None`
    # ("由调用方按市均填充"), so that shape is exempt for the pool and for any
    # copy the engines make of it.
    for name, payload in (('rows', rows), ('pack', pack), ('heat', heat),
                          ('pool', pool), ('gravity', gravity)):
        allow = _DOCUMENTED_MISSING.get(name, ())
        leaks = _numeric_leaks(payload, allow)
        if leaks:
            fail('I2-nonfinite', f'{name}: {leaks[:3]} (+{max(0, len(leaks) - 3)})')
    # ---- I3: pool length untouched, evicted == really lost ----
    if len(pool) != case['pool_expected']:
        fail('I3-pool-built', f'built {len(pool)} != expected {case["pool_expected"]}')
    if pool:
        coverage.hit('pool_nonempty')
        # Only a pool that carries direction-bearing rows can prove the engine
        # keeps them, so count the pools that actually contain 其它 rows.
        if any('其它' in r['score'] for r in pool):
            coverage.hit('pool_with_other_bucket')
    if len(gravity) != len(pool):
        lost = [r['score'] for r in pool]
        for row in gravity:
            if row['score'] in lost:
                lost.remove(row['score'])
        evicted = getattr(gravity, 'evicted', None)
        # `evicted` is only meaningful when the engine reports one; when it does
        # not, the length check above is the whole requirement.
        if evicted is not None and len(evicted) != len(lost):
            fail('I3-evicted-mismatch',
                 f'lost={len(lost)} evicted={len(evicted)} sample={sorted(set(lost))[:4]}')
        else:
            fail('I3-pool-shrink',
                 f'gravity in={len(pool)} out={len(gravity)} '
                 f'lost={sorted(set(lost))[:6]} evicted={evicted!r}')

    # ---- I4: *injected* options ⊆ JC 31 (the inherited pool may legitimately
    #          hold non-JC cells: `_pool15_enriched` is ranked[:31] of a 36/64
    #          cell matrix, see integration contract §6.4) ----
    before = [r['score'] for r in pool]
    for row in gravity:
        if row['score'] not in before and row['score'] not in JC_SET:
            fail('I4-not-jc-option',
                 f'gravity injected non-JC score {row["score"]!r} '
                 f'(pool had {len(before)} rows)')
            break
    stray_rows = [r['score'] for r in rows if r['score'] not in JC_SET]
    if stray_rows:
        fail('I4-not-jc-option', f'rows emitted non-JC scores {stray_rows[:4]}')

    # ---- I5: direction uniqueness ----
    if ventropy:
        primary = str(ventropy.get('primary') or '')
        secondary = str(ventropy.get('secondary') or '')
        d1 = pipe._score_wdl(primary) if primary not in ('', 'N/A') else ''
        d2 = pipe._score_wdl(secondary) if secondary not in ('', 'N/A') else ''
        if d1 and d2:
            coverage.hit('pair_both_directions')
            if d1 != d2:
                fail('I5-cross-direction',
                     f'primary={primary}({d1}) secondary={secondary}({d2})')
        text = str(ventropy.get('log') or '')
        for word in BANNED_WORDS:
            hits = text.count(word) - sum(text.count(ok) for ok in BANNED_ALLOWED)
            if hits > 0:
                fail('I5-banned-word', f'log carries {word!r} x{hits}')
    # the report surface must not carry banned words either
    surface = json.dumps({'rows': [r['score'] for r in rows],
                          'pack_reason': pack.get('reason', ''),
                          'heat': heat.get('top_scores')}, ensure_ascii=False)
    for word in BANNED_WORDS:
        if word in surface:
            fail('I5-banned-word', f'report surface carries {word!r}')

    # ---- I6: probability conservation against the DECLARED theory source ----
    # `build_pool31_option_rows` states its source per row in `theory_source`:
    # `frozen_lambda_poisson` when λ is present (it then ignores prob_dict),
    # otherwise `ranked_pool_aggregate`. Comparing against prob_dict when λ was
    # supplied would compare two different models, not check conservation.
    declared = next((r.get('theory_source') for r in rows if r.get('theory_source')), '')
    if declared == 'frozen_lambda_poisson':
        source_probs = pipe._poisson_score_probs(lam_h, lam_a)
    elif declared == 'ranked_pool_aggregate':
        source_probs = app._pool31_aggregate(prob_dict)
    else:
        source_probs = {}
    if source_probs:
        coverage.hit('conservation_checked')
        expected = _direction_totals(source_probs)
        rows_total = {k: 0.0 for k in expected}
        for row in rows:
            if row['theory'] is None:
                continue
            label = pipe._score_wdl(row['score'])
            if label:
                rows_total[label] += float(row['theory']) / 100.0
        for direction, want in expected.items():
            drift = abs(rows_total[direction] - want) * 100.0
            if drift > DIRECTION_TOL_PCT:
                fail('I6-conservation',
                     f'{declared} {direction}: rows={rows_total[direction]:.6f} '
                     f'source={want:.6f} drift={drift:.2e}pp')
    theory_sum = sum(float(r['theory']) for r in rows if r['theory'] is not None)
    if abs(theory_sum - 100.0) > CONSERVATION_TOL_PCT:
        fail('I6-conservation', f'theory sum {theory_sum:.6f} != 100')
    market_rows = [float(r['market']) for r in rows if r['market'] is not None]
    if len(market_rows) > 1 and abs(sum(market_rows) - 100.0) > CONSERVATION_TOL_PCT:
        fail('I6-conservation', f'market sum {sum(market_rows):.6f} != 100 '
                                f'({len(market_rows)} priced rows)')

    # ---- I7: determinism (same input twice must give the same answer) ----
    strip = ('others_folded',)
    coverage.hit('determinism_checked')

    def _fingerprint(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    pairs = (
        ('rows', [{k: v for k, v in r.items() if k not in strip} for r in rows], None),
        ('pack', pack, pack_two),
        ('ventropy', ventropy, ventropy_two),
        ('gravity', gravity, gravity_two),
    )
    for name, one, two in pairs:
        if two is None:
            continue
        if _fingerprint(one) != _fingerprint(two):  # type: ignore[arg-type]
            fail('I7-nondeterministic', f'{name}: two runs of the same input differ')

    # ---- I8: a locked direction must actually be honoured ----
    # The lock payload is the operator's pin. If it names a direction the pool
    # really contains, no surface may answer with a different direction.
    locked_direction = ''
    if isinstance(lock, dict):
        inner = lock.get('direction_lock')
        if isinstance(inner, dict) and inner.get('locked'):
            locked_direction = str(inner.get('direction') or '')
    if locked_direction:
        pool_dirs = {pipe._score_wdl(r['score']) for r in pool}
        if locked_direction in pool_dirs:
            coverage.hit('lock_matches_pool')
            for label, value in (
                ('primary', str((ventropy or {}).get('primary') or '')),
                ('secondary', str((ventropy or {}).get('secondary') or '')),
            ):
                if value in ('', 'N/A'):
                    continue
                got = pipe._score_wdl(value)
                if got and got != locked_direction:
                    fail('I8-lock-ignored',
                         f'lock={locked_direction} but ventropy {label}={value}({got})')

    return failures


def _pool_expected(shape: str, n_ranked: int) -> int:
    """How many rows `_pool_for` will really produce for a pool of `n_ranked`."""
    if shape == '0':
        return 0
    if shape == 'jc31':
        return 31
    if shape == '1':
        return min(1, n_ranked)
    if shape == '15':
        return min(15, n_ranked)
    if shape == '31':
        return min(31, n_ranked)
    if shape == 'duplicate':
        # 5 base rows plus the first 2 repeated
        base = min(5, n_ranked)
        return base + min(2, base)
    raise AssertionError(shape)


def _case_signature(case: dict[str, Any]) -> tuple[Any, ...]:
    return (case['regime'], case['odds'], case['league_kind'], case['lock'],
            case['heat'], case['pool'])


def _make_case(index: int, rng: random.Random) -> dict[str, Any]:
    lam_h, lam_a, regime = _sample_lambda(rng)
    league = rng.choice((*KNOWN_LEAGUES, 'UNKNOWN_联赛_XYZ', '', None, 123))
    return {
        'index': index,
        'seed': rng.getrandbits(32),
        'rng': rng,
        'lam_h': lam_h,
        'lam_a': lam_a,
        'regime': regime,
        'odds': rng.choice(ODD_SHAPES),
        'league': league,
        'league_kind': ('known' if league in KNOWN_LEAGUES
                        else 'none' if league is None else
                        'empty' if league == '' else 'odd'),
        'lock': rng.choice(LOCK_SHAPES),
        'heat': rng.choice(HEAT_SHAPES),
        'pool': rng.choice(POOL_SHAPES),
        'pool_expected': 0,  # filled after the pool shape is known
    }


def run_sweep(n_cases: int, seed: int = 20261126
              ) -> tuple[list[dict[str, Any]], int, Coverage]:
    """Run `n_cases` sampled cases; return (failures, total, coverage)."""
    # Seeded on purpose: the sweep must replay to the same 10,000 cases.
    # Nothing here is security-sensitive, so a non-cryptographic PRNG is correct.
    rng = random.Random(seed)  # nosec B311
    failures: list[dict[str, Any]] = []
    coverage = Coverage()
    total = 0
    for index in range(n_cases):
        case = _make_case(index, rng)
        # the pool size follows from the probability table, so it must be
        # computed from the same producer the case body uses.
        case['pool_expected'] = _pool_expected(
            case['pool'],
            len(app.calc_poisson_prob(case['lam_h'], case['lam_a'], max_goals=8)),
        )
        total += 1
        try:
            case_failures = _check_case(case, coverage)
        except Exception as exc:  # noqa: BLE001 - I1 is exactly "must not raise"
            failures.append({
                'case': _describe(case),
                'invariant': 'I1-uncaught-exception',
                'detail': f'{type(exc).__name__}: {exc}',
            })
            continue
        for failure in case_failures:
            failures.append({'case': _describe(case), 'invariant': failure.invariant,
                             'detail': failure.detail})
    return failures, total, coverage


def _describe(case: dict[str, Any]) -> str:
    league = case['league']
    return (f'seed={case["seed"]} lam=({case["lam_h"]},{case["lam_a"]}) '
            f'regime={case["regime"]} odds={case["odds"]} '
            f'league={league!r} lock={case["lock"]} heat={case["heat"]} '
            f'pool={case["pool"]}')


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------
def _cluster(failures: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Collapse 10k raw failures into root-cause buckets.

    Buckets are keyed on the invariant plus the *mechanism* named in the detail,
    not on the concrete scores, so the report shows classes rather than a ledger.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for failure in failures:
        invariant = failure['invariant']
        detail = failure['detail']
        if invariant == 'I5-cross-direction':
            key = 'I5 方向唯一性：ventropy 首/次选落在不同方向'
        elif invariant == 'I8-lock-ignored':
            key = 'I8 方向锁被忽略：锁定方向与输出方向不一致'
        elif invariant == 'I1-swallowed-exception':
            key = 'I1 异常被静默吞掉（调用方拿到降级答案而非真实结果）'
        elif invariant == 'I3-pool-shrink':
            key = 'I3 池被缩短：apply_gravity_reversal 的 continue 静默丢行'
        elif invariant == 'I3-evicted-mismatch':
            key = 'I3 evicted 计数与真实丢失不一致'
        elif invariant == 'I4-not-jc-option':
            key = 'I4 注入项不在竞彩 31 项内'
        elif invariant == 'I2-nonfinite':
            key = 'I2 数值字段泄漏 ' + ('non-finite' if any(
                token in detail for token in ('nan', 'inf')) else 'None')
        elif invariant == 'I6-conservation':
            key = 'I6 概率守恒偏差'
        elif invariant == 'I7-nondeterministic':
            key = 'I7 同一输入两次结果不同'
        elif invariant == 'I5-banned-word':
            key = 'I5 禁用词命中'
        else:
            key = f'{invariant} {detail[:48]}'
        grouped[key].append(failure)
    return dict(grouped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=10000)
    parser.add_argument('--seed', type=int, default=20261126)
    parser.add_argument('--json', default='')
    parser.add_argument('--cluster-limit', type=int, default=12)
    args = parser.parse_args(argv)

    failures, total, coverage = run_sweep(args.cases, args.seed)
    grouped = _cluster(failures)
    print(f'SWEEP n_cases={total} failures={len(failures)} clusters={len(grouped)}')
    print(f'COVERAGE {coverage.report()}')
    for key in sorted(grouped, key=lambda k: -len(grouped[k])):
        bucket = grouped[key]
        first = bucket[0]
        print()
        print(f'[{len(bucket):5d}x] {key}')
        print(f'        example: {first["case"]}')
        print(f'        detail : {first["detail"][:200]}')
    if args.json:
        Path(args.json).write_text(json.dumps({
            'n_cases': total,
            'n_failures': len(failures),
            'coverage': dict(coverage.counts),
            'clusters': {k: {'count': len(v), 'examples': v[:20]}
                         for k, v in grouped.items()},
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nJSON evidence -> {args.json}')
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
