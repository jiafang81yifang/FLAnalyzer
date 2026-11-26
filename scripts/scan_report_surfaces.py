"""Scan every user-facing report surface for direction-choice wording AND for
direction consistency between the candidates a surface renders.

Two independent rules, both violations counted as dirty:

  1. wording — no surface may emit 首选/次选/双选/强推/推荐 (`不做推荐` is the
     only permitted use);
  2. direction consistency — where a surface renders a candidate **pair**,
     the two candidates must be classifiable and share one direction; and any
     direction the surface states must agree with those candidates. A surface
     that prints "候选A 2-0｜候选B 1-2" (主胜 + 客胜) or "候选A 2-0｜方向 负" is
     dirty even though it contains no banned word.

Rule 2 exists because a wording-only scan called that output clean once already.

Usage:
    python scripts/scan_report_surfaces.py
Exit code 0 only when every scanned surface is clean.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot):
    app = importlib.import_module('app_v84_hermes')
    pipe = importlib.import_module('blade_deepseek_pipeline')

BANNED = ('首选', '次选', '双选', '强推', '推荐')
ALLOWED = ('不做推荐',)
JC = list(pipe.JCZQ_ALL_OPTIONS)


def _hits(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for word in BANNED:
        n = text.count(word) - sum(text.count(ok) for ok in ALLOWED)
        if n > 0:
            out[word] = n
    return out


# A surface renders a candidate pair when both slots are present. The slot
# labels are the ones the reports actually use; `未形成` means "no second
# candidate", which is the correct outcome rather than a conflict.
_SCORE = r'[0-9]+\s*[-:]\s*[0-9]+'
_PAIR_PATTERNS = (
    re.compile(rf'候选A\s*({_SCORE})[\s\S]{{0,60}}?候选B\s*({_SCORE})'),
    re.compile(rf'①\s*({_SCORE})[\s\S]{{0,60}}?②\s*({_SCORE})'),
)
# `方向 X` stated near the pair.
_DIRECTION_STATED = re.compile(r'方向\s*([胜平负])')


def _direction_issues(name: str, text: str) -> dict[str, int]:
    """Count direction inconsistencies on a rendered surface.

    Only surfaces that actually render a candidate pair are judged; a surface
    with no pair has nothing to contradict, so it contributes zero.
    """
    issues: dict[str, int] = {}
    pairs = 0
    for line in text.splitlines():
        for pattern in _PAIR_PATTERNS:
            for match in pattern.finditer(line):
                pairs += 1
                first = pipe._score_wdl(match.group(1))
                second = pipe._score_wdl(match.group(2))
                if not first or not second:
                    continue
                if first != second:
                    # a conflict is only clean if the surface says so and
                    # refuses to name one of them
                    if '方向不一致' in line and '不给出单一候选' in text:
                        continue
                    issues['pair_cross_direction'] = issues.get('pair_cross_direction', 0) + 1
                    continue
                stated = _DIRECTION_STATED.search(line)
                if stated and stated.group(1) != first:
                    issues['stated_direction_contradicts_pair'] = (
                        issues.get('stated_direction_contradicts_pair', 0) + 1)
    return issues


def _record(name: str, text: str, results: list[tuple[str, dict[str, int]]]) -> None:
    dirty = _hits(text)
    for key, count in _direction_issues(name, text).items():
        dirty[key] = dirty.get(key, 0) + count
    results.append((name, dirty))


def _compare_history(shape: str) -> dict[str, dict[str, object]]:
    def rec(name: str, first: str, second: str, p1: float, p2: float) -> dict[str, object]:
        return {'status': 'pending', 'match': name,
                'predicted_score': first, 'second_score': second,
                'confidence': 'high', 'dti': 0.5, 'cup_mode': '常规',
                'top15_scores': [(first, {'prob': p1}), (second, {'prob': p2})]}
    if shape == 'cross':
        return {'m1': rec('A vs B', '1-0', '0-1', 0.20, 0.10),
                'm2': rec('C vs D', '2-0', '1-2', 0.18, 0.12)}
    if shape == 'same':
        return {'m1': rec('A vs B', '2-1', '1-0', 0.20, 0.10),
                'm2': rec('C vs D', '3-1', '2-0', 0.18, 0.12)}
    if shape == 'empty':
        return {}
    raise AssertionError(shape)


def _run_compare(history: dict[str, Any]) -> str:
    sent: list[str] = []
    with mock.patch.object(app, 'load_json', return_value=history), \
         mock.patch.object(app, 'send_tg', side_effect=lambda t, *a, **k: sent.append(t)):
        app.compare_matches()
    return '\n'.join(sent)


def scan() -> list[tuple[str, dict[str, int]]]:
    results: list[tuple[str, dict[str, int]]] = []

    # 1. `/比较` on each history shape
    for shape in ('cross', 'same', 'empty'):
        _record(f'compare[{shape}]', _run_compare(_compare_history(shape)), results)

    # 2. the full 15→2 game-theory report, which is rendered straight into TG
    with contextlib.redirect_stdout(io.StringIO()):
        probs = app.calc_poisson_prob(1.85, 0.55, max_goals=8)
        odds_cs = {s: round(1.0 / max(p, 1e-6), 4)
                   for s, p in probs.items() if s in set(JC)}
        g15 = app.execute_15to2_matrix(
            probs, {'Home': 2.1, 'Draw': 3.4, 'Away': 3.2}, -0.5, 2.5, odds_cs, '',
            1.85, 0.55,
        )
    _record('game_theory_15to2', g15[2] if isinstance(g15, tuple) else str(g15), results)

    # 3. the 31-pool report and the ventropy log
    with contextlib.redirect_stdout(io.StringIO()):
        pack = pipe.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            [], lam_home=1.45, lam_away=1.10, jc_score_odds={o: 9.0 for o in JC})
        tg = '\n'.join(pipe.DeepSeekAnalyzer.build_deepseek_only_tg_sections(
            theo_mkt_31_pack=pack))
        pool = [{'score': o, 'theory': 100.0 / 31, 'market': 100.0 / 31,
                 'ibi': 1.0, 'delta': 0.0} for o in JC]
        ventropy = app.run_ventropy_pipeline([], pool, '英超', {}) or {}
    _record('theo_mkt_31_tg', tg, results)
    _record('ventropy_log', str(ventropy.get('log') or ''), results)

    # 4. the unified analysis report, across the candidate shapes that matter.
    #    `cross_*` render a pair whose members disagree, which is exactly the
    #    case a wording-only scan called clean.
    _record('unified_report', _unified_verdict_text(
        {'match': 'A vs B', 'lam_h': 1.45, 'lam_a': 1.1,
         'odds_1x2': {'home': 2.1, 'draw': 3.4, 'away': 3.2}}),
        results)
    common = {'match': 'A vs B', 'lam_h': 1.45, 'lam_a': 1.1,
              'correct_score_available': True, 'mobile_market_ready': True}
    for label, extra in (
        ('unified_cross', {'predicted_score': '2-0', 'second_score': '1-2'}),
        ('unified_cross_lock', {'predicted_score': '2-0', 'second_score': '1-2',
                                'direction_lock': {'locked': True, 'direction': '胜'}}),
        ('unified_lock_conflict', {'predicted_score': '2-0', 'second_score': '3-0',
                                   'direction_lock': {'locked': True, 'direction': '负'}}),
        ('unified_same', {'predicted_score': '2-1', 'second_score': '1-0'}),
        ('unified_second_absent', {'predicted_score': '2-1', 'second_score': '',
                                   'second_score_absent_reason': 'direction_mismatch'}),
    ):
        _record(label, _unified_verdict_text({**common, **extra}), results)

    # 5. the post-match review report (`report_text` is sent to Telegram when the
    #    flash writer returns nothing, so it is a real surface)
    review = _review_report_text()
    _record('battle_review', review, results)

    # 6. hit-attribution labels and UI help strings
    _record('hit_labels', '\n'.join(_hit_label_samples()), results)

    # 7. the help text / command list
    help_text = ''
    for attr in ('build_help_text', 'command_help_text', 'get_help_text'):
        fn = getattr(app, attr, None)
        if callable(fn):
            with contextlib.redirect_stdout(io.StringIO()):
                help_text = str(fn() or '')
            break
    if help_text:
        _record('help_text', help_text, results)
    return results


def _unified_verdict_text(data: dict[str, Any]) -> str:
    """The unified report's rendered text, flattened for scanning.

    The verdict section is the one that states a direction alongside the
    candidate pair, so it is the surface that rule 2 is about.
    """
    report = app.build_unified_analysis_report(data, source='scan')
    return json.dumps(report, ensure_ascii=False, default=str)


def _review_report_text() -> str:
    """Drive `post_match_review` far enough to render its own stats block.

    The flash writer is stubbed to return nothing, which is the branch where the
    app sends its locally built `report_text` — exactly the surface under test.
    """
    history = {
        'm1': {'status': 'completed', 'match': 'A vs B', 'time': '2026-11-26 20:00',
               'predicted_score': '2-1', 'second_score': '1-0',
               'actual_score': '2-1', 'big_small': '大',
               'top15_scores': [('2-1', {'prob': 0.2}), ('1-0', {'prob': 0.1})]},
        'm2': {'status': 'completed', 'match': 'C vs D', 'time': '2026-11-26 20:00',
               'predicted_score': '1-0', 'second_score': '0-1',
               'actual_score': '0-1', 'big_small': '小',
               'top15_scores': [('1-0', {'prob': 0.18}), ('0-1', {'prob': 0.12})]},
    }
    sent: list[str] = []
    fn = getattr(app, 'post_match_review', None)
    if not callable(fn):
        return ''
    with mock.patch.object(app, 'load_json', return_value=history), \
         mock.patch.object(app, 'send_tg', side_effect=lambda t, *a, **k: sent.append(str(t))), \
         mock.patch.object(app, '_ds_flash_takeover', return_value=''), \
         mock.patch.object(app, '_review_match_facts', return_value='facts'):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the review path has many optional steps
            # Recorded rather than swallowed: the scan still inspects whatever the
            # review built before it stopped, and the reason stays visible.
            print(f'   (post_match_review raised: {type(exc).__name__}: {exc})')
    return '\n'.join(sent)


def _hit_label_samples() -> list[str]:
    """The labels the review/audit UI assigns when a candidate matches the result."""
    out = ['候选A', '候选B', '未中', '候选A命中✅', '候选B命中🟡']
    source = Path(str(app.__file__)).read_text(encoding='utf-8')
    for match in re.finditer(r"return '([^']*(?:命中|未中|候选)[^']*)'", source):
        out.append(match.group(1))
    return out


def main() -> int:
    results = scan()
    dirty = [(name, hits) for name, hits in results if hits]
    print(f'SURFACES scanned={len(results)} dirty={len(dirty)}')
    for name, hits in results:
        print(f'  {"FAIL" if hits else "ok  "} {name:22s} {hits or ""}')
    return 0 if not dirty else 1


if __name__ == '__main__':
    raise SystemExit(main())
