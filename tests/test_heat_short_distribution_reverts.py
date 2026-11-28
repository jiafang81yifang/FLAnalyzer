"""`【市热·市短分布｜冷门窗口】` 的反向验证：改坏一处，对应测试必须红。

用户第 7 条列了六种坏法，这里每一条都真跑一遍——不是「写了断言就算数」，
而是把源码做一次定点替换、编译成独立模块、再跑目标测试，断言那次运行失败。
一条测试通过只证明「现在对」，证明不了「改坏了会红」。

每条反证都写明：改哪里 → 哪条必须红。
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE = ROOT / 'blade_deepseek_pipeline.py'
TESTS = ROOT / 'tests' / 'test_heat_short_distribution.py'


def _mutated(replacements: list[tuple[str, str]], name: str) -> types.ModuleType:
    source = MODULE.read_text(encoding='utf-8')
    for old, new in replacements:
        assert old in source, f'反证的锚点不存在（测试已过期）: {old!r}'
        assert source.count(old) == 1, f'反证的锚点不唯一: {old!r} × {source.count(old)}'
        source = source.replace(old, new)
    spec = _ilu.spec_from_loader(name, loader=None)
    module = _ilu.module_from_spec(spec)
    module.__file__ = str(MODULE)
    exec(compile(source, str(MODULE), 'exec'), module.__dict__)
    return module


def _run(module: types.ModuleType, test_name: str) -> None:
    saved_mod = sys.modules.get('blade_deepseek_pipeline')
    saved_test = sys.modules.pop('test_heat_short_distribution', None)
    sys.modules['blade_deepseek_pipeline'] = module
    try:
        spec = _ilu.spec_from_file_location('test_heat_short_distribution', TESTS)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert mod.pipe is module, '测试没跑在被改坏的模块上，反证无效'
        getattr(mod, test_name)()
    finally:
        sys.modules.pop('test_heat_short_distribution', None)
        if saved_test is not None:
            sys.modules['test_heat_short_distribution'] = saved_test
        if saved_mod is not None:
            sys.modules['blade_deepseek_pipeline'] = saved_mod


# --------------------------------------------------------------------------- #
# 1. 头向改成「取第一个方向」而不是「取最大」
# --------------------------------------------------------------------------- #
def test_first_direction_instead_of_max_turns_the_guard_red() -> None:
    module = _mutated(
        [("        leaders = [d for d in ('胜', '平', '负') if counts[d] == top] if top else []",
          "        leaders = [d for d in ('胜', '平', '负') if counts[d] > 0][:1] if top else []")],
        'mut_first_dir',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_leading_direction_is_the_max_not_the_first')


# --------------------------------------------------------------------------- #
# 2. 热而非短 改成硬编码列表
# --------------------------------------------------------------------------- #
def test_hardcoded_only_hot_turns_the_guard_red() -> None:
    module = _mutated(
        [("        only_hot = [s for s in hot_seq if s not in short_scores]",
          "        only_hot = ['3-2', '1-1', '2-2', '0-2', '0-3', '0-4', "
          "'1-2', '1-3', '1-4', '2-3', '负其它']")],
        'mut_hardcoded_only_hot',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_reading_changes_between_short_heavy_and_hot_heavy')


# --------------------------------------------------------------------------- #
# 3. 去掉「剔除 XX其它」这条规则
# --------------------------------------------------------------------------- #
def test_keeping_summary_buckets_turns_the_guard_red() -> None:
    module = _mutated(
        [("            if opposite and r.get('wdl') == opposite and '其它' not in str(r['score'])",
          "            if opposite and r.get('wdl') == opposite")],
        'mut_keep_other',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_candidates_keep_opposite_direction_and_drop_summary_buckets')


# --------------------------------------------------------------------------- #
# 4. 市热组内排序改成升序
# --------------------------------------------------------------------------- #
def test_reading_order_instead_of_the_shared_rule_turns_the_guard_red() -> None:
    """把组内排序换回阅读顺序 → 共用规则守卫必须红。

    这是用户明确作废的旧实现之一（另一处是「按差值」）。
    """
    module = _mutated(
        [("                out.extend(sort_scores(str(r['score']) for r in by_dir[d]))",
          "                out.extend(str(r['score']) for r in sorted("
          "by_dir[d], key=lambda r: JCZQ_ODDS_ORDER.index(str(r['score']))))")],
        'mut_reading_order',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_hot_groups_follow_the_shared_score_order')


# --------------------------------------------------------------------------- #
# 5. 解读句写死成样本原文
# --------------------------------------------------------------------------- #
def test_hardcoded_reading_turns_the_guard_red() -> None:
    module = _mutated(
        [("        bits.append('本段只作对照，不改方向锁')",
          "        bits.append('本段只作对照，不改方向锁')\n"
          "        return ('市热11项、头向【负】（8/11）；市短14项、头向【胜】（9/14）；'"
          "'两市方向错位；短而非热14项、热而非短11项；冷门候选8项。')")],
        'mut_hardcoded_reading',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_reading_changes_between_short_heavy_and_hot_heavy')


# --------------------------------------------------------------------------- #
# 6. 并集里塞进边界六项
# --------------------------------------------------------------------------- #
def test_union_with_edge_items_turns_the_guard_red() -> None:
    """把差恰为 0 的六项也算进市短：并集 31 项、互斥与 25 项断言全红。"""
    module = _mutated(
        [("            else:\n                edge.append(row)",
          "            else:\n                edge.append(row)\n                short.append(row)")],
        'mut_union_edge',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_hot_and_short_are_complementary_and_disjoint')


# --------------------------------------------------------------------------- #
# 7. 冷门候选退回 `≠ 头向`（用户正文写法，样本上会多出平向两项）
# --------------------------------------------------------------------------- #
def test_not_equal_candidate_rule_turns_the_sample_guard_red() -> None:
    module = _mutated(
        [("            if opposite and r.get('wdl') == opposite and '其它' not in str(r['score'])",
          "            if hot_dir and r.get('wdl') != hot_dir and '其它' not in str(r['score'])")],
        'mut_not_equal_candidates',
    )
    with pytest.raises(AssertionError):
        _run(module, 'test_candidates_keep_opposite_direction_and_drop_summary_buckets')
