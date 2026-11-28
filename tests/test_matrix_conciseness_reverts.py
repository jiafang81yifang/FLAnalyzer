"""「简洁」与「对齐」的反向验证：每条坏法都真跑一遍，断言对应守卫变红。

用户在简洁要求里点了四类坏法，对齐要求里点了五类。这里逐条实测：

  简洁：加回三行装饰分隔线 / 把单位加回每个格子 / 同表重复一个比分 /
        把解读从一行扩成四行
  对齐：`_display_width` 换 `len` / 删掉某行一个单元格 /
        某列数值变长不重算列宽 / 半行改用全角 `｜` / 把表挪出等宽块 /
        行宽相等但列边界错位

外加一条「防过度删减」的反证：删掉一个用户点名的数字锚 → 锚点守卫红。
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

CONCISE = 'test_matrix_conciseness'
ALIGN = 'test_matrix_alignment'
TABLE = 'test_market_vs_jingcai_table'


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


def _run(module: types.ModuleType, test_module: str, test_name: str,
         params: dict | None = None) -> None:
    """在被改坏的模块上跑 tests/<test_module>.py::test_name；任何异常都算红。

    `params` 用于参数化测试（`func[param]` 那种名字 `getattr` 取不到）。
    """
    saved_mod = sys.modules.get('blade_deepseek_pipeline')
    saved_test = sys.modules.pop(test_module, None)
    sys.modules['blade_deepseek_pipeline'] = module
    try:
        spec = _ilu.spec_from_file_location(test_module, ROOT / 'tests' / f'{test_module}.py')
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert mod.pipe is module, '测试没跑在被改坏的模块上，反证无效'
        fn = getattr(mod, test_name)
        if params:
            fn(**params)
        else:
            fn()
    finally:
        sys.modules.pop(test_module, None)
        if saved_test is not None:
            sys.modules[test_module] = saved_test
        if saved_mod is not None:
            sys.modules['blade_deepseek_pipeline'] = saved_mod


# --------------------------------------------------------------------------- #
# 简洁 1：加回三行装饰分隔线
# --------------------------------------------------------------------------- #
def test_decorative_separator_lines_turn_the_guard_red() -> None:
    module = _mutated(
        [("        lines.extend(build_grid_lines(\n"
          "            ['方向', '项数', '比分'], grid_rows,",
          "        lines.extend([' ───────────────', ' ===============', ' ───────────────'])\n"
          "        lines.extend(build_grid_lines(\n"
          "            ['方向', '项数', '比分'], grid_rows,")],
        'mut_decor',
    )
    with pytest.raises(Exception):
        _run(module, CONCISE, 'test_no_decorative_separator_lines', {'name': '市热·胜平负比分矩阵'})


# --------------------------------------------------------------------------- #
# 简洁 2：把单位加回每个格子
# --------------------------------------------------------------------------- #
def test_unit_back_in_every_cell_turns_the_guard_red() -> None:
    module = _mutated(
        [("*[GRID_EMPTY if m is None else f'{m:.2f}' for m in page['column_means']],",
          "*[GRID_EMPTY if m is None else f'均{m:.2f}' for m in page['column_means']],")],
        'mut_unit',
    )
    with pytest.raises(Exception):
        _run(module, CONCISE, 'test_goal_grid_does_not_repeat_the_mean_unit_per_cell')


# --------------------------------------------------------------------------- #
# 简洁 3：同一张表里重复一个比分
# --------------------------------------------------------------------------- #
def test_duplicated_score_in_the_same_table_turns_the_guard_red() -> None:
    module = _mutated(
        [("        return {'hot': hot, 'short': short, 'edge': edge, 'missing': missing}",
          "        if hot:\n"
          "            hot.append(dict(hot[0]))\n"
          "        return {'hot': hot, 'short': short, 'edge': edge, 'missing': missing}")],
        'mut_dup_score',
    )
    with pytest.raises(Exception):
        _run(module, CONCISE, 'test_no_score_appears_twice', {'name': '市热·胜平负比分矩阵'})


# --------------------------------------------------------------------------- #
# 简洁 4：解读从一行扩成四行
# --------------------------------------------------------------------------- #
def test_splitting_the_reading_into_four_lines_turns_the_guard_red() -> None:
    module = _mutated(
        [("        lines.append(' ' + '｜'.join(bits))",
          "        for _b in bits:\n"
          "            lines.append(' ' + _b + '。' + '补' * 60)")],
        'mut_four_line_reading',
    )
    with pytest.raises(Exception):
        _run(module, CONCISE, 'test_at_most_one_reading_line_outside_the_grid', {'name': '★市短·胜平负比分矩阵'})


# --------------------------------------------------------------------------- #
# 简洁 5（豁免的反向）：删掉「热而非短 / 短而非热」两行 → 两条守卫都要红
# --------------------------------------------------------------------------- #
def test_dropping_the_difference_lines_turns_both_guards_red() -> None:
    """这两行豁免于简洁规则，但**不许**因此被删掉。

    删除后：
      * `test_exempt_lines_really_exist_and_are_really_the_difference_sets` 红
        （豁免登记了、行却不在）；
      * 第五段的 `test_difference_lines_sit_between_short_scores_and_candidates` 红。
    """
    module = _mutated(
        [("        lines.append(' 热而非短：' + ('/'.join(only_hot) if only_hot else '无'))\n"
          "        lines.append(' 短而非热：' + ('/'.join(only_short) if only_short else '无'))",
          "        pass")],
        'mut_drop_diff_lines',
    )
    with pytest.raises(Exception):
        _run(module, CONCISE, 'test_exempt_lines_really_exist_and_are_really_the_difference_sets')
    with pytest.raises(Exception):
        _run(module, 'test_heat_short_distribution',
             'test_difference_lines_sit_between_short_scores_and_candidates')


# --------------------------------------------------------------------------- #
# 对齐 1：`_display_width` 换成 `len`
# --------------------------------------------------------------------------- #
def test_len_width_turns_the_alignment_guard_red() -> None:
    module = _mutated(
        [("    import unicodedata\n"
          "    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in str(text or ''))",
          "    return len(str(text or ''))")],
        'mut_len_w',
    )
    with pytest.raises(Exception):
        _run(module, ALIGN, 'test_new_section_is_a_strict_grid', {'name': '★市短·胜平负比分矩阵'})


# --------------------------------------------------------------------------- #
# 对齐 2：删掉某行一个单元格
# --------------------------------------------------------------------------- #
def test_dropping_a_cell_turns_the_alignment_guard_red() -> None:
    module = _mutated(
        [("    for idx, row in enumerate(body):\n"
          "        if len(row) != ncols:",
          "    for idx, row in enumerate(body):\n"
          "        if len(row) > ncols:\n"
          "            row = row[:-1]\n"
          "        elif len(row) != ncols:")],
        'mut_drop_cell',
    )
    # 少一格时按 ncols 拼出来的行会短一截 → 行宽不等。
    module._FORCE_DROP_CELL = True  # type: ignore[attr-defined]
    src = MODULE.read_text(encoding='utf-8')
    src = src.replace(
        "            + GRID_SEP.join(\n"
        "                _cell_fit(cell, widths[col], align_of[col]) for col, cell in enumerate(cells)\n"
        "            )\n"
        "            + GRID_SEP",
        "            + GRID_SEP.join(\n"
        "                _cell_fit(cell, widths[col], align_of[col]) for col, cell in enumerate(cells)\n"
        "            )[:-2]\n"
        "            + GRID_SEP",
    )
    spec = _ilu.spec_from_loader('mut_drop_cell2', loader=None)
    m2 = _ilu.module_from_spec(spec)
    exec(compile(src, str(MODULE), 'exec'), m2.__dict__)
    with pytest.raises(Exception):
        _run(m2, ALIGN, 'test_new_section_is_a_strict_grid', {'name': '市热·胜平负比分矩阵'})


# --------------------------------------------------------------------------- #
# 对齐 3：数值从 8.05 变 108.05 而不重算列宽
# --------------------------------------------------------------------------- #
def test_longer_value_without_rewidening_turns_the_guard_red() -> None:
    """某列数值变长（`8.05` → `108.05`）而列宽不重算 → 对齐必须红。

    做法：把 `_grid_widths` 的「按最长格算」改成写死 5 列，再让赔率变成两位数
    整数部。渲染时会截断（`_cell_fit` 拒绝截断就抛错），两种都算红。
    """
    src = MODULE.read_text(encoding='utf-8')
    old = """    widths: list[int] = []
    for col in range(ncols):"""
    assert old in src
    src = src.replace(old, """    widths: list[int] = []
    for col in range(ncols):
        widths.append(5)
        continue""")
    src = src.replace("f\"{r['market']:.2f}\"", "'108.05'")
    src = src.replace("f'{m:.2f}'", "'108.05'")
    spec = _ilu.spec_from_loader('mut_long_value', loader=None)
    m2 = _ilu.module_from_spec(spec)
    exec(compile(src, str(MODULE), 'exec'), m2.__dict__)
    with pytest.raises(Exception):
        _run(m2, ALIGN, 'test_new_section_is_a_strict_grid', {'name': '市均前15·进球赔率矩阵'})


# --------------------------------------------------------------------------- #
# 对齐 4：半行改用全角 `｜`
# --------------------------------------------------------------------------- #
def test_half_fullwidth_separator_turns_the_guard_red() -> None:
    module = _mutated(
        [("            + GRID_SEP\n",
          "            + (GRID_SEP if len(lines) % 2 == 0 else '\\uff5c')\n")],
        'mut_fullwidth_half',
    )
    with pytest.raises(Exception):
        _run(module, ALIGN, 'test_only_one_separator_char_used_inside_grid_rows')


# --------------------------------------------------------------------------- #
# 对齐 5：把表挪出等宽块
# --------------------------------------------------------------------------- #
def test_table_outside_pre_block_turns_the_guard_red() -> None:
    module = _mutated(
        [("            if GRID_SEP in line and line.strip():\n"
          "                block.append(line)\n"
          "                continue",
          "            if False:\n"
          "                block.append(line)\n"
          "                continue")],
        'mut_no_pre',
    )
    with pytest.raises(Exception):
        _run(module, ALIGN, 'test_table_body_is_wrapped_in_pre_and_title_stays_outside')


# --------------------------------------------------------------------------- #
# 对齐 6：行宽相等但列边界错位（第 ② 条独立于第 ① 条）
# --------------------------------------------------------------------------- #
def test_equal_width_shifted_boundary_is_caught_independently() -> None:
    """构造两行同宽、但 `|` 位置不同：第 ② 条必须能单独抓出来。"""
    sys.path.insert(0, str(ROOT / 'tests'))
    import importlib as _i
    mod = _i.import_module(ALIGN)
    a = '|胜 |1-0 |14.55|'
    b = '|胜|1-0 | 14.55|'
    assert mod._w(a) == mod._w(b), '前提：两行等宽'
    assert mod._pipe_cols(a) != mod._pipe_cols(b), '前提：边界不同'
    with pytest.raises(Exception):
        mod.assert_grid_aligned([a, b], 'synthetic')


# --------------------------------------------------------------------------- #
# 防过度删减：删掉一个用户点名的数字锚
# --------------------------------------------------------------------------- #
def test_dropping_a_named_anchor_turns_the_anchor_guard_red() -> None:
    """删掉一段里用户点名的整组数字 → 锚点守卫红。

    做法：把市均前15 的汇总行整行删掉。那一行承载用户点名的
    计数/占比/少球多球均值/最低市均，删掉后锚点守卫必须报红。
    （只删单个比分不够：`build_theo_mkt_31_pack` 的 `setdefault` 会把它补回来，
    这一点本身已在报告里记录。）
    """
    # 直接清空汇总行：把整段 summary 的 append 变成提前返回。
    src = MODULE.read_text(encoding='utf-8')
    marker = "            # 表格之外只留**一行**汇总（简洁要求第 7 条）：用户点名的每个数字"
    assert marker in src
    src = src.replace(marker, "            return lines\n" + marker)
    spec = _ilu.spec_from_loader('mut_drop_anchor2', loader=None)
    m2 = _ilu.module_from_spec(spec)
    exec(compile(src, str(MODULE), 'exec'), m2.__dict__)
    with pytest.raises(Exception):
        _run(m2, CONCISE, 'test_user_named_number_anchors_are_all_present')
