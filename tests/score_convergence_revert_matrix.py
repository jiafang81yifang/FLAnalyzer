#!/usr/bin/env python3
"""单点 revert 证明：逐个删除守卫，验证对应测试变红，然后恢复。

每个用例：(名称, 文件, 原文, 替换成, 必须变红的测试)
不做 git 操作 —— 直接改文件、跑 pytest、按字节还原，保证可重复。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'score_convergence_engine.py'

CASES: list[tuple[str, Path, str, str, str]] = [
    (
        'G1 同向断言（禁止对冲）',
        MODULE,
        """    expected_bucket = _BUCKET_OF_DIRECTION[direction]
    forbidden = {'H': 'away', 'A': 'home'}.get(direction, '')
    for item in items:
        bucket = _score_bucket(item.get('score'))
        if bucket != expected_bucket:""",
        """    expected_bucket = _BUCKET_OF_DIRECTION[direction]
    forbidden = {'H': 'away', 'A': 'home'}.get(direction, '')
    for item in items:
        bucket = _score_bucket(item.get('score'))
        if False:""",
        'test_assert_locked_contract_rejects_hedge_across_directions',
    ),
    (
        'G2 长度恰为 2',
        MODULE,
        """    if len(items) != 2:
        raise ConvergenceContractError('locked_scores_length', len(items))""",
        '    pass',
        'test_assert_locked_contract_rejects_length_not_two',
    ),
    (
        'G3 ①!=② 去重',
        MODULE,
        """    if items[0].get('score') == items[1].get('score'):
        raise ConvergenceContractError('locked_scores_identical', items[0].get('score'))""",
        '    pass',
        'test_assert_locked_contract_rejects_duplicate_scores',
    ),
    (
        'G4 31 项值必填（零兜底）',
        MODULE,
        """    if result['missing']:
        result['status'] = 'aborted'
        result['reason'] = (
            f'{_ERROR_TEXT["step3_missing_inputs"]}'
            f'（31项值缺 {len(result["missing"])} 处）'
        )
        log.add('CANDIDATE_TREE', 'STEP_ABORTED', {'missing': len(result['missing'])})
        return result""",
        '    pass',
        'test_iron_law2_missing_single_pool31_market_value_aborts',
    ),
    (
        'G5 赔率 <=1.0 判异常',
        MODULE,
        """        if value <= 1.0:
            result['invalid_reason'] = f'{key} 赔率 ≤1.0: {value!r}'
            result['missing'].append(f'odds_1x2.{key}')
            continue""",
        '        pass',
        'test_iron_law2_odds_at_or_below_one_is_invalid_not_corrected',
    ),
    (
        'G6 加时/点球排除',
        MODULE,
        """        if regular_time_only and minute is not None and minute > REGULAR_TIME_MINUTES + STOPPAGE_MINUTES_MAX:
            record['excluded_reason'] = '超出常规时间+补时（加时/点球不计入）'
            out['excluded'].append(record)
            out['excluded_count'] += 1
            continue""",
        '        pass',
        'test_iron_law3_extra_time_and_penalties_excluded_and_counted',
    ),
    (
        'G7 两两投票并列不判平',
        MODULE,
        """    if len(winners) != 1:
        out['direction'] = None
        out['reason'] = _ERROR_TEXT['pairwise_tie']
        return out""",
        """    if len(winners) != 1:
        out['direction'] = 'D'
        out['direction_cn'] = '平'
        return out""",
        'test_pairwise_tie_is_not_draw_evidence_and_aborts_step1',
    ),
    (
        'G8 最低赔平局降权',
        MODULE,
        """    if bucket == 'draw' and direction in ('H', 'A'):
        code = (
            'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_HOME' if direction == 'H'
            else 'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_AWAY'
        )""",
        """    if False:
        code = (
            'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_HOME' if direction == 'H'
            else 'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_AWAY'
        )""",
        'test_iron_law1_no_hedge_when_lowest_odds_is_draw',
    ),
    (
        'G9 缺维 0 分（不补分）',
        MODULE,
        """        if not dim.get('available'):
            dim['score'] = 0.0
            result['missing_dimensions'].append(key)""",
        """        if not dim.get('available'):
            dim['score'] = weight
            result['missing_dimensions'].append(key)""",
        'test_step5_missing_dimension_scores_zero_and_is_listed',
    ),
    (
        'G10 conf_mod clamp',
        MODULE,
        """    return max(-0.5, min(0.5, number))""",
        """    return number""",
        'test_clamp_conf_mod_bounds_extremes',
    ),
    (
        'G11 盘口符号保留（abs 删除）',
        MODULE,
        """    if line < 0:
        side = 'home_gives'
    elif line > 0:
        side = 'away_gives'
    else:
        side = 'level'
    return {
        'valid': True,
        'raw': value,
        'line': line,""",
        """    line = abs(line)
    side = 'level'
    return {
        'valid': True,
        'raw': value,
        'line': line,""",
        'test_macro_keeps_signed_handicap_direction',
    ),
    (
        'G12 tier_diff 必须有来源',
        MODULE,
        """    if not tier.get('valid'):
        result['invalid'].append({'field': 'tier_diff', 'reason': tier.get('reason')})
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step4_missing_inputs']""",
        """    if not tier.get('valid'):
        tier = {'valid': True, 'tier_diff': 0, 'source': 'assumed', 'raw': {}}""",
        'test_tier_diff_requires_computable_source',
    ),
    (
        'G13 goal_expect 不静默回退',
        MODULE,
        """            if normalized not in {'high', '高', 'medium', '中', 'low', '低'}:
                result['invalid'].append({
                    'field': 'goal_expect',
                    'reason': f'{_ERROR_TEXT["invalid_goal_expect"]}: {payload.get("goal_expect")!r}',
                })""",
        '            pass',
        'test_step3_goal_expect_unknown_is_invalid_and_aborts',
    ),
    (
        'G14 聚合桶不缺席（31 项）',
        MODULE,
        """    tree: list[dict[str, Any]] = []
    for option in JCZQ_ALL_OPTIONS:""",
        """    tree: list[dict[str, Any]] = []
    for option in [s for s in JCZQ_ALL_OPTIONS if s not in JCZQ_OTHER_OPTIONS]:""",
        'test_step3_complete_tree_keeps_all_31_including_aggregate_buckets',
    ),
    (
        'G15 两两投票不判平（方向来源）',
        MODULE,
        """    if vote.get('available') and vote.get('direction'):""",
        """    if vote.get('available'):""",
        'test_pairwise_tie_is_not_draw_evidence_and_aborts_step1',
    ),
]


def run_test(node: str) -> bool:
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest', f'tests/test_score_convergence_engine.py::{node}', '-q'],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    return proc.returncode == 0


def main() -> int:
    rows: list[tuple[str, str, str, str]] = []
    for name, path, original, replacement, test in CASES:
        original_bytes = path.read_bytes()
        text = original_bytes.decode('utf-8')
        if original not in text:
            rows.append((name, test, 'ERROR', '原片段未找到'))
            continue
        try:
            path.write_text(text.replace(original, replacement, 1), encoding='utf-8')
            green_after_revert = run_test(test)
        finally:
            path.write_bytes(original_bytes)
        restored_green = run_test(test)
        status = 'RED (ok)' if not green_after_revert else 'STILL GREEN (BAD)'
        rows.append((name, test, status, 'restored=' + ('green' if restored_green else 'RED')))
    width = max(len(r[0]) for r in rows) + 2
    print(f'{"GUARD".ljust(width)}{"TEST".ljust(58)}AFTER REVERT   RESTORE')
    bad = 0
    for name, test, status, restore in rows:
        if not status.startswith('RED'):
            bad += 1
        print(f'{name.ljust(width)}{test.ljust(58)}{status.ljust(14)}{restore}')
    print(f'\n{len(rows) - bad}/{len(rows)} guards proven by single-point revert')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
