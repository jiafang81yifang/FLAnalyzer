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
    expected_columns = [9, 13, 8]
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
