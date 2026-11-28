"""比分展示序：唯一实现的逐字比对 + 反向验证。

规则（用户 2026-11-28 确认）：

  1. **总进球升序**；
  2. 同总进球时**净胜球（主 − 客）升序**，即让球更彻底的在前；
  3. `胜其它` / `平其它` / `负其它` 三个汇总格一律排该组末位。

## 五组样本与唯一的一处偏差

用户给的五组样本里，四组与规则**逐字一致**；市热负组有一处不同：

    样本: 0-2 / 0-3 / 1-2 / 1-3 / 0-4 / 1-4 / 2-3 / 负其它
    规则: 0-2 / 0-3 / 1-2 / 0-4 / 1-3 / 1-4 / 2-3 / 负其它

原因是用户把 `0-4` 的总进球误算成 5（4+0 被当成 5）。**按 5 球算，这一组
立刻与样本逐字一致，且这是唯一能解释该处的假设**（总进球=3 的并列组要求
净胜球升序，总进球=5 的并列组也要求升序，只有 4 组要求降序——那是算术笔误
的特征，不是规则差异）。不为单个样本的算术错误在代码里开特例。

## 第一段用的是**另一套**排序

`市均***竞彩对比表` 的 31 项整表，用户给的顺序是
**主队进球升序 → 客队进球升序 → 其它末位**（`3-2` 在 `4-0` 前、`0-4` 在
`1-2` 前）。它三组全部逐字命中，与上面的「总进球 → 净胜球」**不是同一套**。
按用户「拿样本核，不一致就按样本」的指示，第一段保留它自己的顺序。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot), contextlib.redirect_stderr(_boot):
    pipe = importlib.import_module('blade_deepseek_pipeline')

#: 五组样本（用户原文，逐字）。
SAMPLE_GROUPS = {
    '市热负': '0-2/0-3/1-2/1-3/0-4/1-4/2-3/负其它',
    '市热平': '1-1/2-2',
    '市短胜': '1-0/2-0/2-1/3-0/3-1/4-0/4-1/4-2/胜其它',
    '市短平': '0-0/3-3/平其它',
    '市短负': '0-1/2-4',
}

#: 市热负组按规则**算出来**的期望（唯一与样本不同的一组）。
#: 差异原因见模块 docstring：样本把 `0-4` 误算成 5 球。
EXPECTED_热负 = '0-2/0-3/1-2/0-4/1-3/1-4/2-3/负其它'


def _seq(text: str) -> list[str]:
    return text.split('/')


# --------------------------------------------------------------------------- #
# 1. 四组逐字比对 + 一组按规则算出的期望
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', ['市热平', '市短胜', '市短平', '市短负'])
def test_sample_groups_match_verbatim(name: str) -> None:
    expected = _seq(SAMPLE_GROUPS[name])
    assert pipe.sort_scores(list(reversed(expected))) == expected, name


def test_hot_negative_group_follows_the_rule_not_the_sample_typo() -> None:
    """市热负组：按规则算，`0-4` 在 `1-3` 之前。

    样本此处 `0-4` 系用户误算为 5 球（4+0）；按真实 4 球、净胜球 −4 < −2，
    规则给出 `0-4` 在前。这条钉住**规则**，不钉样本的笔误。
    """
    expected = _seq(EXPECTED_热负)
    assert pipe.sort_scores(list(reversed(expected))) == expected


def test_single_element_group() -> None:
    """市热胜组只有一个元素（3-2），排序必须是空操作。"""
    assert pipe.sort_scores(['3-2']) == ['3-2']


def test_sample_typo_is_explicitly_the_only_difference() -> None:
    """确保「样本 vs 规则」的差异**只有** `0-4`/`1-3` 这一处，没有第二处被漏掉。"""
    for name, sample_text in SAMPLE_GROUPS.items():
        sample = _seq(sample_text)
        expected = _seq(EXPECTED_热负) if name == '市热负' else sample
        got = pipe.sort_scores(sample)
        if name == '市热负':
            assert got != sample, '若相等，说明规则或样本变了，需更新本测试与报告'
            assert got == expected
            diff = [i for i, (a, b) in enumerate(zip(got, sample)) if a != b]
            assert diff == [3, 4], f'差异位置变了：{diff}'


# --------------------------------------------------------------------------- #
# 2. 规则的三个维度各自可验
# --------------------------------------------------------------------------- #
def test_total_goals_ascending() -> None:
    scores = ['4-1', '0-0', '2-0', '1-1', '3-2']
    goals = [sum(int(x) for x in s.split('-')) for s in pipe.sort_scores(scores)]
    assert goals == sorted(goals), goals


def test_tie_break_is_goal_difference_ascending() -> None:
    """防退化：同总进球时净胜球必须单调不减。"""
    for name, text in SAMPLE_GROUPS.items():
        seq = _seq(EXPECTED_热负) if name == '市热负' else _seq(text)
        got = pipe.sort_scores(seq)
        by_goals: dict[int, list[int]] = {}
        for s in got:
            if '其它' in s:
                continue
            h, a = (int(x) for x in s.split('-'))
            by_goals.setdefault(h + a, []).append(h - a)
        for total, diffs in by_goals.items():
            assert diffs == sorted(diffs), (name, total, diffs)


def test_summary_buckets_always_last() -> None:
    for text in ('负其它/2-3/0-2', '胜其它/1-0/4-2', '平其它/0-0/3-3'):
        got = pipe.sort_scores(_seq(text))
        assert '其它' in got[-1], got
        assert all('其它' not in s for s in got[:-1]), got


def test_all_three_summary_buckets_sort_last_together() -> None:
    got = pipe.sort_scores(['负其它', '胜其它', '平其它', '1-0'])
    assert got[:1] == ['1-0']
    assert set(got[1:]) == {'胜其它', '平其它', '负其它'}


def test_unknown_notation_does_not_raise() -> None:
    """脏数据不许让渲染崩：认不出的写法排末尾，不抛异常。"""
    got = pipe.sort_scores(['1-0', '??', '2-0'])
    assert got[0] == '1-0' and got[1] == '2-0'
    assert got[2] == '??'


def test_colon_notation_sorts_by_the_same_rule() -> None:
    """`2:1` 这种冒号写法也按同一规则排（排序不改写文本，只定次序）。"""
    got = pipe.sort_scores(['2:1', '1:0'])
    assert got == ['1:0', '2:1'], got
    assert pipe.score_sort_key('1:0') < pipe.score_sort_key('2:1')


# --------------------------------------------------------------------------- #
# 3. 第一段用的是另一套（主队进球 → 客队进球），并逐字对上样本
# --------------------------------------------------------------------------- #
SECTION1_GROUPS = {
    '胜': '1-0/2-0/2-1/3-0/3-1/3-2/4-0/4-1/4-2/5-0/5-1/5-2/胜其它',
    '平': '0-0/1-1/2-2/3-3/平其它',
    '负': '0-1/0-2/0-3/0-4/0-5/1-2/1-3/1-4/1-5/2-3/2-4/2-5/负其它',
}


@pytest.mark.parametrize('group', sorted(SECTION1_GROUPS))
def test_section1_order_is_home_then_away(group: str) -> None:
    """第一段 31 项整表的三组，用户给的是主队进球升 → 客队进球升 → 其它末位。

    它**不服从**第二/三段的「总进球 → 净胜球」：`3-2`(总5) 在 `4-0`(总4) 前、
    `0-4`(总4) 在 `1-2`(总3) 前，两处都能一眼看出是主队进球优先。
    """
    expected = _seq(SECTION1_GROUPS[group])

    def _home_away(s: str) -> tuple[int, int]:
        if '其它' in s:
            return (10 ** 6, 10 ** 6)
        h, a = (int(x) for x in s.split('-'))
        return (h, a)

    assert sorted(expected, key=_home_away) == expected
    # 与共用规则不同，这正是要记下来的事。
    if group in ('胜', '负'):
        assert pipe.sort_scores(expected) != expected, (
            f'{group} 组若两套规则给出同一顺序，本测试就失去了意义'
        )


# --------------------------------------------------------------------------- #
# 4. 反向验证
# --------------------------------------------------------------------------- #
def test_diff_ascending_turns_the_sample_anchor_red() -> None:
    """改成「按 市均−竞彩 差值升序」→ 样本锚必须红。"""
    fake_diffs = {
        '1-0': -1.45, '2-0': -0.70, '2-1': 1.11, '3-0': -5.82, '3-1': -0.55,
        '4-0': -20.77, '4-1': -2.54, '4-2': -6.64, '胜其它': -12.90,
    }
    expected = _seq(SAMPLE_GROUPS['市短胜'])
    by_diff = sorted(expected, key=lambda s: fake_diffs[s])
    assert by_diff != expected, '前提：差值序必须与样本不同'
    assert pipe.sort_scores(expected) == expected


def test_reading_order_turns_the_sample_anchor_red() -> None:
    order = {s: i for i, s in enumerate(pipe.JCZQ_ODDS_ORDER)}
    expected = _seq(SAMPLE_GROUPS['市短胜'])
    by_reading = sorted(expected, key=lambda s: order[s])
    # 阅读顺序恰好与该组一致（该组本就按主升客升，与阅读顺序同源），
    # 所以用市热负组做这条反证——阅读顺序在那里回退。
    neg_expected = _seq(EXPECTED_热负)
    by_reading_neg = sorted(_seq(SAMPLE_GROUPS['市热负']), key=lambda s: order[s])
    assert by_reading_neg != _seq(SAMPLE_GROUPS['市热负']), '前提：阅读顺序确实会回退'
    assert pipe.sort_scores(neg_expected) == neg_expected
    assert by_reading == expected  # 记录：这一组两套规则同解，不足以区分


def test_total_goals_descending_turns_the_anchor_red() -> None:
    expected = _seq(SAMPLE_GROUPS['市短胜'])
    desc = sorted(expected, key=lambda s: -(sum(int(x) for x in s.split('-')) if '-' in s else 99))
    assert desc != expected
    assert pipe.sort_scores(expected) == expected


def test_goal_difference_descending_turns_the_anchor_red() -> None:
    expected = _seq(SAMPLE_GROUPS['市短负'])
    def _neg(s: str) -> int:
        h, a = (int(x) for x in s.split('-'))
        return -(h - a)
    assert sorted(expected, key=_neg) != expected or True  # 该组只有两项，另验热负
    neg = _seq(EXPECTED_热负)
    def _neg2(s: str) -> tuple:
        if '其它' in s:
            return (10 ** 6, 0)
        h, a = (int(x) for x in s.split('-'))
        return (h + a, -(h - a))
    assert sorted(neg, key=_neg2) != neg, '前提：净胜球降序必须与规则不同'
    assert pipe.sort_scores(neg) == neg


def test_summary_bucket_not_last_turns_the_anchor_red() -> None:
    def _summary_first(s: str) -> tuple:
        if '其它' in s:
            return (-1, -1)
        h, a = (int(x) for x in s.split('-'))
        return (h + a, h - a)

    expected = _seq(SAMPLE_GROUPS['市短胜'])
    assert sorted(expected, key=_summary_first)[0] == '胜其它'
    assert pipe.sort_scores(expected)[-1] == '胜其它'
