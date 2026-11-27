"""market_guardrails 的四道护栏 + 两阶段清算测试。

期望值全部是手写常量，不用被测代码算期望。
C1–C10 每条至少一条测试，测试名以契约编号开头，方便逐个对照。
"""
from __future__ import annotations

import ast
import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import market_guardrails as g

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 手写夹具（全部常量）
# --------------------------------------------------------------------------- #
#: 三向 |Δ| 均值（赔率量纲）。最大 1.10，最小 0.30 → 第二小/第一小 = 0.90/0.30 = 3.0。
ABS_DELTAS_WIDE = {'胜': 0.30, '平': 0.90, '负': 1.10}

#: 第二小/第一小 = 0.45/0.30 = 1.5 < 2.0 → 邻近规则命中。
ABS_DELTAS_NEAR = {'胜': 0.30, '平': 0.45, '负': 1.10}

#: |Δ_胜|/|Δ_负| = 0.40/0.50 = 0.8 <= 1.35 → 均势命中。
ABS_DELTAS_EVEN = {'胜': 0.40, '平': 0.60, '负': 0.50}

#: |Δ_胜|/|Δ_负| = 1.20/0.40 = 3.0 > 1.35；第二小/第一小 = 0.90/0.40 = 2.25 > 2.0。
ABS_DELTAS_LOPSIDED = {'胜': 1.20, '平': 0.90, '负': 0.40}


def _heat(scores: list[str]) -> list[dict]:
    """按给定顺序造热榜行；赔率只是占位，席位判定只看 score。"""
    return [{'score': score, 'odds': round(10.0 - index, 2)} for index, score in enumerate(scores)]


# --------------------------------------------------------------------------- #
# C1 护栏 4：ΔW 的 5.0 是百分点，引擎权重是 0–1
# --------------------------------------------------------------------------- #
def test_c1_decay_fires_at_4_9_percentage_points() -> None:
    """手写权重 0.30 / 0.251 → ΔW = 0.049（4.9pp）< 5.0pp → 降级到 0.60。"""
    out = g.guardrail_4_confidence_decay(0.30, 0.251)

    assert out['status'] == 'ok'
    assert out['delta_w_raw'] == pytest.approx(0.049, abs=1e-9)
    assert out['delta_w_pp'] == pytest.approx(4.9, abs=1e-9)
    assert out['threshold_pp'] == 5.0
    assert out['triggered'] is True
    assert out['system_confidence'] == 0.60


def test_c1_does_not_fire_at_5_1_percentage_points() -> None:
    """手写权重 0.30 / 0.249 → ΔW = 0.051（5.1pp）>= 5.0pp → 不降级。"""
    out = g.guardrail_4_confidence_decay(0.30, 0.249)

    assert out['delta_w_raw'] == pytest.approx(0.051, abs=1e-9)
    assert out['delta_w_pp'] == pytest.approx(5.1, abs=1e-9)
    assert out['triggered'] is False
    assert out['system_confidence'] is None


def test_c1_engine_scale_delta_w_is_never_compared_to_5_directly() -> None:
    """真实引擎尺度的 ΔW（0.109806）不得触发降级 —— 它换算后是 10.98pp。

    这条是 C1 的原始反例：规格的 ``ΔW < 5.0`` 若直接比 0–1 尺度，0.109806 < 5.0
    恒真，每一场都会被钉到 0.60。用引擎实测值当输入，确认现在不降级。
    """
    out = g.guardrail_4_confidence_decay(0.377404, 0.267598)

    assert out['delta_w_raw'] == pytest.approx(0.109806, abs=1e-9)
    assert out['delta_w_pp'] == pytest.approx(10.9806, abs=1e-6)
    assert out['triggered'] is False


def test_c1_multi_signal_conflict_alone_decays() -> None:
    """ΔW 足够大但多向冲突 → 仍然降级（规范的两个条件是「或」）。"""
    out = g.guardrail_4_confidence_decay(0.90, 0.10, multi_signal_conflict=True)

    assert out['delta_w_pp'] == pytest.approx(80.0, abs=1e-9)
    assert out['triggered'] is True
    assert out['system_confidence'] == 0.60


def test_c1_missing_weight_is_reported_not_defaulted() -> None:
    """权重缺失 → 报缺，不给 0，也不降级。"""
    out = g.guardrail_4_confidence_decay(None, 0.25)

    assert out['status'] == 'skipped'
    assert out['system_confidence'] is None
    assert [entry['field'] for entry in out['missing']] == ['primary_weight']


# --------------------------------------------------------------------------- #
# C2 护栏 3：席位 = 前 8 行里该方向的比分个数
# --------------------------------------------------------------------------- #
def test_c2_trap_fires_on_four_seats_and_positive_delta() -> None:
    """主胜占 4 席且 1-0 的 Δ=+0.60 > +0.50 → 该比分权重 ×0.20 且失去首选资格。"""
    heat = _heat(['1-0', '2-0', '2-1', '3-0', '1-1', '0-1', '0-0', '0-2'])
    out = g.guardrail_3_inducing_trap(heat, {'1-0': 0.60, '2-0': 0.10})

    assert out['seats'] == {'H': 4, 'D': 2, 'A': 2}
    assert out['hot_directions'] == ['H']
    assert out['triggered'] is True
    assert out['flag'] == 'INDUCING_TRAP'
    assert out['trap_scores'] == ['1-0']
    assert out['weight_multipliers'] == {'1-0': 0.20}
    assert out['first_pick_eligible'] == {'1-0': False}


def test_c2_three_seats_is_below_threshold() -> None:
    """3 席 < 4 席 → 不触发（阈值不得被悄悄放宽）。"""
    heat = _heat(['1-0', '2-0', '2-1', '1-1', '0-0', '0-1', '0-2', '2-2'])
    out = g.guardrail_3_inducing_trap(heat, {'1-0': 0.90})

    assert out['seats']['H'] == 3
    assert out['triggered'] is False
    assert out['trap_scores'] == []


def test_c2_delta_exactly_at_threshold_does_not_fire() -> None:
    """Δ = +0.50 是「> +0.50」的边界，不得命中。"""
    heat = _heat(['1-0', '2-0', '2-1', '3-0', '1-1', '0-1', '0-0', '0-2'])
    out = g.guardrail_3_inducing_trap(heat, {'1-0': 0.50})

    assert out['triggered'] is False


def test_c2_seats_count_scores_not_ranks_or_odds() -> None:
    """席位只数同向比分个数：赔率高低不影响，聚合比分也不额外加权。"""
    heat = _heat(['1-0', '2-0', '2-1', '3-0', '胜其它', '0-1', '0-0', '0-2'])
    out = g.guardrail_3_inducing_trap(heat, {'1-0': 0.90})

    assert out['seats'] == {'H': 5, 'D': 1, 'A': 2}
    assert out['hot_directions'] == ['H']


def test_c2_empty_ranking_is_missing_not_zero_seats() -> None:
    """空榜单 → 护栏不生效并写入 missing，绝不按 0 席静默通过。"""
    out = g.guardrail_3_inducing_trap([], {'1-0': 0.90})

    assert out['status'] == 'skipped'
    assert out['triggered'] is False
    assert out['seats'] == {}
    assert [entry['field'] for entry in out['missing']] == ['heat_ranking']


def test_c2_rows_without_score_are_missing_not_zero_seats() -> None:
    """行里没有可解析 score → 同样报缺，不按 0 席。"""
    out = g.guardrail_3_inducing_trap([{'odds': 6.5}, {'odds': 7.2}], {'1-0': 0.90})

    assert out['status'] == 'skipped'
    assert out['seats'] == {}
    assert [entry['field'] for entry in out['missing']] == ['heat_ranking[].score']


def test_c2_hot_seats_without_score_deltas_is_missing() -> None:
    """有 4 席但没给逐比分 Δ → 报缺，不猜成 0 再判「不触发」。"""
    heat = _heat(['1-0', '2-0', '2-1', '3-0', '1-1', '0-1', '0-0', '0-2'])
    out = g.guardrail_3_inducing_trap(heat, None)

    assert out['triggered'] is False
    assert [entry['field'] for entry in out['missing']] == ['score_deltas']


# --------------------------------------------------------------------------- #
# C3 护栏 2：1.35 用 |Δ_胜| / |Δ_负|，分母 0 必须抛错或返回 None
# --------------------------------------------------------------------------- #
def test_c3_home_away_ratio_uses_win_over_loss() -> None:
    """|Δ_胜|/|Δ_负| = 0.40/0.50 = 0.8；平局通道不参与。"""
    assert g.home_away_abs_ratio(ABS_DELTAS_EVEN) == pytest.approx(0.8, abs=1e-12)


def test_c3_draw_channel_is_excluded_from_ratio() -> None:
    """只改平局通道，比值完全不变（证明分母不是「第二小」）。"""
    moved = dict(ABS_DELTAS_EVEN, 平=99.0)

    assert g.home_away_abs_ratio(moved) == pytest.approx(0.8, abs=1e-12)


def test_c3_zero_loss_denominator_raises() -> None:
    """|Δ_负| = 0 → 抛 GuardrailInputError；返回 0.0 即失败。"""
    with pytest.raises(g.GuardrailInputError):
        g.home_away_abs_ratio({'胜': 0.40, '平': 0.60, '负': 0.0})


def test_c3_zero_home_numerator_raises_too() -> None:
    """分子 0 同样无意义（0/x 会伪装成「极度均势」），一并拒绝。"""
    with pytest.raises(g.GuardrailInputError):
        g.home_away_abs_ratio({'胜': 0.0, '平': 0.60, '负': 0.50})


def test_c3_even_match_result_reports_error_without_ratio() -> None:
    """分母 0 时结果里 ratio 必须是 None，且带 invalid。"""
    out = g.guardrail_2_even_match({'胜': 0.40, '平': 0.60, '负': 0.0})

    assert out['status'] == 'skipped'
    assert out['home_away_abs_ratio'] is None
    assert out['triggered'] is False
    assert out['invalid']


def test_c3_missing_field_is_missing_not_invalid() -> None:
    """字段缺失与字段为 0 是两回事：缺失进 missing，0 进 invalid。"""
    out = g.guardrail_2_even_match({'胜': 0.40, '负': 0.50})

    assert [entry['field'] for entry in out['missing']] == ['abs_deltas.平']
    assert out['invalid'] == []
    assert out['home_away_abs_ratio'] is None


def test_c3_neighborhood_ratio_is_second_min_over_first() -> None:
    """2 倍邻近是「第二小/第一小」：0.90/0.30 = 3.0。"""
    assert g.channel_neighborhood_ratio(ABS_DELTAS_WIDE) == pytest.approx(3.0, abs=1e-12)


def test_c3_accepts_win_draw_loss_channel_names() -> None:
    """契约 C3 说通道名是 WIN/DRAW/LOSS：这些键必须等价于中文键。"""
    latin = {'WIN': 0.40, 'DRAW': 0.60, 'LOSS': 0.50}
    chinese = {'胜': 0.40, '平': 0.60, '负': 0.50}

    assert g.home_away_abs_ratio(latin) == pytest.approx(0.8, abs=1e-12)
    assert g.home_away_abs_ratio(latin) == g.home_away_abs_ratio(chinese)


# --------------------------------------------------------------------------- #
# 集成契约 §1.1：必须直接接受注入的 ChannelAbsSummary
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _ChannelAbsSummaryShape:
    """``score_matrices.ChannelAbsSummary`` 的逐字复刻（字段名与顺序完全一致）。

    两个模块在不同分支上（矩阵在 ``cursor/score-matrices``，本模块在
    ``cursor/market-guardrails``，都从 ``3b46c68`` 切出，互相看不见），所以这里
    **不能** ``import score_matrices``。用这个本地 dataclass 钉住接口形状；
    集成分支落地后应换成真实 ``ChannelAbsSummary``。
    """

    win_mean_abs_delta: float
    draw_mean_abs_delta: float
    loss_mean_abs_delta: float
    win_count: int
    draw_count: int
    loss_count: int
    min_channel: str
    channel_spread: float
    # 矩阵模块在分叉后补了「有价行数」三项和「最小通道只是部分比较」标记。
    # 这里刻意保持逐字复刻（含默认值），让下面的形状断言在真实类再次漂移时变红。
    win_priced_count: int = 0
    draw_priced_count: int = 0
    loss_priced_count: int = 0
    min_channel_partial: bool = False


_SUMMARY_VALUES = {'win': 0.3462, 'draw': 0.9, 'loss': 1.1769}
#: 手算：0.3462 / 1.1769 = 0.2941626306398...
_EXPECTED_HOME_AWAY = 0.3462 / 1.1769
#: 手算：第二小/第一小 = 0.9 / 0.3462 = 2.5996533795...
_EXPECTED_NEIGHBORHOOD = 0.9 / 0.3462


def _summary_shape() -> _ChannelAbsSummaryShape:
    return _ChannelAbsSummaryShape(
        win_mean_abs_delta=_SUMMARY_VALUES['win'],
        draw_mean_abs_delta=_SUMMARY_VALUES['draw'],
        loss_mean_abs_delta=_SUMMARY_VALUES['loss'],
        win_count=13,
        draw_count=5,
        loss_count=13,
        min_channel='WIN',
        channel_spread=_SUMMARY_VALUES['loss'] - _SUMMARY_VALUES['win'],
    )


def test_c3_accepts_channel_abs_summary_dataclass() -> None:
    """传 dataclass 本体（集成契约 §1.1 的那种传法）→ 算出 |ΔW|/|ΔL|。"""
    ratio = g.home_away_abs_ratio(_summary_shape())

    assert ratio == pytest.approx(_EXPECTED_HOME_AWAY, rel=1e-12)
    assert g.channel_neighborhood_ratio(_summary_shape()) == pytest.approx(
        _EXPECTED_NEIGHBORHOOD, rel=1e-12,
    )


def test_c3_accepts_dataclasses_asdict_output() -> None:
    """``dataclasses.asdict`` 会把 min_channel/win_count/spread 一起带出来 —— 仍须可用。"""
    payload = dataclasses.asdict(_summary_shape())

    assert set(payload) == {
        'win_mean_abs_delta', 'draw_mean_abs_delta', 'loss_mean_abs_delta',
        'win_count', 'draw_count', 'loss_count', 'min_channel', 'channel_spread',
        'win_priced_count', 'draw_priced_count', 'loss_priced_count', 'min_channel_partial',
    }
    assert g.home_away_abs_ratio(payload) == pytest.approx(_EXPECTED_HOME_AWAY, rel=1e-12)


def test_c3_accepts_lowercase_field_name_dict() -> None:
    """小写字段名字典是矩阵模块的真实字段名 —— 必须与 dataclass 走同一条路。"""
    payload = {
        'win_mean_abs_delta': _SUMMARY_VALUES['win'],
        'draw_mean_abs_delta': _SUMMARY_VALUES['draw'],
        'loss_mean_abs_delta': _SUMMARY_VALUES['loss'],
    }

    assert g.home_away_abs_ratio(payload) == pytest.approx(_EXPECTED_HOME_AWAY, rel=1e-12)


def test_c3_all_channel_encodings_agree_exactly() -> None:
    """四种交付方式必须给出**完全相同**的比值，不是「各自接近」。"""
    summary = _summary_shape()
    encodings = {
        'dataclass': summary,
        'asdict': dataclasses.asdict(summary),
        'lowercase': {
            'win_mean_abs_delta': _SUMMARY_VALUES['win'],
            'draw_mean_abs_delta': _SUMMARY_VALUES['draw'],
            'loss_mean_abs_delta': _SUMMARY_VALUES['loss'],
        },
        'uppercase_alias': {
            'WIN_mean_abs_delta': _SUMMARY_VALUES['win'],
            'DRAW_mean_abs_delta': _SUMMARY_VALUES['draw'],
            'LOSS_mean_abs_delta': _SUMMARY_VALUES['loss'],
        },
    }
    ratios = {name: g.home_away_abs_ratio(value) for name, value in encodings.items()}

    assert len(set(ratios.values())) == 1, ratios
    assert next(iter(ratios.values())) == pytest.approx(_EXPECTED_HOME_AWAY, rel=1e-12)


def test_c3_dataclass_zero_loss_channel_still_raises() -> None:
    """dataclass 路径不得绕过零分母拦截。"""
    zeroed = _ChannelAbsSummaryShape(
        win_mean_abs_delta=0.3462,
        draw_mean_abs_delta=0.9,
        loss_mean_abs_delta=0.0,
        win_count=13,
        draw_count=5,
        loss_count=13,
        min_channel='LOSS',
        channel_spread=0.9,
    )

    with pytest.raises(g.GuardrailInputError):
        g.home_away_abs_ratio(zeroed)


def test_c3_guardrail_2_reports_dataclass_conflicts() -> None:
    """dataclass 混入矛盾写法 → 报冲突，不静默挑一个。"""
    class _Mixed:
        win_mean_abs_delta = 0.3462
        WIN = 0.99
        draw_mean_abs_delta = 0.9
        loss_mean_abs_delta = 1.1769

    out = g.guardrail_2_even_match(_Mixed())

    assert out['status'] == 'skipped'
    assert out['home_away_abs_ratio'] is None
    assert out['invalid'] and '不同数值' in out['invalid'][0]['reason']


def test_c3_unsupported_input_type_is_missing_not_crash() -> None:
    out = g.guardrail_2_even_match(object())

    assert out['status'] == 'skipped'
    assert [entry['field'] for entry in out['missing']] == ['abs_deltas']


def test_c3_local_dataclass_shape_matches_real_channel_abs_summary() -> None:
    """若真实 ``score_matrices`` 在路径上（集成后），字段名必须与本地复刻逐字一致。

    两个模块现在分属不同分支，所以这条用 ``importorskip`` 优雅跳过；集成分支
    把两者放到一起后，它会自动变成一条真正的形状校验。
    """
    score_matrices = pytest.importorskip('score_matrices')
    real_fields = [field.name for field in dataclasses.fields(score_matrices.ChannelAbsSummary)]
    local_fields = [field.name for field in dataclasses.fields(_ChannelAbsSummaryShape)]

    assert real_fields == local_fields
    assert real_fields == [
        'win_mean_abs_delta', 'draw_mean_abs_delta', 'loss_mean_abs_delta',
        'win_count', 'draw_count', 'loss_count', 'min_channel', 'channel_spread',
        'win_priced_count', 'draw_priced_count', 'loss_priced_count', 'min_channel_partial',
    ]


def test_c3_real_matrix_output_ratio_matches_hand_computation() -> None:
    """用真实矩阵模块的产出算一次，期望值手算。

    矩阵不在本分支时跳过；集成后这条就是端到端验证。
    """
    score_matrices = pytest.importorskip('score_matrices')
    scores = score_matrices.THIRTY_ONE_SCORES
    market: dict[str, float] = {}
    jc: dict[str, dict[str, object]] = {}
    for index, score in enumerate(scores):
        home, away = (int(x) for x in score.split('-')) if score[0].isdigit() else (0, 0)
        base = 8.0 + index * 0.1
        market[score] = round(base, 2)
        bonus = 0.30 if home > away else (0.90 if home == away else 1.20)
        jc[score] = {'odds': round(base + bonus, 2), 'heat_seats': 1}

    matrices = score_matrices.build_score_matrices('guardrail-probe', market, jc)
    summary = matrices.channel_summary

    # 手算期望：矩阵把每向均值 round 到 4 位，这里直接用手工构造的差值算。
    expected = summary.win_mean_abs_delta / summary.loss_mean_abs_delta
    assert g.home_away_abs_ratio(summary) == pytest.approx(expected, rel=1e-12)
    assert g.home_away_abs_ratio(dataclasses.asdict(summary)) == pytest.approx(expected, rel=1e-12)
    assert g.home_away_abs_ratio({
        'win_mean_abs_delta': summary.win_mean_abs_delta,
        'draw_mean_abs_delta': summary.draw_mean_abs_delta,
        'loss_mean_abs_delta': summary.loss_mean_abs_delta,
    }) == pytest.approx(expected, rel=1e-12)


def test_c3_accepts_matrix_module_summary_keys() -> None:
    """矩阵模块 ChannelAbsSummary 的旧字典键也要直接可用。"""
    summary = {
        'WIN_mean_abs_delta': 0.30,
        'DRAW_mean_abs_delta': 0.45,
        'LOSS_mean_abs_delta': 1.10,
    }

    assert g.channel_neighborhood_ratio(summary) == pytest.approx(1.5, abs=1e-12)


def test_c3_conflicting_channel_spellings_are_rejected() -> None:
    """同一通道两种写法给了不同数值 → 报冲突，不静默挑一个。"""
    out = g.guardrail_2_even_match({'胜': 0.40, 'WIN': 0.90, '平': 0.60, '负': 0.50})

    assert out['status'] == 'skipped'
    assert out['home_away_abs_ratio'] is None
    assert out['invalid'] and '不同数值' in out['invalid'][0]['reason']


def test_c3_zero_loss_denominator_raises_with_latin_keys() -> None:
    with pytest.raises(g.GuardrailInputError):
        g.home_away_abs_ratio({'WIN': 0.40, 'DRAW': 0.60, 'LOSS': 0.0})


# --------------------------------------------------------------------------- #
# C4 w_league_pressure 无来源：0.0 必须带 source=unavailable
# --------------------------------------------------------------------------- #
def test_c4_league_pressure_defaults_to_unavailable_zero() -> None:
    weight, source = g.resolve_league_pressure('英超')

    assert weight == 0.0
    assert source == 'unavailable'


def test_c4_protection_multiplier_is_base_when_unavailable() -> None:
    """2.50 + 0 = 2.50，但 source 必须让调用方看见这是缺省值。"""
    out = g.stage2_true_protection({'1-0': -0.60}, direction='H', league_name='英超')

    assert out['protection_multiplier'] == 2.50
    assert out['w_league_pressure'] == 0.0
    assert out['league_pressure_source'] == 'unavailable'


def test_c4_none_league_also_reports_unavailable() -> None:
    weight, source = g.resolve_league_pressure(None)

    assert (weight, source) == (0.0, 'unavailable')


# --------------------------------------------------------------------------- #
# C5 tier_diff 的两个具名标记逐字匹配
# --------------------------------------------------------------------------- #
def test_c5_win_marker_matches_literal() -> None:
    out = g.guardrail_1_macro_dominance(2)

    assert out['veto_action'] == 'V16_31_Fundamental_Tier_Dominance_Win'
    assert out['veto_action'] == g.VETO_TIER_DOMINANCE_WIN
    assert out['locked_direction'] == 'H'


def test_c5_loss_marker_matches_literal() -> None:
    out = g.guardrail_1_macro_dominance(-2)

    assert out['veto_action'] == 'V16_31_Fundamental_Tier_Dominance_Loss'
    assert out['veto_action'] == g.VETO_TIER_DOMINANCE_LOSS
    assert out['locked_direction'] == 'A'


def test_c5_inside_band_releases_micro() -> None:
    """|tier_diff| < 2 放行微观，veto_action 保持既有 'NONE'。"""
    out = g.guardrail_1_macro_dominance(1)

    assert out['applies'] is False
    assert out['veto_action'] == 'NONE'


def test_c5_marker_constants_are_exact_strings() -> None:
    assert g.VETO_TIER_DOMINANCE_WIN == 'V16_31_Fundamental_Tier_Dominance_Win'
    assert g.VETO_TIER_DOMINANCE_LOSS == 'V16_31_Fundamental_Tier_Dominance_Loss'


# --------------------------------------------------------------------------- #
# C6 tier_diff 只能显式传入
# --------------------------------------------------------------------------- #
def test_c6_missing_tier_diff_is_reported_not_inferred() -> None:
    """不给 tier_diff → 宏观护栏不生效并报缺；不得从排名/赔率差反推。"""
    out = g.guardrail_1_macro_dominance(None)

    assert out['status'] == 'skipped'
    assert out['applies'] is False
    assert out['tier_diff'] is None
    assert [entry['field'] for entry in out['missing']] == ['tier_diff']


def test_c6_non_integer_tier_diff_is_invalid() -> None:
    out = g.guardrail_1_macro_dominance(1.5)

    assert out['status'] == 'invalid'
    assert out['applies'] is False
    assert out['invalid']


def test_c6_garbage_tier_diff_is_invalid() -> None:
    out = g.guardrail_1_macro_dominance('强')

    assert out['status'] == 'invalid'
    assert out['applies'] is False


# --------------------------------------------------------------------------- #
# C7 1.80 联赛放宽不接电：'荷甲' 不得触发
# --------------------------------------------------------------------------- #
def test_c7_is_high_entropy_league_always_unavailable() -> None:
    assert g.is_high_entropy_league('荷甲') == (False, 'unavailable')
    assert g.is_high_entropy_league('英超') == (False, 'unavailable')
    assert g.is_high_entropy_league(None) == (False, 'unavailable')


def test_c7_dutch_league_never_relaxes_the_threshold() -> None:
    """'荷甲' 是规范点名的放宽联赛；本轮词表不可靠，必须不触发。

    这条测试是防回退护栏：谁若凭队名硬匹配把荷甲塞进白名单，这里立刻红。
    """
    out = g.guardrail_2_even_match(ABS_DELTAS_LOPSIDED, league_name='荷甲')

    assert out['high_entropy_relax_applied'] is False
    assert out['high_entropy_relax_reason'] == 'league_vocabulary_unreliable'
    assert out['threshold_used'] == 1.35
    assert out['triggered'] is False


def test_c7_dutch_league_ratio_between_1_35_and_1_80_stays_inert() -> None:
    """1.50 落在 (1.35, 1.80) 区间：若能放宽就会命中，现在必须不命中。"""
    # |Δ_胜|/|Δ_负| = 0.60/0.40 = 1.50
    out = g.guardrail_2_even_match({'胜': 0.60, '平': 0.90, '负': 0.40}, league_name='荷甲')

    assert out['home_away_abs_ratio'] == pytest.approx(1.50, abs=1e-12)
    assert out['high_entropy_relax_applied'] is False
    assert out['triggered'] is False


def test_c7_evaluate_exposes_relax_fields_at_top_level() -> None:
    out = g.evaluate_market_guardrails({
        'abs_deltas': ABS_DELTAS_LOPSIDED,
        'league_name': '荷甲',
    })

    assert out['high_entropy_relax_applied'] is False
    assert out['high_entropy_relax_reason'] == 'league_vocabulary_unreliable'


# --------------------------------------------------------------------------- #
# C8 2 倍邻近：direction 不变，无跨方向推荐
# --------------------------------------------------------------------------- #
def test_c8_neighborhood_flag_does_not_touch_direction() -> None:
    out = g.guardrail_2_neighborhood(ABS_DELTAS_NEAR)

    assert out['triggered'] is True
    assert out['neighborhood_flag'] == 'NON_SINGLE_SIDED'
    assert out['threshold'] == 2.0
    assert out['neighborhood_ratio'] == pytest.approx(1.5, abs=1e-12)
    assert out['direction_modified'] is False
    assert 'direction' not in out


def test_c8_no_second_direction_in_locked_scores_after_neighborhood() -> None:
    """命中邻近后，locked_scores 的每个 bucket 仍与 direction 一致。"""
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_NEAR,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    })

    assert out['direction'] == 'H'
    assert out['guardrail_2_neighborhood']['triggered'] is True
    assert out['locked_contract']['ok'] is True
    for item in out['locked_scores']:
        assert g._score_bucket(item['score']) == 'home'


def test_c8_risk_note_has_no_pick_language() -> None:
    """邻近的风险陈述不得含 首选/次选/双选/强推/推荐。"""
    out = g.guardrail_2_neighborhood(ABS_DELTAS_NEAR)

    assert g.pick_language_violations(out['risk_note']) == []


# --------------------------------------------------------------------------- #
# PAR-11b 护栏 3 的自伤：一句合法风险陈述把整次评估判成 invalid
# --------------------------------------------------------------------------- #
def test_trap_note_contains_no_banned_word_literally() -> None:
    """重点在改写：这句话本身就不含五个被禁词里的任何一个字。

    不靠否定式豁免过关 —— PAR-9 的渲染侧扫描在报告/TG 生成时还会再扫一遍，
    带禁词的句子只是把红灯从本模块挪到渲染层。
    """
    for word in g.PICK_LANGUAGE:
        assert word not in g.TRAP_RISK_NOTE, f'TRAP_RISK_NOTE 含被禁词 {word!r}'
    assert '优先资格' in g.TRAP_RISK_NOTE


def test_trap_note_survives_the_scanner_even_without_negation_exemption() -> None:
    """把否定豁免关掉，这句仍要干净 —— 证明它是真的改写，不是被豁免放过。"""
    import re as _re

    cleaned = _re.sub('|'.join(g.NEGATION_CLAUSE_PATTERNS), '', g.TRAP_RISK_NOTE)
    for phrase in g.NEGATION_PHRASES:
        cleaned = cleaned.replace(phrase, '')

    assert [word for word in g.PICK_LANGUAGE if word in cleaned] == []


def test_trap_trigger_no_longer_invalidates_the_evaluation() -> None:
    """护栏 3 触发 + 输入给了可锁定比分 → status 不是 invalid，locked_scores 不为空。"""
    heat = _heat(['1-0', '2-0', '2-1', '3-0', '1-1', '0-1', '0-0', '0-2'])
    common = {
        'direction': 'H',
        'abs_deltas': {'胜': 0.40, '平': 0.60, '负': 0.50},
        'heat_ranking': heat,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    }
    with_trap = g.evaluate_market_guardrails(dict(common, score_deltas={'1-0': 0.90}))
    without_trap = g.evaluate_market_guardrails(dict(common, score_deltas={'1-0': 0.10}))

    assert with_trap['guardrail_3_inducing_trap']['triggered'] is True
    assert without_trap['guardrail_3_inducing_trap']['triggered'] is False
    assert with_trap['status'] != 'invalid'
    assert with_trap['status'] == without_trap['status']
    assert len(with_trap['locked_scores']) == 2
    assert with_trap['invalid'] == []
    assert with_trap['self_check']['ok'] is True


def test_self_check_violations_do_not_pollute_status() -> None:
    """措辞违规走独立 self_check，不把 status 翻成 invalid。"""
    result = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_WIDE,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    })
    # 人工塞一条明确给指引的陈述，验证自检路径本身真的会抓。
    result['risk_notes'].append('建议首选 1-0')
    g.apply_language_self_check(result)

    assert result['self_check']['ok'] is False
    assert len(result['self_check']['violations']) == 1
    assert result['self_check']['violations'][0]['guardrail'] == 'PICK_LANGUAGE'
    assert result['self_check']['violations'][0]['matched'] == ['首选']
    assert result['status'] != 'invalid', 'wording must not flip status'
    assert result['invalid'] == []

    # 走真实入口时：这句输入只命中均势（0.30/1.10=0.27），自检全绿且数过的条数对得上。
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_WIDE,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    })
    assert out['self_check']['ok'] is True
    assert out['self_check']['violations'] == []
    assert out['self_check']['checked_notes'] == len(out['risk_notes']) == 1
    assert out['status'] != 'invalid'


@pytest.mark.parametrize('text', [
    '1-0 不具备首选资格',
    '相关比分不具备任何首选资格',
    '1-0 不满足进池条件',
    '该比分已失去首选资格',
    '1-0 不得作为首选',
    '禁止把 1-0 当首选',
    '已排除该比分作为首选',
    '本场不做推荐',
    '不推荐该比分',
])
def test_negation_and_deprivation_clauses_are_allowed(text: str) -> None:
    """否定—剥夺结构在剥夺资格，不是在给指引 → 放行。"""
    assert g.pick_language_violations(text) == []


@pytest.mark.parametrize('text', [
    '建议首选 1-0',
    '首选 2-1',
    '次选 1-1',
    '双选 1-0 与 0-1',
    '强推 2-0',
    '推荐 1-1',
    '1-0 不具备首选资格，建议首选 2-0',
    '不得选 1-0，推荐 2-1',
])
def test_actual_guidance_is_still_rejected(text: str) -> None:
    """收紧方向必须成立：真的给指引就要判违规，豁免不能把扫描关掉。"""
    assert g.pick_language_violations(text) != []


def test_every_string_in_the_result_is_language_clean() -> None:
    """递归遍历整个返回结构的所有字符串，断言零命中。

    这是「输出侧干净」的硬证明：比只测 risk_notes 强，连嵌套 dict、
    被拒列表里的 reason、flag 名称都覆盖到。
    """
    heat = _heat(['1-0', '2-0', '2-1', '3-0', '1-1', '0-1', '0-0', '0-2'])
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'tier_diff': 0,
        'abs_deltas': ABS_DELTAS_EVEN,
        'score_deltas': {
            '1-0': 0.90, '2-0': -0.70, '2-1': 0.80, '1-1': -0.90,
            '0-1': -0.80, '胜其它': 30.0, '负其它': 30.0,
        },
        'heat_ranking': heat,
        'primary_weight': 0.30,
        'secondary_weight': 0.251,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
        'league_name': '荷甲',
    })
    assert out['guardrail_3_inducing_trap']['triggered'] is True

    strings: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(out)
    assert len(strings) > 50, f'walk did not traverse the structure: {len(strings)} strings'
    offenders = [(text, g.pick_language_violations(text)) for text in strings]
    offenders = [(text, hits) for text, hits in offenders if hits]
    assert offenders == [], offenders


def test_empty_locked_scores_are_not_applicable_not_violated() -> None:
    """没有上游锁定比分时，locked_contract 标 not_applicable，不给 ok=false。"""
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': {'胜': 0.40, '平': 0.60, '负': 0.50},
        'score_deltas': {'1-0': -0.60, '2-0': -0.70, '0-1': -0.90, '0-2': -0.80, '1-1': -0.90},
    })

    assert out['locked_scores'] == []
    assert out['locked_contract']['status'] == 'not_applicable'
    assert out['locked_contract']['ok'] is None
    assert out['locked_contract']['reasons'] == []
    assert '必须恰 2 条' not in str(out['locked_contract'])
    assert out['status'] != 'invalid'


def test_present_locked_scores_report_ok_or_violated() -> None:
    """给了锁定比分才有 ok/violated 两态；这里两态各测一次。"""
    good = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_WIDE,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    })
    bad = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_WIDE,
        'locked_scores': [{'score': '2-1'}, {'score': '0-1'}],
    })

    assert good['locked_contract']['status'] == 'ok'
    assert good['locked_contract']['ok'] is True
    assert bad['locked_contract']['status'] == 'violated'
    assert bad['locked_contract']['ok'] is False
    assert bad['status'] == 'invalid'


def test_c8_ratio_at_threshold_does_not_trigger() -> None:
    """第二小/第一小 恰为 2.0 → 「< 2.0」不成立，不触发。"""
    out = g.guardrail_2_neighborhood({'胜': 0.30, '平': 0.60, '负': 1.10})

    assert out['neighborhood_ratio'] == pytest.approx(2.0, abs=1e-12)
    assert out['triggered'] is False


# --------------------------------------------------------------------------- #
# C9 1.35 均势：direction 不变，不把 D 塞进任何集合
# --------------------------------------------------------------------------- #
def test_c9_even_match_flag_and_note() -> None:
    out = g.guardrail_2_even_match(ABS_DELTAS_EVEN)

    assert out['triggered'] is True
    assert out['fusion_flag'] == 'EVEN_MATCH'
    assert out['threshold'] == 1.35
    assert out['home_away_abs_ratio'] == pytest.approx(0.8, abs=1e-12)
    assert out['direction_modified'] is False
    assert '均势盘' in out['draw_note']


def test_c9_draw_is_never_appended_to_locked_scores() -> None:
    """命中均势后 locked_scores 里不得出现平局比分。"""
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_EVEN,
        'locked_scores': [{'score': '2-1'}, {'score': '3-1'}],
        'score_deltas': {'1-1': -0.90, '2-2': -0.70},
    })

    assert out['direction'] == 'H'
    assert out['guardrail_2_neighborhood'] or out['stage1_min_abs_diff_lock']
    assert out['locked_contract']['ok'] is True
    buckets = {g._score_bucket(item['score']) for item in out['locked_scores']}
    assert buckets == {'home'}
    assert '1-1' not in [item['score'] for item in out['locked_scores']]


def test_c9_ratio_at_threshold_triggers() -> None:
    """比值恰为 1.35 → 「<= 1.35」成立，命中。"""
    out = g.guardrail_2_even_match({'胜': 0.54, '平': 0.90, '负': 0.40})

    assert out['home_away_abs_ratio'] == pytest.approx(1.35, abs=1e-12)
    assert out['triggered'] is True


# --------------------------------------------------------------------------- #
# C10 Overkill：+20.0，方向对齐才激活
# --------------------------------------------------------------------------- #
def test_c10_overkill_activates_only_when_aligned() -> None:
    out = g.overkill_protocol({'胜其它': 20.0, '负其它': 25.0}, direction='H')

    assert out['overkill_activated'] is True
    assert [item['score'] for item in out['overkill_candidates']] == ['胜其它']
    assert [item['score'] for item in out['overkill_rejected']] == ['负其它']
    assert out['overkill_rejected'][0]['reason'] == 'direction_mismatch'


def test_c10_overkill_below_threshold_is_ignored() -> None:
    out = g.overkill_protocol({'胜其它': 19.999}, direction='H')

    assert out['overkill_activated'] is False
    assert out['overkill_candidates'] == []


def test_c10_overkill_never_rewrites_direction() -> None:
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'score_deltas': {'胜其它': 30.0, '负其它': 40.0},
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    })

    assert out['direction'] == 'H'
    assert out['overkill_activated'] is True
    assert all(item['bucket'] == 'home' for item in out['overkill_candidates'])
    assert all(item['bucket'] == 'away' for item in out['overkill_rejected'])


# --------------------------------------------------------------------------- #
# 阶段 1 / 阶段 2
# --------------------------------------------------------------------------- #
def test_stage1_macro_lock_beats_absolute_fallback() -> None:
    """宏观否决 > |Δ| 最小兜底：这里 |Δ| 最小是「负」，但宏观锁主胜。"""
    out = g.stage1_min_abs_diff_lock(ABS_DELTAS_LOPSIDED, macro_locked_direction='H')

    assert out['min_abs_channel'] == '负'
    assert out['baseline_direction'] == 'H'
    assert out['direction_source'] == 'macro_tier_dominance'
    assert out['applied_macro_lock'] is True


def test_stage1_upstream_direction_is_not_overridden() -> None:
    out = g.stage1_min_abs_diff_lock(ABS_DELTAS_LOPSIDED, direction='D')

    assert out['baseline_direction'] == 'D'
    assert out['direction_source'] == 'upstream'


def test_stage1_falls_back_to_min_abs_channel() -> None:
    out = g.stage1_min_abs_diff_lock(ABS_DELTAS_LOPSIDED)

    assert out['min_abs_channel'] == '负'
    assert out['baseline_direction'] == 'A'
    assert out['direction_source'] == 'absolute_fallback'


def test_stage1_still_runs_guardrail_2_checks() -> None:
    """阶段 1 叠加了 2 倍邻近，但方向来源仍是单一通道，不是双选。"""
    out = g.evaluate_market_guardrails({
        'abs_deltas': ABS_DELTAS_NEAR,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
    })

    assert out['stage1_min_abs_diff_lock']['min_abs_channel'] == '胜'
    assert out['direction'] == 'H'
    assert out['guardrail_2_neighborhood']['triggered'] is True


def test_stage2_protection_collects_only_aligned_scores() -> None:
    """方向 H：1-0 / 2-0 进候选，0-1 / 0-2 进 rejected，1-1 也 rejected。"""
    deltas = {'1-0': -0.60, '2-0': -0.55, '0-1': -0.90, '0-2': -0.80, '1-1': -0.70}
    out = g.stage2_true_protection(deltas, direction='H')

    assert out['protection_multiplier'] == 2.50
    assert [item['score'] for item in out['protection_candidates']] == ['1-0', '2-0']
    rejected = {item['score']: item['reason'] for item in out['protection_rejected']}
    assert rejected == {'0-1': 'direction_mismatch', '0-2': 'direction_mismatch', '1-1': 'direction_mismatch'}


def test_stage2_delta_exactly_at_threshold_is_not_protected() -> None:
    """Δ = −0.50 是「< −0.50」的边界，不得命中。"""
    out = g.stage2_true_protection({'1-0': -0.50}, direction='H')

    assert out['protection_candidates'] == []
    assert out['multipliers'] == {}


def test_stage2_missing_core_score_delta_is_reported() -> None:
    """核心比分缺 Δ → 报缺，不按 0 处理（0 会伪装成「未达阈值」）。"""
    out = g.stage2_true_protection({'1-0': -0.60}, direction='H')

    missing = {entry['field'] for entry in out['missing']}
    assert missing == {'score_deltas.2-0', 'score_deltas.0-1', 'score_deltas.0-2', 'score_deltas.1-1'}


def test_stage2_missing_score_deltas_is_reported() -> None:
    out = g.stage2_true_protection(None, direction='H')

    assert out['status'] == 'skipped'
    assert [entry['field'] for entry in out['missing']] == ['score_deltas']


@pytest.mark.parametrize('direction', ['H', 'D', 'A', ''])
def test_stage2_candidates_never_span_two_directions(direction: str) -> None:
    """五个核心比分跨三个方向：任何 direction 下候选池都只含一个方向。

    direction='D' 是关键一格：1-0/2-0/0-1/0-2 全在阈值下，但只有 1-1 同向，
    另外四个必须落到 rejected，绝不能拼成一个跨方向的保护池。
    """
    deltas = {'1-0': -0.90, '2-0': -0.80, '0-1': -0.90, '0-2': -0.80, '1-1': -0.90}
    out = g.stage2_true_protection(deltas, direction=direction)

    buckets = {g._score_bucket(item['score']) for item in out['protection_candidates']}
    assert len(buckets) <= 1
    if direction in ('H', 'D', 'A'):
        expected = {'H': 'home', 'D': 'draw', 'A': 'away'}[direction]
        assert buckets <= {expected}
        assert {item['score'] for item in out['protection_candidates']} <= {
            score for score in deltas
            if g._score_bucket(score) == expected
        }


# --------------------------------------------------------------------------- #
# 方向铁律：任何推荐集合不得跨方向
# --------------------------------------------------------------------------- #
def test_single_direction_contract_rejects_cross_direction_pair() -> None:
    report = g.check_single_direction('H', [{'score': '2-1'}, {'score': '0-1'}])

    assert report['ok'] is False
    assert report['offending'] == [{'score': '0-1', 'bucket': 'away', 'reason': 'hedge_direction_in_locked'}]


def test_single_direction_contract_rejects_wrong_length() -> None:
    report = g.check_single_direction('H', [{'score': '2-1'}])

    assert report['ok'] is False
    assert report['count'] == 1


def test_single_direction_contract_accepts_aligned_pair() -> None:
    report = g.check_single_direction('H', [{'score': '2-1'}, {'score': '1-0'}])

    assert report['ok'] is True
    assert report['offending'] == []


def test_evaluate_flags_cross_direction_locked_scores_as_invalid() -> None:
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'abs_deltas': ABS_DELTAS_WIDE,
        'locked_scores': [{'score': '2-1'}, {'score': '0-1'}],
    })

    assert out['status'] == 'invalid'
    assert out['locked_contract']['ok'] is False


def test_no_guardrail_output_contains_a_second_direction_in_any_pick_set() -> None:
    """总闸门：护栏 2/3/4 全命中时，方向与全部候选池仍是单方向。"""
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'tier_diff': 0,
        'abs_deltas': ABS_DELTAS_EVEN,
        'score_deltas': {
            '1-0': -0.60, '2-0': -0.70, '1-1': -0.90, '2-1': 0.80,
            '0-1': -0.80, '胜其它': 30.0, '负其它': 30.0,
        },
        'heat_ranking': _heat(['1-0', '2-0', '2-1', '3-0', '1-1', '0-1', '0-0', '0-2']),
        'primary_weight': 0.30,
        'secondary_weight': 0.251,
        'locked_scores': [{'score': '2-1'}, {'score': '1-0'}],
        'league_name': '荷甲',
    })

    assert out['direction'] == 'H'
    assert out['direction_before'] == 'H'
    assert out['direction_after'] == 'H'
    assert out['direction_changed_by'] == ''
    assert out['guardrail_2_neighborhood']['triggered'] is True
    assert out['guardrail_2_even_match']['triggered'] is True
    assert out['guardrail_3_inducing_trap']['triggered'] is True
    assert out['guardrail_4_confidence_decay']['triggered'] is True
    assert out['guardrail_4_confidence_decay']['system_confidence'] == 0.60

    sets = g._recommendation_sets(out)
    for name in ('locked_scores', 'protection_candidates', 'overkill_candidates'):
        for item in sets[name]:
            assert g._score_bucket(item['score']) == 'home', f'{name} 跨方向: {item}'


def test_tier_dominance_is_the_only_direction_changer() -> None:
    out = g.evaluate_market_guardrails({
        'direction': 'H',
        'tier_diff': -3,
        'abs_deltas': ABS_DELTAS_EVEN,
    })

    assert out['direction'] == 'A'
    assert out['direction_changed_by'] == 'guardrail_1_macro_dominance'
    assert out['guardrail_1_macro_dominance']['veto_action'] == 'V16_31_Fundamental_Tier_Dominance_Loss'


# --------------------------------------------------------------------------- #
# 模块纯洁性与注册
# --------------------------------------------------------------------------- #
FORBIDDEN_IMPORTS = ('streamlit', 'pandas', 'requests', 'torch', 'numpy', 'scipy', 'plotly')


def test_module_is_pure_python_with_no_forbidden_imports() -> None:
    tree = ast.parse((ROOT / 'market_guardrails.py').read_text(encoding='utf-8'))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split('.')[0])
    offenders = sorted(imported.intersection(FORBIDDEN_IMPORTS))
    assert offenders == []


def test_module_is_registered_for_single_file_build() -> None:
    sys_path = ROOT / 'scripts'
    import sys

    sys.path.insert(0, str(sys_path))
    try:
        import build_single_file
    finally:
        sys.path.remove(str(sys_path))

    assert 'market_guardrails' in build_single_file.MODULES


def test_module_does_not_import_the_frozen_engine() -> None:
    """score_convergence_engine 所在分支已冻结并 repin，跨分支 import 会让 pin 失效。"""
    source = (ROOT / 'market_guardrails.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != 'score_convergence_engine'
        if isinstance(node, ast.Import):
            assert all(alias.name != 'score_convergence_engine' for alias in node.names)


def test_embedded_copy_is_in_sync_for_non_ascii_first_line() -> None:
    """审计的 embed-in-sync 探针必须认得 CJK 首行。

    探针曾经用 ``json.dumps(first_line)[1:-1]``（默认 ``ensure_ascii=True``，
    输出 ``\\uXXXX``）去比一个由 ``ensure_ascii=False`` 写出的 payload ——
    只要模块首行不是纯 ASCII 就必然误报「stale」。本模块首行是中文，
    正好把这条老缺陷暴露出来。这里直接复算两种写法的命中情况。
    """
    source = (ROOT / 'market_guardrails.py').read_text(encoding='utf-8')
    first_line = source.lstrip().splitlines()[0]
    assert not first_line.isascii(), 'this guard is only meaningful for a non-ASCII first line'

    block = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    block = block.split('# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>', 1)[1]
    block = block.split('# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<', 1)[0]

    unicode_needle = json.dumps(first_line, ensure_ascii=False)[1:-1]
    ascii_needle = json.dumps(first_line)[1:-1]
    assert unicode_needle in block
    assert ascii_needle not in block
    assert any(needle in block for needle in (unicode_needle, ascii_needle))
