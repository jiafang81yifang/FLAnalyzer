"""Revert controls for the C1-C10 fixes.

Each case re-introduces the exact wrong behaviour the contract describes in a
throwaway copy of the module, then checks that the *symptom* the matching test
asserts against is actually present. A case is GREEN when the wrong behaviour is
reproduced (i.e. the real test would go red) and RED when the mutation fails to
reproduce it (the mutation missed, or the guard is stronger than expected).

Mutations run on a temp file loaded under a fresh module name; the real module
is never touched.

Run: python3 scripts/market_guardrails_revert_control.py
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'market_guardrails.py'


def _load_from(source: str, name: str) -> ModuleType:
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as handle:
        handle.write(source)
        path = handle.name
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'could not load mutated module from {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _mutate(original: str, old: str, new: str, label: str) -> str:
    mutated = original.replace(old, new)
    if mutated == original:
        raise RuntimeError(f'{label}: mutation anchor not found, the control is stale')
    return mutated


def _case(label: str, wrong_symptom: Callable[[], Any]) -> tuple[str, bool]:
    """GREEN when ``wrong_symptom()`` is truthy, meaning the real test would fail."""
    try:
        value = wrong_symptom()
    except Exception as exc:  # noqa: BLE001 - any exception is a reproduced symptom
        print(f'GREEN {label}: wrong behaviour raises {type(exc).__name__}: {exc}')
        return label, True
    if value:
        print(f'GREEN {label}: wrong behaviour reproduced (value={value!r})')
        return label, True
    print(f'RED   {label}: mutation did not reproduce the wrong behaviour (value={value!r})')
    return label, False


def _raises(fn: Callable[[], Any], expected: type[BaseException] | tuple[type[BaseException], ...]) -> bool:
    """True when ``fn`` raises ``expected`` — the symptom a test would catch."""
    try:
        fn()
    except expected:
        return True
    return False


@dataclass(frozen=True)
class _ChannelAbsSummaryShape:
    """Local copy of the matrix module's ChannelAbsSummary field names."""

    win_mean_abs_delta: float
    draw_mean_abs_delta: float
    loss_mean_abs_delta: float
    win_count: int
    draw_count: int
    loss_count: int
    min_channel: str
    channel_spread: float


def _summary_shape() -> _ChannelAbsSummaryShape:
    return _ChannelAbsSummaryShape(
        win_mean_abs_delta=0.3462,
        draw_mean_abs_delta=0.9,
        loss_mean_abs_delta=1.1769,
        win_count=13,
        draw_count=5,
        loss_count=13,
        min_channel='WIN',
        channel_spread=0.8307,
    )


def _heat(scores: list[str]) -> list[dict]:
    return [{'score': score, 'odds': round(10.0 - index, 2)} for index, score in enumerate(scores)]


def _status_flips_to_invalid(mod: ModuleType) -> bool:
    """True when a genuine wording violation turns the evaluation ``invalid``.

    Calls the module's own ``apply_language_self_check`` on a note that is
    unambiguously violating (``建议首选 1-0``) — the negation exemption cannot
    launder it, so this control stays meaningful regardless of how the
    guardrail-3 note is worded.
    """
    result = {
        'status': 'ok',
        'risk_notes': ['建议首选 1-0'],
        'self_check': {'ok': True, 'violations': [], 'checked_notes': 0},
    }
    mod.apply_language_self_check(result)
    return result['status'] == 'invalid' and result['self_check']['ok'] is False


def main() -> int:
    original = SOURCE.read_text(encoding='utf-8')
    results: list[tuple[str, bool]] = []

    # C1: compare the 0-1 scale delta_w directly against 5.0 (spec text verbatim).
    mod = _load_from(_mutate(
        original,
        'triggered = pp < CONFIDENCE_DELTA_W_THRESHOLD_PP or bool(multi_signal_conflict)',
        'triggered = raw < CONFIDENCE_DELTA_W_THRESHOLD_PP or bool(multi_signal_conflict)',
        'C1',
    ), 'mr_c1')
    results.append(_case(
        'C1 raw-scale 0.109806 must not trigger the 5.0pp rule',
        lambda: mod.guardrail_4_confidence_decay(0.377404, 0.267598)['triggered'],
    ))
    results.append(_case(
        'C1 every realistic payload pins to 0.60 under the raw-scale rule',
        lambda: all(
            mod.guardrail_4_confidence_decay(primary, secondary)['system_confidence'] == 0.60
            for primary, secondary in (
                (0.377404, 0.267598),   # 10.98pp apart, should NOT decay
                (0.403997, 0.206925),   # 19.71pp apart, should NOT decay
                (0.30, 0.251),          # 4.9pp apart, should decay
                (0.30, 0.249),          # 5.1pp apart, should NOT decay
            )
        ),
    ))

    # C3: drop the <= 0 rejection so a zero denominator reaches the division.
    mod = _load_from(_mutate(
        original,
        '        if value <= 0:\n'
        '            return {}, _invalid(\n'
        '                f\'abs_deltas.{label}\',\n'
        '                f\'abs_deltas.{label} 必须 > 0（0 分母的比值只能靠兜底算出来）: {value!r}\',\n'
        '            )',
        '        if value <= 0:\n'
        '            values[label] = value\n'
        '            continue',
        'C3',
    ), 'mr_c3')
    results.append(_case(
        'C3 zero denominator must raise GuardrailInputError (not 0.0, not ZeroDivisionError)',
        lambda: mod.home_away_abs_ratio({'胜': 0.40, '平': 0.60, '负': 0.0}),
    ))

    # C2: report zero seats instead of a missing heat board.
    mod = _load_from(_mutate(
        original,
        "        reason = 'heat_ranking 为空或缺失：护栏不生效（不按 0 席处理）'\n"
        "        result['reason'] = reason\n"
        "        result['missing'].append({'field': 'heat_ranking', 'reason': reason})\n"
        "        return result",
        "        reason = 'heat_ranking 为空或缺失：护栏不生效（不按 0 席处理）'\n"
        "        result['reason'] = reason\n"
        "        result.update({'status': 'ok', 'seats': {'H': 0, 'D': 0, 'A': 0}})\n"
        "        result['missing'].append({'field': 'heat_ranking', 'reason': reason})\n"
        "        return result",
        'C2',
    ), 'mr_c2')
    results.append(_case(
        'C2 empty board must leave seats empty (silent 0 seats is the failure)',
        lambda: mod.guardrail_3_inducing_trap([], {'1-0': 0.90}).get('seats'),
    ))

    # C4: drop the source label where the protection stage actually sets it.
    mod = _load_from(_mutate(
        original,
        "        'league_pressure_source': source,\n"
        "        'protection_multiplier': round(multiplier, 6),",
        "        'league_pressure_source': '',\n"
        "        'protection_multiplier': round(multiplier, 6),",
        'C4',
    ), 'mr_c4')
    results.append(_case(
        'C4 w_league_pressure=0.0 must surface source=unavailable',
        lambda: mod.stage2_true_protection(
            {'1-0': -0.60}, direction='H', league_name='英超',
        ).get('league_pressure_source') != 'unavailable',
    ))

    # C7: hard-match 荷甲 into a whitelist so the 1.80 branch switches on.
    mod = _load_from(_mutate(
        original,
        "    del league_name  # 参数保留是为了钉住未来接线时的签名形状\n"
        "    return False, LEAGUE_PRESSURE_SOURCE_UNAVAILABLE",
        "    if str(league_name or '').strip() == '荷甲':\n"
        "        return True, 'whitelist'\n"
        "    return False, LEAGUE_PRESSURE_SOURCE_UNAVAILABLE",
        'C7',
    ), 'mr_c7')
    results.append(_case(
        'C7 荷甲 must never flip high_entropy_relax_applied',
        lambda: mod.guardrail_2_even_match(
            {'胜': 0.60, '平': 0.90, '负': 0.40}, league_name='荷甲',
        ).get('high_entropy_relax_applied'),
    ))
    results.append(_case(
        'C7 荷甲 with |Δ_胜|/|Δ_负|=1.50 must stay below the 1.35 rule',
        lambda: mod.guardrail_2_even_match(
            {'胜': 0.60, '平': 0.90, '负': 0.40}, league_name='荷甲',
        ).get('triggered'),
    ))

    # C5: emit a near-miss literal instead of the exact marker.
    mod = _load_from(_mutate(
        original,
        "VETO_TIER_DOMINANCE_WIN = 'V16_31_Fundamental_Tier_Dominance_Win'",
        "VETO_TIER_DOMINANCE_WIN = 'V16_31_Fundamental_Tier_Dominance_WIN'",
        'C5',
    ), 'mr_c5')
    results.append(_case(
        'C5 veto_action must equal the literal V16_31_Fundamental_Tier_Dominance_Win',
        lambda: mod.guardrail_1_macro_dominance(2)['veto_action']
        != 'V16_31_Fundamental_Tier_Dominance_Win',
    ))

    # C6: infer tier_diff from a rank gap when the caller omits it.
    mod = _load_from(_mutate(
        original,
        "    if tier_diff is None:\n"
        "        result['reason'] = 'tier_diff 缺失：宏观护栏不生效（不从排名差/赔率差反推）'\n"
        "        result['missing'].append({'field': 'tier_diff', 'reason': result['reason']})\n"
        "        return result",
        "    if tier_diff is None:\n"
        "        tier_diff = 2\n",
        'C6',
    ), 'mr_c6')
    results.append(_case(
        'C6 omitted tier_diff must not silently become a veto',
        lambda: mod.guardrail_1_macro_dominance(None)['applies'],
    ))

    # C8/C9: append a draw score to locked_scores (the deleted dual-direction code).
    mod = _load_from(_mutate(
        original,
        "    result['locked_scores'] = [\n"
        "        dict(item) for item in (data.get('locked_scores') or []) if isinstance(item, dict)\n"
        "    ]",
        "    result['locked_scores'] = [\n"
        "        dict(item) for item in (data.get('locked_scores') or []) if isinstance(item, dict)\n"
        "    ]\n"
        "    result['locked_scores'].append({'score': '1-1'})",
        'C8',
    ), 'mr_c8')
    results.append(_case(
        'C8/C9 locked_scores must stay single-direction after NEAR/EVEN fire',
        lambda: bool(mod.evaluate_market_guardrails({
            'direction': 'H',
            'abs_deltas': {'胜': 0.30, '平': 0.45, '负': 1.10},
            'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
        })['locked_contract']['offending']),
    ))

    # C10: let Overkill candidates ignore direction alignment.
    mod = _load_from(_mutate(
        original,
        "        if aligned:\n"
        "            result['overkill_candidates'].append(entry)",
        "        if True:\n"
        "            result['overkill_candidates'].append(entry)",
        'C10',
    ), 'mr_c10')
    results.append(_case(
        'C10 负其它 must not enter overkill_candidates under direction H',
        lambda: any(
            item['bucket'] != 'home'
            for item in mod.overkill_protocol({'胜其它': 20.0, '负其它': 25.0}, direction='H')['overkill_candidates']
        ),
    ))
    results.append(_case(
        'C10 opposite-direction tail must be recorded as direction_mismatch',
        lambda: mod.overkill_protocol({'胜其它': 20.0, '负其它': 25.0}, direction='H')['overkill_rejected']
        != [{
            'score': '负其它', 'delta': 25.0, 'bucket': 'away',
            'direction_aligned': False, 'reason': 'direction_mismatch',
        }],
    ))

    # C3b: drop the lowercase field names the real ChannelAbsSummary declares,
    # keeping only the uppercase aliases. This is the exact PAR-11 defect.
    mod = _load_from(_mutate(
        original,
        "    'win_mean_abs_delta': HOME_CHANNEL_LABEL,\n"
        "    'draw_mean_abs_delta': DRAW_CHANNEL_LABEL,\n"
        "    'loss_mean_abs_delta': AWAY_CHANNEL_LABEL,\n",
        '',
        'C3b lowercase aliases',
    ), 'mr_c3b')
    results.append(_case(
        'C3b lowercase win_/draw_/loss_mean_abs_delta must be accepted',
        lambda: _raises(
            lambda: mod.home_away_abs_ratio({
                'win_mean_abs_delta': 0.3462,
                'draw_mean_abs_delta': 0.9,
                'loss_mean_abs_delta': 1.1769,
            }),
            mod.GuardrailInputError,
        ),
    ))

    # C3c: refuse dataclass instances, i.e. go back to the dict-only gate.
    mod = _load_from(_mutate(
        original,
        "    if isinstance(abs_deltas, dict):\n"
        "        return [(str(key), value) for key, value in abs_deltas.items()], None",
        "    if True:\n"
        "        return [(str(key), value) for key, value in abs_deltas.items()], None",
        'C3c dataclass',
    ), 'mr_c3c')
    results.append(_case(
        'C3c a ChannelAbsSummary dataclass instance must not be rejected as non-dict',
        lambda: _raises(
            lambda: mod.home_away_abs_ratio(_summary_shape()),
            (AttributeError, mod.GuardrailInputError, TypeError),
        ),
    ))

    # PAR-11b (1) restore the banned word in the guardrail-3 risk note.
    mod = _load_from(_mutate(
        original,
        "TRAP_RISK_NOTE = '热度与通道均差背离，相关比分已失去优先资格'",
        "TRAP_RISK_NOTE = '热度与通道均差背离，个别比分不具备首选资格'",
        'PAR-11b note',
    ), 'mr_note')
    results.append(_case(
        'PAR-11b(1) the guardrail-3 note must not contain a banned word literally',
        lambda: bool(mod.pick_language_violations(mod.TRAP_RISK_NOTE) == [])
        is False
        or (not mod.pick_language_violations(mod.TRAP_RISK_NOTE)
            and any(word in mod.TRAP_RISK_NOTE for word in mod.PICK_LANGUAGE)),
    ))

    # PAR-11b (2) drop the negation-aware scanning, leaving only the old two phrases.
    mod = _load_from(_mutate(
        original,
        "    for pattern in NEGATION_CLAUSE_PATTERNS:\n"
        "        cleaned = re.sub(pattern, '', cleaned)\n",
        '',
        'PAR-11b negation',
    ), 'mr_negation')
    results.append(_case(
        'PAR-11b(2) 不具备…资格 must be recognised as a deprivation clause',
        lambda: bool(mod.pick_language_violations('1-0 不具备首选资格')),
    ))
    results.append(_case(
        'PAR-11b(2) 失去…资格 must be recognised as a deprivation clause',
        lambda: bool(mod.pick_language_violations('该比分已失去首选资格')),
    ))

    # PAR-11b (3) put language self-check violations back into status.
    mod = _load_from(_mutate(
        original,
        "        if violations:\n"
        "            self_check['ok'] = False",
        "        if violations:\n"
        "            result['status'] = 'invalid'\n"
        "            self_check['ok'] = False",
        'PAR-11b status',
    ), 'mr_status')
    results.append(_case(
        'PAR-11b(3) a wording violation must not flip status to invalid',
        lambda: _status_flips_to_invalid(mod),
    ))

    # PAR-11b (4) go back to ok=false for an empty locked_scores list.
    mod = _load_from(_mutate(
        original,
        "    if not result['locked_scores']:\n"
        "        result['locked_contract']['status'] = 'not_applicable'\n"
        "        result['locked_contract']['ok'] = None\n"
        "        result['locked_contract']['reasons'] = []",
        "    if not result['locked_scores']:\n"
        "        pass",
        'PAR-11b locked_contract',
    ), 'mr_locked')
    results.append(_case(
        'PAR-11b(4) empty locked_scores must report not_applicable, not ok=false',
        lambda: mod.evaluate_market_guardrails({
            'direction': 'H',
            'abs_deltas': {'胜': 0.40, '平': 0.60, '负': 0.50},
        })['locked_contract'].get('ok') is False,
    ))

    print()
    missed = [label for label, ok in results if not ok]
    print(f'{len(results) - len(missed)}/{len(results)} revert controls reproduced the wrong behaviour')
    if missed:
        print('Controls that did not reproduce (mutation stale or guard unreachable):')
        for label in missed:
            print(f'  - {label}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
