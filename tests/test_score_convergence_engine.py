"""六步收敛引擎测试：期望值全部独立手算，不用被测函数算期望。"""
from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

import score_convergence_engine as engine

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'score_convergence_engine.py'


# =========================================================================== #
# 独立手算的夹具（全部为常量，不调用被测函数）
# =========================================================================== #
# 一份主队热门、31 项齐备的池：主胜 1-0..5-2 计 12 项 + 胜其它；平局 4 项 + 平其它；
# 客胜 12 项 + 负其它 —— 共 31。市场/理论值按下表给定（百分比）。
_POOL_FIXTURE = {
    # 主胜 13 项：市场 4.0%，理论 3.0%
    **{s: {'market': 4.0, 'theory': 3.0} for s in (
        '1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2', '5-0', '5-1', '5-2', '胜其它',
    )},
    # 平局 5 项：市场 2.0%，理论 2.5% → 市场低于理论（市冷不热）
    **{s: {'market': 2.0, 'theory': 2.5} for s in ('0-0', '1-1', '2-2', '3-3', '平其它')},
    # 客胜 13 项：市场 1.0%，理论 1.5%
    **{s: {'market': 1.0, 'theory': 1.5} for s in (
        '0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4', '0-5', '1-5', '2-5', '负其它',
    )},
}


def pool_rows() -> list[dict]:
    """把常量夹具展开成 31 行（顺序与 JCZQ_ALL_OPTIONS 一致）。"""
    return [
        {'score': s, 'market': _POOL_FIXTURE[s]['market'], 'theory': _POOL_FIXTURE[s]['theory']}
        for s in engine.JCZQ_ALL_OPTIONS
    ]


def base_payload() -> dict:
    """一份六步全绿的输入；各测试只改自己关心的字段。"""
    return {
        # 1X2：去水前 1/1.60+1/4.00+1/5.50 = 0.625+0.25+0.181818... = 1.0568181...
        'odds_1x2': {'home': 1.60, 'draw': 4.00, 'away': 5.50},
        'lam_home': 1.70,
        'lam_away': 1.00,
        # 大小球：λ 合计 2.70 > 2.5 → OVER
        'ou_line': 2.5,
        'ou_over_odds': 1.90,
        'ou_under_odds': 1.90,
        'pool31_rows': pool_rows(),
        # 市热：胜 12 条最多 → 与 Step1 主胜同向
        'heat_tg_lines': ['🔥【市热矩阵】', '  胜平负: 胜=12(50.0%) 平=6(25.0%) 负=6(25.0%)'],
        # 市短：胜 9 条最多 → 同向
        'mkt_over_theo_tg_lines': ['📊【市短矩阵】', '  胜平负: 胜=9(60.0%) 平=3(20.0%) 负=3(20.0%)'],
        'asian_handicap': -0.75,          # 主让半一：与主胜同向 = 顺局
        'half_time_1x2': {'home': 2.10, 'draw': 2.30, 'away': 5.00},   # 半场主胜最热
        'half_time_ou_line': 1.0,         # 半场期望 2.70/2 = 1.35 > 1.0 → OVER
        'ou_open_line': 2.25,             # 2.25 -> 2.5 上调 → OVER
        'league_rank_home': 2,            # 名次差 12-2=10 → 10/6=1.67 → 1 档
        'league_rank_away': 12,
        'borsa_signals_count': 5,
        'immutable_factor_total': 5,      # 无变更 → confidence 1.0
        'heat_top8': [('1-0', 6.50), ('2-0', 7.20)],
    }


# =========================================================================== #
# 主规范 §4.5 / §4.8：六步 happy path
# =========================================================================== #
def test_six_step_happy_path_matches_hand_computation() -> None:
    """六步全绿：方向/路径/候选/得分/锁定值全部与手算一致。

    手算 Step1（去水）：
        inv = 1/1.60 + 1/4.00 + 1/5.50 = 0.625 + 0.25 + 0.181818... = 1.0568181...
        market = 0.625/1.0568181=0.591398, 0.25/1.0568181=0.236559,
                 0.181818/1.0568181=0.172043   （和 = 1.000000）
    手算 Step2：λ 合计 = 1.70+1.00 = 2.70 > 2.5 → OVER
    """
    out = engine.run_score_convergence(base_payload())

    assert out['status'] == 'ok'
    assert out['direction'] == 'H'
    assert out['direction_source'] == 'model_market'
    assert out['ou_path'] == 'OVER'

    market = out['step1']['market_probs']
    assert market['home'] == pytest.approx(0.591398, abs=1e-6)
    assert market['draw'] == pytest.approx(0.236559, abs=1e-6)
    assert market['away'] == pytest.approx(0.172043, abs=1e-6)
    assert sum(market.values()) == pytest.approx(1.0, abs=1e-9)

    assert out['step2']['expected_goals_base'] == pytest.approx(2.70, abs=1e-9)
    assert out['step2']['ou_path'] == 'OVER'

    # 候选 = 主胜 13 项里总进球 > 2.5 的项 + 胜其它
    # 手数：主胜 13 项中 total>2.5 的有 2-1(3),3-0(3),3-1(4),3-2(5),4-0(4),4-1(5),
    #       4-2(6),5-0(5),5-1(6),5-2(7) = 10 项，加 胜其它 = 11 项。
    assert out['candidate_count'] == 11
    assert len(out['locked_scores']) == 2
    for item in out['locked_scores']:
        assert item['bucket'] == 'home'
        assert item['score'] != ''
    assert out['locked_scores'][0]['score'] != out['locked_scores'][1]['score']

    # 权重分解各项之和 == 总分
    for item in out['locked_scores']:
        assert sum(item['components'].values()) == pytest.approx(item['total'], abs=1e-9)

    # 六维全齐 → 满分 100（每维 agree）
    assert out['consistency_score'] == pytest.approx(100.0, abs=1e-6)
    assert out['conflict_count'] == 0
    assert out['aborted_steps'] == []


def test_locked_scores_are_always_two_distinct_same_direction_scores() -> None:
    """铁律 1：locked_scores 恰 2 条、互不相同、同一方向。"""
    out = engine.run_score_convergence(base_payload())
    locked = out['locked_scores']
    assert len(locked) == 2
    assert locked[0]['score'] != locked[1]['score']
    assert {item['bucket'] for item in locked} == {'home'}
    assert out['direction'] == 'H'


# =========================================================================== #
# 铁律 1 负向：无对冲、无默认平
# =========================================================================== #
def test_iron_law1_no_hedge_when_lowest_odds_is_draw() -> None:
    """最低赔落在平局通道 + 方向主胜：记冲突码、降权、方向不变、比分仍同向。

    这是 V16.22 段「胜/平双选 + 防波堤」被删除后的行为。
    """
    payload = base_payload()
    payload['heat_top8'] = [('1-1', 5.10), ('1-0', 6.50), ('2-0', 7.20)]
    out = engine.run_score_convergence(payload)

    lowest = out['step4']['lowest_odds_check']
    assert lowest['status'] == 'ok'
    assert lowest['lowest_score'] == '1-1'
    assert lowest['lowest_bucket'] == 'draw'
    assert lowest['conflict_code'] == 'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_HOME'
    # 手算：默认降权 0.85（clamp 到 [0.5,1.0] 后仍是 0.85）
    assert lowest['downweight'] == pytest.approx(0.85, abs=1e-9)

    # 方向不变，比分仍是主胜向的两个不同比分
    assert out['direction'] == 'H'
    assert len(out['locked_scores']) == 2
    assert {item['bucket'] for item in out['locked_scores']} == {'home'}
    assert out['locked_scores'][0]['score'] != out['locked_scores'][1]['score']
    # 降权系数真的进了 Step4 乘数
    assert out['step4']['weight_factors']['lowest_odds_draw'] == pytest.approx(0.85, abs=1e-9)


def test_iron_law1_no_hedge_symmetry_for_away_direction() -> None:
    """对称性：方向客胜 + 最低赔平局，同样告警降权（不只检查主胜）。"""
    payload = base_payload()
    payload['odds_1x2'] = {'home': 5.50, 'draw': 4.00, 'away': 1.60}
    payload['lam_home'] = 1.00
    payload['lam_away'] = 1.70
    payload['asian_handicap'] = 0.75        # 客让半一：与客胜同向
    payload['half_time_1x2'] = {'home': 5.00, 'draw': 2.30, 'away': 2.10}
    payload['heat_tg_lines'] = ['  胜平负: 胜=6(25.0%) 平=6(25.0%) 负=12(50.0%)']
    payload['mkt_over_theo_tg_lines'] = ['  胜平负: 胜=3(20.0%) 平=3(20.0%) 负=9(60.0%)']
    payload['heat_top8'] = [('1-1', 5.10), ('0-1', 6.50)]
    out = engine.run_score_convergence(payload)

    assert out['direction'] == 'A'
    lowest = out['step4']['lowest_odds_check']
    assert lowest['conflict_code'] == 'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_AWAY'
    assert {item['bucket'] for item in out['locked_scores']} == {'away'}
    assert out['locked_scores'][0]['score'] != out['locked_scores'][1]['score']


def test_assert_locked_contract_rejects_length_not_two() -> None:
    """结构性断言：不是恰好 2 条就抛 locked_scores_length（不静默裁剪）。"""
    with pytest.raises(engine.ConvergenceContractError) as excinfo:
        engine.assert_locked_contract('H', [{'score': '2-1'}])
    assert excinfo.value.code == 'locked_scores_length'
    with pytest.raises(engine.ConvergenceContractError):
        engine.assert_locked_contract('H', [{'score': '2-1'}, {'score': '3-0'}, {'score': '4-0'}])


def test_assert_locked_contract_rejects_duplicate_scores() -> None:
    """结构性断言：①==② 抛 locked_scores_identical。"""
    with pytest.raises(engine.ConvergenceContractError) as excinfo:
        engine.assert_locked_contract('H', [{'score': '2-1'}, {'score': '2-1'}])
    assert excinfo.value.code == 'locked_scores_identical'


def test_assert_locked_contract_rejects_hedge_across_directions() -> None:
    """结构性断言：H 方向里混进客胜比分 -> hedge_direction_in_locked。"""
    with pytest.raises(engine.ConvergenceContractError) as excinfo:
        engine.assert_locked_contract('H', [{'score': '2-1'}, {'score': '0-1'}])
    assert excinfo.value.code == 'hedge_direction_in_locked'
    # 同时：A 方向混进主胜比分
    with pytest.raises(engine.ConvergenceContractError) as excinfo_a:
        engine.assert_locked_contract('A', [{'score': '0-1'}, {'score': '2-1'}])
    assert excinfo_a.value.code == 'hedge_direction_in_locked'
    # 平局方向里混进主胜 -> 普通越界码
    with pytest.raises(engine.ConvergenceContractError) as excinfo_d:
        engine.assert_locked_contract('D', [{'score': '1-1'}, {'score': '2-1'}])
    assert excinfo_d.value.code == 'locked_score_against_direction'


def test_assert_locked_contract_accepts_a_valid_pair() -> None:
    """两个同向、互不相同的比分应原样通过。"""
    pair = [{'score': '2-1'}, {'score': '3-0'}]
    assert engine.assert_locked_contract('H', pair) == pair
    # 空列表（中止路径）不抛错
    assert engine.assert_locked_contract('H', []) == []


def test_clamp_conf_mod_bounds_extremes() -> None:
    """clamp_conf_mod 在极值输入下仍落在 [-0.5, +0.5]。"""
    assert engine.clamp_conf_mod(9.0) == 0.5
    assert engine.clamp_conf_mod(-9.0) == -0.5
    assert engine.clamp_conf_mod(0.3) == pytest.approx(0.3)
    assert engine.clamp_conf_mod(-0.35) == pytest.approx(-0.35)
    assert engine.clamp_conf_mod(None) == 0.0
    assert engine.clamp_conf_mod('abc') == 0.0


def test_no_double_choice_or_hedge_keys_in_result() -> None:
    """结果字典不得出现 stake/budget/allocation/对冲 任何键或字样。"""
    out = engine.run_score_convergence(base_payload())
    forbidden_keys = {'allocation_table', 'stake', 'budget', 'hedge', '对冲', 'doubles'}
    text = repr(out)
    for token in ('stake', 'budget', 'allocation', '对冲', '防波堤', '双选', '保本'):
        assert token not in text, token

    def walk(node, path='root'):
        if isinstance(node, dict):
            for k, v in node.items():
                assert str(k) not in forbidden_keys, f'{path}.{k}'
                walk(v, f'{path}.{k}')
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]')

    walk(out)


# =========================================================================== #
# 铁律 2 负向：缺输入 -> 该步中止 + 原因明确 + 不产出 locked_scores
# =========================================================================== #
def test_iron_law2_missing_input_aborts_step_and_produces_no_locked_scores() -> None:
    """删掉大小球盘口 -> Step2 中止（原因明确），locked_scores 必须为空。"""
    payload = base_payload()
    payload.pop('ou_line')
    out = engine.run_score_convergence(payload)

    assert out['status'] == 'aborted'
    assert out['step2']['status'] == 'aborted'
    assert '大小球' in out['step2']['reason'] or 'Step2' in out['step2']['reason']
    assert out['locked_scores'] == []
    assert out['available'] is False
    assert any(m['field'] == 'ou_line' for m in out['missing'])
    # 下游必须如实报「上游中止」，不得伪造
    assert out['step3']['status'] == 'aborted'
    assert out['step6']['status'] == 'aborted'


def test_iron_law2_odds_at_or_below_one_is_invalid_not_corrected() -> None:
    """赔率 ≤1.0 判为异常，绝不修正成可用值。"""
    payload = base_payload()
    payload['odds_1x2'] = {'home': 0.95, 'draw': 4.00, 'away': 5.50}
    out = engine.run_score_convergence(payload)

    assert out['step1']['status'] == 'aborted'
    assert out['locked_scores'] == []
    assert any('≤1.0' in (item.get('reason') or '') for item in out['invalid']) or any(
        '≤1.0' in (item.get('reason') or '') for item in out['step1']['invalid']
    )


def test_iron_law2_missing_single_pool31_market_value_aborts() -> None:
    """31 项里只要有一项市场值缺失，Step3 中止（不拿 0 当默认）。"""
    rows = pool_rows()
    rows[5]['market'] = None
    payload = base_payload()
    payload['pool31_rows'] = rows
    out = engine.run_score_convergence(payload)

    assert out['step3']['status'] == 'aborted'
    assert out['locked_scores'] == []
    assert any('market' in (m['field'] or '') for m in out['missing'])


def test_iron_law2_no_pool31_input_aborts_with_reason() -> None:
    """完全不给 31 项输入 -> Step3 中止，报告里写明缺什么。"""
    payload = base_payload()
    payload.pop('pool31_rows')
    out = engine.run_score_convergence(payload)

    assert out['status'] == 'aborted'
    assert out['candidate_count'] == 0
    assert out['locked_scores'] == []
    rendered = '\n'.join(engine.format_convergence_tg_lines(out))
    assert '中止' in rendered
    assert '未形成（' not in rendered.replace('锁定2个比分: 未形成', '')


# =========================================================================== #
# 铁律 3：时间边界
# =========================================================================== #
def test_iron_law3_extra_time_and_penalties_excluded_and_counted() -> None:
    """加时/点球事件被排除并计数，常规时间+补时内的事件保留。

    手算边界：REGULAR_TIME_MINUTES(90) + STOPPAGE_MINUTES_MAX(15) = 105。
    63 分钟 → 保留；95 分钟 → 保留（仍在补时上限内）；121 分钟 → 排除。
    """
    payload = base_payload()
    payload['live_events'] = [
        {'minute': 63, 'type': 'goal', 'ts': '2026-11-26T20:00:00Z', 'before': '1-1', 'after': '2-1'},
        {'minute': 95, 'type': 'goal', 'ts': '2026-11-26T20:50:00Z', 'before': '2-1', 'after': '3-1'},
        {'minute': 121, 'type': 'penalty', 'ts': '2026-11-26T21:10:00Z', 'before': '3-1', 'after': '3-2'},
    ]
    log = engine.live_event_log(payload)
    assert [e['minute'] for e in log['events']] == [63, 95]
    assert log['excluded_count'] == 1
    assert log['excluded'][0]['minute'] == 121
    assert '加时' in log['excluded'][0]['excluded_reason']
    assert log['regular_time_only'] is True
    assert engine.REGULAR_TIME_MINUTES == 90
    assert engine.STOPPAGE_MINUTES_MAX == 15
    assert engine.REGULAR_TIME_ONLY is True


def test_iron_law3_constants_live_at_module_top_and_are_parameterised() -> None:
    """口径常量集中在模块顶部，且 regular_time_only 可被显式关掉。"""
    source = MODULE_PATH.read_text(encoding='utf-8')
    head = source[:4000]
    assert 'REGULAR_TIME_MINUTES = 90' in head
    assert 'REGULAR_TIME_ONLY = True' in head
    log = engine.live_event_log(
        {'live_events': [{'minute': 121, 'type': 'goal'}]}, regular_time_only=False
    )
    assert log['excluded_count'] == 0


# =========================================================================== #
# Step1：四档与两两投票（附加契约二）
# =========================================================================== #
def test_pairwise_vote_matches_hand_computed_sample() -> None:
    """用户样例手算：s = {胜:2, 平:0, 负:-2}，方向 胜。

    胜 vs 平：2.56 < 8.63/2 = 4.315  → 胜 +1，平 -1
    胜 vs 负：2.56 < 21.09/2 = 10.545 → 胜 +1，负 -1
    平 vs 负：8.63 < 21.09/2 = 10.545 → 平 +1，负 -1
    → 胜 2，平 -1+1=0，负 -1-1=-2
    """
    vote = engine.pairwise_vote({'胜': 2.56, '平': 8.63, '负': 21.09})
    assert vote['scores'] == {'胜': 2, '平': 0, '负': -2}
    assert vote['direction'] == 'H'
    assert vote['available'] is True
    # 三对比较的比值边界与手算一致
    bounds = {(p['left'], p['right']): p['ratio_bound'] for p in vote['pairs']}
    assert bounds[('胜', '平')] == pytest.approx(4.315, abs=1e-9)
    assert bounds[('胜', '负')] == pytest.approx(10.545, abs=1e-9)
    assert bounds[('平', '负')] == pytest.approx(10.545, abs=1e-9)


def test_pairwise_vote_uses_pairwise_tier_and_reports_source() -> None:
    """模型/市场不可用时，方向由两两投票给出，direction_source='pairwise'。"""
    payload = base_payload()
    payload.pop('odds_1x2')          # 市场三向缺失
    payload.pop('lam_home')
    payload.pop('lam_away')          # 模型缺失
    payload['abs_deltas'] = {'胜': 2.56, '平': 8.63, '负': 21.09}
    out = engine.run_score_convergence(payload)

    assert out['direction'] == 'H'
    assert out['direction_source'] == 'pairwise'
    assert out['step1']['pairwise']['scores'] == {'胜': 2, '平': 0, '负': -2}


def test_pairwise_tie_is_not_draw_evidence_and_aborts_step1() -> None:
    """三项 delta 相等 -> 并列 -> Step1 aborted，绝不返回「平」。"""
    payload = base_payload()
    payload.pop('odds_1x2')
    payload.pop('lam_home')
    payload.pop('lam_away')
    payload['abs_deltas'] = {'胜': 5.0, '平': 5.0, '负': 5.0}
    out = engine.run_score_convergence(payload)

    assert out['step1']['status'] == 'aborted'
    assert out['direction'] == ''
    assert out['direction'] != 'D'
    assert out['locked_scores'] == []
    assert out['step1']['pairwise']['direction'] is None
    assert any(c['code'] == 'PAIRWISE_NO_UNIQUE_DIRECTION' for c in out['step1']['conflicts'])


def test_compare_pair_monotonic_in_val_a() -> None:
    """单调性：固定 b，a 递增时输出序列为 1 → 0 → -1，不出现反复。"""
    b = 10.0
    sequence = []
    for step in range(1, 121):          # a = 0.25 .. 30.0（跨过 2×b=20 的边界）
        a = step * 0.25
        sequence.append(engine.compare_pair(a, b))
    filtered = [v for v in sequence if v is not None]
    assert filtered[0] == 1
    assert filtered[-1] == -1
    for prev, nxt in itertools.pairwise(filtered):
        assert nxt <= prev, filtered
    # 序列里必须真的出现「并列」那一段（不是直接从 1 跳到 -1）
    assert 0 in filtered


def test_compare_pair_named_thresholds_behaviour() -> None:
    """两个具名阈值的边界行为与常量一致。"""
    assert engine.PAIRWISE_RATIO_THRESHOLD == 2.0
    assert engine.PAIRWISE_ABS_GAP == 1.0
    # a 小于 b 的一半 → a 优
    assert engine.compare_pair(4.0, 10.0) == 1
    # 差在 1.0 以内 → 并列
    assert engine.compare_pair(9.5, 10.0) == 0
    # 差超过 1.0 且不到两倍 → 近者优
    assert engine.compare_pair(8.5, 10.0) == 1
    assert engine.compare_pair(10.0, 8.5) == -1


def test_step1_directive_tier_wins_with_override_audit() -> None:
    """第一档 directive 命中即用，并带 override_audit。"""
    payload = base_payload()
    payload['direction_directive'] = 'A'
    payload['direction_directive_source'] = 'telegram_operator'
    out = engine.run_score_convergence(payload)
    assert out['direction_source'] == 'directive'
    assert out['direction'] == 'A'
    audit = out['step1']['override_audit']
    assert audit['acknowledged'] is True
    assert audit['value'] == 'A'
    assert audit['source'] == 'telegram_operator'


def test_step1_directive_without_candidates_aborts_instead_of_forcing_scores() -> None:
    """指定方向在 31 项树里无候选 -> 记冲突并 aborted，不硬出比分。

    夹具里客胜 13 项市场值为 0（不是缺失，是真报 0）→ 客胜方向候选数为 0。
    """
    payload = base_payload()
    payload['direction_directive'] = 'A'
    payload['pool31_rows'] = [
        {
            'score': row['score'],
            'market': 0.0 if engine._score_bucket(row['score']) == 'away' else row['market'],
            'theory': row['theory'],
        }
        for row in pool_rows()
    ]
    out = engine.run_score_convergence(payload)
    assert out['step1']['status'] == 'aborted'
    assert out['locked_scores'] == []
    assert any(
        c['code'] == 'DIRECTIVE_DIRECTION_HAS_NO_CANDIDATE'
        for c in out['step1']['conflicts']
    )


# =========================================================================== #
# Step2：翻转样例（OVER -> UNDER）与 xG 修正
# =========================================================================== #
def test_step2_xg_adjustment_flips_over_to_under() -> None:
    """手算：λ 合计 3.00 > 2.5 -> OVER；xG 合计 1.60（0.85+0.75）。

    修正后期望 = 0.5*3.00 + 0.5*1.60 = 1.50 + 0.80 = 2.30 < 2.5 -> UNDER。
    """
    payload = base_payload()
    payload['lam_home'] = 1.80
    payload['lam_away'] = 1.20
    payload['xg_home'] = 0.85
    payload['xg_away'] = 0.75

    no_xg = dict(payload)
    no_xg.pop('xg_home')
    no_xg.pop('xg_away')
    over_out = engine.run_score_convergence(no_xg)
    assert over_out['step2']['expected_goals_base'] == pytest.approx(3.00, abs=1e-9)
    assert over_out['ou_path'] == 'OVER'

    under_out = engine.run_score_convergence(payload)
    assert under_out['step2']['expected_goals_adjusted'] == pytest.approx(2.30, abs=1e-9)
    assert under_out['ou_path'] == 'UNDER'
    assert under_out['step2']['xg_adjustment']['weight'] == pytest.approx(0.5)


def test_step2_xg_adjustment_flips_under_to_over() -> None:
    """反向样例：λ 合计 2.10 < 2.5 -> UNDER；xG 合计 3.90 -> 3.00 > 2.5 -> OVER。"""
    payload = base_payload()
    payload['lam_home'] = 1.10
    payload['lam_away'] = 1.00
    payload['xg_home'] = 2.10
    payload['xg_away'] = 1.80
    # 手算：0.5*(2.10) + 0.5*(3.90) = 1.05 + 1.95 = 3.00
    out = engine.run_score_convergence(payload)
    assert out['step2']['expected_goals_base'] == pytest.approx(2.10, abs=1e-9)
    assert out['step2']['expected_goals_adjusted'] == pytest.approx(3.00, abs=1e-9)
    assert out['ou_path'] == 'OVER'


def test_step2_never_fills_missing_ou_line() -> None:
    """盘口线缺失：aborted，绝不用 2.5 之类的默认值。"""
    payload = base_payload()
    payload['ou_line'] = None
    out = engine.run_score_convergence(payload)
    assert out['step2']['status'] == 'aborted'
    assert out['step2']['ou_line'] is None
    assert out['locked_scores'] == []


# =========================================================================== #
# Step3：31 项完备树（聚合桶不缺席）
# =========================================================================== #
def test_step3_complete_tree_keeps_all_31_including_aggregate_buckets() -> None:
    """完备树恒为 31 项，三个聚合桶都在（不是 28 项）。"""
    tree = engine.build_complete_tree_31(pool_rows())
    assert len(tree) == 31
    aggregate = [r['score'] for r in tree if r['is_aggregate']]
    assert aggregate == ['胜其它', '平其它', '负其它']

    out = engine.run_score_convergence(base_payload())
    assert out['step3']['universe_size'] == 31
    assert out['step3']['aggregate_in_tree'] == 3


def test_step3_rejections_are_enumerable_and_consistent() -> None:
    """候选数 < 31，被剔除项理由可枚举，且候选全与 Step1/Step2 自洽。"""
    out = engine.run_score_convergence(base_payload())
    step3 = out['step3']
    assert step3['candidate_count'] < 31
    assert len(step3['candidates']) + len(step3['rejected']) == 31
    for item in step3['rejected']:
        assert item['reason']
    for row in step3['candidates']:
        assert row['bucket'] == 'home'
        goals = engine._score_total_goals(row['score'])
        if goals is not None:
            assert goals > 2.5


def test_step3_goal_expect_unknown_is_invalid_and_aborts() -> None:
    """goal_expect='中' 现在是已知值；不认识的值 -> invalid + aborted + 无锁定。"""
    ok_payload = base_payload()
    ok_payload['goal_expect'] = '中'
    ok_payload['handicap_side'] = '让胜'
    ok_out = engine.run_score_convergence(ok_payload)
    assert ok_out['status'] == 'ok'

    bad_payload = base_payload()
    bad_payload['goal_expect'] = '不知道'
    bad_payload['handicap_side'] = 'home'
    bad_out = engine.run_score_convergence(bad_payload)
    assert bad_out['step3']['status'] == 'aborted'
    assert bad_out['locked_scores'] == []
    assert any(i['field'] == 'goal_expect' for i in bad_out['step3']['invalid'])


def test_handicap_side_alias_routes_zh_ang_sheng_to_home() -> None:
    """'让胜' 与 'away' 走不同分支；'让胜' 保留主胜项。"""
    rows = pool_rows()
    home_core = engine.reduce_matrix_stratified(rows, '让胜', 'medium')
    away_core = engine.reduce_matrix_stratified(rows, 'away', 'medium')
    assert home_core['side'] == 'home'
    assert away_core['side'] == 'away'
    assert home_core['core'] != away_core['core']
    # 主胜分支的核心集必须都是 h > a；且不含客胜项
    for score in home_core['core']:
        assert engine._score_bucket(score) == 'home'
    for score in away_core['core']:
        assert engine._score_bucket(score) == 'away'


def test_unknown_handicap_side_raises_with_accepted_values() -> None:
    with pytest.raises(ValueError) as excinfo:
        engine.reduce_matrix_stratified(pool_rows(), '不存在的方向', 'medium')
    assert 'away' in str(excinfo.value)


def test_category_of_aggregate_buckets() -> None:
    """聚合桶分类：胜其它 -> home，平其它 -> draw，负其它 -> away（不抛错）。"""
    assert engine._score_bucket('胜其它') == 'home'
    assert engine._score_bucket('平其它') == 'draw'
    assert engine._score_bucket('负其它') == 'away'
    # 两种写法都要认
    assert engine._score_bucket('胜其他') == 'home'
    assert engine._score_bucket('平其他') == 'draw'
    assert engine._score_bucket('负其他') == 'away'


def test_draw_detection_is_structural_across_three_spellings() -> None:
    """平局判定对 '1-1' / '平其它' / '平其他' 三种写法都成立。"""
    assert engine._is_draw_score('1-1') is True
    assert engine._is_draw_score('平其它') is True
    assert engine._is_draw_score('平其他') is True
    assert engine._is_draw_score('1:1') is True     # 冒号形式经 normalize_cs_key 归一
    assert engine._is_draw_score('2-1') is False
    assert engine._is_draw_score('胜其它') is False


def test_direction_consistency_check_has_no_13_over_5_factor() -> None:
    """13/5 密度系数已删除：draw 占比按桶内去水，不放大。

    手算（用常量夹具）：主胜桶 13 项 × 4.0% = 52.0；平局桶 5 × 2.0 = 10.0；
    客胜桶 13 × 1.0 = 13.0；合计 75.0。
    占比 = 52/75 = 69.33%, 10/75 = 13.33%, 13/75 = 17.33%（不再有 48.8% 的平局）。
    """
    check = engine.direction_consistency_check(pool_rows(), 'H')
    assert check['density_factor'] is None
    assert check['bucket_sums']['home'] == pytest.approx(52.0, abs=1e-9)
    assert check['bucket_sums']['draw'] == pytest.approx(10.0, abs=1e-9)
    assert check['bucket_sums']['away'] == pytest.approx(13.0, abs=1e-9)
    assert check['share_pct']['home'] == pytest.approx(69.33, abs=0.01)
    assert check['share_pct']['draw'] == pytest.approx(13.33, abs=0.01)
    assert check['share_pct']['away'] == pytest.approx(17.33, abs=0.01)
    assert check['dominant'] == 'H'
    assert check['conflict'] is False


def test_direction_lock_cannot_override_step1_direction() -> None:
    """循环论证回归：客队热门赔率 + home 盘口，direction 仍由 Step1 决定。

    Step1 用客队热门赔率 -> A；home 盘口只出现在 Step4 的一致性校验里，
    绝不能反过来把方向改成 H，也不得产生 75% 之类的伪置信度。
    """
    payload = base_payload()
    payload['odds_1x2'] = {'home': 5.50, 'draw': 4.00, 'away': 1.60}
    payload['lam_home'] = 1.00
    payload['lam_away'] = 1.70
    payload['asian_handicap'] = -0.75      # 主队让球，与客胜相反
    payload['half_time_1x2'] = {'home': 5.00, 'draw': 2.30, 'away': 2.10}
    payload['heat_tg_lines'] = ['  胜平负: 胜=6(25.0%) 平=6(25.0%) 负=12(50.0%)']
    payload['mkt_over_theo_tg_lines'] = ['  胜平负: 胜=3(20.0%) 平=3(20.0%) 负=9(60.0%)']
    payload['heat_top8'] = [('0-1', 6.50), ('0-2', 7.20)]
    out = engine.run_score_convergence(payload)

    assert out['direction'] == 'A'
    assert out['direction_source'] == 'model_market'
    # 盘口反向 -> 互殴冲突被记录，但方向不改
    assert out['step4']['linkage'] == '互殴'
    assert any(c['code'] == 'HANDICAP_AGAINST_STEP1' for c in out['macro']['conflicts'])
    # 两个锁定比分必须都是客胜向
    assert {item['bucket'] for item in out['locked_scores']} == {'away'}


# =========================================================================== #
# Step4：顺局 / 互殴（权重修正方向相反且可复算）
# =========================================================================== #
def test_step4_shunju_vs_hudou_weights_are_opposite_and_recomputable() -> None:
    """顺局乘 1.10、互殴乘 0.90；两者可复算。"""
    shun = engine.run_score_convergence(base_payload())          # -0.75 与主胜同向
    hu = base_payload()
    hu['asian_handicap'] = 0.75                                   # 客让，与主胜相反
    hu_out = engine.run_score_convergence(hu)

    assert shun['step4']['linkage'] == '顺局'
    assert shun['step4']['weight_factors']['linkage'] == pytest.approx(1.10, abs=1e-9)
    assert hu_out['step4']['linkage'] == '互殴'
    assert hu_out['step4']['weight_factors']['linkage'] == pytest.approx(0.90, abs=1e-9)
    # 顺局的比分权重必须高于互殴的同名比分
    shun_totals = {s['score']: s['total'] for s in shun['step6']['ranked']}
    hu_totals = {s['score']: s['total'] for s in hu_out['step6']['ranked']}
    common = set(shun_totals) & set(hu_totals)
    assert common
    for score in common:
        assert shun_totals[score] > hu_totals[score]


def test_step4_lowest_odds_skipped_when_heat_missing_no_fabricated_draw() -> None:
    """heat 为空 -> missing 含 heat_top8、该项 skipped、不产生 '1-1'。"""
    payload = base_payload()
    payload.pop('heat_top8')
    out = engine.run_score_convergence(payload)

    lowest = out['step4']['lowest_odds_check']
    assert lowest['status'] == 'skipped'
    assert lowest['lowest_score'] == ''
    assert any(m['field'] == 'heat_top8' for m in lowest['missing'])
    assert any(m['field'] == 'heat_top8' for m in out['missing'])


# =========================================================================== #
# Step5：六维得分与缺维
# =========================================================================== #
def test_step5_missing_dimension_scores_zero_and_is_listed() -> None:
    """缺半场大小球 -> 该维 0 分并记冲突，总分 = 100 - 15 = 85。"""
    payload = base_payload()
    payload.pop('half_time_ou_line')
    out = engine.run_score_convergence(payload)

    dims = out['step5']['dimensions']
    assert dims['half_ou']['available'] is False
    assert dims['half_ou']['score'] == 0.0
    assert 'half_ou' in out['step5']['missing_dimensions']
    # 手算：20+20+20+15+0+10 = 85
    assert out['consistency_score'] == pytest.approx(85.0, abs=1e-6)
    assert any(c['code'] == 'DIMENSION_MISSING' and c['dimension'] == 'half_ou'
               for c in out['step5']['conflicts'])


def test_step5_missing_two_dimensions_scores_75() -> None:
    """缺半场胜平负(15) + 半场大小球(15) -> 手算 100-30 = 70。"""
    payload = base_payload()
    payload.pop('half_time_1x2')
    payload.pop('half_time_ou_line')
    out = engine.run_score_convergence(payload)
    assert out['consistency_score'] == pytest.approx(70.0, abs=1e-6)


def test_step5_dimension_conflict_halves_that_dimension() -> None:
    """半场方向与 Step1 相反 -> 该维半分（15/2=7.5），总分 92.5。"""
    payload = base_payload()
    payload['half_time_1x2'] = {'home': 5.00, 'draw': 2.60, 'away': 2.10}
    out = engine.run_score_convergence(payload)
    dims = out['step5']['dimensions']
    assert dims['half_1x2']['available'] is True
    assert dims['half_1x2']['agree'] is False
    assert dims['half_1x2']['score'] == pytest.approx(7.5, abs=1e-6)
    assert out['consistency_score'] == pytest.approx(92.5, abs=1e-6)


def test_borsa_signals_count_missing_is_invalid_and_changes_tempo_score() -> None:
    """borsa_signals_count 缺失 -> invalid，且 tempo 维度得分变化可观测。"""
    present = engine.run_score_convergence(base_payload())
    missing_payload = base_payload()
    missing_payload.pop('borsa_signals_count')
    missing_out = engine.run_score_convergence(missing_payload)

    audit = missing_out['step5']['immutable_factors']
    assert audit['available'] is False
    assert any(i['field'] == 'borsa_signals_count' for i in audit['invalid'])
    assert missing_out['step5']['dimensions']['tempo']['available'] is False
    assert missing_out['step5']['dimensions']['tempo']['score'] == 0.0
    # 手算：tempo 缺 -> 100-10 = 90；存在时 = 100
    assert present['consistency_score'] == pytest.approx(100.0, abs=1e-6)
    assert missing_out['consistency_score'] == pytest.approx(90.0, abs=1e-6)


def test_immutable_factor_confidence_multiplies_tempo_dimension() -> None:
    """变更 1/5 个因子 -> confidence 0.8 -> tempo 10*0.8 = 8.0，总分 98.0。"""
    payload = base_payload()
    payload['immutable_factors_changed'] = ['referee']
    out = engine.run_score_convergence(payload)
    audit = out['step5']['immutable_factors']
    assert audit['confidence'] == pytest.approx(0.8, abs=1e-9)
    assert out['step5']['dimensions']['tempo']['score'] == pytest.approx(8.0, abs=1e-6)
    assert out['consistency_score'] == pytest.approx(98.0, abs=1e-6)


# =========================================================================== #
# Step6：并列 / 候选不足 / 权重分解求和
# =========================================================================== #
def test_step6_tie_break_is_deterministic_and_reproducible() -> None:
    """并列时按确定性规则排序，两次调用结果完全一致。"""
    first = engine.run_score_convergence(base_payload())
    second = engine.run_score_convergence(base_payload())
    assert [s['score'] for s in first['locked_scores']] == [
        s['score'] for s in second['locked_scores']
    ]
    # 全部候选权重相同时（夹具里同桶行完全同值），仍按 score 升序稳定取前 2
    ranked = first['step6']['ranked']
    totals = [r['total'] for r in ranked]
    assert totals == sorted(totals, reverse=True)


def test_step6_candidate_shortage_aborts_without_index_error() -> None:
    """候选 < 2 -> aborted，无 IndexError，且绝不重复同一比分。

    构造：方向锁平局 + 大小球线 6.5 + λ 合计 7.0 (>6.5 → OVER)。
    平局桶里总进球 >6.5 的只有「平其它」（其最小总进球 8；3-3 只有 6 球）,
    因此候选恰好 1 个 → Step6 必须 aborted（旧实现会在这里 IndexError 或重复
    输出同一个比分）。
    """
    payload = base_payload()
    payload['direction_directive'] = 'D'
    payload['lam_home'] = 4.0
    payload['lam_away'] = 3.0
    payload['ou_line'] = 6.5
    payload['half_time_ou_line'] = 3.0
    payload['ou_open_line'] = 6.0
    out = engine.run_score_convergence(payload)

    assert out['step1']['direction'] == 'D'
    assert out['ou_path'] == 'OVER'
    assert out['step3']['candidate_count'] == 1
    assert [r['score'] for r in out['step3']['candidates']] == ['平其它']
    assert out['step6']['status'] == 'aborted'
    assert out['locked_scores'] == []
    assert '< 2' in out['step6']['reason'] or '2 个' in out['step6']['reason']
    assert out['step6']['status'] == 'aborted'
    assert out['locked_scores'] == []
    assert '< 2' in out['step6']['reason'] or '2 个' in out['step6']['reason']


def test_step6_weight_components_sum_to_total() -> None:
    """每个锁定比分的权重分解各项之和 == 总分；且含全部必需键。"""
    out = engine.run_score_convergence(base_payload())
    for item in out['locked_scores']:
        assert set(item['components']) == set(engine.WEIGHT_COMPONENTS)
        assert sum(item['components'].values()) == pytest.approx(item['total'], abs=1e-9)
        assert item['components_sum_check'] == pytest.approx(item['total'], abs=1e-9)


# =========================================================================== #
# 宏观裁决（C 段）
# =========================================================================== #
def test_macro_veto_action_actually_affects_weights() -> None:
    """同一条比分在 HARD_BLOCK 下权重为 0（veto_action 不是装饰）。"""
    payload = base_payload()
    # 深盘 1.5 且与方向相反 -> HARD_BLOCK
    payload['asian_handicap'] = 1.5
    out = engine.run_score_convergence(payload)
    assert out['macro']['veto_action'] == 'HARD_BLOCK'
    assert out['macro']['conf_mod'] == pytest.approx(-0.5, abs=1e-9)
    # 全候选被熔断 -> Step6 aborted（不输出 0 权重比分）
    assert out['step6']['status'] == 'aborted'
    assert out['locked_scores'] == []


def test_macro_conf_mod_is_clamped() -> None:
    """conf_mod 在极值输入下仍在 [-0.5, +0.5]。"""
    for line in (-2.5, -1.75, -1.5, -1.25, -0.75, -0.25, 0.0, 0.25, 0.75, 1.25, 1.5, 2.5):
        for rank in (1, 12):
            payload = base_payload()
            payload['asian_handicap'] = line
            payload['league_rank_home'] = rank
            out = engine.run_score_convergence(payload)
            if out['macro']['status'] == 'ok':
                assert -0.5 <= out['macro']['conf_mod'] <= 0.5, (line, rank)


def test_macro_tier_branches_distinguish_125_15_and_175() -> None:
    """1.25 / 1.5 / >=1.75 必须落在不同档位。"""
    assert engine._tier_branch(-5) == 'MID'
    assert engine._tier_branch(-6) == 'DEEP_1_5'
    assert engine._tier_branch(-7) == 'DEEP_PLUS'
    assert engine._tier_branch(-8) == 'DEEP_PLUS'


def test_macro_keeps_signed_handicap_direction() -> None:
    """-0.75 与 +0.75 是不同盘口（abs() 已被删除）。"""
    neg = engine.normalize_handicap_line(-0.75)
    pos = engine.normalize_handicap_line('0.75')
    assert neg['side'] == 'home_gives'
    assert pos['side'] == 'away_gives'
    assert neg['line'] == -0.75
    assert pos['line'] == 0.75


def test_tier_diff_requires_computable_source() -> None:
    """tier_diff 必须有来源；没有来源 -> invalid，裁决不出。"""
    none = engine.resolve_tier_diff({})
    assert none['valid'] is False
    assert none['source'] == ''

    rank = engine.resolve_tier_diff({'league_rank_home': 2, 'league_rank_away': 12})
    assert rank['valid'] is True
    assert rank['source'] == 'league_rank'
    # 手算：(12-2)/6 = 1.67 -> trunc 1
    assert rank['tier_diff'] == 1
    assert rank['raw']['rank_gap'] == 10

    elo = engine.resolve_tier_diff({'elo_home': 1650, 'elo_away': 1420})
    assert elo['source'] == 'elo'
    # 手算：(1650-1420)/100 = 2.3 -> trunc 2
    assert elo['tier_diff'] == 2

    payload = base_payload()
    payload.pop('league_rank_home')
    payload.pop('league_rank_away')
    out = engine.run_score_convergence(payload)
    assert out['macro']['status'] == 'aborted'
    assert any(i['field'] == 'tier_diff' for i in out['macro']['invalid'])


def test_euro_micro_interlock_conflict_is_recorded() -> None:
    """宏观与欧赔微观冲突 -> conflicts 非空（euro_delta 真的被读取）。"""
    payload = base_payload()
    # 初赔客队热，即时赔率主队热 -> 微观迁移指向客胜，与宏观（利好主胜）冲突
    payload['odds_1x2_open'] = {'home': 6.00, 'draw': 4.20, 'away': 1.50}
    out = engine.run_score_convergence(payload)
    assert out['micro']['available'] is True
    assert out['step4']['interlock'] == 'conflict_halved'
    codes = [c['code'] for c in out['step4']['conflicts']]
    assert 'MACRO_MICRO_INTERLOCK_CONFLICT' in codes


# =========================================================================== #
# 源码 AST 扫描：伪造兜底常量必须彻底消失
# =========================================================================== #
def _module_ast() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding='utf-8'))


def test_source_has_no_mock_pool_or_hardcoded_odds_dict() -> None:
    """AST 扫描：不存在 mock_pool、不存在 31 项字面量赔率字典。"""
    tree = _module_ast()
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert 'mock_pool' not in names
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert 'mock_pool' not in attrs

    # 任何 dict 字面量都不允许同时含 10 个以上"比分->赔率"形式的键
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        score_like = 0
        for key in node.keys:
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and engine._parse_score(key.value) is not None):
                score_like += 1
        assert score_like < 10, f'疑似写死赔率字典（{score_like} 个比分键）在行 {node.lineno}'


def test_source_has_no_fabricated_default_constants() -> None:
    """源码里不存在兜底常量：无写死平局比分、无兜底盘口线、无密度系数。"""
    source = MODULE_PATH.read_text(encoding='utf-8')
    # 用拼装字符串做检测，避免测试文件自身命中
    assert ('default' + '=2.5') not in source
    assert ('13' + '.0/5.0') not in source
    assert ('13' + '/5') not in source
    assert ('Matrix_Tie_' + 'Default_Draw') not in source
    assert ('防' + '波堤') not in source
    assert ('胜/平' + '双选') not in source
    assert ('mock' + '_pool') not in source
    # '1-1' 只能出现在 31 项常量表内，不得作为独立兜底常量
    tree = _module_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == '1-1':
            in_list = any(
                isinstance(parent, ast.List) and node in parent.elts
                for parent in ast.walk(tree)
            )
            assert in_list, f"'1-1' 作为独立兜底常量出现在行 {node.lineno}"


def test_module_declares_no_ui_or_dataframe_dependency() -> None:
    """运行时模块不得依赖 UI 框架 / DataFrame 库（docstring 声明与代码一致）。"""
    tree = _module_ast()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert 'streamlit' not in imported
    assert 'pandas' not in imported
    assert imported <= {
        '__future__', 'math', 're', 'datetime', 'typing',
        'blade_deepseek_pipeline', 'okooo_mobile_cs',
    }, imported


# =========================================================================== #
# 审计快照与回放
# =========================================================================== #
def test_audit_snapshot_has_stable_keys_and_can_be_replayed() -> None:
    """审计快照键名稳定，能被回放脚本读回。"""
    out = engine.run_score_convergence(base_payload())
    snap = out['audit_snapshot']
    assert set(snap) >= {
        'inputs', 'steps', 'macro', 'final', 'regular_time_only',
        'regular_time_minutes', 'stoppage_minutes_max',
    }
    assert set(snap['steps']) == {
        'step1_direction', 'step2_ou_path', 'step3_candidate_tree',
        'step4_linkage', 'step5_consistency', 'step6_lock',
    }
    assert snap['final']['direction'] == 'H'
    assert len(snap['final']['locked_scores']) == 2
    assert snap['regular_time_only'] is True
    # 结构化日志
    for record in out['audit_logs']:
        assert set(record) == {'ts', 'step', 'code', 'detail'}


def test_report_section_states_all_six_required_facts() -> None:
    """Telegram 段逐字给：方向、O/U、候选数、得分、冲突条数、2 个锁定比分。"""
    out = engine.run_score_convergence(base_payload())
    text = '\n'.join(engine.format_convergence_tg_lines(out))
    assert '🧭【六步收敛】' in text
    assert '方向:' in text
    assert 'O/U 路径:' in text
    assert '候选集大小:' in text
    assert '互洽得分:' in text
    assert '冲突明细条数:' in text
    assert '锁定2个比分:' in text
    assert '主选' in text and '次选' in text


def test_report_section_shows_abort_reason_never_bare_unformed() -> None:
    """中止时必须给原因，不允许只写「未形成」不给理由。"""
    payload = base_payload()
    payload.pop('ou_line')
    out = engine.run_score_convergence(payload)
    text = '\n'.join(engine.format_convergence_tg_lines(out))
    assert '中止' in text
    assert 'Step2' in text
    for line in text.splitlines():
        if '未形成' in line:
            assert '（' in line, f'裸「未形成」不允许: {line}'


# =========================================================================== #
# 31 项复用与出处（不另建比分空间）
# =========================================================================== #
def test_score_matrix_from_pack_preserves_provenance_semantics() -> None:
    """pack 的逐键出处与 recorded/derived 语义原样透传。"""
    pack = {
        'rows': [
            {'score': '1-0', 'market': 4.0, 'theory': 3.0, 'market_source': 'market_odds'},
            {'score': '0-0', 'market': 2.0, 'theory': 2.5, 'market_source': 'jc_score_odds'},
        ],
        'cs31_provenance': {'1-0': 'desktop_cs', '0-0': 'jc_official'},
        'cs31_provenance_source': 'derived',
        'cs31_counts': {'desktop_cs': 1, 'jc_official': 1},
        'pos_rows': [{'score': '1-0'}],
    }
    matrix = engine.score_matrix_from_pack(pack)
    assert matrix['available'] is True
    assert matrix['provenance'] == {'1-0': 'desktop_cs', '0-0': 'jc_official'}
    assert matrix['provenance_source'] == 'derived'
    assert matrix['counts'] == {'desktop_cs': 1, 'jc_official': 1}
    assert matrix['pos_rows'] == [{'score': '1-0'}]


# =========================================================================== #
# 生产形态回归：结构化输入优先，渲染文本只作兜底
# =========================================================================== #
def _production_pack() -> dict:
    """用真实 build_theo_mkt_31_pack 造一份 31/31 齐备的包（不手工编行）。"""
    import blade_deepseek_pipeline as pipe_mod

    analyzer = pipe_mod.DeepSeekAnalyzer
    jc = {s: 8.0 + i for i, s in enumerate(pipe_mod.JCZQ_ALL_OPTIONS)}
    page = {s: 9.0 + i for i, s in enumerate(pipe_mod.JCZQ_ALL_OPTIONS[:22])}
    return analyzer.build_theo_mkt_31_pack(
        [],
        extra_market_odds=page,
        jc_score_odds=jc,
        lam_home=1.45,
        lam_away=1.10,
        market_provenance={s: 'desktop_cs' for s in pipe_mod.JCZQ_ALL_OPTIONS[:22]},
        market_provenance_source='recorded',
    )


def _production_payload(with_structured: bool) -> dict:
    """生产形态 payload：heat_tg_lines 为空（生产真实情况），市短专表只有「条数」。

    手算 heat_ranking 方向分布（4 行）：
        1-0 -> 主胜, 2-1 -> 主胜, 1-1 -> 平, 0-0 -> 平
        => {'胜': 2, '平': 2, '负': 0}
    """
    import blade_deepseek_pipeline as pipe_mod

    pack = _production_pack()
    assert pack['formed_count'] == 31, pack['formed_count']
    over_lines = pipe_mod.DeepSeekAnalyzer.format_mkt_over_theo_tg_lines(pack)
    # 真实 formatter 只给两行、没有胜平负计数
    assert over_lines == ['📉【市热超理论专表】', f"  条数: {pack['pos_count']}"], over_lines

    payload = {
        'odds_1x2': {'home': 1.60, 'draw': 4.00, 'away': 5.50},
        'lam_home': 1.45,
        'lam_away': 1.10,
        'ou_line': 2.5,
        'ou_over_odds': 1.90,
        'ou_under_odds': 1.90,
        'theo_mkt_31_pack': pack,
        'heat_tg_lines': [],                 # 生产真实情况：从未被填充
        'mkt_over_theo_tg_lines': over_lines,  # 生产真实情况：无计数
        'asian_handicap': -0.75,
        'half_time_1x2': {'home': 2.10, 'draw': 2.30, 'away': 5.00},
        'half_time_ou_line': 1.0,
        'ou_open_line': 2.25,
        'league_rank_home': 2,
        'league_rank_away': 12,
        'borsa_signals_count': 5,
        'immutable_factor_total': 5,
    }
    if with_structured:
        short_mx = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack)
        payload['heat_ranking'] = [
            {'score': '1-0', 'odds': 6.50, 'heat': 0.1538},
            {'score': '2-1', 'odds': 7.40, 'heat': 0.1351},
            {'score': '1-1', 'odds': 7.34, 'heat': 0.1362},
            {'score': '0-0', 'odds': 9.00, 'heat': 0.1111},
        ]
        payload['short_wdl_counts'] = short_mx['counts']
        payload['short_rows'] = short_mx['rows']
    return payload


def test_production_shape_payload_locks_two_scores() -> None:
    """生产形态回归：heat_tg_lines 为空 + 市短专表只有条数，仍必须锁定 2 个比分。

    This is the defect that shipped: the engine parsed rendered text as its ONLY
    path, so a real production payload aborted Step4 every time. With structured
    inputs (heat_ranking / short_wdl_counts) the run must succeed.
    """
    out = engine.run_score_convergence(_production_payload(with_structured=True))

    assert out['status'] == 'ok', (out['aborted_steps'], out['abort_reasons'])
    assert out['aborted_steps'] == []
    assert len(out['locked_scores']) == 2
    assert out['locked_scores'][0]['score'] != out['locked_scores'][1]['score']
    assert out['direction'] == 'H'
    assert {s['bucket'] for s in out['locked_scores']} == {'home'}
    # 输入确实是生产形态：热量行空、市短专表无计数
    assert out['audit_snapshot']['inputs']['heat_tg_lines'] == []
    assert out['audit_snapshot']['inputs']['mkt_over_theo_tg_lines'] == [
        '📉【市热超理论专表】', f"  条数: {_production_pack()['pos_count']}",
    ]


def test_production_shape_without_structured_inputs_aborts_naming_both_fields() -> None:
    """同样的生产 payload 但没有任何结构化输入 -> 明确中止，且两个字段都点名。"""
    out = engine.run_score_convergence(_production_payload(with_structured=False))

    assert out['status'] == 'aborted'
    assert out['locked_scores'] == []
    assert 'step4_linkage' in out['aborted_steps']
    assert 'step6_lock' in out['aborted_steps']
    fields = ' '.join(m.get('field') or '' for m in out['missing'])
    assert 'heat_ranking' in fields
    assert 'short_rows' in fields
    # 两个原因都必须写清楚，不许只说「未形成」
    assert out['heat_source_path'] == 'none'
    assert out['short_source_path'] == 'none'


def test_source_paths_report_the_path_actually_used() -> None:
    """heat_source_path / short_source_path 如实报告取数路径。"""
    # 1) ranking / rows 优先
    out_ranked = engine.run_score_convergence(_production_payload(with_structured=True))
    assert out_ranked['heat_source_path'] == 'ranking'
    assert out_ranked['short_source_path'] == 'ranking'

    # 2) 只有 counts（无 ranking/rows）-> counts
    payload = _production_payload(with_structured=True)
    payload.pop('heat_ranking')
    payload.pop('short_rows')
    payload['heat_wdl_counts'] = {'胜': 2, '平': 2, '负': 0}
    out_counts = engine.run_score_convergence(payload)
    assert out_counts['heat_source_path'] == 'counts'
    assert out_counts['short_source_path'] == 'counts'
    assert out_counts['status'] == 'ok'

    # 3) 只有渲染文本 -> lines
    payload2 = _production_payload(with_structured=False)
    payload2['heat_tg_lines'] = ['🔥【市热矩阵】', '  胜平负: 胜=9(56.2%) 平=4(25.0%) 负=3(18.8%)']
    payload2['mkt_over_theo_tg_lines'] = ['📊【市短矩阵】', '  胜平负: 胜=5(55.6%) 平=2(22.2%) 负=2(22.2%)']
    out_lines = engine.run_score_convergence(payload2)
    assert out_lines['heat_source_path'] == 'lines'
    assert out_lines['short_source_path'] == 'lines'
    assert out_lines['status'] == 'ok'

    # 4) 全无 -> none
    payload3 = _production_payload(with_structured=False)
    out_none = engine.run_score_convergence(payload3)
    assert out_none['heat_source_path'] == 'none'
    assert out_none['short_source_path'] == 'none'


def test_ranking_derived_counts_match_hand_computed_split() -> None:
    """ranking 计数必须等于手算方向分布（常量写死在测试里）。"""
    ranking = [
        {'score': '1-0', 'odds': 6.50},
        {'score': '2-1', 'odds': 7.40},
        {'score': '1-1', 'odds': 7.34},
        {'score': '0-0', 'odds': 9.00},
    ]
    # 手算：主胜 1-0, 2-1 = 2；平局 1-1, 0-0 = 2；客胜 0
    got = engine._bucket_counts_from_ranking(ranking)
    assert got['counts'] == {'胜': 2, '平': 2, '负': 0}
    assert got['rows_used'] == 4
    assert got['available'] is True

    # 聚合桶也要能归位：胜其它 -> 胜，平其他 -> 平，负其它 -> 负
    agg = engine._bucket_counts_from_ranking([
        {'score': '胜其它'}, {'score': '平其它'}, {'score': '平其他'}, {'score': '负其它'},
    ])
    assert agg['counts'] == {'胜': 1, '平': 2, '负': 1}

    # 无法解析的比分被跳过并记录，不硬塞进任何方向
    bad = engine._bucket_counts_from_ranking([{'score': '1-0'}, {'score': '???'}])
    assert bad['counts'] == {'胜': 1, '平': 0, '负': 0}
    assert bad['skipped'] == ['???']


def test_ranking_limit_keeps_top8_only() -> None:
    """heat_ranking 只数前 8 行（市热榜口径）。"""
    ranking = [{'score': '1-0'} for _ in range(10)]
    got = engine._bucket_counts_from_ranking(ranking, limit=8)
    assert got['rows_used'] == 8
    assert got['counts'] == {'胜': 8, '平': 0, '负': 0}


def test_structured_counts_reject_missing_or_bad_values() -> None:
    """结构化 counts 缺项/非整数/全 0 都判 invalid，不补 0 猜数。"""
    assert engine._counts_from_structured({'胜': 1, '平': 2})['available'] is False
    assert engine._counts_from_structured({'胜': -1, '平': 2, '负': 3})['available'] is False
    assert engine._counts_from_structured({'胜': 1.5, '平': 2, '负': 3})['available'] is False
    assert engine._counts_from_structured({'胜': 0, '平': 0, '负': 0})['available'] is False
    assert engine._counts_from_structured({'胜': 3, '平': 0, '负': 1}) == {
        'available': True, 'counts': {'胜': 3, '平': 0, '负': 1},
    }


def test_engine_consumes_score_matrix_input_directly() -> None:
    """传入 pack 作为 score_matrix -> Step3/6 真的吃 31 项（不再是摆设）。"""
    pack = {
        'rows': pool_rows(),
        'cs31_provenance': {s: 'desktop_cs' for s in engine.JCZQ_ALL_OPTIONS},
        'cs31_provenance_source': 'recorded',
        'cs31_counts': {'desktop_cs': 31},
    }
    payload = base_payload()
    payload.pop('pool31_rows')
    payload['score_matrix'] = pack
    out = engine.run_score_convergence(payload)
    assert out['status'] == 'ok'
    assert out['candidate_count'] == 11
    assert len(out['locked_scores']) == 2
    assert out['pool31_provenance']['source'] == 'recorded'
    assert out['pool31_provenance']['counts'] == {'desktop_cs': 31}


def test_provenance_helper_delegates_to_deepseek_analyzer() -> None:
    """出处解析调用既有 resolve_cs31_provenance，不另开一套推导。"""
    record = {'correct_score_books_avg': {'1-0': 6.0, '2-0': 9.0}}
    out = engine.resolve_pool31_provenance(record, {})
    assert out['available'] is True
    assert out['source'] == 'derived'
    assert out['provenance']['1-0'] == 'desktop_cs'
    assert out['provenance']['2-0'] == 'desktop_cs'
    assert out['provenance']['5-0'] == 'jc_official'
    assert out['provenance']['胜其它'] == 'jc_official'


# =========================================================================== #
# 纯 Python / 无 filler 默认值
# =========================================================================== #
def test_module_returns_plain_dicts_and_lists_only() -> None:
    """返回值里不出现 pandas / numpy 对象。"""
    out = engine.run_score_convergence(base_payload())
    allowed_scalars = (str, int, float, bool, type(None))

    def walk(node, path='root'):
        if isinstance(node, allowed_scalars):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                assert isinstance(k, allowed_scalars), f'{path} 的键类型 {type(k)}'
                walk(v, f'{path}.{k}')
            return
        if isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]')
            return
        raise AssertionError(f'{path} 是 {type(node)}，不是纯 dict/list/标量')

    walk(out)


# =========================================================================== #
# app 接线：生产形态调用点必须真的把结构化输入传下去
# =========================================================================== #
def test_app_wiring_passes_structured_inputs_and_locks_two_scores() -> None:
    """build_six_step_convergence_section 在生产形态下必须锁定 2 个比分。

    这是「测试形状与生产形状不一致」缺陷的接线级回归：heat_tg_lines 为空、
    市短专表只有条数，唯一可用的是 score_market_heat.ranking 与 31 pack。
    """
    import importlib

    import blade_deepseek_pipeline as pipe_mod

    app = importlib.import_module('app_v84_hermes')
    pack = _production_pack()

    rec = {
        'lam_h': 1.45,
        'lam_a': 1.10,
        'ou_line': 2.5,
        'asian_handicap': -0.75,
        'score_market_heat': {
            'available': True,
            'ranking': [
                {'score': '1-0', 'odds': 6.50},
                {'score': '2-1', 'odds': 7.40},
                {'score': '1-1', 'odds': 7.34},
                {'score': '0-0', 'odds': 9.00},
            ],
        },
        'six_market_mobile': {
            'ou_over_water': 1.90, 'ou_under_water': 1.90, 'ou_start_line': 2.25,
        },
        'half_time_1x2': {'home': 2.10, 'draw': 2.30, 'away': 5.00},
        'half_time_ou_line': 1.0,
        'league_rank_home': 2,
        'league_rank_away': 12,
        'borsa_signals_count': 5,
        'immutable_factor_total': 5,
    }
    payload = {
        'odds_1x2': {'home': 1.60, 'draw': 4.00, 'away': 5.50},
        'theo_mkt_31_pack': pack,
    }
    # 生产真实情况：bundle 里两个文本字段都是空/无计数
    bundle = {
        'heat_tg_lines': [],
        'mkt_over_theo_tg_lines': pipe_mod.DeepSeekAnalyzer.format_mkt_over_theo_tg_lines(pack),
    }
    lines = app.build_six_step_convergence_section(rec, bundle=bundle, ds_payload=payload)
    text = '\n'.join(lines)
    result = rec.get('six_step_convergence') or {}

    assert result.get('status') == 'ok', text
    assert len(result.get('locked_scores') or []) == 2
    assert result['heat_source_path'] == 'ranking'
    assert result['short_source_path'] in ('ranking', 'counts')
    assert '锁定2个比分: 主选' in text
    assert '次选' in text


def test_app_wiring_reports_missing_reasons_instead_of_bare_unformed() -> None:
    """接线层中止时，段内必须写出具体缺失字段，而不是裸「未形成」。"""
    import importlib

    app = importlib.import_module('app_v84_hermes')
    rec = {'lam_h': 1.45, 'lam_a': 1.10, 'ou_line': 2.5, 'asian_handicap': -0.75}
    lines = app.build_six_step_convergence_section(rec, bundle={}, ds_payload={})
    text = '\n'.join(lines)
    assert '🧭【六步收敛】' in text
    assert '中止' in text
    assert '缺失' in text or '异常' in text
    for line in text.splitlines():
        if '未形成' in line:
            assert '（' in line, f'裸「未形成」不允许: {line}'


def test_heat_ranking_also_feeds_the_lowest_odds_check() -> None:
    """heat_ranking 必须同时喂 Step4 的方向计数和最低赔检查（同一个数据源）。

    否则会出现「方向计数说市热可用、最低赔检查却说 heat_top8 缺失」的自相矛盾，
    段内还会多报一条假的缺失。
    """
    payload = _production_payload(with_structured=True)
    out = engine.run_score_convergence(payload)

    assert out['heat_source_path'] == 'ranking'
    lowest = out['step4']['lowest_odds_check']
    assert lowest['status'] == 'ok', lowest
    # 手算：榜单里赔率最低的是 1-0 @6.50（其余 7.40 / 7.34 / 9.00）
    assert lowest['lowest_score'] == '1-0'
    assert lowest['lowest_bucket'] == 'home'
    # 不应再报 heat_top8 缺失
    assert not [m for m in out['missing'] if m.get('field') == 'heat_top8']
