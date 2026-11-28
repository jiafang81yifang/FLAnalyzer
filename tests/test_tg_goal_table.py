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
    """每页各 4 列仍超长时，页内按行兜底——每条都 <= 4096 且表头随块重挂。"""
    messages = tgt.build_telegram_goal_table_messages(_overlong_data(30))
    assert len(messages) > 2, [len(m) for m in messages]
    assert all(len(message) <= tgt.TELEGRAM_TEXT_LIMIT for message in messages)
    page1_chunks = 0
    page2_seen = 0
    for message in messages:
        # 每块 = 页标题 + 自己的 <pre> 包装，单独发出去就是合法表格。
        head, _, wrapped = message.partition("\n")
        assert head.startswith("⚽【0球~7+ 均赔/比分表】")
        assert ("（1/2）" in head) != ("（2/2）" in head)
        page1_chunks += "（1/2）" in head
        page2_seen += "（2/2）" in head
        assert wrapped.startswith("<pre>") and wrapped.endswith("</pre>")
        rows = html.unescape(wrapped[len("<pre>"):-len("</pre>")]).split("\n")
        assert rows[0].startswith("项")
        assert len(rows) >= 2       # 表头 + 至少一行
        assert len(_line_widths(rows)) == 1
    assert page1_chunks >= 2 and page2_seen >= 2


def test_d4_split_messages_rejoin_to_two_complete_pages() -> None:
    """按页拼回来：页标题 + 本页 8 行表体，不加任何围栏/语言标识。"""
    data = _sample_copy()
    pages = tgt.build_goal_table_messages_text(data)
    assert len(pages) == 2
    for index, (page, cols) in enumerate(zip(pages, (PAGE1_COLS, PAGE2_COLS)), 1):
        lines = page.splitlines()
        assert lines[0] == tgt.page_title(TITLE, cols, index)
        # 纯文本只锁表体：第 1 行起就是本页 8 行，不许夹围栏或语言标识。
        assert lines[1:] == tgt.build_goal_table_page_lines(data, index)
        assert len(lines) == 9
        assert not any(line.strip() in ("```", "```text") for line in lines)
        assert all(line.count("|") == 4 for line in lines[1:])


def test_d4_short_table_still_returns_two_page_messages() -> None:
    """短表也不许合成一条：固定两页是版式契约，与长度无关。"""
    messages = tgt.build_telegram_goal_table_messages(SAMPLE)
    assert len(messages) == 2
    assert messages[0].startswith("⚽【0球~7+ 均赔/比分表】0球~3球（1/2）")
    assert messages[1].startswith("⚽【0球~7+ 均赔/比分表】4球~7+（2/2）")


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
    assert len(messages) == 2
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


# ================================================================== 固定两页
# 硬性要求：0球~7+ 表从 8 列单条改成**固定两页**。
#   第 1 页 0球 1球 2球 3球（1/2）；第 2 页 4球 5球 6球 7+（2/2）。
# 切分点沿用 score_matrices.GOAL_MATRIX_PAGE_SPECS，不按长度自动决定。
PAGE1_COLS = ("0球", "1球", "2球", "3球")
PAGE2_COLS = ("4球", "5球", "6球", "7+")
TITLE = "⚽【0球~7+ 均赔/比分表】"
PAGE1_TITLE = f"{TITLE}0球~3球（1/2）"
PAGE2_TITLE = f"{TITLE}4球~7+（2/2）"

# 手算的每页完整渲染。列宽（显示列）只由**本页 4 列**决定：
#   第 1 页：首列 max(项=2, 胜赔=4)=4；0球 max(3, 2.91=4, 0-0, 8.74)=4；
#            1球 max(3, 7.72=4, 7.80=4)=4；2球 max(3, 11.79=5, 12.30=5)=5；
#            3球 max(3, "2-1 3-0"=7, "9.12 22.02"=10, "1-2 0-3"=7,
#                    "9.23 22.39"=10)=10
#            行宽 = 4 + 4 个 '|' + (4+4+5+10) = 8 + 23 = 31
#   第 2 页：首列 4；4球 max(3, 16.82=5, 17.39=5)=5；5球 max(3, 21.85=5, 21.33=5)=5；
#            6球 max(3, 全 "-")=3；7+ max(2, 全 "-")=2
#            行宽 = 4 + 4 + (5+5+3+2) = 8 + 15 = 23
PAGE1_EXPECTED_LINES = [
    "项  |0球 |1球 |2球  |3球       ",
    "均  |2.91|7.72|4.97 |5.23      ",
    "胜  |-   |1-0 |2-0  |2-1 3-0   ",
    "胜赔|-   |7.65|11.79|9.12 22.02",
    "平  |0-0 |-   |1-1  |-         ",
    "平赔|8.74|-   |5.75 |-         ",
    "负  |-   |0-1 |0-2  |1-2 0-3   ",
    "负赔|-   |7.80|12.30|9.23 22.39",
]
PAGE1_WIDTH = 31

PAGE2_EXPECTED_LINES = [
    "项  |4球  |5球  |6球|7+",
    "均  |3.82 |4.32 |-  |- ",
    "胜  |3-1  |3-2  |-  |- ",
    "胜赔|16.82|21.85|-  |- ",
    "平  |2-2  |-    |-  |- ",
    "平赔|11.60|-    |-  |- ",
    "负  |1-3  |2-3  |-  |- ",
    "负赔|17.39|21.33|-  |- ",
]
PAGE2_WIDTH = 23

ROW_LABELS = ["均", "胜", "胜赔", "平", "平赔", "负", "负赔"]


def test_page_specs_match_the_score_matrices_split() -> None:
    """切分点必须与 score_matrices.GOAL_MATRIX_PAGE_SPECS 完全一致（不另行发明）。"""
    import score_matrices as sm

    assert [no for no, _cols in tgt.PAGE_SPECS] == [1, 2]
    assert tuple(cols for _no, cols in tgt.PAGE_SPECS) == tuple(
        cols for _no, cols in sm.GOAL_MATRIX_PAGE_SPECS
    )
    assert tgt.PAGE_TOTAL == 2


def test_page1_renders_exactly_the_hand_computed_four_columns() -> None:
    """第 1 页指定列 = 0球/1球/2球/3球，逐行整行比对（含列宽）。"""
    lines = tgt.build_goal_table_page_lines(SAMPLE, 1)
    assert lines == PAGE1_EXPECTED_LINES
    assert _line_widths(lines) == {PAGE1_WIDTH}


def test_page2_renders_exactly_the_hand_computed_four_columns() -> None:
    """第 2 页指定列 = 4球/5球/6球/7+，逐行整行比对（含列宽）。"""
    lines = tgt.build_goal_table_page_lines(SAMPLE, 2)
    assert lines == PAGE2_EXPECTED_LINES
    assert _line_widths(lines) == {PAGE2_WIDTH}


def test_each_page_carries_header_and_all_seven_row_labels() -> None:
    for page_no, cols in ((1, PAGE1_COLS), (2, PAGE2_COLS)):
        lines = tgt.build_goal_table_page_lines(SAMPLE, page_no)
        assert len(lines) == 1 + len(ROW_LABELS)
        assert lines[0].split("|")[0].strip() == "项"
        assert [cell.strip() for cell in lines[0].split("|")[1:]] == list(cols)
        assert [line.split("|")[0].strip() for line in lines[1:]] == ROW_LABELS
        # 每行分隔符个数固定：首列 1 个 + 3 个列间 = 4 个。
        for line in lines:
            assert line.count("|") == 4


def test_page_widths_are_computed_per_page_not_reused() -> None:
    """每页按本页 4 列独立算宽：两页行宽不同，且都等于本页手算值。"""
    page1 = tgt.build_goal_table_page_lines(SAMPLE, 1)
    page2 = tgt.build_goal_table_page_lines(SAMPLE, 2)
    assert _line_widths(page1) == {PAGE1_WIDTH}
    assert _line_widths(page2) == {PAGE2_WIDTH}
    assert PAGE1_WIDTH != PAGE2_WIDTH
    # 第 1 页 3球 列被 "9.12 22.02" 撑到 10 列，第 2 页不许继承这个宽度：
    # 第 2 页最宽列是 5（16.82/21.85），若复用第 1 页列宽，7+ 列会宽到 10。
    assert page2[0].split("|")[-1] == "7+"
    assert len(page2[0].split("|")[-1]) == 2


def test_eight_column_line_and_pages_share_one_row_builder() -> None:
    """8 列整表与两页只按列切片，不许有第二份渲染实现。"""
    full = tgt.build_goal_table_lines(SAMPLE)
    pages = tgt.build_goal_table_pages_lines(SAMPLE)
    # 两页的「首列 + 行标签」与 8 列版逐行相同：同一套行构造，只是列切片。
    for rows, cols in zip(pages, (PAGE1_COLS, PAGE2_COLS)):
        assert [line.split("|")[0] for line in rows] == [
            line.split("|")[0] for line in full
        ]
        for line in rows:
            assert len(line.split("|")[1:]) == len(cols)
    # 8 列版本仍是 8 列，单元格文本就是两页对应列的拼接。
    assert len(full) == 8
    assert all(line.count("|") == 8 for line in full)
    for index in range(8):
        joined = [
            rows[index].split("|")[1 + offset].rstrip()
            for rows in pages
            for offset in range(4)
        ]
        assert [cell.rstrip() for cell in full[index].split("|")[1:]] == joined


def test_single_message_renderer_still_emits_full_eight_columns() -> None:
    """契约：build_telegram_goal_table 仍渲染完整 8 列单条，超 4096 仍抛错。"""
    message = tgt.build_telegram_goal_table(SAMPLE)
    body = html.unescape(message[len("<pre>"):-len("</pre>")])
    lines = body.split("\n")
    assert lines == SAMPLE_EXPECTED_LINES
    assert [cell.strip() for cell in lines[0].split("|")[1:]] == tgt.COLS
    assert len(lines[0].split("|")) == 9
    with pytest.raises(tgt.GoalTableTooLongError):
        tgt.build_telegram_goal_table(_overlong_data())


def test_messages_are_two_with_page_titles_and_notes_only_on_page_one() -> None:
    """两条消息，一页一条；出处行与缺项说明只在第 1 页出现一次。"""
    note = "0球~7+ 表出处: 均赔源=6/8档 | 比分赔率=12项"
    gap_note = tgt.format_goal_table_coverage_note(SAMPLE)
    messages = tgt.build_goal_table_messages_text(SAMPLE, notes=[note, gap_note])
    assert len(messages) == 2
    first_lines, second_lines = [m.split("\n") for m in messages]
    assert first_lines[0] == PAGE1_TITLE
    assert second_lines[0] == PAGE2_TITLE
    assert first_lines[1:3] == [note, gap_note]
    assert note not in messages[1]
    assert messages[0].count(gap_note) == 1
    assert gap_note not in messages[1]


def test_page_title_keeps_the_original_table_name() -> None:
    """标题只加页码与列范围，表名本身一字不改。"""
    first = tgt.page_title(TITLE, PAGE1_COLS, 1)
    second = tgt.page_title(TITLE, PAGE2_COLS, 2)
    assert first.startswith(TITLE) and second.startswith(TITLE)
    assert first == PAGE1_TITLE
    assert second == PAGE2_TITLE
    assert "1/2" in first and "2/2" in second
    assert "0球~3球" in first and "4球~7+" in second


def test_page2_renders_all_seven_rows_even_when_every_cell_is_a_dash() -> None:
    """只在第 1 页有数据、第 2 页全 "-" 时，第 2 页仍必须完整 8 行。"""
    data = {key: {col: "-" for col in tgt.COLS} for key in tgt.DATA_KEYS}
    data["avg"]["0球"] = "2.91"
    data["win_score"]["1球"] = "1-0"
    data["win_odds"]["1球"] = "7.65"
    page2 = tgt.build_goal_table_page_lines(data, 2)
    assert len(page2) == 8
    assert [line.split("|")[0].strip() for line in page2] == ["项", *ROW_LABELS]
    # 全 "-" 的 4 列：列宽只由表头决定 = 3+3+3+2，行宽 = 4 + 4 + 11 = 19。
    assert _line_widths(page2) == {19}
    messages = tgt.build_goal_table_messages_text(data)
    assert len(messages) == 2
    page2_lines = messages[1].split("\n")
    assert page2_lines[0] == PAGE2_TITLE
    # 标题 + 本页 8 行表体；纯文本不加围栏，第 2 行起就是表体。
    body = page2_lines[1:]
    assert len(body) == 8
    assert body == tgt.build_goal_table_page_lines(data, 2)
    assert [line.split("|")[0].strip() for line in body] == ["项", *ROW_LABELS]
    assert _line_widths(body) == {19}


def test_streamlit_two_pages_are_separated_without_formfeed() -> None:
    """Streamlit 没有 \\x0c 分页通道：两页用空行 + 各自标题分开。"""
    import app_v84_hermes as app

    text = app.build_goal_table_streamlit_text(
        {
            "available": True,
            "data": _sample_copy(),
            "avg_columns": 6,
            "score_options": 12,
        }
    )
    assert isinstance(text, str)
    assert "\x0c" not in text
    assert PAGE1_TITLE in text and PAGE2_TITLE in text
    assert text.index(PAGE1_TITLE) < text.index(PAGE2_TITLE)
    assert "\n\n" + PAGE2_TITLE in text


def test_app_messages_helper_returns_two_pages_not_one() -> None:
    import app_v84_hermes as app

    messages = app.build_goal_table_tg_messages(
        {"available": True, "data": _sample_copy(), "avg_columns": 6, "score_options": 12}
    )
    assert len(messages) == 2
    assert messages[0].startswith(PAGE1_TITLE)
    assert messages[1].startswith(PAGE2_TITLE)
    # 出处行只在第 1 页
    assert messages[0].count("0球~7+ 表出处:") == 1
    assert "0球~7+ 表出处:" not in messages[1]


def test_pipeline_puts_the_two_pages_in_two_messages() -> None:
    """报告层：两页必须用 \\x0c 隔开，send_tg 才会各发一条、各自带页标题。"""
    import blade_deepseek_pipeline as pipe

    pages = tgt.build_goal_table_messages_text(SAMPLE)
    assert len(pages) == 2
    sections = pipe.DeepSeekAnalyzer.build_deepseek_only_tg_sections(
        title_line='📌 t',
        goal_table_tg_lines=pages,
    )
    goal_section = [s for s in sections if PAGE1_TITLE in s]
    assert len(goal_section) == 1
    body = goal_section[0]
    assert '\x0c' in body
    chunks = pipe.split_tg_html_preserving_pre(body, 4000)
    assert len(chunks) == 2
    assert chunks[0].startswith(PAGE1_TITLE)
    assert chunks[1].startswith(PAGE2_TITLE)
    # 出处/缺项说明只在第 1 条里出现，第 2 条不重复
    assert PAGE2_TITLE not in chunks[0]
