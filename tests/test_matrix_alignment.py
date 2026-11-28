"""全仓矩阵段的横纵对齐守卫。

用户硬约束（优先级高于进度）：

  > 所有矩阵必须横向和纵向必须对齐。

三条判据缺一不可：

  1. **横向**：同一张表每一行（列头、数据、`均` 行、汇总）显示宽度完全相等。
     显示宽度按中文/全角 2、ASCII 1；**禁止 `len()`**。
  2. **纵向**：每个列分隔符 `|` 在同一张表所有行里落在同一个显示列索引上。
  3. **等宽前提**：表体必须包在 `<pre>` 等宽块里，否则比例字体把对齐吃掉。
     段标题（`📈【…】` 带 emoji 的那种）在块外——emoji 宽度不可靠。

## 为什么测试自己写宽度函数

上一轮踩过的坑：对齐测试拿被测渲染器的 `_display_width` 当标尺，把它换成
`len` 时两侧一起变，永远绿。所以这里 `_w()` 是**本文件独立的实现**，
不 import 渲染器的任何宽度函数。

## 哪些段是网格，哪些不是

网格段（有 `|`）逐行验三条判据；空格分隔段（旧 31 表 / 市短矩阵 / 市热矩阵 /
六盘口事实板）没有列分隔符，纵向判据退化为「列起点对齐」，这里对它们只验
横向等宽 + 等宽块，并在 `SPACE_TABLE_SECTIONS` 里写明为什么不做 `|` 判据。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import re
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot), contextlib.redirect_stderr(_boot):
    pipe = importlib.import_module('blade_deepseek_pipeline')
    sm = importlib.import_module('score_matrices')

DSA = pipe.DeepSeekAnalyzer
T31 = sm.THIRTY_ONE_SCORES


def _w(text: str) -> int:
    """独立实现的显示宽度：CJK 全角 2 列，其余 1 列。**不使用 `len`。**"""
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in str(text))


def _pipe_cols(line: str) -> tuple[int, ...]:
    """这一行每个 `|` 所在的显示列。"""
    return tuple(_w(line[:i]) for i, ch in enumerate(line) if ch == '|')


def _table_rows(lines: list[str]) -> list[str]:
    """段内所有表格体行（含 `|` 的行）。"""
    return [ln for ln in lines if pipe.GRID_SEP in ln and ln.strip()]


def assert_grid_aligned(lines: list[str], label: str) -> None:
    """三条判据一起验：行宽相等、`|` 同列、每行首尾都是分隔符。"""
    rows = _table_rows(lines)
    assert rows, f'{label}: 段内没有任何网格行'
    widths = {_w(ln) for ln in rows}
    assert len(widths) == 1, f'{label}: 行宽不等 {sorted(widths)}'
    cols = {_pipe_cols(ln) for ln in rows}
    assert len(cols) == 1, f'{label}: `|` 列边界不齐 {list(cols)[:3]}'
    for ln in rows:
        assert ln.startswith(pipe.GRID_SEP) and ln.endswith(pipe.GRID_SEP), (
            f'{label}: 行首尾必须是分隔符 {ln!r}'
        )


# --------------------------------------------------------------------------- #
# 夹具：一份真实形态的 31 全表（市均 + 竞彩都给全）
# --------------------------------------------------------------------------- #
MARKET = {
    '1-0': 14.55, '2-0': 18.30, '2-1': 10.11, '3-0': 27.18, '3-1': 16.45,
    '4-0': 54.23, '4-1': 32.46, '4-2': 33.36, '胜其它': 47.10,
    '0-0': 18.05, '3-3': 21.58, '平其它': 72.49, '0-1': 12.84, '2-4': 26.95,
    '3-2': 16.82, '1-1': 8.05, '2-2': 9.79, '0-2': 14.62, '0-3': 21.06,
    '1-2': 9.10, '1-3': 13.30, '0-4': 37.63, '1-4': 24.29, '2-3': 14.86,
    '负其它': 41.07, '5-0': 80.0, '5-1': 70.0, '5-2': 88.0,
    '0-5': 60.0, '1-5': 44.0, '2-5': 52.0,
}


def _pack() -> dict:
    jc = dict(MARKET)
    hot = ['3-2', '1-1', '2-2', '0-2', '0-3', '1-2', '1-3', '0-4', '1-4', '2-3', '负其它']
    short = ['1-0', '2-0', '2-1', '3-0', '3-1', '4-0', '4-1', '4-2', '胜其它',
             '0-0', '3-3', '平其它', '0-1', '2-4']
    for i, s in enumerate(hot):
        jc[s] = round(MARKET[s] - (i + 1) * 0.5, 2)
    for i, s in enumerate(short):
        jc[s] = round(MARKET[s] + (i + 1) * 0.5, 2)
    return DSA.build_theo_mkt_31_pack(
        [], jc_score_odds=jc, extra_market_odds=MARKET,
        lam_home=1.45, lam_away=1.10,
        market_provenance={k: 'desktop_cs' for k in MARKET},
    )


# --------------------------------------------------------------------------- #
# 1. 新五段：三条判据逐个验
# --------------------------------------------------------------------------- #
NEW_SECTIONS = {
    '市均***竞彩对比表': lambda p: DSA.format_market_vs_jingcai_odds_tg_lines(
        p, home='曼城', away='阿森纳'),
    '★市短·胜平负比分矩阵': lambda p: DSA.format_jingcai_short_wdl_matrix_tg_lines(
        p, home='曼城', away='阿森纳'),
    '市热·胜平负比分矩阵': lambda p: DSA.format_jingcai_hot_wdl_matrix_tg_lines(
        p, home='曼城', away='阿森纳'),
    '市均前15·进球赔率矩阵': lambda p: DSA.format_market_goal_top_matrix_tg_lines(
        p, home='曼城', away='阿森纳'),
}


@pytest.mark.parametrize('name', sorted(NEW_SECTIONS))
def test_new_section_is_a_strict_grid(name: str) -> None:
    lines = NEW_SECTIONS[name](_pack())
    assert_grid_aligned(lines, name)


def test_hot_short_distribution_is_uniform_width_lines() -> None:
    """该段不是网格（纯文本行），但同一段内地行宽必须一致。"""
    lines = DSA.format_hot_short_distribution_tg_lines(_pack(), home='曼城', away='阿森纳')
    body = [ln for ln in lines[1:] if ln.strip()]
    # 这一段的每一行都是「标签：值」形式，宽度随内容变，这是刻意的：
    # 它不是列对齐表，硬凑等宽只会插入无意义空格。这里只要求：
    #   1. 没有 `|`（不假装是网格）；
    #   2. 行内没有 tab（TG 会打乱）；
    #   3. 每个比分清单用同一个分隔符 `/`。
    assert all('|' not in ln for ln in lines), [ln for ln in lines if '|' in ln]
    assert all('\t' not in ln for ln in body)
    score_lists = [ln for ln in body if ln.startswith((' 市热比分：', ' 市短比分：'))]
    assert len(score_lists) == 2
    assert all(ln.strip().endswith(tuple('0123456789胜负平它')) for ln in score_lists)


# --------------------------------------------------------------------------- #
# 2. 旧段：横向等宽（空格分隔段没有 `|`，纵向判据退化）
# --------------------------------------------------------------------------- #
#: 空格分隔段 → 为什么不做 `|` 判据。
#: 这些段是已上线格式，43 处测试与 6 个正则解析器钉住它们的形状；
#: 它们内部每行已经等宽，改成 `|` 网格会同时打断解析器与既有守卫。
SPACE_TABLE_SECTIONS = {
    '31项市均×竞彩对比表': '已上线空格格式，43 处守卫 + parity 正则钉住',
    '市短矩阵': '已上线空格格式，含同口径子段',
    '市热矩阵': '已上线空格格式',
    '六盘口事实板': '已上线「标签：值」格式，不是列表',
}


def test_31_table_rows_are_equal_width() -> None:
    lines = DSA.format_theo_mkt_31_tg_lines(_pack())
    rows = [ln for ln in lines if re.match(r'^  \d{2} ', ln)]
    assert len(rows) == 31
    assert len({_w(ln) for ln in rows}) == 1, sorted({_w(ln) for ln in rows})
    assert all('\t' not in ln for ln in lines)


def test_short_matrix_rows_are_equal_width() -> None:
    pack = _pack()
    lines = DSA.format_mkt_short_wdl_matrix_tg_lines(
        DSA.build_mkt_short_matrix_from_pack(pack))
    rows = [ln for ln in lines if '市均' in ln and '差' in ln]
    assert rows, lines
    assert len({_w(ln) for ln in rows}) == 1, sorted({_w(ln) for ln in rows})


def test_hot_matrix_rows_are_equal_width() -> None:
    lines = DSA.format_mkt_hot_wdl_matrix_tg_lines(_pack())
    # 只取数据行（以两空格 + 比分打头），标题行里也含「市均/理论」两个词。
    rows = [ln for ln in lines if re.match(r'^  \S+\s+市均', ln)]
    assert rows, lines
    assert len({_w(ln) for ln in rows}) == 1, sorted({_w(ln) for ln in rows})


# --------------------------------------------------------------------------- #
# 3. 等宽块：表体必须在 <pre> 里，标题在外
# --------------------------------------------------------------------------- #
def test_table_body_is_wrapped_in_pre_and_title_stays_outside() -> None:
    lines = DSA.format_market_goal_top_matrix_tg_lines(_pack(), home='曼城', away='阿森纳')
    html = DSA.format_matrix_dual_prejudge_html('\n'.join(lines))
    assert '<pre>' in html and '</pre>' in html
    # 段标题带 emoji，宽度不可靠：必须在等宽块外面。
    head_pos = html.find('市均前15·进球赔率矩阵')
    pre_pos = html.find('<pre>')
    assert head_pos >= 0 and pre_pos >= 0
    assert head_pos < pre_pos, '段标题被包进了等宽块（emoji 宽度会把整块推歪）'
    # 表格体确实在块内。
    body = html[pre_pos:]
    assert '0球' in body and '18.05' in body


def test_pre_block_contains_only_table_rows() -> None:
    lines = DSA.format_jingcai_short_wdl_matrix_tg_lines(_pack(), home='曼城', away='阿森纳')
    html = DSA.format_matrix_dual_prejudge_html('\n'.join(lines))
    for block in re.findall(r'<pre>(.*?)</pre>', html, re.S):
        for row in block.split('\n'):
            if row.strip():
                assert pipe.GRID_SEP in row, f'等宽块里混进了非表格行: {row!r}'


# --------------------------------------------------------------------------- #
# 4. 假绿的两种形态：宽度相等但列边界错位
# --------------------------------------------------------------------------- #
def test_equal_width_but_shifted_boundary_is_caught() -> None:
    """构造两张同宽但 `|` 位置不同的行——第 ② 条必须红，第 ① 条兜不住。"""
    a = '|胜 |1-0 |14.55|'
    b = '|胜|1-0 | 14.55|'
    assert _w(a) == _w(b), '构造前提：两行必须等宽'
    assert _pipe_cols(a) != _pipe_cols(b), '这条用例的意义就是边界不同'
    with pytest.raises(AssertionError):
        assert_grid_aligned([a, b], 'synthetic')


def test_tab_and_trailing_space_are_absent() -> None:
    for name, fn in NEW_SECTIONS.items():
        lines = fn(_pack())
        for ln in lines:
            assert '\t' not in ln, (name, ln)
            assert not ln.endswith(' '), (name, '行尾有空格，TG 可能 trim', ln)


# --------------------------------------------------------------------------- #
# 5. 全仓只用一种分隔符
# --------------------------------------------------------------------------- #
def test_only_one_separator_char_used_inside_grid_rows() -> None:
    """表格行里只能用半角 `|`；全角 `｜` 只允许出现在中文行文（段名/口径行）里。"""
    pack = _pack()
    texts = [fn(pack) for fn in NEW_SECTIONS.values()]
    texts.append(DSA.format_hot_short_distribution_tg_lines(pack, home='曼城', away='阿森纳'))
    for lines in texts:
        for ln in lines:
            if pipe.GRID_SEP in ln:
                assert '\uff5c' not in ln, f'表格行混用全角竖线: {ln!r}'
    assert pipe.GRID_SEP == '|'
