"""反向验证：把每条被断言的行为故意改坏，对应测试必须变红。

这不是一条普通单测，而是**证据生成器**：它把 `blade_deepseek_pipeline.py`
的源码做一次定点替换、编译成内存模块、再跑指定的测试函数，断言那次运行失败。

存在的理由：一条测试通过只能证明「现在是对的」，不能证明「改坏了会红」。
上一轮的假绿正是「测试写了但抓不住缺陷」——比如把 31 项表的行数断言写成
`len(rows) <= 31`，表只剩 3 行它也是绿的。这里把每一种坏法都真的跑一遍。

每条反证都写明：改哪里 → 哪条测试该红。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = ROOT / 'blade_deepseek_pipeline.py'
TESTS = ROOT / 'tests' / 'test_market_vs_jingcai_table.py'


def _mutated_module(replacements: list[tuple[str, str]], name: str) -> types.ModuleType:
    """把源码按 (old, new) 逐条替换，编译成一个独立模块对象。"""
    source = MODULE.read_text(encoding='utf-8')
    for old, new in replacements:
        assert old in source, f'反证的锚点不存在（测试已过期）: {old!r}'
        assert source.count(old) == 1, f'反证的锚点不唯一: {old!r} 出现 {source.count(old)} 次'
        source = source.replace(old, new)
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(MODULE)
    exec(compile(source, str(MODULE), 'exec'), module.__dict__)
    return module


def _run_test(module: types.ModuleType, test_name: str) -> None:
    """在被改坏的模块上跑一条测试；测试从 `tests/` 里取，只换掉 pipeline。"""
    import importlib.util as _ilu

    saved = sys.modules.get('blade_deepseek_pipeline')
    saved_test = sys.modules.pop('test_market_vs_jingcai_table', None)
    sys.modules['blade_deepseek_pipeline'] = module
    try:
        spec = _ilu.spec_from_file_location('test_market_vs_jingcai_table', TESTS)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        # 测试模块在 import 时已把 `pipe` 绑到当前 sys.modules 里的那份，
        # 这里再确认一次，免得它绑到真模块上（那这条反证就变成自比）。
        assert mod.pipe is module, '测试没跑在被改坏的模块上，反证无效'
        getattr(mod, test_name)()
    finally:
        sys.modules.pop('test_market_vs_jingcai_table', None)
        if saved_test is not None:
            sys.modules['test_market_vs_jingcai_table'] = saved_test
        if saved is not None:
            sys.modules['blade_deepseek_pipeline'] = saved


# --------------------------------------------------------------------------- #
# 1. 段名换成 ×（不是三个半角星号）
# --------------------------------------------------------------------------- #
def test_wrong_star_section_name_turns_the_guard_red() -> None:
    """`***` → `×` 必须红。"""
    module = _mutated_module(
        [("head = '📈【市均***竞彩对比表】'", "head = '📈【市均×竞彩对比表】'")],
        'mut_star_name',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_new_section_names_are_literal_and_registered')


# --------------------------------------------------------------------------- #
# 2. 市短清单被截断（写死「只列 12 项」）
# --------------------------------------------------------------------------- #
def test_truncated_short_list_turns_the_count_guard_red() -> None:
    """`市短共N项` 只列前 12 项 → 项数三方不一致，必须红。"""
    module = _mutated_module(
        [("' / '.join(short_items))",
          "' / '.join(short_items[:12]))")],
        'mut_truncate',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_short_list_line_has_no_truncation_and_agrees_on_the_count')


# --------------------------------------------------------------------------- #
# 3. 市短判据反过来（>0 当成市短）
# --------------------------------------------------------------------------- #
def test_flipped_short_predicate_turns_the_guards_red() -> None:
    """共用判据里 `diff < 0` → `diff > 0`：市短变成市热，计数与样本锚全红。"""
    module = _mutated_module(
        [("            elif diff < 0:\n                short.append(row)",
          "            elif diff > 0:\n                short.append(row)")],
        'mut_short_pred',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_star_short_matches_the_sample_count_line')


def test_flipped_hot_predicate_turns_the_guards_red() -> None:
    """共用判据里 `diff > 0` → `diff < 0`：市热变成市短，同样必须红。"""
    module = _mutated_module(
        [("            if diff > 0:\n                hot.append(row)",
          "            if diff < 0:\n                hot.append(row)")],
        'mut_hot_pred',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_hot_counts_and_rows_match_the_sample')


# --------------------------------------------------------------------------- #
# 4. 两两比对规则反过来（大者 +1）
# --------------------------------------------------------------------------- #
def test_reversed_pairwise_rule_turns_the_sample_anchor_red() -> None:
    """绝对值大者 +1 → 样本锚 胜+0/平-2/负+2 变红。"""
    module = _mutated_module(
        [("""                if lv < rv:
                    score[left] += 1
                    score[right] -= 1
                else:
                    score[left] -= 1
                    score[right] += 1""",
          """                if lv < rv:
                    score[left] -= 1
                    score[right] += 1
                else:
                    score[left] += 1
                    score[right] -= 1""")],
        'mut_pairwise',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_pairwise_rule_minus_one_for_the_larger_abs')


# --------------------------------------------------------------------------- #
# 5. ★市短 比分列改成按市均降序
# --------------------------------------------------------------------------- #
def test_wrong_short_sort_turns_the_order_guard_red() -> None:
    module = _mutated_module(
        [("items = sorted(by_dir[d], key=lambda r: (r['diff'], JCZQ_ODDS_ORDER.index(r['score'])))",
          "items = sorted(by_dir[d], key=lambda r: (-(r['market'] or 0), "
          "JCZQ_ODDS_ORDER.index(r['score'])))")],
        'mut_short_sort',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_star_short_score_column_order_is_diff_ascending')


# --------------------------------------------------------------------------- #
# 6. 市热 比分列改成按市均降序
# --------------------------------------------------------------------------- #
def test_wrong_hot_sort_turns_the_order_guard_red() -> None:
    module = _mutated_module(
        [("items = sorted(by_dir[d], key=lambda r: (r['market'], JCZQ_ODDS_ORDER.index(r['score'])))",
          "items = sorted(by_dir[d], key=lambda r: (-(r['market'] or 0), "
          "JCZQ_ODDS_ORDER.index(r['score'])))")],
        'mut_hot_sort',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_hot_score_column_order_is_market_ascending')


# --------------------------------------------------------------------------- #
# 7. 市-竞 换成隐含概率差
# --------------------------------------------------------------------------- #
def test_probability_difference_turns_the_raw_odds_guard_red() -> None:
    module = _mutated_module(
        [("diff = round(market - jc, 2) if market is not None and jc is not None else None",
          "diff = round(100.0 / market - 100.0 / jc, 2) "
          "if market is not None and jc is not None else None")],
        'mut_prob_diff',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_diff_is_raw_odds_minus_not_implied_probability')


# --------------------------------------------------------------------------- #
# 8. 少球边界改成 ≤1
# --------------------------------------------------------------------------- #
def test_low_ball_boundary_change_turns_the_guards_red() -> None:
    module = _mutated_module(
        [("MARKET_GOAL_LOW_MAX: ClassVar[int] = 2", "MARKET_GOAL_LOW_MAX: ClassVar[int] = 1"),
         ("low = [r for r in selected if (_goal_bucket_of(r['score']) or '6球') in ('0球', '1球', '2球')]",
          "low = [r for r in selected if (_goal_bucket_of(r['score']) or '6球') in ('0球', '1球')]")],
        'mut_low_boundary',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_goal_low_ball_boundary_is_le_two')


# --------------------------------------------------------------------------- #
# 9. 均行改成「均值 ÷ 球数」
# --------------------------------------------------------------------------- #
def test_divided_mean_turns_the_guard_red() -> None:
    """把用户样本那个「÷球数」的算法改回来，必须红。"""
    module = _mutated_module(
        [("round(sum(r['market'] for r in entries) / len(entries), 2) if entries else None",
          "(round(sum(r['market'] for r in entries) / len(entries), 2)"
          " / max(1, int(column[0]) if column[0].isdigit() else 3)) if entries else None")],
        'mut_divided_mean',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_goal_mean_row_is_not_mean_divided_by_goal_count')


# --------------------------------------------------------------------------- #
# 10. 列宽算法改成 len（不区分中英宽度）
# --------------------------------------------------------------------------- #
def test_len_based_width_turns_the_alignment_guard_red() -> None:
    """把 `_display_width` 换成 `len`（不区分中英宽度）→ 对齐必须红。"""
    module = _mutated_module(
        [("    import unicodedata\n"
          "    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in str(text or ''))",
          "    return len(str(text or ''))")],
        'mut_len_width',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_goal_grid_is_visually_aligned')


# --------------------------------------------------------------------------- #
# 11. 形态编号写死成 3
# --------------------------------------------------------------------------- #
def test_hardcoded_shape_number_turns_the_guard_red() -> None:
    module = _mutated_module(
        [("        if shape.get('form_no') is None:",
          "        shape['form_no'] = 3\n        if shape.get('form_no') is None:")],
        'mut_shape_no',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_shape_is_never_numbered_without_a_confirmed_scheme')


# --------------------------------------------------------------------------- #
# 12. 把「市均最低前15」那句加回来
# --------------------------------------------------------------------------- #
def test_restored_top15_claim_turns_the_guard_red() -> None:
    module = _mutated_module(
        [("            ' 计数：' + ' '.join(f'{d}{len(by_dir[d])}' for d in ('胜', '平', '负'))",
          "            ' 计数：' + ' '.join(f'{d}{len(by_dir[d])}' for d in ('胜', '平', '负'))"
          " + '｜市均最低前15'")],
        'mut_top15_claim',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_hot_caliber_line_drops_the_false_top15_claim')


# --------------------------------------------------------------------------- #
# 13. 渲染函数偷偷改方向锁
# --------------------------------------------------------------------------- #
def test_mutating_direction_lock_turns_the_guard_red() -> None:
    """在 ★市短 渲染里塞一行改写 `direction`，不改方向锁的断言必须红。"""
    module = _mutated_module(
        [("        head = '★市短·胜平负比分矩阵'",
          "        head = '★市短·胜平负比分矩阵'\n"
          "        _lock = kwargs.get('direction_lock')\n"
          "        if isinstance(_lock, dict):\n"
          "            _lock['direction'] = '平'")],
        'mut_lock',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_rendering_never_touches_the_direction_lock')


# --------------------------------------------------------------------------- #
# 14. 队名缺失时写「未知 对 未知」
# --------------------------------------------------------------------------- #
def test_placeholder_team_line_turns_the_guard_red() -> None:
    module = _mutated_module(
        [("        return [f' {h} 对 {a}'] if h and a else []",
          "        return [f' {h or \"未知\"} 对 {a or \"未知\"}']")],
        'mut_team_fallback',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_team_line_is_omitted_when_names_are_absent')


# --------------------------------------------------------------------------- #
# 15. 通道取不到时编一个值
# --------------------------------------------------------------------------- #
def test_invented_channel_turns_the_guard_red() -> None:
    module = _mutated_module(
        [("return (source or '—'), (channel or '—')",
          "return (source or '—'), (channel or '直连')")],
        'mut_channel',
    )
    with pytest.raises(AssertionError):
        _run_test(module, 'test_source_and_channel_come_from_the_record_or_dash')
