"""0球~7+ 均赔/比分表：Telegram 与 Streamlit 共用的纯 Python 渲染器。

契约（签名与返回类型不得改动）：
    COLS、``_fmt_num(val, compact=False)``、
    ``build_telegram_goal_table(data, compact=False, use_html_pre=True)``
    data 的 7 个键固定为 avg / win_score / win_odds / draw_score / draw_odds /
    lose_score / lose_odds，每键 8 列 0球..7+。

六处缺陷的修法与取舍（逐条对应 tests/test_tg_goal_table.py）：

    D1 列宽按**显示列宽**计算（``unicodedata.east_asian_width`` 为 W/F 记 2 列），
       补齐空格也按列宽补；因此含中文的「胜赔/平赔/负赔」行与纯数字行对齐到同一
       显示宽度。每行末尾的空格统一去掉：补齐用显示列宽、裁尾用最后一个非空格
       之后的可见内容宽度，两种量纲不能混用。
    D2 compact 只压到 **2 位小数并去掉尾随 0**，不再降到 1 位：22.02/22.39/21.85
       在 compact 下仍是 "22.02"/"22.39"/"21.85"，可逐值比较大小。报告标题会写明
       「赔率保留≤2位小数」，不让读者误以为看到的是 1 位小数的数。
    D3 7 个键缺任一个 -> 抛 ``GoalTableDataError``（ValueError 子类），不再静默整
       行变 "-"。单个单元格是 "-"/""/None 仍渲染 "-"，那是「无该比分」。
    D4 Telegram 单条 4096 字符上限：``build_telegram_goal_table`` 保持 8 列单条
       字符串，超限抛 ``GoalTableTooLongError``（docstring 已写明）。对外发送改用
       ``build_telegram_goal_table_messages``：**固定两页**（0~3 球 / 4~7+），一页
       一条、每页各自重挂标题与表头，页内仍超长才按行再拆，单行永不劈开。
       8 列整表与两页共用 ``_render_rows`` / ``_format_rows``，只按列切片。
    D5 本模块不发送任何东西：没有 token 参数、没有 HTTP 客户端导入、没有
       ``send_telegram_html``。发送由调用方现有通道（``_tg_post``/``send_tg``）负责，
       4xx 的 description 也由那条通道带出。
    D6 单元格里的 ``|``（半角）与 ``｜``（全角）渲染前换成 ``/``，换行/制表符换成
       空格，所以单元格内容再脏也不会把表格劈成额外的列。

数据来源（接线见 app_v84_hermes.goal_table_payload）：
    均    = 总进球市场 0球~7+ 的均赔（``tg_avg_odds`` 的 avg_0..avg_7plus）。
    胜/平/负       = 该方向、该总进球档位的真实比分（按赔率升序，空格分隔）。
    胜赔/平赔/负赔 = 与左侧比分一一对应的真实赔率。
    竞彩「胜其它/平其它/负其它」不是具体比分，但它们是该方向 7 球以上的真实投注
    选项，按本项目既有口径（app 内「其它」按 7 球计）归入 7+ 列。
    某一档没有数据就留 "-"，并由 ``format_goal_table_coverage_note`` 在报告里点名
    缺哪些列——缺数据必须显式可见，绝不能当成功。
"""
from __future__ import annotations

import html
import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from typing import Any

COLS = ["0球", "1球", "2球", "3球", "4球", "5球", "6球", "7+"]

# 固定两页，切分点与 score_matrices.GOAL_MATRIX_PAGE_SPECS 一致：
# 第 1 页 0~3 球，第 2 页 4/5/6/7+。**不按长度自动决定**——列多时 8 列一条
# 在手机上必然横向溢出，永远两页是版式契约，不是长度启发式。
PAGE_SPECS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ("0球", "1球", "2球", "3球")),
    (2, ("4球", "5球", "6球", "7+")),
)
PAGE_TOTAL = len(PAGE_SPECS)

DEFAULT_TITLE = "⚽【0球~7+ 均赔/比分表】"

DATA_KEYS = (
    "avg",
    "win_score",
    "win_odds",
    "draw_score",
    "draw_odds",
    "lose_score",
    "lose_odds",
)

# (行标签, data 键, 该行是否按赔率格式渲染)
ROW_DEFS: tuple[tuple[str, str, bool], ...] = (
    ("均", "avg", False),
    ("胜", "win_score", False),
    ("胜赔", "win_odds", True),
    ("平", "draw_score", False),
    ("平赔", "draw_odds", True),
    ("负", "lose_score", False),
    ("负赔", "lose_odds", True),
)

# 报告里点名的 4 个主行；赔率行与对应的比分行同缺同有。
PRIMARY_ROWS: tuple[tuple[str, str, str], ...] = (
    ("均", "avg", ""),
    ("胜", "win_score", "win_odds"),
    ("平", "draw_score", "draw_odds"),
    ("负", "lose_score", "lose_odds"),
)

TELEGRAM_TEXT_LIMIT = 4096
_MISSING_TOKENS = frozenset({"", "-", "—", "--", "None", "none", "nan", "NaN"})


class GoalTableDataError(ValueError):
    """data 结构不完整（7 个键缺项）——必须显式报错，不能整行吞成 "-"。"""


class GoalTableTooLongError(ValueError):
    """单条消息超过 Telegram 4096 上限，且无法再按行切分。"""


# ---------------------------------------------------------------- 显示列宽 (D1)
def display_width(text: Any) -> int:
    """按终端/等宽字体的显示列宽计数：W/F 记 2 列，组合符记 0 列。"""
    width = 0
    for ch in str(text if text is not None else ""):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad_right(text: str, width: int) -> str:
    gap = width - display_width(text)
    return text + " " * gap if gap > 0 else text


# ---------------------------------------------------------------- 单元格 (D2/D6)
def _fmt_num(val: Any, compact: bool = False) -> str:
    """格式化单个赔率或均赔数值。

    compact=True 只裁到 **2 位小数并去掉尾随 0**（8.70 -> 8.7，22.02 -> 22.02），
    不再把 >=10 的赔率压成 1 位小数——赔率要拿来比大小，22.02/22.39/21.85 必须
    保持可区分。字符串里的空格分隔多值（"9.12 22.02"）逐个 token 处理。
    """
    if val is None or isinstance(val, bool):
        return "-" if val is None else str(val)
    if isinstance(val, (int, float)):
        number = float(val)
        if not math.isfinite(number):
            return "-"
        text = f"{number:.2f}"
        if compact:
            text = text.rstrip("0").rstrip(".") if "." in text else text
        return text or "0"
    text = str(val).strip()
    if text in _MISSING_TOKENS:
        return "-"
    if not compact:
        return text
    trimmed: list[str] = []
    for token in text.split():
        try:
            number = float(token)
        except ValueError:
            trimmed.append(token)
            continue
        if not math.isfinite(number):
            trimmed.append(token)
            continue
        piece = f"{number:.2f}"
        trimmed.append(piece.rstrip("0").rstrip(".") if "." in piece else piece)
    return " ".join(trimmed) or "-"


def clean_cell_text(text: Any) -> str:
    """把单元格内容压成不会破坏表格结构的一行（D6）。"""
    out = str(text if text is not None else "")
    out = out.replace("|", "/").replace("｜", "/")
    return re.sub(r"[\r\n\t]+", " ", out).strip()


# ------------------------------------------------------------------ 结构校验 (D3)
def validate_goal_table_data(data: Any) -> dict[str, Mapping[Any, Any]]:
    """校验 7 个键齐全并返回浅拷贝；缺项或整行为 None 都抛 GoalTableDataError。

    值为 None 等于「这一行悄悄全变 -」，正是要被拦下的情况。真要表达「这一档
    没有数据」请传 ``{}``，缺口会由 ``format_goal_table_coverage_note`` 点名。
    """
    if not isinstance(data, Mapping):
        raise GoalTableDataError(f"0球~7+ 表数据必须是 dict，收到 {type(data).__name__}")
    missing = [key for key in DATA_KEYS if key not in data]
    if missing:
        raise GoalTableDataError(
            "0球~7+ 表缺数据键: "
            + ", ".join(missing)
            + f"（必须齐 {len(DATA_KEYS)} 个键；缺整行数据不能用 '-' 冒充）"
        )
    out: dict[str, Mapping[Any, Any]] = {}
    for key in DATA_KEYS:
        value = data[key]
        if value is None:
            raise GoalTableDataError(
                f"0球~7+ 表的 {key} 是 None（整行数据缺失）；空行请显式传 {{}}"
            )
        if not isinstance(value, Mapping):
            raise GoalTableDataError(
                f"0球~7+ 表的 {key} 必须是 dict，收到 {type(value).__name__}"
            )
        out[key] = dict(value)
    return out


def _cell_text(raw: Any, is_odds: bool, compact: bool) -> str:
    cleaned = clean_cell_text(_fmt_num(raw, compact=is_odds and compact))
    return cleaned or "-"


# --------------------------------------------------------------------- 渲染
def _render_rows(valid: Mapping[str, Mapping[Any, Any]], columns: Iterable[str],
                 compact: bool) -> list[tuple[str, list[str]]]:
    """按给定列切片构造 (行标签, 单元格文本) —— 8 列整表与每页都走这一份代码。"""
    cols = list(columns)
    built: list[tuple[str, list[str]]] = []
    for label, key, is_odds in ROW_DEFS:
        src = valid[key]
        built.append(
            (label, [_cell_text(src.get(col, "-"), is_odds, compact) for col in cols])
        )
    return built


def _format_rows(columns: Iterable[str], built: list[tuple[str, list[str]]]) -> list[str]:
    """给一份已构造好的行挂表头并按**本份列**独立算列宽，返回等宽行列表。

    列宽只由传入的 `columns` 决定，所以第 2 页不会继承第 1 页某列被撑开的宽度。
    """
    cols = list(columns)
    first_width = max(display_width("项"), *(display_width(lbl) for lbl, _ in built))
    col_widths = [
        max(display_width(col), *(display_width(cells[idx]) for _, cells in built))
        for idx, col in enumerate(cols)
    ]

    def format_line(first_text: str, cells: Iterable[str]) -> str:
        head = _pad_right(first_text, first_width)
        body = "|".join(_pad_right(txt, width) for txt, width in zip(cells, col_widths))
        # 不做 rstrip：行尾空格也是列宽的一部分，裁掉会让最后一列的行宽变短。
        return f"{head}|{body}"

    return [format_line("项", cols)] + [
        format_line(label, cells) for label, cells in built
    ]


def build_goal_table_lines(data: Any, compact: bool = False) -> list[str]:
    """渲染为**完整 8 列**纯文本行（表头 + 7 行）；每行显示列宽完全相等。"""
    valid = validate_goal_table_data(data)
    return _format_rows(COLS, _render_rows(valid, COLS, compact))


def build_goal_table_page_lines(
    data: Any, page_no: int, compact: bool = False
) -> list[str]:
    """渲染**某一页**（4 列）的纯文本行：本页自己的表头 + 7 个行标签。

    `page_no` 取 1 或 2。本页列宽按本页 4 列独立计算，不受另一页影响——
    第 1 页某格特别长时不会把第 2 页的列撑成同一宽度。
    """
    spec = {no: cols for no, cols in PAGE_SPECS}.get(int(page_no))
    if spec is None:
        raise ValueError(f"0球~7+ 表只有 {PAGE_TOTAL} 页，收到 page_no={page_no!r}")
    valid = validate_goal_table_data(data)
    return _format_rows(spec, _render_rows(valid, spec, compact))


def build_goal_table_pages_lines(data: Any, compact: bool = False) -> list[list[str]]:
    """两页的行列表，一页一项；顺序固定 0~3 球 → 4/5/6/7+。"""
    valid = validate_goal_table_data(data)
    return [
        _format_rows(cols, _render_rows(valid, cols, compact))
        for _no, cols in PAGE_SPECS
    ]


def build_goal_table_text(data: Any, compact: bool = False) -> str:
    """纯文本表格（无任何标记），供 Streamlit 等宽块直接展示。"""
    return "\n".join(build_goal_table_lines(data, compact=compact))


def _wrap_table_text(table_text: str, use_html_pre: bool) -> str:
    if use_html_pre:
        return f"<pre>{html.escape(table_text)}</pre>"
    return f"```text\n{table_text}\n```"


def build_telegram_goal_table(
    data: dict, compact: bool = False, use_html_pre: bool = True
) -> str:
    """渲染成**单条 8 列** Telegram 消息（HTML ``<pre>`` 或 ```text 围栏）。

    返回值永远是单条字符串。渲染后的最终文本超过 Telegram 4096 上限时抛
    ``GoalTableTooLongError``——本函数不会静默截断，也不返回半张表。要发
    固定两页请改用 ``build_telegram_goal_table_messages``（一页一条）。
    """
    message = _wrap_table_text(build_goal_table_text(data, compact=compact), use_html_pre)
    if len(message) > TELEGRAM_TEXT_LIMIT:
        raise GoalTableTooLongError(
            f"0球~7+ 表单条 {len(message)} 字符，超过 Telegram {TELEGRAM_TEXT_LIMIT} 上限；"
            "请用 build_telegram_goal_table_messages 按页发送"
        )
    return message


def _chunk_rows(
    header: str, rows: list[str], limit: int, renderer: Callable[[str], str]
) -> list[list[str]]:
    """按行把 rows 切成若干块（表头另挂），单行装不下就抛错，绝不劈开一行。

    ``renderer`` 把「表体纯文本」变成最终形态（HTML ``<pre>`` 或原样），
    长度闸门按**渲染后**的长度算，所以两种形态共用这一份切分逻辑。
    """

    def rendered_len(chunk: list[str]) -> int:
        return len(renderer("\n".join([header, *chunk])))

    if rendered_len(rows) <= limit:
        return [rows]

    chunks: list[list[str]] = []
    current: list[str] = []
    for row in rows:
        if not current:
            if rendered_len([row]) > limit:
                raise GoalTableTooLongError(
                    f"0球~7+ 表单行 {rendered_len([row])} 字符，超过 {limit} 上限，无法按行拆条"
                )
            current = [row]
            continue
        if rendered_len([*current, row]) > limit:
            chunks.append(current)
            current = [row]
        else:
            current.append(row)
    if current:
        chunks.append(current)
    return chunks


def page_title(title: str, columns: Iterable[str], page_no: int) -> str:
    """页码标题：原表名后只加「列范围 + 页码」，不改表名本身。"""
    cols = list(columns)
    return f"{title}{cols[0]}~{cols[-1]}（{page_no}/{PAGE_TOTAL}）"


def render_page_block(body: str, use_html_pre: bool) -> str:
    """页内表体：HTML 模式包 ``<pre>``，纯文本模式**直接给表体**。

    纯文本不带任何围栏/语言标识——页与页之间只靠各自的页标题分隔，
    所以本模块的纯文本输出不依赖围栏写法（``_wrap_table_text`` 的
    纯文本分支另有在途改动，这里不去耦合它）。
    """
    if use_html_pre:
        return f"<pre>{html.escape(body)}</pre>"
    return body


def _page_messages(
    page_rows: list[str],
    title_line: str,
    note_lines: list[str],
    limit: int,
    use_html_pre: bool,
) -> list[str]:
    """一页 -> 1..n 条消息：页内仍超长时才按行兜底，每块重挂本页表头与页标题。

    ``note_lines``（出处行 / 缺项说明）**只挂在本页第一条**：一页被拆成多条时
    它们也不会跟着复制，全仓同一信息仍然只出现一次。
    """
    header, rows = page_rows[0], page_rows[1:]

    def heads(first: bool) -> list[str]:
        lines = [title_line, *note_lines] if first else [title_line]
        return [html.escape(line) if use_html_pre else line for line in lines]

    def rendered(chunk_text: str) -> str:
        return render_page_block(chunk_text, use_html_pre)

    max_head_len = max(
        len("\n".join(heads(first=True))), len("\n".join(heads(first=False)))
    )
    inner_limit = limit - (max_head_len + 1)
    if inner_limit <= 0:
        raise GoalTableTooLongError(
            f"0球~7+ 表页标题 {max_head_len} 字符，已占满 {limit} 上限，无法容纳表体"
        )
    out: list[str] = []
    for index, chunk in enumerate(_chunk_rows(header, rows, inner_limit, rendered)):
        table = rendered("\n".join([header, *chunk]))
        out.append("\n".join([*heads(first=index == 0), table]))
    return out


def _all_page_messages(
    data: Any,
    compact: bool,
    max_len: int,
    title: str,
    notes: Iterable[str],
    use_html_pre: bool,
) -> list[str]:
    """两页 -> 消息列表的**唯一**装配路径（Telegram 与纯文本共用）。

    每页先出自己的页码标题；``notes`` 只随第 1 页走，所以「出处行/缺项说明
    全仓只出现一次」由这一处保证，两条出口不可能各写一份而漂移。
    """
    limit = int(max_len or TELEGRAM_TEXT_LIMIT)
    pages = build_goal_table_pages_lines(data, compact=compact)
    note_lines = [str(note) for note in notes if str(note).strip()]
    out: list[str] = []
    for index, ((page_no, columns), rows) in enumerate(zip(PAGE_SPECS, pages)):
        out.extend(
            _page_messages(
                rows,
                page_title(title, columns, page_no),
                note_lines if index == 0 else [],
                limit,
                use_html_pre,
            )
        )
    return out


def build_telegram_goal_table_messages(
    data: dict,
    compact: bool = False,
    use_html_pre: bool = True,
    max_len: int = TELEGRAM_TEXT_LIMIT,
    title: str = DEFAULT_TITLE,
    notes: Iterable[str] = (),
) -> list[str]:
    """渲染成**固定两页**的 Telegram 消息，一页一条。

    第 1 页 0球~3球、第 2 页 4球~7+（切分点与 ``score_matrices`` 一致），
    每页各自重挂表头与 7 个行标签、按本页 4 列独立算列宽。``notes``
    （出处行 / 缺项说明）只挂在第 1 页，第 2 页不重复。

    正常情况下返回长度恰为 2 的列表；某页仍超过 ``max_len`` 时该页按行再拆，
    单行本身装不下时抛 ``GoalTableTooLongError``——宁可报错，也不把一行劈成
    两半破坏对齐。
    """
    return _all_page_messages(data, compact, max_len, title, notes, use_html_pre)


def build_goal_table_messages_text(
    data: Any,
    compact: bool = False,
    max_len: int = TELEGRAM_TEXT_LIMIT,
    title: str = DEFAULT_TITLE,
    notes: Iterable[str] = (),
) -> list[str]:
    """纯文本（无围栏）的**固定两页**结果，供 Streamlit/日志按页展示。"""
    return _all_page_messages(data, compact, max_len, title, notes, False)


# ------------------------------------------------------------ 数据缺口可见化
def format_goal_table_coverage_note(data: Any) -> str:
    """一行说明哪些列没有数据（"-" 必须在报告里点名，不能当成功）。"""
    valid = validate_goal_table_data(data)
    gaps: list[str] = []
    for label, key, odds_key in PRIMARY_ROWS:
        src = valid[key]
        empty = [col for col in COLS if _fmt_num(src.get(col, "-")) == "-"]
        if odds_key:
            odds_empty = [
                col for col in COLS if _fmt_num(valid[odds_key].get(col, "-")) == "-"
            ]
            extra = [col for col in odds_empty if col not in empty]
            if extra:
                gaps.append(f"{label}赔另缺 {'、'.join(extra)}")
        if len(empty) == len(COLS):
            gaps.append(f"{label} 整行无数据")
        elif empty:
            gaps.append(f"{label} {'、'.join(empty)}")
    if not gaps:
        return "数据覆盖：均/胜/平/负 4 行 × 8 列 均有数据"
    return "数据缺口（表内显示为 -）：" + "；".join(gaps)


# ------------------------------------------------------------------- 数据装配
def _bucket_of_total(total: int) -> str:
    return "7+" if total >= 7 else f"{total}球"


def _bucket_key(raw: Any) -> str | None:
    """把 avg_0 / avg_7plus / 0 / '7+' / '3球' 统一成 COLS 里的列名。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        number = int(raw)
        return _bucket_of_total(number) if number >= 0 else None
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("avg_"):
        text = text[len("avg_"):]
    elif text.startswith("prob_"):
        text = text[len("prob_"):]
    text = text.strip()
    if text in COLS:
        return text
    if text.endswith("球"):
        text = text[:-1].strip()
        if text in COLS:
            return text
    if text in ("7+", "7plus", "7+plus", "7"):
        return "7+"
    try:
        number = int(text)
    except ValueError:
        return None
    return _bucket_of_total(number) if number >= 0 else None


_OTHER_OPTIONS = (
    ("胜其它", "胜"),
    ("胜其他", "胜"),
    ("平其它", "平"),
    ("平其他", "平"),
    ("负其它", "负"),
    ("负其他", "负"),
)


def _split_option(raw: Any) -> tuple[str, str] | None:
    """返回 (方向, 列名)；识别不了返回 None。"""
    text = clean_cell_text(str(raw or "").replace("：", "-").replace(":", "-"))
    for option, direction in _OTHER_OPTIONS:
        if text == option:
            return direction, "7+"
    match = re.match(r"^(\d+)\s*-\s*(\d+)$", text)
    if not match:
        return None
    home, away = int(match.group(1)), int(match.group(2))
    direction = "胜" if home > away else ("平" if home == away else "负")
    return direction, _bucket_of_total(home + away)


def _odd_value(raw: Any) -> float | None:
    if isinstance(raw, Mapping):
        for key in ("odds", "avg", "value"):
            if raw.get(key) is not None:
                raw = raw.get(key)
                break
        else:
            return None
    try:
        number = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 1 else None


def build_goal_table_data(
    total_goals_avg_odds: Any = None,
    correct_score_odds: Any = None,
) -> dict[str, Any]:
    """用真实的总进球均赔与比分赔率装配 7 键 data（不填充、不兜底、不编造）。

    total_goals_avg_odds：``tg_avg_odds`` 原样（avg_0..avg_7plus），也接受
        {'0球': 2.91} 或 {0: 2.91}。
    correct_score_odds：比分 -> 赔率（或 {'odds': x}）。竞彩其它选项按 7+ 归集。

    返回 {'data': 7 键 dict, 'missing': [...], 'avg_columns': n,
          'score_options': n}；missing 逐项列出没有数据的「行 列」。
    """
    avg_src = total_goals_avg_odds if isinstance(total_goals_avg_odds, Mapping) else {}
    score_src = correct_score_odds if isinstance(correct_score_odds, Mapping) else {}

    avg: dict[str, str] = {col: "-" for col in COLS}
    for raw_key, raw_val in avg_src.items():
        col = _bucket_key(raw_key)
        odd = _odd_value(raw_val)
        if col and odd is not None:
            avg[col] = f"{odd:.2f}"

    buckets: dict[str, dict[str, list[tuple[float, str]]]] = {
        "胜": {col: [] for col in COLS},
        "平": {col: [] for col in COLS},
        "负": {col: [] for col in COLS},
    }
    used = 0
    for raw_score, raw_odd in score_src.items():
        split = _split_option(raw_score)
        odd = _odd_value(raw_odd)
        if split is None or odd is None:
            continue
        direction, col = split
        buckets[direction][col].append((odd, clean_cell_text(raw_score)))
        used += 1

    data: dict[str, dict[str, str]] = {key: {} for key in DATA_KEYS}
    data["avg"] = avg
    for direction, prefix in (("胜", "win"), ("平", "draw"), ("负", "lose")):
        for col in COLS:
            items = sorted(buckets[direction][col], key=lambda item: (item[0], item[1]))
            if not items:
                data[f"{prefix}_score"][col] = "-"
                data[f"{prefix}_odds"][col] = "-"
                continue
            data[f"{prefix}_score"][col] = " ".join(label for _, label in items)
            data[f"{prefix}_odds"][col] = " ".join(f"{odd:.2f}" for odd, _ in items)

    return {
        "data": data,
        "missing": [
            f"{key} {col}"
            for key in DATA_KEYS
            for col in COLS
            if _fmt_num(data[key].get(col)) == "-"
        ],
        "avg_columns": sum(1 for col in COLS if avg[col] != "-"),
        "score_options": used,
    }


# ------------------------------------------------------------------- 报告段落
def build_goal_table_section(
    data: Any,
    compact: bool = False,
    use_html_pre: bool = True,
    title: str = "⚽【0球~7+ 均赔/比分表】",
    source_note: str = "",
    max_len: int = TELEGRAM_TEXT_LIMIT,
) -> list[str]:
    """组装成可直接发给 Telegram 的**固定两页**段落（一页一条）。

    标题、压缩说明、出处行与缺口说明全部只挂在第 1 页；第 2 页只有自己的
    页码标题和表体，所以「同一信息全仓只出现一次」。缺口说明永远在，缺数据
    不可能被当成「整表正常」。
    """
    notes: list[str] = []
    if compact:
        notes.append("窄屏压缩：赔率保留≤2位小数（22.02/22.39/21.85 仍可区分）")
    if source_note:
        notes.append(source_note)
    notes.append(format_goal_table_coverage_note(data))
    return build_telegram_goal_table_messages(
        data,
        compact=compact,
        use_html_pre=use_html_pre,
        max_len=int(max_len or TELEGRAM_TEXT_LIMIT),
        title=title,
        notes=notes,
    )
