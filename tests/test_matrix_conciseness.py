"""矩阵段的「简洁」守卫：布局简洁（结构层）+ 内容简洁（信息层）。

「简洁」在这里是可判定的，不是「看起来短」：

  1. 段内没有纯装饰行（连续 `─`/`=`/`*` 画的分隔线、空行堆叠）；
  2. 同一比分在同一段里不出现两次；
  3. 段内总行数不超过该段的上限（常量写在本文件，改多了必须显式改这里）；
  4. 标题行不重复段名；
  5. 表格之外最多一行数据解读；
  6. 单位在列头，不在每个格子里重复（`均` 不再逐格出现、`@赔率` 不再逐比分出现）。

## 防「过度删减」

`test_user_named_number_anchors_are_all_present` 逐条断言用户点名的每个数字
仍能从装配后的正文里找到。删多了就红——简洁是去冗余，不是去信息。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot), contextlib.redirect_stderr(_boot):
    pipe = importlib.import_module('blade_deepseek_pipeline')

DSA = pipe.DeepSeekAnalyzer

MARKET = {
    '1-0': 14.55, '2-0': 18.30, '2-1': 10.11, '3-0': 27.18, '3-1': 16.45,
    '4-0': 54.23, '4-1': 32.46, '4-2': 33.36, '胜其它': 47.10,
    '0-0': 18.05, '3-3': 21.58, '平其它': 72.49, '0-1': 12.84, '2-4': 26.95,
    '3-2': 16.82, '1-1': 8.05, '2-2': 9.79, '0-2': 14.62, '0-3': 21.06,
    '1-2': 9.10, '1-3': 13.30, '0-4': 37.63, '1-4': 24.29, '2-3': 14.86,
    '负其它': 41.07, '5-0': 80.0, '5-1': 70.0, '5-2': 88.0,
    '0-5': 60.0, '1-5': 44.0, '2-5': 52.0,
}
HOT = ['3-2', '1-1', '2-2', '0-2', '0-3', '1-2', '1-3', '0-4', '1-4', '2-3', '负其它']
SHORT = ['1-0', '2-0', '2-1', '3-0', '3-1', '4-0', '4-1', '4-2', '胜其它',
         '0-0', '3-3', '平其它', '0-1', '2-4']

#: 每段的「行数上限」（含标题行与解读行）。改多一行就必须改这个常量，
#: 逼着改动者显式承认「这一段变长了」。
MAX_LINES = {
    '市均***竞彩对比表': 40,        # 标题+队名+口径+列头+31行+市短清单
    '★市短·胜平负比分矩阵': 8,       # 标题+队名+列头+3行+解读
    '市热·胜平负比分矩阵': 8,        # 标题+队名+计数+列头+3行+口径
    '市均前15·进球赔率矩阵': 30,     # 两页各 10 行网格 + 汇总/口径
    '【市热·市短分布｜冷门窗口】': 8,  # 标题+4组比分行+候选+解读
}

#: 纯装饰行：只有分隔符/空白，或连续 3 个以上同类画线字符。
_DECOR = re.compile(r'^\s*([─━═—\-=*_~·])\1{2,}\s*$')


def _pack() -> dict:
    jc = dict(MARKET)
    for i, s in enumerate(HOT):
        jc[s] = round(MARKET[s] - (i + 1) * 0.5, 2)
    for i, s in enumerate(SHORT):
        jc[s] = round(MARKET[s] + (i + 1) * 0.5, 2)
    return DSA.build_theo_mkt_31_pack(
        [], jc_score_odds=jc, extra_market_odds=MARKET,
        lam_home=1.45, lam_away=1.10,
        market_provenance={k: 'desktop_cs' for k in MARKET},
    )


SECTIONS = {
    '市均***竞彩对比表': lambda p: DSA.format_market_vs_jingcai_odds_tg_lines(
        p, home='曼城', away='阿森纳'),
    '★市短·胜平负比分矩阵': lambda p: DSA.format_jingcai_short_wdl_matrix_tg_lines(
        p, home='曼城', away='阿森纳'),
    '市热·胜平负比分矩阵': lambda p: DSA.format_jingcai_hot_wdl_matrix_tg_lines(
        p, home='曼城', away='阿森纳'),
    '市均前15·进球赔率矩阵': lambda p: DSA.format_market_goal_top_matrix_tg_lines(
        p, home='曼城', away='阿森纳'),
    '【市热·市短分布｜冷门窗口】': lambda p: DSA.format_hot_short_distribution_tg_lines(
        p, home='曼城', away='阿森纳'),
}


# --------------------------------------------------------------------------- #
# 1. 没有装饰行
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', sorted(SECTIONS))
def test_no_decorative_separator_lines(name: str) -> None:
    lines = SECTIONS[name](_pack())
    for ln in lines:
        assert not _DECOR.match(ln), f'{name}: 纯装饰分隔行 {ln!r}'
        # 三个以上连续横线/等号出现在行首也算装饰。
        assert not re.match(r'^\s*[-=─]{3,}', ln), f'{name}: 装饰线 {ln!r}'


@pytest.mark.parametrize('name', sorted(SECTIONS))
def test_no_consecutive_blank_lines(name: str) -> None:
    lines = SECTIONS[name](_pack())
    for prev, cur in zip(lines, lines[1:]):
        assert not (not prev.strip() and not cur.strip()), f'{name}: 空行堆叠'


# --------------------------------------------------------------------------- #
# 2. 同一比分不重复
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', ['★市短·胜平负比分矩阵', '市热·胜平负比分矩阵', '市均***竞彩对比表'])
def test_no_score_appears_twice(name: str) -> None:
    lines = SECTIONS[name](_pack())
    seen: list[str] = []
    for ln in lines:
        if pipe.GRID_SEP in ln:
            # 网格行里只数比分形态的格（`1-0` / `胜其它`），不数数字。
            for token in re.findall(r'\b\d+-\d+\b|(?:胜|平|负)其它', ln):
                seen.append(token)
    dupes = {s for s in seen if seen.count(s) > 1}
    assert not dupes, f'{name}: 同一比分出现多次 {sorted(dupes)}'


# --------------------------------------------------------------------------- #
# 3. 行数上限
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', sorted(SECTIONS))
def test_section_line_count_within_budget(name: str) -> None:
    lines = [ln for ln in SECTIONS[name](_pack()) if ln.strip()]
    budget = MAX_LINES[name]
    assert len(lines) <= budget, (
        f'{name}: {len(lines)} 行超过预算 {budget}。'
        '要么删冗余，要么显式改本文件的 MAX_LINES 并说明理由。'
    )


# --------------------------------------------------------------------------- #
# 4. 标题不重复段名
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', sorted(SECTIONS))
def test_title_line_does_not_repeat_the_section_name(name: str) -> None:
    lines = SECTIONS[name](_pack())
    title = lines[0]
    assert name in title
    # 标题之后的行不许再把段名复述一遍。
    for ln in lines[1:]:
        assert name not in ln, f'{name}: 正文重述段名 {ln!r}'


# --------------------------------------------------------------------------- #
# 5. 表格之外最多一行解读
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', sorted(SECTIONS))
def test_at_most_one_reading_line_outside_the_grid(name: str) -> None:
    """表格之外的长散文最多两行：一行汇总/口径 + 一行解读。

    判据不能按「行首是不是空格」过滤——解读行本身就以一个空格缩进，
    那样过滤等于把要守的东西先排除掉（实测过一次：整段加长到四行仍是绿的）。
    """
    lines = [ln for ln in SECTIONS[name](_pack()) if ln.strip()]
    long_prose = [
        ln for ln in lines
        if pipe.GRID_SEP not in ln and len(ln) > 60
    ]
    assert len(long_prose) <= 2, f'{name}: 表格外长句过多 {long_prose}'


# --------------------------------------------------------------------------- #
# 6. 单位在列头，不在格子里
# --------------------------------------------------------------------------- #
def test_goal_grid_does_not_repeat_the_mean_unit_per_cell() -> None:
    lines = DSA.format_market_goal_top_matrix_tg_lines(_pack(), home='曼城', away='阿森纳')
    mean_row = next(ln for ln in lines if ln.startswith('|   均'))
    assert '均18.05' not in mean_row, mean_row
    assert '18.05' in mean_row


def test_short_and_hot_grids_do_not_repeat_odds_per_score() -> None:
    for renderer in ('format_jingcai_short_wdl_matrix_tg_lines',
                     'format_jingcai_hot_wdl_matrix_tg_lines'):
        text = '\n'.join(getattr(DSA, renderer)(_pack(), home='曼城', away='阿森纳'))
        assert '@' not in text, f'{renderer}: 比分后仍带 @赔率（31 项表已给过）'


def test_31_table_units_live_in_the_header_only() -> None:
    lines = DSA.format_market_vs_jingcai_odds_tg_lines(_pack(), home='曼城', away='阿森纳')
    header = next(ln for ln in lines if ln.startswith('| 比分'))
    assert '市均赔率' in header and '竞彩赔率' in header and '市-竞差' in header
    for ln in lines:
        if pipe.GRID_SEP in ln and ln.startswith('|') and not ln.startswith('| 比分'):
            assert '赔率' not in ln, f'格子里重复了单位: {ln!r}'


# --------------------------------------------------------------------------- #
# 7. 防过度删减：用户点名的数字锚一个都不能少
# --------------------------------------------------------------------------- #
def test_user_named_number_anchors_are_all_present() -> None:
    pack = _pack()
    body = '\n'.join(DSA.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=DSA.format_theo_mkt_31_tg_lines(pack),
        ds_payload={'theo_mkt_31_pack': pack},
        heat_tg_lines=['HEATM'],
        home='曼城', away='阿森纳',
    ))
    # 31 项全表：31 个比分都要在。
    for score in MARKET:
        assert score in body, f'缺比分 {score}'
    # 市短 N 项且列全：清单行的项数等于它的长度。
    short_line = next(ln for ln in body.splitlines() if '市短共' in ln)
    claimed = int(re.search(r'市短共(\d+)项', short_line).group(1))
    assert claimed == len([s for s in short_line.split('：', 1)[1].split('/') if s.strip()])
    # 三向计数、均市-竞、首位对次位比值、两两比对得分、对抗
    for token in ('/13', '/5', '均市-竞', '对次位', '得分胜', '对抗'):
        assert token in body, token
    # 少球/多球均值与占比、15 项入选
    for token in ('少球', '多球', '均赔', '入选'):
        assert token in body, token
