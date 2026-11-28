"""score_matrices 的契约测试：D1–D10、三项缺失功能、命名防撞。

所有期望值都是手写常量，没有一个是从被测代码反算出来的。
"""
from __future__ import annotations

import ast
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score_matrices as sm

MODULE_PATH = ROOT / 'score_matrices.py'

# --------------------------------------------------------------------------- #
# 手写夹具
# --------------------------------------------------------------------------- #
# 基准夹具：其余 29 项一律 market=10.0 / jc=4.0 / heat=0（每条 Δ=+6.0），
# 只有这两项是小均差，手算见各测试断言。
CANON_MARKET: dict[str, float] = {sc: 10.0 for sc in sm.THIRTY_ONE_SCORES}
CANON_MARKET['2-1'] = 5.0   # Δ = 5.0 - 4.5 = +0.5
CANON_MARKET['1-1'] = 5.0   # Δ = 5.0 - 4.0 = +1.0

CANON_JC: dict[str, dict[str, Any]] = {
    sc: {'odds': 4.0, 'heat_seats': 0} for sc in sm.THIRTY_ONE_SCORES
}
CANON_JC['2-1'] = {'odds': 4.5, 'heat_seats': 3}
CANON_JC['1-1'] = {'odds': 4.0, 'heat_seats': 2}


def canon_bundle():
    return sm.build_score_matrices('M20261126_CANON', dict(CANON_MARKET), {k: dict(v) for k, v in CANON_JC.items()})


def tie_fixture(heat_override: dict[str, int] | None = None,
                market_override: dict[str, float] | None = None,
                jc_override: dict[str, float] | None = None):
    """全部 31 项 |Δ| 恒等 0.5，并列排序只能靠次级键打破。"""
    market = {sc: 10.0 for sc in sm.THIRTY_ONE_SCORES}
    jc = {sc: 9.5 for sc in sm.THIRTY_ONE_SCORES}
    heat = {sc: 1 for sc in sm.THIRTY_ONE_SCORES}
    market.update(market_override or {})
    jc.update(jc_override or {})
    heat.update(heat_override or {})
    return market, {sc: {'odds': jc[sc], 'heat_seats': heat[sc]} for sc in sm.THIRTY_ONE_SCORES}


# --------------------------------------------------------------------------- #
# 0.2 / 0.3 缺失功能：rank1_rank2_ratio、is_even_match、ChannelAbsSummary
# --------------------------------------------------------------------------- #
def test_rank1_rank2_ratio_and_channel_summary_hand_computed() -> None:
    """手算样例：rank1=|0.5|(2-1)、rank2=|1.0|(1-1) → 比值 2.0，非均势。

    三向均值：WIN = (12*6.0 + 0.5)/13 = 72.5/13 = 5.5769
              DRAW = (4*6.0 + 1.0)/5 = 25.0/5 = 5.0000
              LOSS = 13*6.0/13 = 6.0000
    """
    bundle = canon_bundle()

    assert [r.score for r in bundle.top_8_min_abs_delta[:2]] == ['2-1', '1-1']
    assert bundle.top_8_min_abs_delta[0].abs_delta == 0.5
    assert bundle.top_8_min_abs_delta[1].abs_delta == 1.0
    assert bundle.rank1_rank2_ratio == 2.0
    assert bundle.is_even_match is False

    cs = bundle.channel_summary
    assert isinstance(cs, sm.ChannelAbsSummary)
    assert (cs.win_mean_abs_delta, cs.draw_mean_abs_delta, cs.loss_mean_abs_delta) == (
        5.5769, 5.0, 6.0,
    )
    assert (cs.win_count, cs.draw_count, cs.loss_count) == (13, 5, 13)
    assert cs.min_channel == 'DRAW'
    assert cs.channel_spread == pytest.approx(1.0, abs=1e-9)


def test_channel_abs_summary_dict_alias_matches_dataclass() -> None:
    bundle = canon_bundle()
    alias = bundle.channel_abs_summary
    assert alias == {
        'WIN_mean_abs_delta': 5.5769,
        'DRAW_mean_abs_delta': 5.0,
        'LOSS_mean_abs_delta': 6.0,
    }


def test_is_even_match_true_when_ratio_below_threshold() -> None:
    """rank1=0.5，rank2=0.6 → 1.2 <= 1.35 → 均势。"""
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-1'] = 4.6
    bundle = sm.build_score_matrices('M_EVEN', market, jc)
    assert bundle.top_8_min_abs_delta[1].abs_delta == 0.6
    assert bundle.rank1_rank2_ratio == 1.2
    assert bundle.is_even_match is True


def test_is_even_match_boundary_is_inclusive_at_1_35() -> None:
    """rank2/rank1 = 0.675/0.5 = 1.35，边界取 <= → 均势。"""
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-1'] = 5.0
    jc['1-1'] = {'odds': 4.325, 'heat_seats': 2}
    bundle = sm.build_score_matrices('M_BOUNDARY', market, jc)
    assert bundle.top_8_min_abs_delta[1].abs_delta == 0.675
    assert bundle.rank1_rank2_ratio == 1.35
    assert bundle.is_even_match is True


def test_is_even_match_just_above_threshold_is_false() -> None:
    """rank2/rank1 = 0.7/0.5 = 1.3 → True；0.7 与阈值无关的那条另测 1.4。"""
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-1'] = 5.0
    jc['1-1'] = {'odds': 4.3, 'heat_seats': 2}  # Δ = 0.7 → 0.7/0.5 = 1.4
    bundle = sm.build_score_matrices('M_ABOVE', market, jc)
    assert bundle.top_8_min_abs_delta[1].abs_delta == 0.7
    assert bundle.rank1_rank2_ratio == 1.4
    assert bundle.is_even_match is False


# --------------------------------------------------------------------------- #
# 0.1 池内核心占比 / core_heat_ratio
# --------------------------------------------------------------------------- #
def test_tactical_pool_core_columns_hand_computed() -> None:
    """P03=(2-1,3-2) 无核心；P08=(1-1) 是核心；P01=(1-0) 池内零热度。"""
    bundle = canon_bundle()
    pools = {p.pool_id: p for p in bundle.matrix_tactical_pools}
    assert len(bundle.matrix_tactical_pools) == 15

    p03 = pools['P03_WIN_TIGHT_MID']
    assert p03.scores == ('2-1', '3-2')
    assert p03.avg_market_odds == 7.5          # (5.0 + 10.0) / 2
    assert p03.avg_lottery_odds == 4.25        # (4.5 + 4.0) / 2
    assert p03.avg_delta == 3.25               # 7.5 - 4.25
    assert p03.total_heat_seats == 3           # 3 + 0
    assert (p03.core_count, p03.core_heat_seats, p03.overkill_count) == (0, 0, 0)
    assert p03.core_ratio == 0.0
    assert p03.core_heat_ratio == 0.0          # 分母 3 != 0 → 真 0.0，不是 None

    p08 = pools['P08_DRAW_ONE']
    assert (p08.core_count, p08.core_ratio, p08.core_heat_seats) == (1, 1.0, 2)
    assert p08.core_heat_ratio == 1.0

    p06 = pools['P06_WIN_OVERKILL']
    assert p06.scores == ('胜其它',)
    assert (p06.overkill_count, p06.core_count) == (1, 0)


def test_core_heat_ratio_is_none_not_zero_when_pool_heat_is_zero() -> None:
    """分母 0 → None。返回 0.0 即失败（零默认值兜底）。"""
    bundle = canon_bundle()
    pools = {p.pool_id: p for p in bundle.matrix_tactical_pools}
    p01 = pools['P01_WIN_LOW_LOCK']
    assert p01.total_heat_seats == 0
    assert p01.core_heat_seats == 0
    assert p01.core_heat_ratio is None, '池内零热度时 core_heat_ratio 必须是 None，不能兜底成 0.0'
    assert p01.core_heat_ratio != 0.0


def test_core_ratio_is_degenerate_under_current_definitions() -> None:
    """把 addendum 0.1 的实测结论固化成断言：现有定义下 core_ratio 只取 0.0/1.0。"""
    bundle = canon_bundle()
    values = {p.core_ratio for p in bundle.matrix_tactical_pools}
    assert values == {0.0, 1.0}
    multi_with_core = [
        p.pool_id for p in bundle.matrix_tactical_pools
        if len(p.scores) > 1 and p.core_count > 0
    ]
    assert multi_with_core == []


def test_pool_heat_ratio_is_continuous_in_multi_score_pool() -> None:
    """P03 里让 3-2 带热度：核心热度占比应给出真正的比例 0.0 与分母一起变化。"""
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['3-2'] = {'odds': 4.0, 'heat_seats': 6}
    bundle = sm.build_score_matrices('M_HEAT', dict(CANON_MARKET), jc)
    p03 = {p.pool_id: p for p in bundle.matrix_tactical_pools}['P03_WIN_TIGHT_MID']
    assert p03.total_heat_seats == 9      # 3 + 6
    assert p03.core_heat_seats == 0       # 2-1 非核心
    assert p03.core_heat_ratio == 0.0


def test_pool_core_heat_ratio_reflects_enrichment() -> None:
    """P09=(2-2,3-3,平其它) 全非核心；P08 核心独占 → 1.0 与 0.0 同时可辨。"""
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['2-2'] = {'odds': 4.0, 'heat_seats': 1}
    jc['3-3'] = {'odds': 4.0, 'heat_seats': 1}
    jc['平其它'] = {'odds': 4.0, 'heat_seats': 2}
    bundle = sm.build_score_matrices('M_P09', dict(CANON_MARKET), jc)
    pools = {p.pool_id: p for p in bundle.matrix_tactical_pools}
    assert pools['P09_DRAW_HIGH'].total_heat_seats == 4
    assert pools['P09_DRAW_HIGH'].core_heat_ratio == 0.0
    assert pools['P08_DRAW_ONE'].core_heat_ratio == 1.0


# --------------------------------------------------------------------------- #
# D1 rank1 为 0 时禁止 1e-9 兜底
# --------------------------------------------------------------------------- #
def test_d1_all_zero_deltas_raise_instead_of_faking_ratios() -> None:
    """31 项赔率全相等 → 全部 |Δ|=0 → 比值无定义，必须抛错。"""
    market = {sc: 10.0 for sc in sm.THIRTY_ONE_SCORES}
    jc = {sc: {'odds': 10.0, 'heat_seats': 1} for sc in sm.THIRTY_ONE_SCORES}
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_ZERO', market, jc)
    message = str(excinfo.value)
    assert '|Δ|' in message
    assert '无定义' in message
    assert '1e-9' in message or '兜底' in message


def test_d1_single_zero_rank1_also_raises() -> None:
    market = {sc: 10.0 for sc in sm.THIRTY_ONE_SCORES}
    jc = {sc: {'odds': 9.0, 'heat_seats': 1} for sc in sm.THIRTY_ONE_SCORES}
    jc['3-3'] = {'odds': 10.0, 'heat_seats': 1}   # Δ=0，独占最小值
    with pytest.raises(sm.MatrixIntegrityError, match='无定义'):
        sm.build_score_matrices('M_ONE_ZERO', market, jc)


def test_no_epsilon_fallback_pattern_in_source() -> None:
    """静态守卫：源码里不得出现 `1e-9` 形式的除数兜底。"""
    source = MODULE_PATH.read_text(encoding='utf-8')
    assert 'else 1e-9' not in source
    assert 'or 0.0' not in source


# --------------------------------------------------------------------------- #
# D2 Markdown 表头列数 == 数据行列数
# --------------------------------------------------------------------------- #
def _markdown_table_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith('|'):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _column_count(row: str) -> int:
    """先去掉转义竖线，再数列；未转义的 |Δ| 会被算成两列。"""
    return row.replace(r'\|', '').count('|') - 1


def test_d2_every_table_row_has_same_column_count_as_header() -> None:
    bundle = canon_bundle()
    text = sm.format_matrices_markdown(bundle)
    blocks = _markdown_table_blocks(text)
    assert len(blocks) == 3, f'期望三张表，实际 {len(blocks)}'
    expected_columns = [9, 14, 8]
    for block, expected in zip(blocks, expected_columns):
        header = block[0]
        assert _column_count(header) == expected
        # 分隔行必须与表头同列数
        assert _column_count(block[1]) == expected
        for row in block[2:]:
            assert _column_count(row) == expected, (
                f'表头 {expected} 列但数据行 {_column_count(row)} 列: {row[:80]}'
            )


def test_d2_header_rows_contain_no_bare_pipe() -> None:
    bundle = canon_bundle()
    text = sm.format_matrices_markdown(bundle)
    for block in _markdown_table_blocks(text):
        header = block[0]
        # 表头去掉首尾竖线后按未转义竖线切分，段数必须等于列数
        inner = header[1:-1]
        parts = re.split(r'(?<!\\)\|', inner)
        assert len(parts) == _column_count(header)
        for part in parts:
            assert '|' not in part.replace(r'\|', '')


def test_d2_markdown_contains_all_31_rows_and_15_pools() -> None:
    bundle = canon_bundle()
    text = sm.format_matrices_markdown(bundle)
    blocks = _markdown_table_blocks(text)
    assert len(blocks[0]) == 2 + 31
    assert len(blocks[1]) == 2 + 15
    assert len(blocks[2]) == 2 + 8


# --------------------------------------------------------------------------- #
# D3 赔率接受字符串（严格解析，仍不给默认值）
# --------------------------------------------------------------------------- #
def test_d3_string_odds_are_accepted() -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-0'] = '8.50'
    jc['1-0'] = {'odds': '7.25', 'heat_seats': 1}
    bundle = sm.build_score_matrices('M_STR_ODDS', market, jc)
    row = {r.score: r for r in bundle.matrix_31}['1-0']
    assert row.market_avg_odds == 8.5
    assert row.lottery_odds == 7.25
    assert row.delta == 1.25


@pytest.mark.parametrize('bad', ['abc', '', '  ', '1.0', '0.5', '-3', 'nan', 'inf'])
def test_d3_unparseable_or_illegal_string_odds_raise(bad: str) -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'odds': bad, 'heat_seats': 1}
    with pytest.raises(sm.MatrixIntegrityError):
        sm.build_score_matrices('M_BAD_STR', market, jc)


@pytest.mark.parametrize('bad', [True, False, None, [], {}, float('nan'), float('inf'), 1.0])
def test_d3_illegal_odds_values_still_raise(bad: Any) -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'odds': bad, 'heat_seats': 1}
    with pytest.raises(sm.MatrixIntegrityError):
        sm.build_score_matrices('M_ILLEGAL', market, jc)


def test_d3_missing_odds_key_raises_without_default() -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'heat_seats': 1}
    with pytest.raises(sm.MatrixIntegrityError, match='odds'):
        sm.build_score_matrices('M_NO_ODDS', market, jc)


# --------------------------------------------------------------------------- #
# D3 补充：超出 float 范围的整数必须收口成 MatrixIntegrityError
# --------------------------------------------------------------------------- #
# 10**400 大到 float() 抛 OverflowError（不是 ValueError）。断言消息里的
# 「溢出」二字，证明真的走到了新增的 overflow 分支，而不是被后面的
# `num <= 1.0` 提前拦下 —— 那条消息是「必须 > 1.0」，与这里不同。
OVERFLOW_MESSAGE = '溢出'


def test_d3_huge_int_market_odds_raise_matrix_error_not_overflow() -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-0'] = 10**400
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_OVF_MARKET', market, jc)
    assert OVERFLOW_MESSAGE in str(excinfo.value), str(excinfo.value)
    assert 'market_avg_scores.1-0' in str(excinfo.value)


def test_d3_huge_int_jc_odds_raise_matrix_error_not_overflow() -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'odds': 10**400, 'heat_seats': 1}
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_OVF_JC', market, jc)
    assert OVERFLOW_MESSAGE in str(excinfo.value), str(excinfo.value)
    assert 'jc_scores_data.1-0.odds' in str(excinfo.value)


@pytest.mark.parametrize('huge', [2**1024, 10**400, -(10**400)])
def test_d3_huge_int_magnitudes_never_leak_overflow(huge: int) -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-0'] = huge
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_OVF_MAG', market, jc)
    assert not isinstance(excinfo.value, OverflowError)


def test_d3_parse_odds_directly_guards_overflow() -> None:
    """直接打 _parse_odds：转换与有限性检查都不许把 OverflowError 漏出去。"""
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm._parse_odds(10**400, 'direct.label')
    assert OVERFLOW_MESSAGE in str(excinfo.value)
    assert 'direct.label' in str(excinfo.value)


def test_d3_oversized_decimal_string_still_routes_through_isfinite() -> None:
    """'1e400' 解析成 inf：走的是有限性分支，消息是「数值非法」而非「溢出」。"""
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    market['1-0'] = '1e400'
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_INF_STR', market, jc)
    assert '数值非法' in str(excinfo.value), str(excinfo.value)


# --------------------------------------------------------------------------- #
# D4 heat_seats 接受 1.0 / "1"，但拒绝小数与非数字
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('value,expected', [(1, 1), (1.0, 1), ('7', 7), ('7.0', 7), ('  4 ', 4), (0.0, 0)])
def test_d4_integral_heat_values_are_accepted(value: Any, expected: int) -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'odds': 4.0, 'heat_seats': value}
    bundle = sm.build_score_matrices('M_HEAT_OK', market, jc)
    row = {r.score: r for r in bundle.matrix_31}['1-0']
    assert row.heat_seats == expected
    assert isinstance(row.heat_seats, int)


@pytest.mark.parametrize('bad', [1.5, -1, -1.0, 'abc', '1.5', True, None, '', [], float('nan'), float('inf')])
def test_d4_fractional_or_illegal_heat_values_raise(bad: Any) -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'odds': 4.0, 'heat_seats': bad}
    with pytest.raises(sm.MatrixIntegrityError):
        sm.build_score_matrices('M_HEAT_BAD', market, jc)


def test_d4_1_9_is_not_silently_truncated_to_1() -> None:
    market = dict(CANON_MARKET)
    jc = {k: dict(v) for k, v in CANON_JC.items()}
    jc['1-0'] = {'odds': 4.0, 'heat_seats': 1.9}
    with pytest.raises(sm.MatrixIntegrityError, match='整数'):
        sm.build_score_matrices('M_TRUNC', market, jc)


# --------------------------------------------------------------------------- #
# D5 match_id 校验
# --------------------------------------------------------------------------- #
def test_d5_match_id_is_stripped_and_kept() -> None:
    bundle = sm.build_score_matrices('  M_OK  ', dict(CANON_MARKET), {k: dict(v) for k, v in CANON_JC.items()})
    assert bundle.match_id == 'M_OK'


def test_d5_64_char_match_id_is_accepted() -> None:
    ok = 'x' * 64
    bundle = sm.build_score_matrices(ok, dict(CANON_MARKET), {k: dict(v) for k, v in CANON_JC.items()})
    assert bundle.match_id == ok


@pytest.mark.parametrize('bad', ['', '   ', 'x' * 65, 'a|b', 'a\nb', 'a\rb', None, 123])
def test_d5_illegal_match_id_raises(bad: Any) -> None:
    with pytest.raises(sm.MatrixIntegrityError):
        sm.build_score_matrices(bad, dict(CANON_MARKET), {k: dict(v) for k, v in CANON_JC.items()})


def test_d5_match_id_is_escaped_in_markdown() -> None:
    """校验拦住的输入不会再进 Markdown，但 _cell 仍必须转义竖线。"""
    bundle = canon_bundle()
    assert 'M20261126_CANON' in sm.format_matrices_markdown(bundle)
    assert sm._cell('a|b') == r'a\|b'


# --------------------------------------------------------------------------- #
# D6 并列排序：核心优先、尾部靠后、热度降序、竞彩升序、全集下标兜底
# --------------------------------------------------------------------------- #
def test_d6_tie_break_prefers_core_scores_then_pool_order() -> None:
    """全部 |Δ|=0.5 且热度/赔率全同 → 5 个核心先按全集下标，再按全集下标。"""
    market, jc = tie_fixture()
    bundle = sm.build_score_matrices('M_TIE', market, jc)
    assert [r.abs_delta for r in bundle.top_8_min_abs_delta] == [0.5] * 8
    assert [r.score for r in bundle.top_8_min_abs_delta] == [
        '1-0', '2-0', '1-1', '0-1', '0-2', '2-1', '3-0', '3-1',
    ]


def test_d6_tie_break_heat_desc_then_lottery_asc() -> None:
    """5-2 热度 9 挤进前列；2-1 竞彩 19.5 被推到同组末尾。

    全集下标顺序本应是 ...2-1, 3-0, 3-1...，这里必须变成 ...3-0, 3-1...
    """
    market, jc = tie_fixture(
        heat_override={'5-2': 9},
        market_override={'2-1': 20.0},
        jc_override={'2-1': 19.5},
    )
    bundle = sm.build_score_matrices('M_TIE2', market, jc)
    assert [r.score for r in bundle.top_8_min_abs_delta] == [
        '1-0', '2-0', '1-1', '0-1', '0-2', '5-2', '3-0', '3-1',
    ]


def test_d6_overkill_tail_sinks_below_equal_non_core_scores() -> None:
    """胜其它/负其它在并列时必须排在非核心普通比分之后。"""
    market, jc = tie_fixture(heat_override={'胜其它': 9, '负其它': 9})
    bundle = sm.build_score_matrices('M_TIE3', market, jc)
    order = [r.score for r in bundle.top_8_min_abs_delta]
    assert '胜其它' not in order
    assert '负其它' not in order
    assert order == ['1-0', '2-0', '1-1', '0-1', '0-2', '2-1', '3-0', '3-1']


def test_d6_ordering_is_deterministic_across_insertion_orders() -> None:
    market, jc = tie_fixture()
    reversed_market = dict(reversed(list(market.items())))
    reversed_jc = dict(reversed(list(jc.items())))
    a = sm.build_score_matrices('M_TIE', market, jc)
    b = sm.build_score_matrices('M_TIE', reversed_market, reversed_jc)
    assert [r.score for r in a.top_8_min_abs_delta] == [r.score for r in b.top_8_min_abs_delta]
    assert [r.score for r in a.matrix_31] == [r.score for r in b.matrix_31]


# --------------------------------------------------------------------------- #
# D7 空样本均值：MatrixIntegrityError 而不是 StatisticsError
# --------------------------------------------------------------------------- #
def test_d7_empty_pool_raises_matrix_error_naming_the_pool() -> None:
    definitions = list(sm.TACTICAL_POOL_DEFINITIONS)
    definitions[6] = ('P07_DRAW_ZERO', '闷平零球防线池', ())
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_tactical_pools(
            {r.score: r for r in canon_bundle().matrix_31}, definitions
        )
    assert 'P07_DRAW_ZERO' in str(excinfo.value)


def test_d7_statistics_error_never_escapes() -> None:
    definitions = list(sm.TACTICAL_POOL_DEFINITIONS)
    definitions[0] = ('P01_WIN_LOW_LOCK', '主胜零封一球池', ())
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_tactical_pools(
            {r.score: r for r in canon_bundle().matrix_31}, definitions
        )
    assert not isinstance(excinfo.value, statistics.StatisticsError)


# --------------------------------------------------------------------------- #
# D8 战术池定义表自校验
# --------------------------------------------------------------------------- #
def _defs_without(score: str) -> list[tuple[str, str, tuple[str, ...]]]:
    out = []
    for pool_id, name, scores in sm.TACTICAL_POOL_DEFINITIONS:
        kept = tuple(s for s in scores if s != score)
        out.append((pool_id, name, kept))
    return out


def test_d8_missing_score_raises_and_names_the_score() -> None:
    definitions = _defs_without('平其它')
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm._validate_tactical_pool_definitions(definitions)
    assert '平其它' in str(excinfo.value)


def test_d8_missing_score_via_build_path_raises_matrix_error() -> None:
    definitions = _defs_without('平其它')
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_tactical_pools({r.score: r for r in canon_bundle().matrix_31}, definitions)
    assert '平其它' in str(excinfo.value)


def test_d8_duplicate_score_across_pools_raises() -> None:
    definitions = list(sm.TACTICAL_POOL_DEFINITIONS)
    definitions[1] = ('P02_WIN_TWO_LOCK', '主胜零封两球池', ('2-0', '1-0'))
    with pytest.raises(sm.MatrixIntegrityError, match='互斥'):
        sm._validate_tactical_pool_definitions(definitions)


def test_d8_duplicate_pool_id_raises() -> None:
    definitions = list(sm.TACTICAL_POOL_DEFINITIONS)
    definitions[1] = ('P01_WIN_LOW_LOCK', '重名池', ('2-0',))
    with pytest.raises(sm.MatrixIntegrityError, match='重复'):
        sm._validate_tactical_pool_definitions(definitions)


def test_d8_unknown_score_raises() -> None:
    definitions = list(sm.TACTICAL_POOL_DEFINITIONS)
    definitions[-1] = ('P15_LOSS_OVERKILL', '客胜极端杀穿尾部池', ('负其它', '9-9'))
    with pytest.raises(sm.MatrixIntegrityError, match='9-9'):
        sm._validate_tactical_pool_definitions(definitions)


def test_d8_wrong_pool_count_raises() -> None:
    with pytest.raises(sm.MatrixIntegrityError, match='15'):
        sm._validate_tactical_pool_definitions(list(sm.TACTICAL_POOL_DEFINITIONS)[:-1])


def test_d8_shipped_definitions_cover_all_31_once() -> None:
    covered = [sc for _pid, _name, scores in sm.TACTICAL_POOL_DEFINITIONS for sc in scores]
    assert len(covered) == 31
    assert set(covered) == set(sm.THIRTY_ONE_SCORES)
    assert len(set(covered)) == 31
    sm._validate_tactical_pool_definitions(sm.TACTICAL_POOL_DEFINITIONS)


# --------------------------------------------------------------------------- #
# D9 池 avg_delta 与 avg_market - avg_lottery 逐位一致（400 组固定种子）
# --------------------------------------------------------------------------- #
def test_d9_400_random_trials_pool_delta_identity() -> None:
    """400 组固定种子随机赔率，15 个池的 avg_delta 必须逐位等于 avg_m - avg_j。

    注意：随机 2dp 赔率会以约 1/6 的概率撞出某个比分 Δ 恰好为 0，此时 rank1
    为 0，D1 的规则要求抛错 —— 那是正确行为，不是 D9 的失败。所以这里刻意
    让竞彩赔率永远不等于市均赔率（+0.01 的偏移），确保 D9 只测一致性。
    """
    rng = random.Random(20261126)
    worst = 0.0
    trials = 0
    for _trial in range(400):
        market = {sc: round(rng.uniform(1.01, 60.0), 2) for sc in sm.THIRTY_ONE_SCORES}
        jc = {}
        for sc in sm.THIRTY_ONE_SCORES:
            market_odd = market[sc]
            # 与市均至少差 0.01，避免制造 |Δ|=0 触发 D1（那是另一条契约）。
            delta = round(rng.uniform(0.01, 30.0), 2)
            jc_odd = round(market_odd - delta, 2)
            if jc_odd <= 1.0:
                jc_odd = round(market_odd + delta, 2)
            jc[sc] = {'odds': jc_odd, 'heat_seats': rng.randint(0, 9)}
        bundle = sm.build_score_matrices('M_RND', market, jc)
        trials += 1
        assert len(bundle.matrix_tactical_pools) == 15
        for pool in bundle.matrix_tactical_pools:
            gap = abs(pool.avg_delta - (pool.avg_market_odds - pool.avg_lottery_odds))
            worst = max(worst, gap)
            assert gap < 1e-9, (
                f'{pool.pool_id}: avg_delta={pool.avg_delta!r} '
                f'avg_m-avg_j={pool.avg_market_odds - pool.avg_lottery_odds!r}'
            )
    assert trials == 400
    assert worst < 1e-9


def test_d9_pool_delta_is_derived_not_averaged() -> None:
    """反证：若 avg_delta 取「每行 delta 先 round 再平均」，P05 会得到不同的值。"""
    market = {sc: 10.0 for sc in sm.THIRTY_ONE_SCORES}
    jc = {sc: {'odds': 3.0, 'heat_seats': 0} for sc in sm.THIRTY_ONE_SCORES}
    # 构造 P05 六项里每行 delta 都是 6.6667 (10 - 3.3333) 之外的舍入组合
    p05 = ('4-0', '4-1', '4-2', '5-0', '5-1', '5-2')
    values = [4.7, 4.8, 4.9, 5.1, 5.2, 5.3]
    for score, odd in zip(p05, values):
        jc[score] = {'odds': odd, 'heat_seats': 0}
    bundle = sm.build_score_matrices('M_DERIVED', market, jc)
    pool = {p.pool_id: p for p in bundle.matrix_tactical_pools}['P05_WIN_HEAVY_BLOWOUT']
    # 手算：avg_m = 10.0；avg_j = (4.7+4.8+4.9+5.1+5.2+5.3)/6 = 30.0/6 = 5.0
    assert pool.avg_market_odds == 10.0
    assert pool.avg_lottery_odds == 5.0
    assert pool.avg_delta == 5.0
    assert pool.abs_avg_delta == 5.0


# --------------------------------------------------------------------------- #
# D10 全集顺序：保留用户顺序，同时给出与流水线的双射
# --------------------------------------------------------------------------- #
def test_d10_thirty_one_scores_keeps_user_order() -> None:
    assert sm.THIRTY_ONE_SCORES[:13] == (
        '1-0', '2-0', '2-1', '3-0', '3-1', '3-2',
        '4-0', '4-1', '4-2', '5-0', '5-1', '5-2', '胜其它',
    )
    assert sm.THIRTY_ONE_SCORES[13:18] == ('0-0', '1-1', '2-2', '3-3', '平其它')
    assert sm.THIRTY_ONE_SCORES[18:] == (
        '0-1', '0-2', '1-2', '0-3', '1-3', '2-3',
        '0-4', '1-4', '2-4', '0-5', '1-5', '2-5', '负其它',
    )


def test_d10_canonical_order_matches_pipeline_and_is_a_bijection() -> None:
    from blade_deepseek_pipeline import JCZQ_ALL_OPTIONS

    assert set(sm.THIRTY_ONE_SCORES) == set(JCZQ_ALL_OPTIONS)
    assert set(sm.CANONICAL_PIPELINE_ORDER) == set(JCZQ_ALL_OPTIONS)
    assert list(sm.CANONICAL_PIPELINE_ORDER) == list(JCZQ_ALL_OPTIONS)
    # 顺序确实不同（首个分歧 idx 12），所以绝不能按下标 zip
    assert list(sm.THIRTY_ONE_SCORES) != list(JCZQ_ALL_OPTIONS)
    assert sm.THIRTY_ONE_SCORES[12] == '胜其它'
    assert JCZQ_ALL_OPTIONS[12] == '0-0'

    # 双射：每个比分在两套顺序里的下标一一对应，且来回可逆
    assert set(sm.PIPELINE_POSITION) == set(sm.THIRTY_ONE_SCORES)
    assert sorted(sm.PIPELINE_POSITION.values()) == list(range(31))
    for idx, score in enumerate(sm.CANONICAL_PIPELINE_ORDER):
        assert sm.PIPELINE_POSITION[score] == idx
        assert JCZQ_ALL_OPTIONS[sm.PIPELINE_POSITION[score]] == score
    for score in sm.THIRTY_ONE_SCORES:
        assert sm.CANONICAL_PIPELINE_ORDER[sm.PIPELINE_POSITION[score]] == score


def test_d10_direction_map_covers_all_31_with_13_5_13() -> None:
    assert set(sm.SCORE_DIRECTION_MAP) == set(sm.THIRTY_ONE_SCORES)
    assert len(sm.SCORE_DIRECTION_MAP) == 31
    counts = {'WIN': 0, 'DRAW': 0, 'LOSS': 0}
    for direction in sm.SCORE_DIRECTION_MAP.values():
        counts[direction] += 1
    assert counts == {'WIN': 13, 'DRAW': 5, 'LOSS': 13}


# --------------------------------------------------------------------------- #
# 命名防撞：本模块不得出现 pool15（含 pool15_enriched / current_pool15）
# --------------------------------------------------------------------------- #
def _module_docstring_and_body() -> tuple[str, str]:
    source = MODULE_PATH.read_text(encoding='utf-8')
    tree = ast.parse(source)
    docstring = ast.get_docstring(tree) or ''
    return docstring, source


def test_naming_module_body_never_mentions_pool15() -> None:
    """结构性守卫：去掉模块 docstring 后，全文（含标识符）零命中 pool15。

    用**子串**匹配而不是 \\bpool15\\b：`_` 属于 \\w，所以 \\bpool15\\b 会漏掉
    最危险的 `pool15_enriched` 与 `current_pool15` —— 那两个恰恰是 app 里的真名。
    """
    docstring, source = _module_docstring_and_body()
    body = source.replace(docstring, '', 1)
    hits = [
        (lineno, line.strip())
        for lineno, line in enumerate(body.splitlines(), 1)
        if 'pool15' in line.lower()
    ]
    assert hits == [], f'模块正文出现 pool15 命名撞车: {hits[:5]}'


def test_naming_docstring_states_the_difference_explicitly() -> None:
    """豁免不是放水：docstring 必须点名 pool15_enriched 并说清区别。"""
    docstring, _ = _module_docstring_and_body()
    assert 'pool15_enriched' in docstring
    assert '不是' in docstring or 'not' in docstring
    assert 'ranked_scores[:15]' in docstring or '概率' in docstring


def test_naming_public_api_has_no_bare_pool15_symbol() -> None:
    docstring, source = _module_docstring_and_body()
    body = source.replace(docstring, '', 1)
    tree = ast.parse(body)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
    offenders = [n for n in names if 'pool15' in n.lower()]
    assert offenders == []


def test_naming_bundle_field_is_matrix_tactical_pools() -> None:
    bundle = canon_bundle()
    assert hasattr(bundle, 'matrix_tactical_pools')
    assert not hasattr(bundle, 'matrix_15_pools')
    assert sm.TACTICAL_POOL_COUNT == 15
    assert len(sm.TACTICAL_POOL_DEFINITIONS) == 15
    assert 'TACTICAL_POOL_DEFINITIONS' in dir(sm)
    assert 'TacticalPoolRow' in dir(sm)


# --------------------------------------------------------------------------- #
# 结构 / 契约杂项
# --------------------------------------------------------------------------- #
def test_build_rejects_31_set_mismatch_naming_missing_score() -> None:
    market = dict(CANON_MARKET)
    market.pop('3-3')
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_MISSING', market, {k: dict(v) for k, v in CANON_JC.items()})
    assert '3-3' in str(excinfo.value)


def test_build_rejects_extra_unknown_score() -> None:
    market = dict(CANON_MARKET)
    market['4-3'] = 50.0
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_score_matrices('M_EXTRA', market, {k: dict(v) for k, v in CANON_JC.items()})
    assert '4-3' in str(excinfo.value)


def test_matrix_31_follows_user_order() -> None:
    bundle = canon_bundle()
    assert tuple(r.score for r in bundle.matrix_31) == sm.THIRTY_ONE_SCORES


def test_row_level_delta_and_flags_hand_computed() -> None:
    bundle = canon_bundle()
    rows = {r.score: r for r in bundle.matrix_31}
    assert rows['2-1'].delta == 0.5
    assert rows['2-1'].abs_delta == 0.5
    assert rows['1-1'].delta == 1.0
    assert rows['1-0'].delta == 6.0
    assert rows['1-0'].is_core_protected is True
    assert rows['1-0'].is_overkill_tail is False
    assert rows['胜其它'].is_overkill_tail is True
    assert rows['胜其它'].is_core_protected is False
    assert rows['3-3'].is_core_protected is False


def test_module_has_no_streamlit_pandas_requests_import() -> None:
    source = MODULE_PATH.read_text(encoding='utf-8')
    tree = ast.parse(source)
    forbidden = {'streamlit', 'pandas', 'requests', 'numpy', 'plotly'}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert imported & forbidden == set()


# --------------------------------------------------------------------------- #
# Telegram 分页
# --------------------------------------------------------------------------- #
def test_telegram_pages_respect_limit_and_repeat_header() -> None:
    bundle = canon_bundle()
    pages = sm.format_matrices_telegram(bundle, max_len=400)
    assert len(pages) > 1
    for page in pages:
        assert len(page) <= 400
    # 31 项表被拆开时，每个分页块都必须带自己的表头
    for page in pages:
        first = page.splitlines()[0]
        assert first.startswith(('📊', '【'))


def test_telegram_all_rows_survive_pagination() -> None:
    bundle = canon_bundle()
    pages = sm.format_matrices_telegram(bundle, max_len=400)
    joined = '\n'.join(pages)
    for score in sm.THIRTY_ONE_SCORES:
        assert f'{score:<5s}' in joined
    for pool in bundle.matrix_tactical_pools:
        assert pool.pool_id in joined


def test_telegram_oversized_single_line_raises_instead_of_truncating() -> None:
    bundle = canon_bundle()
    with pytest.raises(sm.MatrixIntegrityError, match='拒绝截断'):
        sm.format_matrices_telegram(bundle, max_len=10)


def test_telegram_default_limit_is_4096() -> None:
    bundle = canon_bundle()
    pages = sm.format_matrices_telegram(bundle)
    for page in pages:
        assert len(page) <= 4096


# --------------------------------------------------------------------------- #
# 生产记录适配：真实抓包夹具（merge_cs31 的真实输出，不是手写 31 项）
# --------------------------------------------------------------------------- #
FIXTURES = ROOT / 'tests' / 'fixtures'


def _real_rec(match_id: str = '1347776') -> dict[str, Any]:
    """用真实抓包构造生产记录形状：市均已由 merge_cs31 合成 31 键。

    与 tests/test_runtime.py::_record_shape 同一条取数路径（同一份
    okooo_desktop_cs_1347776.html + okooo_jczq_oddsdata_20slots.html），
    这里额外挂上 score_market_heat，因为三套矩阵需要 heat_seats。
    """
    import okooo_mobile_cs as okooo

    text = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes())
    jc = okooo.parse_jczq_score_odds(text, match_id)['odds']
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(fh.read()))
    merged = okooo.merge_cs31(page['page_avg'], page['books_avg'], jc)
    heat = okooo.build_score_market_heat_ranking(
        merged['scores'],
        page_avg_odds=merged['scores'],
        heat_source='okooo_cs_books_avg',
        channel='okooo_cs',
    )
    return {
        'id': match_id,
        'match': f'真实夹具 {match_id}',
        'correct_score_page_avg': merged['scores'],
        'correct_score_books_avg': page['books_avg'],
        'correct_score_books_n': page['books_n'],
        'correct_score_jc_official': jc,
        'jc_score_odds': jc,
        'cs31_provenance': merged['provenance'],
        'cs31_extras': merged['extras'],
        'cs31_counts': merged['counts'],
        'cs31_complete': merged['complete'],
        'score_market_heat': heat,
        '_merged_complete': merged['complete'],
        '_merged_row_count': merged['row_count'],
    }


def test_real_record_fixture_is_complete_31() -> None:
    """夹具本身必须是真实完整的 31 项，否则后面的三段测试没有意义。"""
    rec = _real_rec()
    assert rec['_merged_complete'] is True
    assert rec['_merged_row_count'] == 31
    assert len(rec['correct_score_page_avg']) == 31
    assert len(rec['correct_score_jc_official']) == 31
    # 22 家市均 + 9 项竞彩官方，是这份抓包的真实出处分布
    assert rec['cs31_counts'] == {'desktop_cs': 22, 'jc_official': 9}


def test_real_record_builds_three_matrices() -> None:
    """真实记录 -> 31 项 / 15 池 / Top-8 三段全部成型。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)

    assert len(bundle.matrix_31) == 31
    assert len(bundle.matrix_tactical_pools) == 15
    assert len(bundle.top_8_min_abs_delta) == 8
    assert tuple(r.score for r in bundle.matrix_31) == sm.THIRTY_ONE_SCORES

    # 方向树 13/5/13，且每行方向与手写的集合一致
    assert (bundle.channel_summary.win_count, bundle.channel_summary.draw_count,
            bundle.channel_summary.loss_count) == (13, 5, 13)
    core = {r.score for r in bundle.matrix_31 if r.is_core_protected}
    assert core == {'1-0', '2-0', '0-1', '0-2', '1-1'}
    tail = {r.score for r in bundle.matrix_31 if r.is_overkill_tail}
    assert tail == {'胜其它', '负其它'}

    # Top-8 按 |Δ| 升序，且 rank1 <= rank2 <= ... <= rank8
    abs_deltas = [r.abs_delta for r in bundle.top_8_min_abs_delta]
    assert abs_deltas == sorted(abs_deltas)
    assert bundle.rank1_rank2_ratio >= 1.0

    # 15 池覆盖 31 项，avg_delta 与两个均值逐位一致
    covered = [s for p in bundle.matrix_tactical_pools for s in p.scores]
    assert len(covered) == 31 and len(set(covered)) == 31
    for pool in bundle.matrix_tactical_pools:
        if pool.priced_count == 0:
            # 整池都是竞彩填充项：没有可比价，均值/均差必须是 None，不是 0
            assert pool.avg_market_odds is None
            assert pool.avg_delta is None
            assert pool.abs_avg_delta is None
        else:
            assert pool.avg_delta == pool.avg_market_odds - pool.avg_lottery_odds


def test_real_record_markdown_and_telegram_are_well_formed() -> None:
    """真实记录渲染出的 Markdown 三张表列数自洽；TG 分页每页都带表头。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    text = sm.format_matrices_markdown(bundle)
    blocks = _markdown_table_blocks(text)
    assert len(blocks) == 3
    for block in blocks:
        want = _column_count(block[0])
        for row in block:
            assert _column_count(row) == want

    pages = sm.format_matrices_tg_messages(rec)
    assert len(pages) >= 1
    for page in pages:
        assert len(page) <= 4096
        assert sm.HEAT_SEATS_DEFINITION_NOTE in page
    joined = '\n'.join(pages)
    for score in sm.THIRTY_ONE_SCORES:
        assert score in joined
    for pool in bundle.matrix_tactical_pools:
        assert pool.pool_id in page or pool.pool_id in joined


# --------------------------------------------------------------------------- #
# heat_seats 访问器：三种情形各一条
# --------------------------------------------------------------------------- #
def test_heat_seats_follow_rank_not_presence() -> None:
    """真实抓包：heat_seats 按排名位次给席，不是「是否在榜」。

    presence 口径下 31 项全是 1（ranking 有 31 行），这一列等于没有信息。
    位次口径下前 8 名依次 8..1 席，第 9 名及以后 0 席。
    """
    rec = _real_rec()
    seats = sm._matrix_heat_seats_from_rec(rec)
    assert len(seats) == 31
    ranking = [r['score'] for r in rec['score_market_heat']['ranking']]
    assert len(ranking) == 31, '真实抓包的 ranking 必须覆盖 31 项（这正是 presence 退化 的原因）'

    # 前 8 名：8,7,6,5,4,3,2,1
    for position, score in enumerate(ranking[:8], start=1):
        assert seats[score] == 9 - position, (score, position, seats[score])
    # 第 9 名及以后：0
    for score in ranking[8:]:
        assert seats[score] == 0, (score, seats[score])
    assert sum(seats.values()) == 36          # 8+7+...+1
    assert sorted(set(seats.values())) == [0, 1, 2, 3, 4, 5, 6, 7, 8]


def test_real_record_heat_seats_has_at_least_two_distinct_values() -> None:
    """要求：真实抓包上 heat_seats 至少 2 个不同取值（presence 口径只有 1 个）。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    values = {r.heat_seats for r in bundle.matrix_31}
    assert len(values) >= 2, f'heat_seats 退化成单值 {values}'
    assert values != {1}, 'heat_seats 恒为 1 说明退回 presence 口径'
    assert max(values) == sm.TOP_HEAT_SEATS


def test_rank9_and_beyond_get_zero_seats() -> None:
    """第 9 名恰好 0 席——边界，不是 1 也不是负数。"""
    rec = _real_rec()
    rec['score_market_heat'] = {
        'ranking': [{'score': s} for s in sm.THIRTY_ONE_SCORES]
    }
    seats = sm._matrix_heat_seats_from_rec(rec)
    for position, score in enumerate(sm.THIRTY_ONE_SCORES, start=1):
        expect = 9 - position if position <= 8 else 0
        assert seats[score] == expect, (position, score, seats[score])
    assert seats[sm.THIRTY_ONE_SCORES[8]] == 0   # 第 9 名
    assert seats[sm.THIRTY_ONE_SCORES[30]] == 0  # 第 31 名


def test_ranked_score_absent_from_list_gets_zero_without_fusing() -> None:
    """榜单只列一项：第 1 名 8 席，其余 30 项 0 席，且不熔断。"""
    rec = _real_rec()
    rec['score_market_heat'] = {'available': True, 'ranking': [{'score': '1-0', 'odds': 17.0, 'heat': 0.0588}]}
    seats = sm._matrix_heat_seats_from_rec(rec)
    assert seats['1-0'] == 8
    assert seats['2-0'] == 0
    assert seats['负其它'] == 0
    assert sum(seats.values()) == 8
    # 0 是真实观测（第 9 名及以后），不是兜底
    bundle = sm.build_matrices_from_rec(rec)
    assert sum(r.heat_seats for r in bundle.matrix_31) == 8


def test_core_heat_ratio_is_structurally_binary_but_seats_carry_the_signal() -> None:
    """core_heat_ratio 在现有 15 池定义下**必然**只取 0.0/1.0——这不是热度口径问题。

    5 个核心比分（1-0/2-0/0-1/0-2/1-1）各自独占一个单比分池，
    所以任何一个池要么整池是核心（ratio=1.0），要么整池无核心（ratio=0.0），
    要么整池零热度（ratio=None）。把 heat_seats 从 presence 改成位次**改变不了**
    这一点——它只改变分子的数值，不改变「池里有没有核心」这个二分。

    真正的信息量落在 core_heat_seats / total_heat_seats 这两个**整数**上：
    位次口径下它们不再等于 1 和池内项数，取值有分布。
    """
    # 结构前提：每个核心比分都独占单比分池
    core = sm.CORE_PROTECTED_SET
    core_pools = [
        (pid, scores) for pid, _n, scores in sm.TACTICAL_POOL_DEFINITIONS
        if any(sc in core for sc in scores)
    ]
    assert len(core_pools) == 5
    assert all(len(scores) == 1 for _pid, scores in core_pools)
    assert not any(
        any(sc in core for sc in scores) and len(scores) > 1
        for _pid, _n, scores in sm.TACTICAL_POOL_DEFINITIONS
    )

    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    ratios = {p.core_heat_ratio for p in bundle.matrix_tactical_pools if p.core_heat_ratio is not None}
    assert ratios <= {0.0, 1.0}, ratios          # 恒二值，已固化
    # 位次口径下，携带信息的是席位整数，不是这个比值
    assert len({p.core_heat_seats for p in bundle.matrix_tactical_pools}) >= 2
    assert sorted({p.core_heat_seats for p in bundle.matrix_tactical_pools}) == [0, 4, 6, 7, 8]


def test_fixed_heat_seats_stop_equalling_pool_size() -> None:
    """位次口径下 total_heat_seats 不再恒等于池内项数（presence 口径的退化特征）。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    assert any(
        p.total_heat_seats != len(p.scores) for p in bundle.matrix_tactical_pools
    ), 'total_heat_seats 全部等于池内项数，说明退回了 presence 口径'
    assert sum(p.total_heat_seats for p in bundle.matrix_tactical_pools) == 36


def test_heat_seats_missing_heat_block_fuses_not_zero() -> None:
    rec = _real_rec()
    rec.pop('score_market_heat')
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm._matrix_heat_seats_from_rec(rec)
    assert 'score_market_heat' in str(excinfo.value)
    assert '热度席位缺失' in str(excinfo.value)


def test_heat_seats_empty_ranking_fuses_not_zero() -> None:
    rec = _real_rec()
    rec['score_market_heat'] = {'available': False, 'ranking': [], 'reason': 'x'}
    # 文案已改为中文；英文路径只留在括号里作排障位。
    with pytest.raises(sm.MatrixIntegrityError, match='市热榜排名列表.*为空'):
        sm._matrix_heat_seats_from_rec(rec)


def test_duplicate_ranked_score_takes_its_best_position() -> None:
    """同一比分在榜单出现两次：只能坐一个席位，取最靠前的位次。"""
    rec = _real_rec()
    rec['score_market_heat'] = {
        'ranking': [{'score': '2-0'}, {'score': '1-0'}, {'score': '1-0'}]
    }
    seats = sm._matrix_heat_seats_from_rec(rec)
    assert seats['2-0'] == 8     # 第 1 名
    assert seats['1-0'] == 7     # 第 2 名（不是第 3 名的 6，也不累加成 13）
    assert seats['0-0'] == 0


# --------------------------------------------------------------------------- #
# 缺项必须点名，且 TG 段如实写「未形成（原因）」
# --------------------------------------------------------------------------- #
def test_real_record_missing_market_score_fuses_and_names_it() -> None:
    rec = _real_rec()
    market = dict(rec['correct_score_page_avg'])
    market.pop('3-3')
    rec['correct_score_page_avg'] = market
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_matrices_from_rec(rec)
    assert '3-3' in str(excinfo.value)

    message = sm.format_matrices_tg_messages(rec)[0]
    assert '未形成' in message
    assert '3-3' in message
    # 绝不补 0 / 打印半张表
    assert 'P01_WIN_LOW_LOCK' not in message


def test_real_record_missing_jc_score_fuses_and_names_it() -> None:
    rec = _real_rec()
    jc = dict(rec['correct_score_jc_official'])
    jc.pop('平其它')
    rec['correct_score_jc_official'] = jc
    rec.pop('jc_score_odds', None)
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_matrices_from_rec(rec)
    assert '平其它' in str(excinfo.value)

    message = sm.format_matrices_tg_messages(rec)[0]
    assert '未形成' in message
    assert '平其它' in message


def test_missing_heat_shows_unformed_reason_in_tg() -> None:
    rec = _real_rec()
    rec.pop('score_market_heat')
    message = sm.format_matrices_tg_messages(rec)[0]
    assert '未形成' in message
    assert '热度席位缺失' in message
    assert 'score_market_heat' in message


def test_missing_whole_market_block_fuses_with_key_name() -> None:
    rec = _real_rec()
    rec.pop('correct_score_page_avg')
    rec.pop('correct_score_site_avg', None)
    with pytest.raises(sm.MatrixIntegrityError, match='市均 31 项缺失'):
        sm.build_matrices_from_rec(rec)


def test_site_avg_is_used_as_market_fallback() -> None:
    """correct_score_page_avg 缺失时回退 correct_score_site_avg。"""
    rec = _real_rec()
    site = rec.pop('correct_score_page_avg')
    rec['correct_score_site_avg'] = site
    bundle = sm.build_matrices_from_rec(rec)
    assert len(bundle.matrix_31) == 31


def test_jc_dict_and_bare_odds_shapes_both_accepted() -> None:
    """竞彩表的值可能是裸赔率，也可能是 {'odds': ...}：两种都要吃。"""
    rec = _real_rec()
    jc = rec['correct_score_jc_official']
    rec['correct_score_jc_official'] = {
        s: ({'odds': v} if i % 2 else v) for i, (s, v) in enumerate(jc.items())
    }
    bundle = sm.build_matrices_from_rec(rec)
    plain = sm.build_matrices_from_rec(_real_rec())
    assert [r.lottery_odds for r in bundle.matrix_31] == [r.lottery_odds for r in plain.matrix_31]


def test_build_matrices_from_rec_never_invents_missing_odds() -> None:
    """竞彩有键但赔率为 None：熔断，不补默认值。"""
    rec = _real_rec()
    jc = dict(rec['correct_score_jc_official'])
    jc['0-5'] = None
    rec['correct_score_jc_official'] = jc
    with pytest.raises(sm.MatrixIntegrityError) as excinfo:
        sm.build_matrices_from_rec(rec)
    assert '0-5' in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Option A：竞彩填充的 9 项 = 「市均无报价」，None 不得静默变 0
# --------------------------------------------------------------------------- #
def test_real_record_has_22_priced_and_9_unpriced() -> None:
    """真实抓包：31 行里 22 行有真实市均，9 行是竞彩填充。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    assert bundle.market_priced_count == 22
    priced = [r for r in bundle.matrix_31 if r.has_market_price]
    unpriced = [r for r in bundle.matrix_31 if not r.has_market_price]
    assert len(priced) == 22
    assert len(unpriced) == 9
    # 这 9 项必须正好是 provenance 标为 jc_official 的那 9 个
    assert {r.score for r in unpriced} == set(sm._jc_filled_scores(rec))
    assert {r.score for r in unpriced} == {
        '5-0', '5-1', '5-2', '胜其它', '平其它', '0-5', '1-5', '2-5', '负其它',
    }


def test_unpriced_rows_have_none_delta_not_zero() -> None:
    """无市均的 9 行：market_avg_odds / delta / abs_delta 全是 None，不是 0.0。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    for row in bundle.matrix_31:
        if row.has_market_price:
            continue
        assert row.market_avg_odds is None, row.score
        assert row.delta is None, f'{row.score} delta 必须是 None，0.0 会被读成「贴着盘口」'
        assert row.abs_delta is None, row.score
        # 竞彩赔率本身仍然有值
        assert row.lottery_odds > 1.0


def test_top8_contains_no_jc_filled_row() -> None:
    """Top-8 里零个竞彩填充项——它们没有可比价，不该占「最小阻力」榜。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    unpriced = {r.score for r in bundle.matrix_31 if not r.has_market_price}
    top8 = {t.score for t in bundle.top_8_min_abs_delta}
    assert len(bundle.top_8_min_abs_delta) == 8
    assert top8 & unpriced == set()
    assert all(t.abs_delta is not None for t in bundle.top_8_min_abs_delta)


def test_pool_means_exclude_unpriced_rows_from_denominator() -> None:
    """池均差分母只数有市均的项；整池无价则均值与均差均为 None。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    by_score = {r.score: r for r in bundle.matrix_31}

    for pool in bundle.matrix_tactical_pools:
        priced = [by_score[s] for s in pool.scores if by_score[s].has_market_price]
        assert pool.priced_count == len(priced)
        if not priced:
            assert pool.avg_market_odds is None
            assert pool.avg_delta is None
            continue
        # 手算：只对 priced 行求均值
        expect_m = round(sum(r.market_avg_odds for r in priced) / len(priced), 4)
        expect_j = round(sum(by_score[s].lottery_odds for s in pool.scores) / len(pool.scores), 4)
        assert pool.avg_market_odds == expect_m, pool.pool_id
        assert pool.avg_lottery_odds == expect_j, pool.pool_id
        assert pool.avg_delta == expect_m - expect_j, pool.pool_id

    # 全由竞彩填充构成的池：P06(胜其它) / P15(负其它) 在这个夹具上必然是 None
    pools = {p.pool_id: p for p in bundle.matrix_tactical_pools}
    assert pools['P06_WIN_OVERKILL'].priced_count == 0
    assert pools['P06_WIN_OVERKILL'].avg_delta is None
    assert pools['P15_LOSS_OVERKILL'].priced_count == 0
    assert pools['P15_LOSS_OVERKILL'].avg_delta is None


def test_channel_summary_denominators_reflect_priced_rows() -> None:
    """三向均值只对有价行求值，并给出各方向的有价项数。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    cs = bundle.channel_summary
    assert cs.win_priced_count + cs.draw_priced_count + cs.loss_priced_count == 22
    for direction, mean, priced_n in (
        ('WIN', cs.win_mean_abs_delta, cs.win_priced_count),
        ('DRAW', cs.draw_mean_abs_delta, cs.draw_priced_count),
        ('LOSS', cs.loss_mean_abs_delta, cs.loss_priced_count),
    ):
        rows = [
            r for r in bundle.matrix_31
            if r.direction == direction and r.has_market_price
        ]
        assert len(rows) == priced_n
        expect = round(sum(r.abs_delta for r in rows) / len(rows), 4)
        assert mean == expect, direction


def test_unpriced_marker_rendered_in_tg_and_markdown() -> None:
    """TG 与 Markdown 都要写明「市均无报价(竞彩填充)」，不显示 0.00。"""
    rec = _real_rec()
    bundle = sm.build_matrices_from_rec(rec)
    pages = sm.format_matrices_telegram(bundle)
    joined = '\n'.join(pages)
    assert '市均无报价(竞彩填充)' in joined
    assert '有市均报价项数 22/31' in joined
    # 无价行的市均列是 — 而不是 0.00
    row_line = next(l for l in joined.splitlines() if l.startswith('5-0 '))
    assert '—' in row_line

    md = sm.format_matrices_markdown(bundle)
    assert '有市均报价项数=`22/31`' in md


def test_old_record_without_provenance_derives_from_books_avg() -> None:
    """老记录没有 cs31_provenance：从 correct_score_books_avg 的键集反推。

    与 blade_deepseek_pipeline.resolve_cs31_provenance 同一口径：
    在原始市均页键集里 = 有市均，不在 = 竞彩填充。结果必须与新记录的显式
    出处**逐键一致**，否则同一场比赛会有两个答案。
    """
    rec = _real_rec()
    explicit = set(sm._jc_filled_scores(rec))
    rec.pop('cs31_provenance')
    derived = set(sm._jc_filled_scores(rec))
    assert derived == explicit
    assert len(derived) == 9
    bundle = sm.build_matrices_from_rec(rec)
    assert bundle.market_priced_count == 22


def test_record_without_provenance_and_without_books_treats_all_as_priced() -> None:
    """出处与 books_avg 都缺：无从判断，按「全部有市均」处理，不猜也不丢项。"""
    rec = _real_rec()
    rec.pop('cs31_provenance')
    rec.pop('correct_score_books_avg')
    assert sm._jc_filled_scores(rec) == []
    # 这份夹具的 9 项被 merge 成了竞彩价，Δ 恒 0 → D1 照常熔断（不静默出表）
    with pytest.raises(sm.MatrixIntegrityError, match='无定义'):
        sm.build_matrices_from_rec(rec)


def test_all_unpriced_fuses_instead_of_emitting_all_none() -> None:
    """31 项全被判为无价：直接熔断，不出全 None 的表。"""
    rec = _real_rec()
    rec['cs31_provenance'] = {s: 'jc_official' for s in sm.THIRTY_ONE_SCORES}
    with pytest.raises(sm.MatrixIntegrityError, match='全部无市均报价'):
        sm.build_matrices_from_rec(rec)


# --------------------------------------------------------------------------- #
# 【市均前16·进球赔率矩阵】
# --------------------------------------------------------------------------- #
def test_goal_bucket_mapping_is_pinned() -> None:
    """进球数列映射，含「其它一律 7+」这条最容易写错的规则。"""
    assert sm.goal_bucket_of('0-0') == '0球'
    assert sm.goal_bucket_of('1-0') == '1球'
    assert sm.goal_bucket_of('0-1') == '1球'
    assert sm.goal_bucket_of('2-0') == '2球'
    assert sm.goal_bucket_of('1-1') == '2球'
    assert sm.goal_bucket_of('0-2') == '2球'
    assert sm.goal_bucket_of('2-1') == '3球'
    assert sm.goal_bucket_of('3-0') == '3球'
    assert sm.goal_bucket_of('0-3') == '3球'
    assert sm.goal_bucket_of('3-1') == '4球'
    assert sm.goal_bucket_of('2-2') == '4球'
    assert sm.goal_bucket_of('3-2') == '5球'
    assert sm.goal_bucket_of('2-3') == '5球'
    # 恰为 6 球
    for score in ('4-2', '2-4', '3-3', '5-1', '1-5'):
        assert sm.goal_bucket_of(score) == '6球', score
    # >= 7 球
    for score in ('4-3', '5-2', '2-5', '4-4'):
        assert sm.goal_bucket_of(score) == '7+', score
    # 三个「其它」一律 7+（不是 6，也不是被丢掉）
    for other in ('胜其它', '平其它', '负其它'):
        assert sm.goal_bucket_of(other) == '7+', other


def test_goal_direction_mapping() -> None:
    assert sm.direction_of('胜其它') == '胜'
    assert sm.direction_of('平其它') == '平'
    assert sm.direction_of('负其它') == '负'
    assert sm.direction_of('2-1') == '胜'
    assert sm.direction_of('1-1') == '平'
    assert sm.direction_of('0-2') == '负'


def test_goal_matrix_top16_from_real_capture() -> None:
    """真实抓包：前 16 全部来自真实市均，零个竞彩填充项，按赔率升序。"""
    rec = _real_rec()
    real_market = {
        s: v for s, v in rec['correct_score_page_avg'].items()
        if s not in sm._jc_filled_scores(rec)
    }
    matrix = sm.build_goal_odds_matrix(
        real_market, rec['correct_score_jc_official'], match_id='1347776',
        home='主队X', away='客队Y',
    )
    assert len(matrix.selected) == 16
    assert matrix.available_market_count == 22
    assert matrix.is_short is False

    # 手写期望：这 22 项按赔率升序的前 16（来自真实抓包，不是算出来的）
    hand = sorted(real_market.items(), key=lambda kv: kv[1])[:16]
    got_scores = [s for s, _ in matrix.selected]
    assert got_scores == [s for s, _ in hand]
    odds = [o for _, o in matrix.selected]
    assert odds == sorted(odds)
    # 一个竞彩填充项都不许出现
    assert set(got_scores) & set(sm._jc_filled_scores(rec)) == set()


def test_goal_matrix_both_pages_have_three_rows_and_aligned_pipes() -> None:
    rec = _real_rec()
    real_market = {
        s: v for s, v in rec['correct_score_page_avg'].items()
        if s not in sm._jc_filled_scores(rec)
    }
    matrix = sm.build_goal_odds_matrix(real_market, {}, home='A', away='B')
    assert len(matrix.pages) == 2
    assert matrix.pages[0].columns == ('0球', '1球', '2球', '3球')
    assert matrix.pages[1].columns == ('4球', '5球', '6球', '7+')
    for page in matrix.pages:
        assert [r.direction for r in page.rows] == ['胜', '平', '负']
        for row in page.rows:
            assert len(row.cells) == 4
        assert len(page.column_means) == 4

    pages = sm.format_goal_odds_matrix_tg(matrix)
    for page_text in pages:
        lines = [l for l in page_text.splitlines() if '|' in l]
        assert lines
        widths = [
            [sm._display_width(l[:i]) for i, ch in enumerate(l) if ch == '|']
            for l in lines
        ]
        # 必须按**显示宽度**比对：CJK 在 len 里算 1、在终端占 2 列
        assert len({tuple(w) for w in widths}) == 1, widths


def test_goal_matrix_column_means_are_hand_computed() -> None:
    """列均 = 整列所有出现比分的赔率均值，手写期望值。"""
    # 构造：2-1=10.00, 3-0=20.00 同落 3球 列（一个胜局），0-3=30.00 也落 3球（负局）
    market = {
        '1-0': 6.00,    # 1球 胜
        '2-1': 10.00,   # 3球 胜
        '3-0': 20.00,   # 3球 胜
        '0-3': 30.00,   # 3球 负
        '0-0': 8.00,    # 0球 平
    }
    matrix = sm.build_goal_odds_matrix(market, {}, home='H', away='A')
    page1 = matrix.pages[0]
    means = dict(zip(page1.columns, page1.column_means))
    assert means['0球'] == 8.00            # 0-0
    assert means['1球'] == 6.00            # 1-0
    assert means['2球'] is None            # 没有 2 球比分 → —，不是 0
    assert means['3球'] == 20.00           # (10+20+30)/3 = 20.00


def test_goal_matrix_empty_cell_renders_dash_not_zero() -> None:
    """空格画 ``—`` 且下半行留空；绝不出现 0.00。"""
    market = {'1-0': 6.00}
    matrix = sm.build_goal_odds_matrix(market, {})
    text = sm.format_goal_odds_matrix_tg(matrix)[0]
    assert '—' in text
    assert '0.00' not in text
    # 1球 胜 有 1-0，其余格全空
    win_row = next(l for l in text.splitlines() if l.startswith('【胜】'))
    assert '1-0' in win_row


def test_goal_matrix_short_of_16_says_so() -> None:
    """真实市均不足 16 项：有多少出多少，并在末尾如实注明。"""
    market = {s: 5.0 + i for i, s in enumerate(
        ('1-0', '2-0', '2-1', '0-0', '1-1', '0-1', '0-2', '3-0', '3-1', '1-2')
    )}
    matrix = sm.build_goal_odds_matrix(market, {})
    assert len(matrix.selected) == 10
    assert matrix.is_short is True
    assert matrix.available_market_count == 10
    text = sm.format_goal_odds_matrix_tg(matrix)[-1]
    assert '真实市均仅 10 项，前 16 不足' in text


def test_goal_matrix_full_31_priced_works() -> None:
    """全部 31 项都有市均：取前 16，不报 short。"""
    market = {s: 3.0 + i * 0.5 for i, s in enumerate(sm.THIRTY_ONE_SCORES)}
    matrix = sm.build_goal_odds_matrix(market, {})
    assert len(matrix.selected) == 16
    assert matrix.is_short is False
    assert matrix.available_market_count == 31


def test_goal_matrix_no_market_prices_fuses() -> None:
    with pytest.raises(sm.MatrixIntegrityError, match='无任何真实市均报价'):
        sm.build_goal_odds_matrix({}, {})


def test_goal_matrix_pages_split_into_two_messages_with_header_first() -> None:
    """两页用 \\x0c 分隔，切分后每页第一行都是自己的表头。"""
    import blade_deepseek_pipeline as pipe

    rec = _real_rec()
    rec['home'] = '主队X'
    rec['away'] = '客队Y'
    msgs = sm.format_goal_odds_matrix_messages(rec, '1347776')
    assert len(msgs) == 1
    assert '\x0c' in msgs[0]
    parts = pipe.split_tg_html_preserving_pre(msgs[0], 4000)
    assert len(parts) == 2, [len(p) for p in parts]
    for idx, part in enumerate(parts, 1):
        first = part.splitlines()[0]
        assert first.startswith('【市均前16·进球赔率矩阵】'), first
        assert f'第{idx}页' in part


# --------------------------------------------------------------------------- #
# 报告接线：矩阵段与进球赔率段真的进了 TG 报告，且互相隔离
# --------------------------------------------------------------------------- #
def test_app_source_wires_both_matrix_sections_into_the_tg_report() -> None:
    """app 的组装处必须真的调用这两个小节，否则模块再好也不会出现在报告里。"""
    app_source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert 'format_matrices_tg_messages' in app_source
    assert 'format_goal_odds_matrix_messages' in app_source
    # 两段各自包在 try 里，且矩阵段有前置分页符（第一页也从新消息开始）
    # 用原始字符串比对源码里的转义写法（普通字符串里 \x0c 会变成真实的换页符）
    assert r"_final_sections.append('\x0c' + '\x0c'.join(_matrix_pages))" in app_source
    assert r"_final_sections.append('\x0c' + _goal_msg)" in app_source


def test_report_sections_are_isolated_when_one_layer_fails() -> None:
    """矩阵段熔断不影响进球赔率段，反之亦然——两个生产入口各自独立降级。"""
    rec = _real_rec()
    # 1) 三套矩阵成功、进球段因无市均而熔断
    broken_goal = dict(rec)
    broken_goal['correct_score_page_avg'] = {}
    broken_goal.pop('correct_score_site_avg', None)
    ok_msgs = sm.format_matrices_tg_messages(rec)
    bad_goal = sm.format_goal_odds_matrix_messages(broken_goal)
    assert '未形成' not in ok_msgs[0]
    assert '未形成' in bad_goal[0]

    # 2) 进球段成功、三套矩阵因缺热度而熔断
    broken_matrix = dict(rec)
    broken_matrix.pop('score_market_heat')
    bad_matrix = sm.format_matrices_tg_messages(broken_matrix)
    ok_goal = sm.format_goal_odds_matrix_messages(rec)
    assert '未形成' in bad_matrix[0]
    assert '热度席位缺失' in bad_matrix[0]
    assert '未形成' not in ok_goal[0]


def test_every_matrix_page_survives_the_sender_split_with_header_first() -> None:
    """把整份报告的矩阵段交给真正的 splitter，每一页都必须自成一条且表头在首行。"""
    import blade_deepseek_pipeline as pipe

    rec = _real_rec()
    rec['home'] = '主队X'
    rec['away'] = '客队Y'
    matrix_pages = sm.format_matrices_tg_messages(rec, '1347776')
    goal_msgs = sm.format_goal_odds_matrix_messages(rec, '1347776')
    sections = ['\x0c' + '\x0c'.join(matrix_pages)]
    sections += ['\x0c' + g for g in goal_msgs]
    report = '\n\n'.join(sections)
    parts = pipe.split_tg_html_preserving_pre(report, 4000)

    expected_heads = [p.splitlines()[0] for p in matrix_pages]
    for g in goal_msgs:
        expected_heads.extend(p.splitlines()[0] for p in g.split('\x0c'))
    # splitter 不该产出空页；有空白页说明某个小节退化成了空串
    assert all(p.strip() for p in parts), [repr(p[:40]) for p in parts]
    got_heads = [p.splitlines()[0] for p in parts if p.strip()]
    assert got_heads == expected_heads, got_heads
    for part in parts:
        assert len(part) <= 4000
