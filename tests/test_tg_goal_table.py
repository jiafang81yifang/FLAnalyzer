"""tg_goal_table 的验收测试：一条缺陷一处测试，期望值全部手写常量。

期望值不来自被测函数：列宽、整行文本、缺口说明都是按契约手算的常量。
"""
from __future__ import annotations

import html
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tg_goal_table as tgt

# ---------------------------------------------------------------- 契约 fixture
# 用户给的样例数据（原文照抄），用作回归基线。
SAMPLE = {
    "avg": {
        "0球": "2.91", "1球": "7.72", "2球": "4.97", "3球": "5.23",
        "4球": "3.82", "5球": "4.32", "6球": "-", "7+": "-",
    },
    "win_score": {
        "0球": "-", "1球": "1-0", "2球": "2-0", "3球": "2-1 3-0",
        "4球": "3-1", "5球": "3-2", "6球": "-", "7+": "-",
    },
    "win_odds": {
        "0球": "-", "1球": "7.65", "2球": "11.79", "3球": "9.12 22.02",
        "4球": "16.82", "5球": "21.85", "6球": "-", "7+": "-",
    },
    "draw_score": {
        "0球": "0-0", "1球": "-", "2球": "1-1", "3球": "-",
        "4球": "2-2", "5球": "-", "6球": "-", "7+": "-",
    },
    "draw_odds": {
        "0球": "8.74", "1球": "-", "2球": "5.75", "3球": "-",
        "4球": "11.60", "5球": "-", "6球": "-", "7+": "-",
    },
    "lose_score": {
        "0球": "-", "1球": "0-1", "2球": "0-2", "3球": "1-2 0-3",
        "4球": "1-3", "5球": "2-3", "6球": "-", "7+": "-",
    },
    "lose_odds": {
        "0球": "-", "1球": "7.80", "2球": "12.30", "3球": "9.23 22.39",
        "4球": "17.39", "5球": "21.33", "6球": "-", "7+": "-",
    },
}

# 手算的完整渲染。列宽（显示列，取该列所有单元格与表头的最宽者）：
#   首列 max(项=2, 均=2, 胜=2, 胜赔=4, 平=2, 平赔=4, 负=2, 负赔=4) = 4
#   0球 max(3, 2.91=4, 1-0/-/7.65/0-0/8.74/-/-) = 4
#   1球 max(3, 7.72=4, 1-0, 7.65, -, -, 0-1, 7.80=4) = 4
#   2球 max(3, 4.97, 2-0, 11.79=5, 1-1, 5.75, 0-2, 12.30=5) = 5
#   3球 max(3, 5.23, "2-1 3-0"=7, "9.12 22.02"=10, -, -, "1-2 0-3"=7, "9.23 22.39"=10) = 10
#   4球 max(3, 3.82, 3-1, 16.82=5, 2-2, 11.60=5, 1-3, 17.39=5) = 5
#   5球 max(3, 4.32, 3-2, 21.85=5, -, -, 2-3, 21.33=5) = 5
#   6球 max(3, 全为 "-") = 3 ；7+ max(2, 全为 "-") = 2
#   行宽 = 4 + 8 个 '|' + (4+4+5+10+5+5+3+2) = 4 + 8 + 38 = 50
SAMPLE_EXPECTED_LINES = [
    "项  |0球 |1球 |2球  |3球       |4球  |5球  |6球|7+",
    "均  |2.91|7.72|4.97 |5.23      |3.82 |4.32 |-  |- ",
    "胜  |-   |1-0 |2-0  |2-1 3-0   |3-1  |3-2  |-  |- ",
    "胜赔|-   |7.65|11.79|9.12 22.02|16.82|21.85|-  |- ",
    "平  |0-0 |-   |1-1  |-         |2-2  |-    |-  |- ",
    "平赔|8.74|-   |5.75 |-         |11.60|-    |-  |- ",
    "负  |-   |0-1 |0-2  |1-2 0-3   |1-3  |2-3  |-  |- ",
    "负赔|-   |7.80|12.30|9.23 22.39|17.39|21.33|-  |- ",
]

SAMPLE_LINE_WIDTH = 50


def _sample_copy() -> dict:
    return {key: dict(value) for key, value in SAMPLE.items()}


def _line_widths(lines: list[str]) -> set[int]:
    return {tgt.display_width(line) for line in lines}


# =========================================================== D1 显示列宽对齐
def test_d1_sample_lines_have_identical_display_width() -> None:
    """中文行（胜赔/平赔/负赔）与纯数字行必须对齐到同一显示列宽。"""
    lines = tgt.build_goal_table_lines(SAMPLE)
    assert lines == SAMPLE_EXPECTED_LINES
    assert _line_widths(lines) == {SAMPLE_LINE_WIDTH}


def test_d1_all_ascii_table_lines_have_identical_display_width() -> None:
    """全 ASCII 单元格：列宽只由 4 字符的 "9.99" 与首列「胜赔」决定。"""
    data = {key: {col: "9.99" for col in tgt.COLS} for key in tgt.DATA_KEYS}
    # 手算：首列 max(项/均/胜/平/负 = 2, 胜赔/平赔/负赔 = 4) = 4；
    # 8 列 max(表头 0球=3 或 7+=2, "9.99"=4) = 4。
    # 行宽 = 4 + 8 个 '|' + 8*4 = 44。
    lines = tgt.build_goal_table_lines(data)
    assert lines[0] == "项  |0球 |1球 |2球 |3球 |4球 |5球 |6球 |7+  "
    assert lines[1] == "均  |9.99|9.99|9.99|9.99|9.99|9.99|9.99|9.99"
    assert lines[3] == "胜赔|9.99|9.99|9.99|9.99|9.99|9.99|9.99|9.99"
    assert len(lines) == 8
    assert _line_widths(lines) == {44}
    # 原生 len() 并不相等 —— 这正是 D1 的缺陷本身（'项' 1 字符占 2 列）。
    assert len({len(line) for line in lines}) > 1


def test_d1_display_width_counts_cjk_as_two_columns() -> None:
    assert tgt.display_width("项") == 2
    assert tgt.display_width("胜赔") == 4
    assert tgt.display_width("1-0") == 3
    assert tgt.display_width("0球") == 3
    assert tgt.display_width("7+") == 2
    assert tgt.display_width("") == 0
    # 组合符不占列（e + ́ 仍算 1 列）。
    assert tgt.display_width("e\u0301") == 1


def test_d1_cjk_cell_wider_than_header_expands_that_column() -> None:
    """单元格出现中文（如「胜其它」，显示 6 列）时列宽按显示列宽撑开。"""
    data = _sample_copy()
    data["win_score"]["7+"] = "胜其它"   # 6 列，把 7+ 列从 2 撑到 6
    data["win_odds"]["7+"] = "250.00"    # 6 列，同样落在 7+ 列
    lines = tgt.build_goal_table_lines(data)
    # 7+ 列 6 列宽，行宽 = 原 50 - 2 + 6 = 54。
    assert _line_widths(lines) == {54}
    assert lines[2] == "胜  |-   |1-0 |2-0  |2-1 3-0   |3-1  |3-2  |-  |胜其它"
    assert lines[3] == "胜赔|-   |7.65|11.79|9.12 22.02|16.82|21.85|-  |250.00"


# ================================================================== D2 赔率不失真
def test_d2_compact_keeps_three_long_odds_distinguishable() -> None:
    """22.02 / 22.39 / 21.85 在 compact 下必须仍可区分（不许砍成 1 位小数）。"""
    data = _sample_copy()
    data["win_odds"]["3球"] = "22.02"
    data["win_odds"]["4球"] = "22.39"
    data["win_odds"]["5球"] = "21.85"
    compact_text = tgt.build_goal_table_text(data, compact=True)
    assert "22.02" in compact_text
    assert "22.39" in compact_text
    assert "21.85" in compact_text
    # 逐个断言手算的 compact 结果，避免只靠子串匹配。
    assert tgt._fmt_num(22.02, compact=True) == "22.02"
    assert tgt._fmt_num(22.39, compact=True) == "22.39"
    assert tgt._fmt_num(21.85, compact=True) == "21.85"
    assert tgt._fmt_num(22.02, compact=False) == "22.02"


def test_d2_compact_trims_trailing_zeros_only() -> None:
    assert tgt._fmt_num(8.70, compact=True) == "8.7"
    assert tgt._fmt_num(8.70, compact=False) == "8.70"
    assert tgt._fmt_num(9.0, compact=True) == "9"
    assert tgt._fmt_num("9.12 22.02", compact=True) == "9.12 22.02"
    assert tgt._fmt_num("11.60", compact=True) == "11.6"
    # 不降到 1 位：22.02 不是 22.0，21.85 不是 21.9。
    assert tgt._fmt_num(22.02, compact=True) != "22.0"
    assert tgt._fmt_num(21.85, compact=True) != "21.9"


def test_d2_compact_never_collides_in_one_row() -> None:
    """同一行三种长赔率 compact 后互不相同（旧实现会撞成 22.0/22.4/21.9）。"""
    data = _sample_copy()
    data["win_odds"]["3球"] = "22.02"
    data["win_odds"]["4球"] = "22.39"
    data["win_odds"]["5球"] = "21.85"
    compact_line = tgt.build_goal_table_lines(data, compact=True)[3]
    # 手算：compact 只去尾随 0，三个长赔率原样保留两位小数。
    # 列宽仍是 3球=10、0球/1球=4、2球=5、4球/5球=5、6球=3、7+=2。
    assert compact_line == "胜赔|-   |7.65|11.79|22.02     |22.39|21.85|-  |- "
    cells = [cell.strip() for cell in compact_line.split("|")[1:]]
    assert cells == ["-", "7.65", "11.79", "22.02", "22.39", "21.85", "-", "-"]
    assert len(set(cells)) == 6


def test_d2_docstring_states_the_two_decimal_rule() -> None:
    doc = tgt._fmt_num.__doc__ or ""
    assert "2 位小数" in doc
    assert "1 位" in doc


# ================================================================== D3 缺键显式失败
def test_d3_missing_key_raises_value_error() -> None:
    for key in tgt.DATA_KEYS:
        broken = _sample_copy()
        del broken[key]
        with pytest.raises(tgt.GoalTableDataError) as excinfo:
            tgt.build_telegram_goal_table(broken)
        assert key in str(excinfo.value)
        assert isinstance(excinfo.value, ValueError)


def test_d3_none_row_raises_instead_of_silently_dashing() -> None:
    broken = _sample_copy()
    broken["win_odds"] = None
    with pytest.raises(tgt.GoalTableDataError) as excinfo:
        tgt.build_goal_table_lines(broken)
    assert "win_odds" in str(excinfo.value)


def test_d3_missing_key_never_yields_a_plausible_table() -> None:
    """缺键时绝不能返回「看起来正常、其实 8 格全 -」的表。"""
    broken = _sample_copy()
    del broken["draw_odds"]
    with pytest.raises(tgt.GoalTableDataError):
        tgt.build_goal_table_text(broken)


def test_d3_dash_inside_present_key_still_renders_dash() -> None:
    """单个单元格 "-"/""/None 是「无该比分」，仍然渲染 "-"，不抛错。"""
    data = _sample_copy()
    data["win_score"]["6球"] = "-"
    data["win_odds"]["6球"] = ""
    data["lose_odds"]["7+"] = None
    lines = tgt.build_goal_table_lines(data)
    assert _line_widths(lines) == {SAMPLE_LINE_WIDTH}
    assert lines[2].split("|")[7].strip() == "-"
    assert lines[3].split("|")[7].strip() == "-"
    assert lines[7].split("|")[8].strip() == "-"


def test_d3_coverage_note_names_every_empty_column() -> None:
    note = tgt.format_goal_table_coverage_note(SAMPLE)
    # 手写期望：从 SAMPLE 逐行数出来的缺格。
    assert note.startswith("数据缺口（表内显示为 -）：")
    assert "均 6球、7+" in note
    assert "胜 0球、6球、7+" in note
    assert "平 1球、3球、5球、6球、7+" in note
    assert "负 0球、6球、7+" in note


def test_d3_coverage_note_reports_full_coverage() -> None:
    full = {key: {col: "1.00" for col in tgt.COLS} for key in tgt.DATA_KEYS}
    assert tgt.format_goal_table_coverage_note(full) == (
        "数据覆盖：均/胜/平/负 4 行 × 8 列 均有数据"
    )


# ================================================================== D4 长度上限
def _overlong_data(score_len: int = 18) -> dict:
    """把 8 列 × 7 行每格都塞满，构造一张确定超过 4096 的表。

    不能假设市场页的比分选项数有上界（桌面 /cs/ 页面一屏几十个选项是常态），
    所以这里刻意做「每格 N 个比分 + N 个赔率」的饱和数据，用来证明长度闸门真的
    存在，而不是靠「现实中不会超」蒙混过去。
    """
    data: dict[str, dict[str, str]] = {}
    for key in tgt.DATA_KEYS:
        row = {}
        for col in tgt.COLS:
            if key.endswith("_score"):
                row[col] = " ".join(f"9{i}-{i}" for i in range(score_len))
            elif key.endswith("_odds"):
                row[col] = " ".join(f"{100 + i}.{i:02d}" for i in range(score_len))
            else:
                row[col] = "12.34"
        data[key] = row
    return data


def test_d4_overlong_raises_clear_exception_instead_of_400() -> None:
    """单条模式下超限必须抛 GoalTableTooLongError，而不是让 Telegram 回 400。"""
    with pytest.raises(tgt.GoalTableTooLongError) as excinfo:
        tgt.build_telegram_goal_table(_overlong_data())
    assert "4096" in str(excinfo.value)
    assert isinstance(excinfo.value, ValueError)


def test_d4_overlong_splits_into_messages_under_limit() -> None:
    messages = tgt.build_telegram_goal_table_messages(_overlong_data())
    assert len(messages) > 1
    assert all(len(message) <= tgt.TELEGRAM_TEXT_LIMIT for message in messages)
    # 每条都带表头与自己的 <pre> 包装，单独发出去就是一张合法表格。
    for message in messages:
        assert message.startswith("<pre>") and message.endswith("</pre>")
        body = html.unescape(message[len("<pre>"):-len("</pre>")])
        rows = body.split("\n")
        assert rows[0].startswith("项")
        assert len(rows) >= 2
        assert len(_line_widths(rows)) == 1


def test_d4_split_messages_rejoin_to_full_table() -> None:
    """按块拼回来：每块重挂同一表头，数据行一字不差且不重不漏。"""
    data = _overlong_data()
    messages = tgt.build_goal_table_messages_text(data)
    full_rows = tgt.build_goal_table_lines(data)
    assert len(messages) > 1
    seen_rows: list[str] = []
    for message in messages:
        rows = message.split("\n")
        assert rows[0] == full_rows[0]
        seen_rows.extend(rows[1:])
    assert seen_rows == full_rows[1:]


def test_d4_short_table_is_single_message() -> None:
    messages = tgt.build_telegram_goal_table_messages(SAMPLE)
    assert messages == [tgt.build_telegram_goal_table(SAMPLE)]


def test_d4_unsplittable_row_raises_rather_than_being_cut() -> None:
    """连一行都装不下时抛错（而不是把一行劈成两半、对齐全毁）。"""
    data = {}
    for key in tgt.DATA_KEYS:
        row = {}
        for col in tgt.COLS:
            if key.endswith("_score"):
                row[col] = " ".join(f"{h}-{a}" for h in range(30) for a in range(10))
            elif key.endswith("_odds"):
                row[col] = " ".join(f"{20 + h}.{a:02d}" for h in range(30) for a in range(10))
            else:
                row[col] = "12.34"
        data[key] = row
    with pytest.raises(tgt.GoalTableTooLongError) as excinfo:
        tgt.build_telegram_goal_table_messages(data)
    assert "无法按行拆条" in str(excinfo.value)


def test_d4_docstring_documents_the_single_message_contract() -> None:
    doc = tgt.build_telegram_goal_table.__doc__ or ""
    assert "单条" in doc
    assert "build_telegram_goal_table_messages" in doc


# ================================================================== D5 复用发送通道
def test_d5_module_has_no_send_stack() -> None:
    """模块里不许自带发送栈：无 HTTP 客户端导入、无 token 参数、无独立发送函数。"""
    source = (ROOT / "tg_goal_table.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "import urllib" not in code
    assert "import requests" not in code
    assert "urlopen" not in code
    assert "def send_telegram_html" not in code
    assert not hasattr(tgt, "send_telegram_html")
    for name in dir(tgt):
        if name.startswith("send"):
            pytest.fail(f"模块不该有发送函数: {name}")
    assert list(inspect.signature(tgt.build_telegram_goal_table).parameters) == [
        "data", "compact", "use_html_pre",
    ]


def test_d5_app_reuses_existing_send_path() -> None:
    """接线点必须走既有 send_tg/_tg_post，不另起一行发送代码。"""
    app_source = (ROOT / "app_v84_hermes.py").read_text(encoding="utf-8")
    assert "def _tg_post(url, payload):" in app_source
    start = app_source.index("def build_goal_table_tg_messages")
    section = app_source[start:start + 1500]
    assert "_tg_post" not in section
    assert "urlopen" not in section
    assert "SNIPER_BOT_TOKEN" not in section
    # 4xx 的 description 必须被带出来，不能吞成静默失败。
    assert "def _tg_description_text" in app_source
    assert "Telegram: {_desc}" in app_source
    assert "_tg_description_text(_body_text)" in app_source


def test_d5_description_helper_surfaces_telegram_text() -> None:
    app_source = (ROOT / "app_v84_hermes.py").read_text(encoding="utf-8")
    # 只校验取值逻辑的源码契约，不在测试里导入 4 万行的 app。
    assert "parsed.get('description')" in app_source
    assert "error_code" in app_source


def test_d5_no_hardcoded_telegram_token() -> None:
    source = (ROOT / "tg_goal_table.py").read_text(encoding="utf-8")
    assert re.search(r"\b\d{8,10}:[A-Za-z0-9_-]{33,}\b", source) is None
    assert "TELEGRAM_BOT_TOKEN" not in source


# ================================================================== D6 竖线防护
def test_d6_pipe_in_cell_cannot_break_table() -> None:
    data = _sample_copy()
    data["win_score"]["3球"] = "2-1|3-0"
    data["win_odds"]["3球"] = "9.12|22.02"
    lines = tgt.build_goal_table_lines(data)
    # 每行分隔符个数固定：首列 1 个 + 7 个列间 = 8 个。
    for line in lines:
        assert line.count("|") == 8
    assert lines[2].split("|")[4].strip() == "2-1/3-0"
    assert lines[3].split("|")[4].strip() == "9.12/22.02"


def test_d6_fullwidth_pipe_and_newline_are_neutralised() -> None:
    data = _sample_copy()
    data["lose_score"]["3球"] = "1-2｜0-3"
    data["lose_odds"]["3球"] = "9.23\n22.39"
    lines = tgt.build_goal_table_lines(data)
    for line in lines:
        assert line.count("|") == 8
    assert lines[6].split("|")[4].strip() == "1-2/0-3"
    assert lines[7].split("|")[4].strip() == "9.23 22.39"


def test_d6_header_row_is_exactly_eight_columns() -> None:
    lines = tgt.build_goal_table_lines(SAMPLE)
    assert lines[0].split("|") == [
        "项  ", "0球 ", "1球 ", "2球  ", "3球       ",
        "4球  ", "5球  ", "6球", "7+",
    ]


# =========================================================== 装配（真实数据来源）
def test_build_data_from_real_odds_shapes() -> None:
    built = tgt.build_goal_table_data(
        {"avg_0": 2.91, "avg_1": 7.72, "avg_7plus": 12.5},
        {
            "1-0": {"odds": 7.65},
            "2-1": 9.12,
            "3-0": 22.02,
            "1-2": 9.23,
            "0-3": 22.39,
            "2-2": 11.60,
            "胜其它": 250.0,
            "平其它": 500.0,
        },
    )
    data = built["data"]
    assert data["avg"]["0球"] == "2.91"
    assert data["avg"]["1球"] == "7.72"
    assert data["avg"]["7+"] == "12.50"
    assert data["avg"]["2球"] == "-"
    assert data["win_score"]["3球"] == "2-1 3-0"
    assert data["win_odds"]["3球"] == "9.12 22.02"
    assert data["win_score"]["7+"] == "胜其它"
    assert data["draw_score"]["4球"] == "2-2"
    assert data["draw_odds"]["4球"] == "11.60"
    assert data["lose_score"]["3球"] == "1-2 0-3"
    assert data["lose_odds"]["3球"] == "9.23 22.39"
    assert built["avg_columns"] == 3
    assert built["score_options"] == 8
    # 没有数据的格子显式列进 missing，不静默。
    assert "avg 2球" in built["missing"]
    assert "win_score 6球" in built["missing"]
    # 装配结果可以直接渲染。
    assert "9.12 22.02" in tgt.build_goal_table_text(data)


def test_build_data_ignores_bogus_odds_without_inventing_values() -> None:
    built = tgt.build_goal_table_data(
        {"avg_0": None, "avg_1": "-", "avg_2": 0.5, "avg_3": "abc"},
        {"1-0": {"odds": None}, "2-1": "n/a", "bad-key": 3.5},
    )
    assert built["avg_columns"] == 0
    assert built["score_options"] == 0
    assert all(value == "-" for value in built["data"]["avg"].values())
    assert all(value == "-" for value in built["data"]["win_score"].values())


def test_build_data_buckets_total_goals_by_colour() -> None:    # 6-1 是 7 球 -> 7+ 列（本项目既有口径），不会被塞进 6球。
    built = tgt.build_goal_table_data(
        {}, {"6-1": 40.0, "5-0": 30.0, "3-4": 60.0, "1-3": 20.0}
    )
    data = built["data"]
    # 6-1 归 7+；3-4 是客胜 7 球，也归 7+（各按赔率升序拼接）。
    assert data["win_score"]["7+"] == "6-1"
    assert data["win_score"]["5球"] == "5-0"
    assert data["lose_score"]["7+"] == "3-4"
    assert data["lose_score"]["4球"] == "1-3"
    assert data["win_score"]["6球"] == "-"

# =========================================================== Telegram 段落
def test_section_keeps_gap_note_visible() -> None:
    messages = tgt.build_goal_table_section(SAMPLE, compact=True, use_html_pre=False)
    assert len(messages) == 1
    text = messages[0]
    assert text.startswith("⚽【0球~7+ 均赔/比分表】")
    assert "窄屏压缩：赔率保留≤2位小数" in text
    assert "数据缺口（表内显示为 -）" in text
    assert "均 6球、7+" in text
    assert "项  |0球" in text


def test_section_is_html_safe() -> None:
    data = _sample_copy()
    data["win_score"]["3球"] = "<script>&"
    messages = tgt.build_goal_table_section(data, use_html_pre=True)
    joined = "\n".join(messages)
    assert "<script>" not in joined
    assert "&lt;script&gt;" in joined
    assert "&amp;" in joined
