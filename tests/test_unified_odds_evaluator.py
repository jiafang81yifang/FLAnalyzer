"""Pin unified_odds_evaluator's arithmetic against independent expectations.

Every expected number here comes from an exact-rational re-derivation of the
vig formulas (``fractions.Fraction``) or from literals written by hand, never
from calling the implementation under test. The reference block at the bottom
of the original spec drives the demo pins; the two values where that block
drifted are called out explicitly at their assertion.
"""
from __future__ import annotations

import importlib
import json
import math
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

unified = importlib.import_module('unified_odds_evaluator')
UnifiedOddsEvaluator = unified.UnifiedOddsEvaluator


# --------------------------------------------------------------------------- #
# Independent oracle: exact rational vig math, sharing no code with the module.
# --------------------------------------------------------------------------- #
def exact_vig(odds: tuple[float, ...]) -> tuple[float, float, float, list[float]]:
    """Recompute (overround, payout, margin, fair probs) in exact rationals."""
    inv = [Fraction(1) / Fraction(str(o)) for o in odds]
    overround = sum(inv)
    payout = Fraction(1) / overround
    margin = 1 - payout
    fair = [float(x / overround) for x in inv]
    return float(overround), float(payout), float(margin), fair


def exact_ev(odds: tuple[float, ...], probs: tuple[float, ...]) -> list[float]:
    """EV_i = p_i * o_i - 1, in exact rationals."""
    return [float(Fraction(str(p)) * Fraction(str(o)) - 1) for p, o in zip(probs, odds)]


# The demo block's four markets, exactly as the spec's sample_matches writes them.
CN_1X2 = (1.50, 3.60, 5.20)
INTL_1X2 = (1.85, 3.55, 4.25)
CN_OU = (1.72, 1.75)
INTL_OU = (1.95, 1.91)


def demo_evaluator() -> UnifiedOddsEvaluator:
    return UnifiedOddsEvaluator(
        min_ev_thresholds={'CN': 0.05, 'INTL': 0.015},
        kelly_fractions={'CN': 0.125, 'INTL': 0.25},
        max_stake_fractions={'CN': 0.02, 'INTL': 0.05},
        min_allowed_margin_spread=0.15,
        max_allowed_margin_spread=0.35,
        max_allowed_odds_rel_diff=0.15,
        max_allowed_fair_prob_diff=0.05,
        exclude_anomalous_from_ev=True,
    )


def demo_matches() -> list[dict]:
    return [
        {
            'match_id': '20261126_EPL_01',
            'markets': [
                {'source_type': 'CN', 'type': '1X2', 'odds': CN_1X2, 'true_probs': (0.68, 0.19, 0.13)},
                {'source_type': 'INTL', 'type': '1X2', 'odds': INTL_1X2, 'true_probs': (0.58, 0.22, 0.20)},
                {'source_type': 'CN', 'type': 'OU', 'line': 2.5, 'odds': CN_OU, 'true_probs': (0.55, 0.45)},
                {'source_type': 'INTL', 'type': 'OU', 'line': 2.5, 'odds': INTL_OU, 'true_probs': (0.54, 0.46)},
            ],
        }
    ]


# --------------------------------------------------------------------------- #
# 1. Golden payout / margin / fair-probability arithmetic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ('odds', 'payout', 'margin'),
    [
        # payout = 1 / (1/1.50 + 1/3.60 + 1/5.20) = 117/133 = 0.8796992481...
        (CN_1X2, 0.879699, 0.120301),
        # 1 / (1/1.85 + 1/3.55 + 1/4.25) = 0.9456043025...
        (INTL_1X2, 0.945604, 0.054396),
        # 1 / (1/1.72 + 1/1.75) = 0.8674351585...
        (CN_OU, 0.867435, 0.132565),
        # 1 / (1/1.95 + 1/1.91) = 0.9648963731...
        (INTL_OU, 0.964896, 0.035104),
    ],
)
def test_payout_and_margin_match_hand_computed_values(odds, payout, margin) -> None:
    if len(odds) == 3:
        report = demo_evaluator().evaluate_1x2('m', 'CN', odds)
    else:
        report = demo_evaluator().evaluate_two_way('m', 'CN', 'OU', odds, ('OVER', 'UNDER'), line=2.5)

    assert report['payout_rate'] == pytest.approx(payout, abs=1e-9)
    assert report['margin_rate'] == pytest.approx(margin, abs=1e-9)

    _overround, exp_payout, exp_margin, exp_fair = exact_vig(odds)
    assert report['payout_rate'] == pytest.approx(exp_payout, abs=5e-7)
    assert report['margin_rate'] == pytest.approx(exp_margin, abs=5e-7)
    assert report['fair_probs_no_vig'] == pytest.approx(exp_fair, abs=5e-7)

    # payout and margin must be exactly complementary at the reported precision.
    assert report['payout_rate'] + report['margin_rate'] == pytest.approx(1.0, abs=1e-6)
    # Sum of per-outcome values rounded to 6dp: worst case is n * 5e-7 of drift,
    # so the tolerance is the rounding budget, not implementation slack.
    assert sum(report['fair_probs_no_vig']) == pytest.approx(1.0, abs=len(odds) * 5e-7 + 1e-9)
    # overround is the reciprocal of payout.
    assert report['overround'] == pytest.approx(1.0 / report['payout_rate'], rel=1e-6)


@pytest.mark.parametrize(
    ('odds', 'overround', 'payout', 'margin', 'fair'),
    [
        # Exact fractions, written out so the reference numbers are auditable
        # without floating point. Each is the reduced form of
        # overround=sum(1/o), payout=1/overround, margin=1-payout, fair_i=(1/o_i)/overround.
        (
            CN_1X2,
            Fraction(133, 117),   # 2/3 + 5/18 + 5/26
            Fraction(117, 133),   # = 0.8796992481...
            Fraction(16, 133),    # = 0.1203007519...
            [Fraction(78, 133), Fraction(65, 266), Fraction(45, 266)],
        ),
        (
            INTL_1X2,
            Fraction(47228, 44659),   # 20/37 + 20/71 + 4/17
            Fraction(44659, 47228),   # = 0.9456043025...
            Fraction(2569, 47228),    # = 0.0543956975...
            [Fraction(6035, 11807), Fraction(3145, 11807), Fraction(2627, 11807)],
        ),
        (
            CN_OU,
            Fraction(347, 301),    # 25/43 + 4/7
            Fraction(301, 347),    # = 0.8674351585...
            Fraction(46, 347),     # = 0.1325648415...
            [Fraction(175, 347), Fraction(172, 347)],
        ),
        (
            INTL_OU,
            Fraction(7720, 7449),  # 20/39 + 100/191
            Fraction(7449, 7720),  # = 0.9648963731...
            Fraction(271, 7720),   # = 0.0351036269...
            [Fraction(191, 386), Fraction(195, 386)],
        ),
    ],
)
def test_every_reported_value_is_the_exact_fraction_rounded(odds, overround, payout, margin, fair) -> None:
    """The report's 6-dp numbers must be exactly these fractions rounded."""
    ev = UnifiedOddsEvaluator()
    if len(odds) == 3:
        report = ev.evaluate_1x2('m', 'CN', odds)
    else:
        report = ev.evaluate_two_way('m', 'CN', 'OU', odds, ('OVER', 'UNDER'), line=2.5)

    assert report['overround'] == round(float(overround), 6)
    assert report['payout_rate'] == round(float(payout), 6)
    assert report['margin_rate'] == round(float(margin), 6)
    assert report['fair_probs_no_vig'] == [round(float(f), 6) for f in fair]
    # payout + margin are exactly 1 by construction, not merely close.
    assert payout + margin == 1
    assert payout == 1 / overround
    assert sum(fair) == 1


def test_ev_matches_hand_computed_values() -> None:
    ev = demo_evaluator()
    cn = ev.evaluate_1x2('m', 'CN', CN_1X2, true_probs=(0.68, 0.19, 0.13))
    intl = ev.evaluate_1x2('m', 'INTL', INTL_1X2, true_probs=(0.58, 0.22, 0.20))
    cn_ou = ev.evaluate_two_way('m', 'CN', 'OU', CN_OU, ('OVER', 'UNDER'), line=2.5, true_probs=(0.55, 0.45))
    intl_ou = ev.evaluate_two_way('m', 'INTL', 'OU', INTL_OU, ('OVER', 'UNDER'), line=2.5, true_probs=(0.54, 0.46))

    assert cn['ev_list'] == pytest.approx([0.02, -0.316, -0.324], abs=1e-6)
    assert intl['ev_list'] == pytest.approx([0.073, -0.219, -0.150], abs=1e-6)
    # 0.55*0.72-0.45 = -0.054 ; 0.45*0.75-0.55 = -0.2125
    assert cn_ou['ev_list'] == pytest.approx([-0.054, -0.2125], abs=1e-6)
    # 0.54*0.95-0.46 = 0.053 ; 0.46*0.91-0.54 = -0.1214
    assert intl_ou['ev_list'] == pytest.approx([0.053, -0.1214], abs=1e-6)

    assert cn['ev_list'] == pytest.approx(exact_ev(CN_1X2, (0.68, 0.19, 0.13)), abs=5e-7)
    assert cn_ou['ev_list'] == pytest.approx(exact_ev(CN_OU, (0.55, 0.45)), abs=5e-7)


# --------------------------------------------------------------------------- #
# 2. The demo's real outcome: margin spreads under the 0.15 floor, zero picks
# --------------------------------------------------------------------------- #
def test_demo_margin_spreads_are_below_floor_and_yield_zero_picks() -> None:
    _active, picks, comparisons = demo_evaluator().batch_evaluate_and_filter_ev(demo_matches())

    # Both markets must be compared, CN vs INTL, and both must be flagged.
    assert sorted(c['market_type'] for c in comparisons) == ['1X2', 'OU']
    assert all(c['is_anomalous'] for c in comparisons)

    by_type = {c['market_type']: c for c in comparisons}

    # 1X2: 16/133 - 2569/47228 = 413971/6281324 = 0.065905..., below the 0.15 floor.
    # The exact fraction is the audit trail: 117/133 = .879699 payout for CN, and
    # the spread is a MARGIN difference, not a payout difference (which would be
    # its negation, -0.065905).
    assert by_type['1X2']['cn_margin_rate'] == pytest.approx(0.120301, abs=1e-9)
    assert by_type['1X2']['intl_margin_rate'] == pytest.approx(0.054396, abs=1e-9)
    assert by_type['1X2']['margin_diff_cn_minus_intl'] == pytest.approx(0.065905, abs=1e-6)
    assert by_type['1X2']['margin_diff_cn_minus_intl'] == pytest.approx(
        float(Fraction(413971, 6281324)), abs=5e-7
    )

    # OU: 0.132565 - 0.035104 = 0.097461, also below the floor.
    assert by_type['OU']['margin_diff_cn_minus_intl'] == pytest.approx(0.097461, abs=1e-6)

    exp_1x2 = exact_vig(CN_1X2)[2] - exact_vig(INTL_1X2)[2]
    exp_ou = exact_vig(CN_OU)[2] - exact_vig(INTL_OU)[2]
    assert by_type['1X2']['margin_diff_cn_minus_intl'] == pytest.approx(exp_1x2, abs=5e-7)
    assert by_type['OU']['margin_diff_cn_minus_intl'] == pytest.approx(exp_ou, abs=5e-7)
    assert exp_1x2 < 0.15 and exp_ou < 0.15

    # Every market carries MARGIN_SPREAD_TOO_LOW, so with
    # exclude_anomalous_from_ev=True the demo yields ZERO picks.
    for comp in comparisons:
        assert any('MARGIN_SPREAD_TOO_LOW' in r for r in comp['market_anomaly_reasons'])
    assert picks == []

    # ...and the picks are only absent *because* of the filter.
    _active2, unfiltered, _comparisons2 = demo_evaluator().batch_evaluate_and_filter_ev(
        demo_matches(), exclude_anomalous=False
    )
    # CN HOME_WIN (ev .02 < .05) fails its own bar; INTL HOME_WIN (.073) and
    # INTL OVER (.053) clear theirs, sorted by EV descending.
    assert [p['outcome'] for p in unfiltered] == ['HOME_WIN', 'OVER']
    assert [p['source_type'] for p in unfiltered] == ['INTL', 'INTL']


# --------------------------------------------------------------------------- #
# 3. CN / INTL source isolation
# --------------------------------------------------------------------------- #
ISOLATION_ODDS = (2.0, 4.0, 4.0)
ISOLATION_PROBS = (0.55, 0.25, 0.20)  # EV = [0.10, 0.00, -0.20] exactly.


def isolation_matches() -> list[dict]:
    # Distinct match_ids: no CN/INTL pair, so no comparison and no anomaly.
    return [
        {
            'match_id': 'M_CN',
            'markets': [
                {'source_type': 'CN', 'type': '1X2', 'odds': ISOLATION_ODDS, 'true_probs': ISOLATION_PROBS},
            ],
        },
        {
            'match_id': 'M_INTL',
            'markets': [
                {'source_type': 'INTL', 'type': '1X2', 'odds': ISOLATION_ODDS, 'true_probs': ISOLATION_PROBS},
            ],
        },
    ]


def test_cn_threshold_does_not_apply_to_intl() -> None:
    ev = UnifiedOddsEvaluator(min_ev_thresholds={'CN': 0.05, 'INTL': 0.30})
    _active, picks, _comparisons = ev.batch_evaluate_and_filter_ev(isolation_matches())

    assert [p['source_type'] for p in picks] == ['CN']
    assert picks[0]['min_ev_threshold_used'] == pytest.approx(0.05)
    assert picks[0]['ev'] == pytest.approx(0.10, abs=1e-9)


def test_intl_threshold_does_not_apply_to_cn() -> None:
    ev = UnifiedOddsEvaluator(min_ev_thresholds={'CN': 0.30, 'INTL': 0.05})
    _active, picks, _comparisons = ev.batch_evaluate_and_filter_ev(isolation_matches())

    assert [p['source_type'] for p in picks] == ['INTL']
    assert picks[0]['min_ev_threshold_used'] == pytest.approx(0.05)


def test_threshold_override_is_per_source() -> None:
    ev = UnifiedOddsEvaluator(min_ev_thresholds={'CN': 0.05, 'INTL': 0.05})
    _active, both, _comparisons = ev.batch_evaluate_and_filter_ev(isolation_matches())
    assert sorted(p['source_type'] for p in both) == ['CN', 'INTL']

    # Raise only CN's bar above the 0.10 EV: only INTL may survive.
    _active, picks, _comparisons = ev.batch_evaluate_and_filter_ev(
        isolation_matches(), override_min_ev={'CN': 0.11}
    )
    assert [p['source_type'] for p in picks] == ['INTL']
    assert picks[0]['min_ev_threshold_used'] == pytest.approx(0.05)


def test_kelly_fraction_and_cap_are_per_source() -> None:
    ev = UnifiedOddsEvaluator(
        kelly_fractions={'CN': 0.10, 'INTL': 0.50},
        max_stake_fractions={'CN': 0.005, 'INTL': 0.90},
    )
    _active, picks, _comparisons = ev.batch_evaluate_and_filter_ev(isolation_matches())
    by_src = {p['source_type']: p for p in picks}

    # full_kelly = ev / (o - 1) = 0.10 / 1.0 = 0.10 for both.
    assert by_src['CN']['full_kelly'] == pytest.approx(0.10, abs=1e-6)
    assert by_src['INTL']['full_kelly'] == pytest.approx(0.10, abs=1e-6)
    # CN: 0.10 * 0.10 = 0.01 scaled, capped at 0.005.
    assert by_src['CN']['scaled_kelly'] == pytest.approx(0.01, abs=1e-6)
    assert by_src['CN']['recommended_stake'] == pytest.approx(0.005, abs=1e-6)
    # INTL: 0.10 * 0.50 = 0.05 scaled, cap 0.90 not binding.
    assert by_src['INTL']['scaled_kelly'] == pytest.approx(0.05, abs=1e-6)
    assert by_src['INTL']['recommended_stake'] == pytest.approx(0.05, abs=1e-6)


# --------------------------------------------------------------------------- #
# 4. The +EV threshold is strictly greater-than
# --------------------------------------------------------------------------- #
def test_ev_exactly_equal_to_threshold_is_excluded() -> None:
    # odds 3.0/3.0/3.0 with probs .5/.25/.25 -> EV = [0.5, -0.25, -0.25] exactly.
    payload = [
        {
            'match_id': 'BOUNDARY',
            'markets': [
                {'source_type': 'INTL', 'type': '1X2', 'odds': (3.0, 3.0, 3.0),
                 'true_probs': (0.5, 0.25, 0.25)},
            ],
        }
    ]
    report = UnifiedOddsEvaluator().evaluate_1x2(
        'BOUNDARY', 'INTL', (3.0, 3.0, 3.0), true_probs=(0.5, 0.25, 0.25)
    )
    assert report['ev_list'][0] == 0.5  # exact, no float drift

    at_threshold = UnifiedOddsEvaluator(min_ev_thresholds={'INTL': 0.5})
    _a, picks_equal, _c = at_threshold.batch_evaluate_and_filter_ev(payload)
    assert picks_equal == [], 'ev == threshold must not count as +EV'

    just_below = UnifiedOddsEvaluator(min_ev_thresholds={'INTL': 0.499999})
    _a, picks_below, _c = just_below.batch_evaluate_and_filter_ev(payload)
    assert [p['outcome'] for p in picks_below] == ['HOME_WIN']

    above = UnifiedOddsEvaluator(min_ev_thresholds={'INTL': 0.500001})
    _a, picks_above, _c = above.batch_evaluate_and_filter_ev(payload)
    assert picks_above == []


# --------------------------------------------------------------------------- #
# 5. Outcome alignment by name (defect 1: the HOME-vs-AWAY money bug)
# --------------------------------------------------------------------------- #
def _two_way(match_id: str, source: str, outcomes: tuple[str, str], odds: tuple[float, float]):
    return UnifiedOddsEvaluator().evaluate_two_way(
        match_id=match_id, source_type=source, market_type='AH',
        odds=odds, outcomes=outcomes, line=-0.5,
    )


def test_inverted_outcome_order_gives_the_same_comparison_as_aligned_order() -> None:
    ev = UnifiedOddsEvaluator()

    # Same underlying market: HOME 1.50 / AWAY 2.60 in both sources, but INTL
    # lists its outcomes in the opposite order.
    cn = _two_way('M', 'CN', ('HOME', 'AWAY'), (1.50, 2.60))
    intl_aligned = _two_way('M', 'INTL', ('HOME', 'AWAY'), (1.50, 2.60))
    intl_inverted = _two_way('M', 'INTL', ('AWAY', 'HOME'), (2.60, 1.50))

    aligned = ev.compare_cn_vs_intl_same_match([cn, intl_aligned])[0]
    inverted = ev.compare_cn_vs_intl_same_match([cn, intl_inverted])[0]

    assert aligned['outcome_alignment'] == 'by_name'
    assert inverted['outcome_alignment'] == 'by_name'
    assert inverted['outcome_odds_deviations'] == aligned['outcome_odds_deviations']

    # And the comparison is genuinely zero-diff: an index pairing would have
    # compared HOME 1.50 against AWAY 2.60.
    for row in inverted['outcome_odds_deviations']:
        assert row['cn_outcome'] == row['intl_outcome']
        assert row['odds_diff_intl_minus_cn'] == pytest.approx(0.0, abs=1e-9)
        assert row['fair_prob_diff_intl_minus_cn'] == pytest.approx(0.0, abs=1e-9)


def test_inverted_order_would_have_mismatched_if_paired_by_index() -> None:
    """The numbers the buggy index pairing produced, to prove it was a real bug."""
    ev = UnifiedOddsEvaluator(max_allowed_odds_rel_diff=0.10)
    cn = _two_way('M', 'CN', ('HOME', 'AWAY'), (1.50, 2.60))
    intl_inverted = _two_way('M', 'INTL', ('AWAY', 'HOME'), (2.60, 1.50))
    comp = ev.compare_cn_vs_intl_same_match([cn, intl_inverted])[0]

    home = next(r for r in comp['outcome_odds_deviations'] if r['cn_outcome'] == 'HOME')
    assert home['intl_outcome'] == 'HOME'
    assert home['intl_odds'] == pytest.approx(1.50)
    # Index pairing would have put intl_odds = 2.60 here: a +0.7333 relative
    # "deviation" and a spurious anomaly on a perfectly matched market.
    index_pair_rel_diff = (2.60 - 1.50) / 1.50
    assert index_pair_rel_diff > 0.10
    assert home['odds_rel_diff_ratio'] == pytest.approx(0.0, abs=1e-9)
    assert comp['is_anomalous'] is False


def test_mismatched_outcome_sets_fall_back_to_index_and_say_so() -> None:
    ev = UnifiedOddsEvaluator()
    cn = _two_way('M', 'CN', ('OVER', 'UNDER'), (1.90, 1.90))
    intl = _two_way('M', 'INTL', ('OVER_PRICE', 'UNDER_PRICE'), (1.95, 1.85))
    comp = ev.compare_cn_vs_intl_same_match([cn, intl])[0]

    assert comp['outcome_alignment'] == 'by_index'
    rows = comp['outcome_odds_deviations']
    assert [(r['cn_outcome'], r['intl_outcome']) for r in rows] == [
        ('OVER', 'OVER_PRICE'),
        ('UNDER', 'UNDER_PRICE'),
    ]
    # Mismatched labels are visible instead of being silently "equal".
    assert rows[0]['outcome'] == 'OVER vs OVER_PRICE'


def test_three_way_inverted_order_is_aligned_by_name() -> None:
    ev = UnifiedOddsEvaluator()
    cn = ev.evaluate_1x2('M', 'CN', (1.50, 3.60, 5.20))
    # Same three outcomes, reversed, with the odds reversed to match.
    intl = ev.evaluate_1x2('M', 'INTL', (1.50, 3.60, 5.20))
    intl['outcomes'] = ['AWAY_WIN', 'DRAW', 'HOME_WIN']
    intl['odds'] = list(reversed(intl['odds']))
    intl['fair_probs_no_vig'] = list(reversed(intl['fair_probs_no_vig']))

    comp = ev.compare_cn_vs_intl_same_match([cn, intl])[0]
    assert comp['outcome_alignment'] == 'by_name'
    assert all(r['cn_outcome'] == r['intl_outcome'] for r in comp['outcome_odds_deviations'])
    assert all(r['odds_diff_intl_minus_cn'] == pytest.approx(0.0, abs=1e-9)
               for r in comp['outcome_odds_deviations'])


# --------------------------------------------------------------------------- #
# 6. Defect 2: kelly_note only when a push exists
# --------------------------------------------------------------------------- #
def test_ev_ignores_push_because_the_push_term_is_exactly_zero() -> None:
    """EV must stay p_a*b - p_b with no (1-push) scaling: the push outcome pays 0.

    Staking 1 unit on A: A wins +b, B wins -1, push 0, so the full expectation is
    p_win*b + p_loss*(-1) + push*0, and the push term is multiplied by exactly
    zero. Any future "fix" that adds a push correction to EV is therefore wrong.
    """
    p_a, p_b, push, o_a, o_b = 0.50, 0.40, 0.10, 2.10, 1.80
    report = UnifiedOddsEvaluator().evaluate_two_way(
        'M', 'INTL', 'AH', (o_a, o_b), ('AH_HOME', 'AH_AWAY'), line=-0.5,
        true_probs=(p_a, p_b), push_prob=push, true_push_prob=push,
    )
    b_a, b_b = o_a - 1.0, o_b - 1.0
    # Hand-computed: .5*1.10-.4 = .15 ; .4*.80-.5 = -.18
    assert report['ev_list'] == pytest.approx([p_a * b_a - p_b, p_b * b_b - p_a], abs=1e-12)
    assert report['ev_list'] == pytest.approx([0.15, -0.18], abs=1e-12)
    # Spelled out: the full three-outcome expectation equals the two-term one.
    assert p_a * b_a + p_b * (-1.0) + push * 0.0 == pytest.approx(0.15, abs=1e-12)


def test_kelly_note_absent_without_push() -> None:
    report = UnifiedOddsEvaluator().evaluate_two_way(
        'M', 'INTL', 'OU', (2.10, 1.80), ('OVER', 'UNDER'), line=2.5,
        true_probs=(0.55, 0.45),
    )
    assert 'kelly_note' not in report
    # The numbers stay exactly as before: ev / (o - 1).
    ev_a = 0.55 * 1.10 - 0.45
    assert report['ev_list'][0] == pytest.approx(ev_a, abs=1e-6)
    assert report['kelly_stakes'][0]['full_kelly'] == pytest.approx(ev_a / 1.10, abs=1e-6)


def test_kelly_note_present_and_numbers_unchanged_with_push() -> None:
    report = UnifiedOddsEvaluator().evaluate_two_way(
        'M', 'INTL', 'AH', (2.10, 1.80), ('AH_HOME', 'AH_AWAY'), line=-0.5,
        true_probs=(0.50, 0.40), push_prob=0.10, true_push_prob=0.10,
    )
    assert 'kelly_note' in report
    note = report['kelly_note']
    assert '近似' in note
    assert '0.1' in note  # names the push probability that makes it an approximation

    # Reported numbers are preserved: EV ignores the push, Kelly is ev/(o-1).
    ev_a = 0.50 * 1.10 - 0.40
    assert report['ev_list'][0] == pytest.approx(ev_a, abs=1e-6)
    assert report['kelly_stakes'][0]['full_kelly'] == pytest.approx(ev_a / 1.10, abs=1e-6)
    assert report['push_prob_input'] == pytest.approx(0.10)
    # effective payout blends the no-push payout with the 1.0 stake return.
    assert report['effective_payout_with_push'] == pytest.approx(
        0.90 * report['payout_rate'] + 0.10, abs=1e-6
    )

    # The approximation is real: with a push the exact Kelly stake is
    # ev/(b*(p_win+p_loss)), so ev/(o-1) *understates* it. Checked by numerically
    # maximizing E[log(1+f*X)] over f: argmax 0.151515 vs implemented 0.136364.
    b = 1.10
    exact_kelly = ev_a / (b * (0.50 + 0.40))
    assert exact_kelly == pytest.approx(0.151515, abs=1e-6)
    assert exact_kelly > report['kelly_stakes'][0]['full_kelly']


def _numeric_growth_argmax(p_win: float, p_loss: float, p_push: float, odds: float) -> float:
    """f maximizing E[log(1+f*X)] for a win/loss/push bet, searched numerically.

    Deliberately does not use any closed form: this is the independent oracle the
    implemented Kelly figure is checked against.
    """
    b = odds - 1.0
    best_f, best_g = 0.0, -math.inf
    steps = 200_000
    for i in range(1, steps):
        f = i / steps / (1.0 / 0.5)  # scan f in (0, 0.5)
        growth = p_win * math.log(1.0 + f * b) + p_loss * math.log(1.0 - f) + p_push * math.log(1.0)
        if growth > best_g:
            best_g, best_f = growth, f
    return best_f


def test_full_kelly_times_push_correction_equals_numerically_maximized_fraction() -> None:
    """full_kelly * 1/(1-push_prob) must reproduce the growth-optimal fraction.

    The implementation reports ev/(o-1); the exact growth optimum divides that by
    the no-push mass (p_win+p_loss) = 1-push_prob. This asserts the relation the
    kelly_note now states, against a numerically maximized fraction rather than
    against a second closed form.
    """
    push = 0.10
    p_win, p_loss = 0.50, 0.40
    odds = 2.10
    assert p_win + p_loss + push == pytest.approx(1.0, abs=1e-12)

    report = UnifiedOddsEvaluator().evaluate_two_way(
        'M', 'INTL', 'AH', (odds, 1.80), ('AH_HOME', 'AH_AWAY'), line=-0.5,
        true_probs=(p_win, p_loss), push_prob=push, true_push_prob=push,
    )
    reported = report['kelly_stakes'][0]['full_kelly']
    # Hand-computed: ev = .5*1.10 - .4 = .15 ; .15/1.10 = 3/22 = 0.13636...
    assert reported == pytest.approx(3 / 22, abs=1e-6)

    corrected = reported * 1.0 / (1.0 - push)
    # 3/22 * 10/9 = 5/33 = 0.151515...
    assert corrected == pytest.approx(5 / 33, abs=1e-5)

    numeric = _numeric_growth_argmax(p_win, p_loss, push, odds)
    assert numeric == pytest.approx(5 / 33, abs=2e-3)
    assert corrected == pytest.approx(numeric, abs=2e-3)

    # And the uncorrected figure is genuinely below the optimum, so the note's
    # direction ("understates") is correct.
    assert reported < numeric
    # The note must actually say so, and cite the divisor.
    assert '低估' in report['kelly_note']
    assert '1-push_prob' in report['kelly_note']


def test_kelly_note_follows_the_push_into_ev_picks() -> None:
    matches = [
        {
            'match_id': 'P',
            'markets': [
                {'source_type': 'INTL', 'type': 'AH', 'line': -0.5, 'odds': (2.10, 1.80),
                 'outcomes': ('AH_HOME', 'AH_AWAY'), 'true_probs': (0.50, 0.40),
                 'push_prob': 0.10, 'true_push_prob': 0.10},
            ],
        }
    ]
    _active, picks, _comparisons = UnifiedOddsEvaluator().batch_evaluate_and_filter_ev(matches)
    assert picks, 'expected the +EV side to be picked'
    assert 'kelly_note' in picks[0]
    assert '近似' in picks[0]['kelly_note']


# --------------------------------------------------------------------------- #
# 7. Defect 3: break-even alias
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('odds', [CN_1X2, INTL_1X2, CN_OU, INTL_OU])
def test_break_even_alias_matches_legacy_key_and_is_one_over_odds(odds) -> None:
    ev = UnifiedOddsEvaluator()
    if len(odds) == 3:
        report = ev.evaluate_1x2('m', 'CN', odds)
    else:
        report = ev.evaluate_two_way('m', 'CN', 'OU', odds, ('OVER', 'UNDER'))

    break_even = [round(1.0 / o, 6) for o in odds]
    assert report['min_true_prob_thresholds'] == break_even
    assert report['break_even_prob_thresholds'] == break_even
    # The alias must be equal in value but a separate list (no shared mutation).
    assert report['break_even_prob_thresholds'] == report['min_true_prob_thresholds']
    assert report['break_even_prob_thresholds'] is not report['min_true_prob_thresholds']
    # 1/odds is the per-outcome break-even probability. The no-vig probability is
    # higher than it exactly when the outcome is a favourite (o below the payout
    # multiple), so the relationship is asserted via the vig identity instead:
    # 1/o_i = q_i * overround, with overround > 1.
    overround = report['overround']
    assert overround > 1.0
    assert all(
        be == pytest.approx(q * overround, abs=1e-5)
        for be, q in zip(report['break_even_prob_thresholds'], report['fair_probs_no_vig'])
    )


# --------------------------------------------------------------------------- #
# 8. Defect 4: validation bugs proven by test
# --------------------------------------------------------------------------- #
def test_negative_true_probability_is_rejected() -> None:
    """Old code only checked length and sum: (-0.1, 0.6, 0.5) sums to 1.0 and slipped through."""
    assert -0.1 + 0.6 + 0.5 == pytest.approx(1.0)  # the bug is reachable
    with pytest.raises(ValueError, match='概率'):
        UnifiedOddsEvaluator().evaluate_1x2('M', 'CN', (2.0, 3.0, 4.0), true_probs=(-0.1, 0.6, 0.5))


def test_nan_true_probability_is_rejected() -> None:
    """NaN defeats ``abs(sum - 1) > 1e-6``: the comparison is False, so it passed validation."""
    assert not (abs(math.nan + 0.6 + 0.4 - 1.0) > 1e-6)  # the bug is reachable
    with pytest.raises(ValueError, match='概率'):
        UnifiedOddsEvaluator().evaluate_1x2('M', 'CN', (2.0, 3.0, 4.0), true_probs=(math.nan, 0.6, 0.4))


def test_nan_two_way_true_probability_is_rejected() -> None:
    with pytest.raises(ValueError, match='概率'):
        UnifiedOddsEvaluator().evaluate_two_way(
            'M', 'INTL', 'OU', (2.0, 2.0), ('OVER', 'UNDER'),
            line=2.5, true_probs=(math.nan, 0.5),
        )


def test_nan_threshold_is_rejected() -> None:
    """NaN thresholds silently disabled every comparison: a NaN bound never trips."""
    # Proof the bug is reachable: a comparison against NaN is always False, so a
    # NaN ceiling can never flag anything anomalous. (Bound through a variable so
    # the linter does not read this deliberate NaN comparison as a typo.)
    nan_bound = float('nan')
    assert not bool(0.5 > nan_bound)
    assert not bool(nan_bound < 0.5)
    with pytest.raises(ValueError, match='有限'):
        UnifiedOddsEvaluator(max_allowed_margin_spread=math.nan)
    with pytest.raises(ValueError, match='有限'):
        UnifiedOddsEvaluator(max_allowed_odds_rel_diff=float('inf'))


def test_nan_odds_are_rejected() -> None:
    with pytest.raises(ValueError, match='有限'):
        UnifiedOddsEvaluator().evaluate_1x2('M', 'CN', (2.0, math.nan, 4.0))


def test_duplicate_same_source_market_is_flagged_instead_of_silently_dropped() -> None:
    """Two CN books on one key used to overwrite each other; only the last was compared."""
    ev = UnifiedOddsEvaluator()
    first = ev.evaluate_1x2('M', 'CN', (1.50, 3.60, 5.20))
    second = ev.evaluate_1x2('M', 'CN', (1.60, 3.50, 5.00))
    intl = ev.evaluate_1x2('M', 'INTL', (1.85, 3.55, 4.25))

    comp = ev.compare_cn_vs_intl_same_match([first, second, intl])[0]
    assert any('DUPLICATE_SOURCE_MARKET' in r for r in comp['market_anomaly_reasons'])
    assert comp['is_anomalous'] is True

    clean = ev.compare_cn_vs_intl_same_match([first, intl])[0]
    assert not any('DUPLICATE_SOURCE_MARKET' in r for r in clean['market_anomaly_reasons'])


def test_duplicate_source_markets_do_not_leak_into_ev_picks() -> None:
    matches = [
        {
            'match_id': 'DUP',
            'markets': [
                {'source_type': 'CN', 'type': '1X2', 'odds': (3.0, 4.0, 4.0),
                 'true_probs': (0.5, 0.25, 0.25)},
                {'source_type': 'CN', 'type': '1X2', 'odds': (3.0, 4.0, 4.0),
                 'true_probs': (0.5, 0.25, 0.25)},
                {'source_type': 'INTL', 'type': '1X2', 'odds': (1.50, 3.60, 5.20),
                 'true_probs': (0.68, 0.19, 0.13)},
            ],
        }
    ]
    ev = UnifiedOddsEvaluator(min_ev_thresholds={'CN': 0.0, 'INTL': 0.0})
    _active, picks, comparisons = ev.batch_evaluate_and_filter_ev(matches)
    assert comparisons[0]['is_anomalous'] is True
    assert picks == [], 'an anomalously duplicated market must not yield picks'


# --------------------------------------------------------------------------- #
# 9. filter_anomalous_markets_and_comparisons returns all three lists
# --------------------------------------------------------------------------- #
def test_filter_returns_three_disjoint_complete_lists() -> None:
    ev = demo_evaluator()
    evaluations = ev.evaluate_match(demo_matches()[0])
    demo_comparisons = ev.compare_cn_vs_intl_same_match(evaluations)
    assert len(demo_comparisons) == 2 and all(c['is_anomalous'] for c in demo_comparisons)

    # A second, genuinely clean CN/INTL pair. M_cn = 1-1/(1/1.80+1/1.85) = 0.087674,
    # M_intl = 0.035104, spread 0.052570 -- inside the band set below, and both the
    # relative odds diff (max .0833) and no-vig prob diff (max .0120) are under
    # their limits, so this pair must come back clean.
    clean_cn = ev.evaluate_two_way(
        'CLEAN_MATCH', 'CN', 'OU', (1.80, 1.85), ('OVER', 'UNDER'), line=2.5
    )
    clean_intl = ev.evaluate_two_way(
        'CLEAN_MATCH', 'INTL', 'OU', (1.95, 1.91), ('OVER', 'UNDER'), line=2.5
    )
    clean_comparisons_in = ev.compare_cn_vs_intl_same_match(
        [clean_cn, clean_intl],
        min_allowed_margin_spread=0.01,
        max_allowed_margin_spread=0.15,
        max_allowed_odds_rel_diff=0.15,
        max_allowed_fair_prob_diff=0.05,
    )
    assert len(clean_comparisons_in) == 1 and clean_comparisons_in[0]['is_anomalous'] is False

    all_evals = evaluations + [clean_cn, clean_intl]
    comparisons = demo_comparisons + clean_comparisons_in

    clean_evaluations, clean_comparisons, filtered = ev.filter_anomalous_markets_and_comparisons(
        all_evals, comparisons
    )

    # 1. clean_evaluations keeps exactly the entries whose key is not anomalous.
    anomalous_keys = {(c['match_id'], c['market_type'], c['line']) for c in comparisons if c['is_anomalous']}
    assert {
        (e['match_id'], e['market_type'], e['line']) for e in clean_evaluations
    } == {
        (e['match_id'], e['market_type'], e['line']) for e in all_evals
    } - anomalous_keys
    assert all(
        (e['match_id'], e['market_type'], e['line']) not in anomalous_keys for e in clean_evaluations
    )
    # Both books of the clean match survive; all four demo books are dropped.
    assert len(clean_evaluations) == 2
    assert {e['match_id'] for e in clean_evaluations} == {'CLEAN_MATCH'}

    # 2 + 3. The two comparison lists partition the input exactly.
    assert len(clean_comparisons) + len(filtered) == len(comparisons)
    assert all(not c['is_anomalous'] for c in clean_comparisons)
    assert all(c['is_anomalous'] for c in filtered)
    assert {c['match_id'] for c in clean_comparisons} == {'CLEAN_MATCH'}
    assert {c['match_id'] for c in filtered} == {'20261126_EPL_01'}
    # No comparison object is silently duplicated or replaced by a copy.
    assert all(any(c is orig for orig in comparisons) for c in clean_comparisons + filtered)


def test_filter_with_no_comparisons_keeps_everything() -> None:
    ev = demo_evaluator()
    evaluations = ev.evaluate_match(demo_matches()[0])
    clean_evaluations, clean_comparisons, filtered = ev.filter_anomalous_markets_and_comparisons(
        evaluations, []
    )
    assert clean_evaluations == evaluations
    assert clean_comparisons == [] and filtered == []


# --------------------------------------------------------------------------- #
# 10. Anomalous filtering end-to-end
# --------------------------------------------------------------------------- #
def test_anomalous_market_is_excluded_from_ev_when_requested() -> None:
    matches = [
        {
            'match_id': 'A',
            'markets': [
                # CN margin 0.2000 vs INTL 0.1203 -> spread 0.0797, above the
                # 0.05 ceiling set below, so this market must be flagged.
                # (High margin comes from high odds, not short ones: the overround
                # on 1.50/3.00/4.00 is 1.25.)
                {'source_type': 'CN', 'type': '1X2', 'odds': (1.50, 3.00, 4.00),
                 'true_probs': (0.68, 0.19, 0.13)},
                {'source_type': 'INTL', 'type': '1X2', 'odds': (1.50, 3.60, 5.20),
                 'true_probs': (0.68, 0.19, 0.13)},
            ],
        }
    ]
    ev = UnifiedOddsEvaluator(
        min_ev_thresholds={'CN': 0.0, 'INTL': 0.0},
        max_allowed_margin_spread=0.05,
        exclude_anomalous_from_ev=True,
    )
    comparisons = ev.compare_cn_vs_intl_same_match(ev.evaluate_match(matches[0]))
    assert comparisons[0]['margin_diff_cn_minus_intl'] > 0.05
    assert comparisons[0]['is_anomalous'] is True

    _active, filtered_picks, _c = ev.batch_evaluate_and_filter_ev(matches)
    assert filtered_picks == []

    _active2, unfiltered_picks, _c2 = ev.batch_evaluate_and_filter_ev(
        matches, exclude_anomalous=False
    )
    assert unfiltered_picks, 'the same market must produce picks once the filter is off'
    assert all(p['is_anomalous'] for p in unfiltered_picks)
    assert all(p['anomaly_reasons'] for p in unfiltered_picks)


def test_active_evaluations_are_the_filtered_set() -> None:
    ev = demo_evaluator()
    active, _picks, comparisons = ev.batch_evaluate_and_filter_ev(demo_matches())
    assert active == []  # every demo market is anomalous
    assert len(comparisons) == 2

    active_all, _picks, _comparisons = ev.batch_evaluate_and_filter_ev(
        demo_matches(), exclude_anomalous=False
    )
    assert len(active_all) == 4


def test_source_filter_restricts_picks_after_comparison() -> None:
    matches = [
        {
            'match_id': 'S',
            'markets': [
                {'source_type': 'CN', 'type': '1X2', 'odds': (2.0, 4.0, 4.0),
                 'true_probs': (0.55, 0.25, 0.20)},
                {'source_type': 'INTL', 'type': '1X2', 'odds': (2.0, 4.0, 4.0),
                 'true_probs': (0.55, 0.25, 0.20)},
            ],
        }
    ]
    ev = UnifiedOddsEvaluator()
    _active, cn_only, _c = ev.batch_evaluate_and_filter_ev(matches, source_filter='cn')
    assert [p['source_type'] for p in cn_only] == ['CN']
    _active, intl_only, _c = ev.batch_evaluate_and_filter_ev(matches, source_filter='INTL')
    assert [p['source_type'] for p in intl_only] == ['INTL']


def test_picks_are_sorted_by_ev_descending() -> None:
    # Deliberately ordered so the +EV outcomes arrive in ASCENDING EV order:
    # HOME_WIN .10 then AWAY_WIN .20. Insertion order is therefore not the
    # sorted order, so dropping the sort is observable.
    matches = [
        {
            'match_id': 'SORT',
            'markets': [
                {'source_type': 'CN', 'type': '1X2', 'odds': (2.20, 5.00, 6.00),
                 'true_probs': (0.50, 0.20, 0.30)},
            ],
        }
    ]
    _active, picks, _c = UnifiedOddsEvaluator().batch_evaluate_and_filter_ev(matches)
    assert [p['outcome'] for p in picks] == ['AWAY_WIN', 'HOME_WIN'], (
        'expected EV .80 then .10, sorted descending'
    )
    evs = [p['ev'] for p in picks]
    assert evs == [0.80, 0.10]
    assert evs == sorted(evs, reverse=True)


# --------------------------------------------------------------------------- #
# 11. Packaging: no top-level side effects, embedded payload in sync
# --------------------------------------------------------------------------- #
def test_module_has_no_main_block_and_imports_silently() -> None:
    source = (ROOT / 'unified_odds_evaluator.py').read_text(encoding='utf-8')
    assert 'if __name__' not in source, 'the demo must live in scripts/, not the embedded module'

    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, '-c', 'import unified_odds_evaluator'],
        cwd=str(ROOT), text=True, capture_output=True, check=False, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == '', f'import produced output: {proc.stdout!r}'


def test_module_is_registered_in_the_single_file_builder() -> None:
    builder = (ROOT / 'scripts' / 'build_single_file.py').read_text(encoding='utf-8')
    assert "'unified_odds_evaluator'" in builder

    app_text = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    start = '# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>'
    end = '# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<'
    assert start in app_text and end in app_text
    block = app_text.split(start, 1)[1].split(end, 1)[0]
    assert "'unified_odds_evaluator'" in block

    # The embedded copy must be byte-identical to the sibling module.
    payload = block.split('_sources = ', 1)[1].split('\n    for _name in', 1)[0]
    embedded = json.loads(payload)
    assert 'unified_odds_evaluator' in embedded
    assert embedded['unified_odds_evaluator'] == (
        ROOT / 'unified_odds_evaluator.py'
    ).read_text(encoding='utf-8'), 'embedded payload is stale; run scripts/build_single_file.py'


def test_embedded_module_installs_when_the_sibling_file_is_absent(tmp_path: Path) -> None:
    """Simulate single-file deployment: the app must install the module from the payload."""
    app_text = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    start = '# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>'
    end = '# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<'
    block = start + app_text.split(start, 1)[1].split(end, 1)[0] + end

    script = block + '\nimport unified_odds_evaluator as m\n'
    script += (
        "print(m.__file__)\n"
        "print(m.UnifiedOddsEvaluator().evaluate_1x2('m', 'CN', (2.0, 3.5, 4.0))['payout_rate'])\n"
    )
    (tmp_path / 'probe.py').write_text(script, encoding='utf-8')
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(tmp_path / 'probe.py')],
        cwd=str(tmp_path), text=True, capture_output=True, check=False, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == '<embedded:unified_odds_evaluator>'
    # 1 / (1/2.0 + 1/3.5 + 1/4.0) = 1/1.035714 = 0.965517.
    assert float(lines[1]) == pytest.approx(0.965517, abs=1e-6)
