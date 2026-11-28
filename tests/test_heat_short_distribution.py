"""`【市热·市短分布｜冷门窗口】` 的守卫。

这一段把 31 项按 `市均 − 竞彩` 的符号切成市热 / 市短两组，再给出
两个**真差集**、冷门候选与一句由数据生成的解读。

三条最容易写错的规矩，这里逐条钉住：

  1. 判据只有一份（`DeepSeekAnalyzer.split_hot_short`），★市短 / 市热 /
     本段共用。三处各写一份 `diff > 0`，迟早某处把 0 或 None 算进去。
  2. 差恰为 0 的项**两边都不算**。样本 11 + 14 = 25，缺的 6 项正是
     5-0/5-1/5-2 与 0-5/1-5/2-5。
  3. 冷门候选不是「方向 ≠ 市热头向」，而是「方向与市热头向**相反**」再剔除
     `XX其它` 汇总格。用户正文写的是 `≠`，但那个写法在样本上得 10 项，
     样本列的是 8 项，且样本末句写的是「相反」。
"""
from __future__ import annotations

import importlib
import io
import contextlib
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
ALL31 = pipe.JCZQ_ODDS_ORDER

HOME, AWAY = '曼城', '阿森纳'

#: 样本逐项清单（用户给的原文，已逐字核对）。
SAMPLE_HOT = [
    '3-2', '1-1', '2-2', '0-2', '0-3', '1-2', '1-3', '0-4', '1-4', '2-3', '负其它',
]
SAMPLE_SHORT = [
    '1-0', '2-0', '2-1', '3-0', '3-1', '4-0', '4-1', '4-2', '胜其它',
    '0-0', '3-3', '平其它', '0-1', '2-4',
]
#: 差恰为 0 的六项：市均与竞彩同价，两边都不算。
SAMPLE_EDGE = ['5-0', '5-1', '5-2', '0-5', '1-5', '2-5']

#: 市均赔率。样本只给了「哪些项是市热/市短」，值取一组可复算的合理价。
MARKET: dict[str, float] = {
    '1-0': 14.55, '2-0': 18.30, '2-1': 10.11, '3-0': 27.18, '3-1': 16.45,
    '4-0': 54.23, '4-1': 32.46, '4-2': 33.36, '胜其它': 47.10,
    '0-0': 18.05, '3-3': 21.58, '平其它': 72.49, '0-1': 12.84, '2-4': 26.95,
    '3-2': 16.82, '1-1': 8.05, '2-2': 9.79, '0-2': 14.62, '0-3': 21.06,
    '1-2': 9.10, '1-3': 13.30, '0-4': 37.63, '1-4': 24.29, '2-3': 14.86,
    '负其它': 41.07,
    '5-0': 80.0, '5-1': 70.0, '5-2': 88.0, '0-5': 60.0, '1-5': 44.0, '2-5': 52.0,
}


def _pack(hot: list[str], short: list[str], edge: list[str] | None = None,
          market: dict[str, float] | None = None,
          hot_step: float = 1.0, short_step: float = 1.0) -> dict:
    """按「哪些项热 / 哪些项短」反算竞彩，走生产同一个 31 全表构建器。

    `hot_step` / `short_step` 让同一组内的差**不相等**，否则组内排序断言
    （降序/升序）会因为所有差都一样而没有区分力 —— 实测踩过。
    """
    market = dict(market or MARKET)
    edge = list(edge if edge is not None else SAMPLE_EDGE)
    assert set(hot) | set(short) | set(edge) == set(market), '夹具必须覆盖全部 31 项'
    assert not (set(hot) & set(short)), '同一项不能既是市热又是市短'
    jc = dict(market)
    for i, s in enumerate(hot):
        jc[s] = round(market[s] - hot_step * (i + 1), 2)
    for i, s in enumerate(short):
        jc[s] = round(market[s] + short_step * (i + 1), 2)
    return DSA.build_theo_mkt_31_pack(
        [], jc_score_odds=jc, extra_market_odds=market,
        lam_home=1.45, lam_away=1.10,
        market_provenance={k: 'desktop_cs' for k in market},
    )


def _sample_pack() -> dict:
    return _pack(SAMPLE_HOT, SAMPLE_SHORT)


def _lines(pack: dict, **kwargs) -> list[str]:
    kwargs.setdefault('home', HOME)
    kwargs.setdefault('away', AWAY)
    return list(DSA.format_hot_short_distribution_tg_lines(pack, **kwargs))


def _field(lines: list[str], prefix: str) -> str:
    hit = next((ln for ln in lines if ln.strip().startswith(prefix)), None)
    assert hit is not None, f'没有 {prefix!r} 这一行: {lines}'
    return hit.strip()


def _reading(lines: list[str]) -> str:
    """末行是唯一的一段解读（简洁后不再带 `分布解读：` 前缀）。"""
    return lines[-1].strip()


def _score_seq(lines: list[str], label: str) -> list[str]:
    return _field(lines, label).split('：', 1)[1].split('/')


# --------------------------------------------------------------------------- #
# 段名与登记
# --------------------------------------------------------------------------- #
def test_section_name_is_literal_fullwidth_bar() -> None:
    lines = _lines(_sample_pack())
    assert lines[0] == '【市热·市短分布｜冷门窗口】'
    # 全角竖线 U+FF5C，不是 ASCII `|`。
    assert '\uff5c' in lines[0]
    assert '|' not in lines[0]


def test_section_is_registered() -> None:
    assert '【市热·市短分布｜冷门窗口】' in DSA.REGISTERED_TG_SECTIONS
    assert '【市热·市短分布｜冷门窗口】' not in DSA.REQUIRED_TG_SECTIONS


def test_existing_heat_and_short_sections_are_untouched() -> None:
    """`市短矩阵｜市均隐含 > 竞彩隐含` 与 `市热超理论专表` 的名字/口径不动。"""
    source = (ROOT / 'blade_deepseek_pipeline.py').read_text(encoding='utf-8')
    assert '📊【市短矩阵｜市均隐含 > 竞彩隐含】' in source
    assert '📉【市热超理论专表】' in source


# --------------------------------------------------------------------------- #
# 样本锚：计数、两个方向、两个差集、冷门候选
# --------------------------------------------------------------------------- #
def test_sample_counts_match_verbatim() -> None:
    lines = _lines(_sample_pack())
    assert _field(lines, '市热(11)') == '市热(11)：胜1 平2 负8｜头向【负】（8/11）', lines
    assert _field(lines, '★市短(14)') == '★市短(14)：胜9 平3 负2｜头向【胜】（9/14）', lines


def test_sample_score_lists_match_as_sets() -> None:
    """两个比分列与样本逐项一致（组内顺序由下面的排序用例单独钉）。"""
    lines = _lines(_sample_pack())
    hot = set(_field(lines, '市热比分：').split('：', 1)[1].split('/'))
    short = set(_field(lines, '市短比分：').split('：', 1)[1].split('/'))
    assert hot == set(SAMPLE_HOT), hot ^ set(SAMPLE_HOT)
    assert short == set(SAMPLE_SHORT), short ^ set(SAMPLE_SHORT)


def test_sample_differences_and_candidates_match() -> None:
    lines = _lines(_sample_pack())
    # 市热/市短按定义不相交，所以差集与「市热比分 / 市短比分」完全相同：
    # 简洁后那两行**不再重复打印**（同一串比分只出现一次）。
    assert not any(ln.strip().startswith(('热而非短', '短而非热')) for ln in lines), lines
    hot = _score_seq(lines, '市热比分：')
    short = _score_seq(lines, '市短比分：')
    assert len(hot) == 11 and set(hot) == set(SAMPLE_HOT)
    assert len(short) == 14 and set(short) == set(SAMPLE_SHORT)
    cand = _score_seq(lines, '冷门候选（非终裁）：')
    assert cand == ['1-0', '2-0', '2-1', '3-0', '3-1', '4-0', '4-1', '4-2'], cand


def test_edge_items_are_counted_on_neither_side() -> None:
    """11 + 14 = 25，缺的 6 项必须是那六个边界格。"""
    parts = DSA.split_hot_short(DSA._raw_odds_rows(_sample_pack()))
    assert len(parts['hot']) == 11 and len(parts['short']) == 14
    assert {r['score'] for r in parts['edge']} == set(SAMPLE_EDGE)
    assert len(parts['hot']) + len(parts['short']) + len(parts['edge']) == 31


def test_hot_and_short_are_complementary_and_disjoint() -> None:
    rows = DSA._raw_odds_rows(_sample_pack())
    parts = DSA.split_hot_short(rows)
    hot = {r['score'] for r in parts['hot']}
    short = {r['score'] for r in parts['short']}
    nonzero = {r['score'] for r in rows if r['diff'] not in (None, 0.0)}
    assert not (hot & short)
    assert hot | short == nonzero
    assert len(nonzero) == 25


def test_differences_are_real_set_operations() -> None:
    """差集由集合运算得出，且与集合的关系仍然成立。

    简洁后两个差集行只在「有重叠」时才打印；这里直接验底层关系：
    热而非短 = 市热 − 市短，短而非热 = 市短 − 市热，两集合不相交。
    """
    rows = DSA._raw_odds_rows(_sample_pack())
    parts = DSA.split_hot_short(rows)
    hot = {str(r['score']) for r in parts['hot']}
    short = {str(r['score']) for r in parts['short']}
    only_hot = hot - short
    only_short = short - hot
    assert not (only_hot & only_short)
    assert only_hot | (hot & short) == hot
    assert only_short | (hot & short) == short
    # 本夹具两个集合本来就互斥，所以差集等于原集合。
    assert only_hot == hot and only_short == short


def test_reading_sentence_is_generated_from_data() -> None:
    lines = _lines(_sample_pack())
    reading = _reading(lines)
    for token in (
        '市热11项',
        '头向【负】（8/11）',
        '市短14项',
        '头向【胜】（9/14）',
        '两市方向错位',
        '短而非热14项、热而非短11项',
        '优先选择短而非热比分',
        '冷门候选8项',
        '不改方向锁',
    ):
        assert token in reading, (token, reading)
    assert '。；' not in reading and not reading.endswith('。。')


# --------------------------------------------------------------------------- #
# 排序：市热组内按差**降序**、市短组内按差**升序**
# --------------------------------------------------------------------------- #
def test_hot_groups_are_sorted_by_diff_descending() -> None:
    pack = _sample_pack()
    rows = {r['score']: r for r in DSA._raw_odds_rows(pack)}
    seq = _field(_lines(pack), '市热比分：').split('：', 1)[1].split('/')
    # 组内（胜/平/负各自）按差降序，最正的最前。
    for group in ('胜', '平', '负'):
        diffs = [rows[s]['diff'] for s in seq if rows[s]['wdl'] == group]
        assert diffs == sorted(diffs, reverse=True), (group, diffs)


def test_short_groups_are_sorted_by_diff_ascending() -> None:
    pack = _sample_pack()
    rows = {r['score']: r for r in DSA._raw_odds_rows(pack)}
    seq = _field(_lines(pack), '市短比分：').split('：', 1)[1].split('/')
    for group in ('胜', '平', '负'):
        diffs = [rows[s]['diff'] for s in seq if rows[s]['wdl'] == group]
        assert diffs == sorted(diffs), (group, diffs)


def test_short_list_leading_item_is_the_most_negative() -> None:
    rows = {r['score']: r for r in DSA._raw_odds_rows(_sample_pack())}
    seq = _field(_lines(_sample_pack()), '市短比分：').split('：', 1)[1].split('/')
    assert rows[seq[0]]['diff'] == min(
        rows[s]['diff'] for s in seq if rows[s]['wdl'] == rows[seq[0]]['wdl']
    )


# --------------------------------------------------------------------------- #
# 头向：并列时不许静默取第一个
# --------------------------------------------------------------------------- #
def test_leading_direction_is_the_max_not_the_first() -> None:
    lead = DSA.leading_direction([
        {'score': '1-0', 'wdl': '胜'}, {'score': '0-1', 'wdl': '负'},
        {'score': '0-2', 'wdl': '负'},
    ])
    assert lead['direction'] == '负' and lead['count'] == 2 and not lead['tied']


def test_leading_direction_tie_uses_fixed_priority_and_says_so() -> None:
    lead = DSA.leading_direction([
        {'score': '1-0', 'wdl': '胜'}, {'score': '0-1', 'wdl': '负'},
    ])
    assert lead['tied'] is True
    assert lead['leaders'] == ['胜', '负']
    assert lead['direction'] == '胜', '并列按固定优先级 胜>平>负 取，但必须标并列'
    # 渲染层要把「并列」写出来，读者才不会以为它是唯一领先。
    # 两个集合各只有一项，头向在各自集合内并列。
    lines = _lines(_pack(['1-0', '0-2'], ['0-1', '1-2'],
                         [s for s in MARKET if s not in ('1-0', '0-2', '0-1', '1-2')]))
    text = '\n'.join(lines)
    assert '并列' in text, text


# --------------------------------------------------------------------------- #
# 冷门候选：相反方向 + 剔除汇总格；空集有专句
# --------------------------------------------------------------------------- #
def test_candidates_keep_opposite_direction_and_drop_summary_buckets() -> None:
    """9 项「与负向相反且市短」里，去掉 `胜其它` 正好 8 项。"""
    lines = _lines(_sample_pack())
    cand = _field(lines, '冷门候选（非终裁）：').split('：', 1)[1].split('/')
    assert '胜其它' not in cand
    assert len(cand) == 8
    # 平向的 0-0 / 3-3 不算「相反」，所以不在候选里。
    assert '0-0' not in cand and '3-3' not in cand


def test_candidates_empty_says_so_explicitly() -> None:
    """市短里没有「与市热头向相反」的项时，必须写「无（判据下无符合项）」。

    市热全在负向（头向负），市短也只给负向项 —— 相反方向是胜，一项都没有。
    """
    pack = _pack(['0-2', '0-3'], ['0-1', '1-2'],
                 [s for s in MARKET if s not in ('0-2', '0-3', '0-1', '1-2')])
    lines = _lines(pack)
    text = '\n'.join(lines)
    assert '冷门候选' in text, text
    assert '无（判据下无符合项）' in text, text
    assert not re.search(r'冷门候选（非终裁）：\s*\n', text), text


def test_reading_says_same_direction_when_headings_agree() -> None:
    """两个头向相同时写「同向」，不同时写「错位」。"""
    mismatched = _pack(['1-0', '2-0', '0-2'], ['0-1', '1-2'],
                       [s for s in MARKET if s not in ('1-0', '2-0', '0-2', '0-1', '1-2')])
    reading = _reading(_lines(mismatched))
    assert '两市方向错位' in reading, reading

    # 市热头向胜（2/2）、市短头向胜（2/2）→ 同向。
    same = _pack(['1-0', '2-0'], ['2-1', '3-0'],
                 [s for s in MARKET if s not in ('1-0', '2-0', '2-1', '3-0')])
    reading_same = _reading(_lines(same))
    assert '两市方向同向' in reading_same, reading_same
    assert '两市方向错位' not in reading_same, reading_same


# --------------------------------------------------------------------------- #
# 两套数据 → 解读必须跟着变（防写死）
# --------------------------------------------------------------------------- #
def test_reading_changes_between_short_heavy_and_hot_heavy() -> None:
    short_heavy = _lines(_sample_pack())
    hot_heavy = _lines(_pack(
        ['0-2', '0-3', '1-2', '1-3', '2-3', '负其它'],
        ['1-0', '2-0', '0-1'],
        [s for s in MARKET if s not in (
            '0-2', '0-3', '1-2', '1-3', '2-3', '负其它', '1-0', '2-0', '0-1')],
    ))
    a = _reading(short_heavy)
    b = _reading(hot_heavy)
    assert a != b, '换数据后解读没变，说明写死了'
    assert '短而非热14项、热而非短11项' in a
    assert '短而非热3项、热而非短6项' in b, b
    assert '热而非短更多，短而非热不占优' in b, b
    assert '市热6项' in b and '市短3项' in b, b


# --------------------------------------------------------------------------- #
# 降级路径
# --------------------------------------------------------------------------- #
def test_unformed_is_all_chinese_when_both_sides_empty() -> None:
    pack = _pack([], [], list(MARKET))
    lines = _lines(pack)
    assert lines[0] == '【市热·市短分布｜冷门窗口】'
    assert lines[1] == '  未形成（市热与市短均无项）', lines
    # 降级句里不许出现英文 token。
    assert not re.search(r'[A-Za-z]{3,}', lines[1]), lines[1]


def test_no_direction_lock_mutation() -> None:
    pack = _sample_pack()
    payload = {
        'theo_mkt_31_pack': pack,
        'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-1', '1-0']},
        'first_score': '2-1',
    }
    before = (payload['direction_lock']['direction'], payload['first_score'],
              tuple(payload['direction_lock']['scores']))
    DSA.format_hot_short_distribution_tg_lines(pack, **payload)
    DSA.build_deepseek_only_tg_sections(ds_payload=payload)
    after = (payload['direction_lock']['direction'], payload['first_score'],
             tuple(payload['direction_lock']['scores']))
    assert after == before


# --------------------------------------------------------------------------- #
# 装配：五段都在，且本节排在第四段之后
# --------------------------------------------------------------------------- #
def test_section_reaches_the_report_after_the_fourth_section() -> None:
    pack = _sample_pack()
    text = '\n'.join(DSA.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=DSA.format_theo_mkt_31_tg_lines(pack),
        ds_payload={'theo_mkt_31_pack': pack},
        mkt_over_theo_tg_lines=['📉【市热超理论专表】', '  条数: 8'],
        heat_tg_lines=['HEATM'],
        home=HOME, away=AWAY,
    ))
    order = [
        text.find(n) for n in (
            '31项市均×竞彩对比表', '市均***竞彩对比表', '市短矩阵',
            '市热矩阵', '★市短·胜平负比分矩阵', '市热·胜平负比分矩阵',
            '【市热·市短分布｜冷门窗口】', '市热超理论专表',
            '市均前15·进球赔率矩阵',
        )
    ]
    assert all(i >= 0 for i in order), order
    assert order == sorted(order), order
    assert not DSA._section_is_empty(text, '【市热·市短分布｜冷门窗口】')
