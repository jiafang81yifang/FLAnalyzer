"""Exact-score matrices: 31 scores, 15 tactical pools, and the minimum-|delta| top 8.

命名警告（必读）：本模块的 15 个「战术池」按比分组合与战术语义把 31 项全集
切成 15 个固定桶（互斥、无遗漏、永不截断），每个池是一条聚合行。

它 **不是** app_v84_hermes 里的 ``pool15_enriched``：后者是
``ranked_scores[:15]`` 按模型概率取出的逐比分候选清单，元素是
``score / theory / market / ibi / delta`` 的单比分 dict，会被截断。

一句话区别：**战术池是 31 项全集的固定 15 桶聚合，pool15_enriched 是概率
Top-15 的比分清单**。两者元素类型、基数、语义都不同，禁止互相赋值、禁止
zip、禁止把它们喂给任何以「概率 Top-15 清单」为形参的函数（例如
``run_ventropy_pipeline`` 的第二个参数、``evaluate_pool15_battle`` 的入参）。

本模块的公开标识一律使用 ``tactical_pool`` / ``matrix_tactical_pools``，
刻意不含 ``pool15`` 字样；``THIRTY_ONE_SCORES`` / ``CANONICAL_PIPELINE_ORDER`` /
``PIPELINE_POSITION`` 三者关系见各自 docstring。
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class MatrixIntegrityError(ValueError):
    """31 比分矩阵、15 战术池矩阵或最低前 8 均差矩阵构建时因缺失/非法数据抛出的熔断异常。"""


# 标准 31 个精确比分全集（顺序与竞彩/澳客 31 项保持一致，用户指定，勿改）。
THIRTY_ONE_SCORES: tuple[str, ...] = (
    # 主胜 13 项
    "1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
    "4-0", "4-1", "4-2", "5-0", "5-1", "5-2", "胜其它",
    # 平局 5 项
    "0-0", "1-1", "2-2", "3-3", "平其它",
    # 客胜 13 项
    "0-1", "0-2", "1-2", "0-3", "1-3", "2-3",
    "0-4", "1-4", "2-4", "0-5", "1-5", "2-5", "负其它",
)

# 仓库既有规范顺序（blade_deepseek_pipeline.JCZQ_ALL_OPTIONS）：
# 12 主胜 + 4 平局 + 12 客胜 + ['胜其它','平其它','负其它']。
# 与 THIRTY_ONE_SCORES 集合相同、顺序不同（首个分歧在 idx 12：
# 本模块 '胜其它' vs 流水线 '0-0'）。两者**不可**按下标 zip，必须按比分名映射；
# PIPELINE_POSITION 给出 score -> 流水线下标，专门用来避免错位。
CANONICAL_PIPELINE_ORDER: tuple[str, ...] = (
    "1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
    "4-0", "4-1", "4-2", "5-0", "5-1", "5-2",
    "0-0", "1-1", "2-2", "3-3",
    "0-1", "0-2", "1-2", "0-3", "1-3", "2-3",
    "0-4", "1-4", "2-4", "0-5", "1-5", "2-5",
    "胜其它", "平其它", "负其它",
)

_SCORE_INDEX: dict[str, int] = {score: idx for idx, score in enumerate(THIRTY_ONE_SCORES)}

# score -> 流水线（JCZQ_ALL_OPTIONS）下标。按名映射，不按下标。
PIPELINE_POSITION: dict[str, int] = {
    score: idx for idx, score in enumerate(CANONICAL_PIPELINE_ORDER)
}

SCORE_DIRECTION_MAP: dict[str, str] = {
    "1-0": "WIN", "2-0": "WIN", "2-1": "WIN",
    "3-0": "WIN", "3-1": "WIN", "3-2": "WIN",
    "4-0": "WIN", "4-1": "WIN", "4-2": "WIN",
    "5-0": "WIN", "5-1": "WIN", "5-2": "WIN",
    "胜其它": "WIN",
    "0-0": "DRAW", "1-1": "DRAW", "2-2": "DRAW", "3-3": "DRAW", "平其它": "DRAW",
    "0-1": "LOSS", "0-2": "LOSS", "1-2": "LOSS",
    "0-3": "LOSS", "1-3": "LOSS", "2-3": "LOSS",
    "0-4": "LOSS", "1-4": "LOSS", "2-4": "LOSS",
    "0-5": "LOSS", "1-5": "LOSS", "2-5": "LOSS",
    "负其它": "LOSS",
}

CORE_PROTECTED_SET = {"1-0", "2-0", "0-1", "0-2", "1-1"}
OVERKILL_TAIL_SET = {"胜其它", "负其它"}

DIRECTION_ORDER: tuple[str, ...] = ("WIN", "DRAW", "LOSS")
EXPECTED_DIRECTION_COUNTS: dict[str, int] = {"WIN": 13, "DRAW": 5, "LOSS": 13}

# 第 1 名与第 2 名比值 <= 该阈值即判为均势（1.35 均势检测）。
EQUAL_MATCH_RATIO_THRESHOLD: float = 1.35

# 15 战术池定义（覆盖全部 31 个比分，互斥无遗漏）。
# 注意：这不是「概率 Top-15 比分清单」，见模块 docstring。
TACTICAL_POOL_DEFINITIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("P01_WIN_LOW_LOCK", "主胜零封一球池", ("1-0",)),
    ("P02_WIN_TWO_LOCK", "主胜零封两球池", ("2-0",)),
    ("P03_WIN_TIGHT_MID", "主胜一球胶着池", ("2-1", "3-2")),
    ("P04_WIN_THREE_PLUS", "主胜三球优势池", ("3-0", "3-1")),
    ("P05_WIN_HEAVY_BLOWOUT", "主胜四球及以上穿盘池", ("4-0", "4-1", "4-2", "5-0", "5-1", "5-2")),
    ("P06_WIN_OVERKILL", "主胜极端杀穿尾部池", ("胜其它",)),
    ("P07_DRAW_ZERO", "闷平零球防线池", ("0-0",)),
    ("P08_DRAW_ONE", "一球对攻平局池", ("1-1",)),
    ("P09_DRAW_HIGH", "多球激战平局池", ("2-2", "3-3", "平其它")),
    ("P10_LOSS_LOW_LOCK", "客胜零封一球池", ("0-1",)),
    ("P11_LOSS_TWO_LOCK", "客胜零封两球池", ("0-2",)),
    ("P12_LOSS_TIGHT_MID", "客胜一球胶着池", ("1-2", "2-3")),
    ("P13_LOSS_THREE_PLUS", "客胜三球优势池", ("0-3", "1-3")),
    ("P14_LOSS_HEAVY_BLOWOUT", "客胜四球及以上穿盘池", ("0-4", "1-4", "2-4", "0-5", "1-5", "2-5")),
    ("P15_LOSS_OVERKILL", "客胜极端杀穿尾部池", ("负其它",)),
]

TACTICAL_POOL_COUNT: int = 15


@dataclass(frozen=True)
class ScoreMatrixRow:
    """31 个比分矩阵单行条目。"""

    score: str
    direction: str
    market_avg_odds: float
    lottery_odds: float
    delta: float  # 市均赔率 - 竞彩赔率
    abs_delta: float
    heat_seats: int
    is_core_protected: bool
    is_overkill_tail: bool


@dataclass(frozen=True)
class TacticalPoolRow:
    """15 战术池矩阵单行条目（固定分桶聚合，不是概率 Top-15 清单）。

    ``core_ratio`` 与 ``core_heat_ratio`` 的分工：
      - ``core_ratio = core_count / len(scores)``：核心比分在池内的**个数**占比。
        在本模块固定的 15 池定义下，5 个核心比分各自独占一个单比分池，
        因此它只会取 0.0 或 1.0，等价于「这个池是不是核心池」的布尔量，
        不携带任何富集度信息。保留它只为满足需求原文，请勿当作连续指标。
      - ``core_heat_ratio = core_heat_seats / total_heat_seats``：核心比分的
        **热度席位**占池内全部热度的比例，是真正连续的富集度指标。
        分母为 0（池内无人下注）时返回 ``None`` —— 无定义就是无定义，
        绝不兜底成 0.0。
    """

    pool_id: str
    pool_name: str
    scores: tuple[str, ...]
    avg_market_odds: float
    avg_lottery_odds: float
    avg_delta: float
    abs_avg_delta: float
    total_heat_seats: int
    core_count: int
    core_ratio: float
    overkill_count: int
    core_heat_seats: int
    core_heat_ratio: float | None


@dataclass(frozen=True)
class Top8DeltaRow:
    """最低前 8 均差矩阵单行条目（按 abs_delta 升序）。"""

    rank: int
    score: str
    direction: str
    delta: float
    abs_delta: float
    lottery_odds: float
    heat_seats: int
    ratio_to_rank1: float


@dataclass(frozen=True)
class ChannelAbsSummary:
    """三向通道 |Δ| 摘要（WIN/DRAW/LOSS）。"""

    win_mean_abs_delta: float
    draw_mean_abs_delta: float
    loss_mean_abs_delta: float
    win_count: int
    draw_count: int
    loss_count: int
    min_channel: str
    channel_spread: float


@dataclass(frozen=True)
class CompleteScoreMatrices:
    """三套矩阵完整输出容器。"""

    match_id: str
    matrix_31: list[ScoreMatrixRow]
    matrix_tactical_pools: list[TacticalPoolRow]
    top_8_min_abs_delta: list[Top8DeltaRow]
    rank1_rank2_ratio: float
    is_even_match: bool
    channel_summary: ChannelAbsSummary

    @property
    def channel_abs_summary(self) -> dict[str, float]:
        """旧字典形状的只读别名（键名与原版一致），主输出仍是 channel_summary。"""
        return {
            "WIN_mean_abs_delta": self.channel_summary.win_mean_abs_delta,
            "DRAW_mean_abs_delta": self.channel_summary.draw_mean_abs_delta,
            "LOSS_mean_abs_delta": self.channel_summary.loss_mean_abs_delta,
        }


def _parse_odds(val: Any, label: str) -> float:
    """严格解析赔率：接受 int / float / 数字字符串，失败即抛，绝不给默认值。

    与 convergence_arbitrator._to_float 接受同一类输入（``float(value)`` 能解析的
    数值与字符串），但**没有** ``default`` 参数：那个实现失败时返回 2.5，
    正是本模块禁止的静默兜底。

    超出 float 表示范围的整数（如 ``10**400``）会让 ``float()`` 抛 ``OverflowError``，
    而不是 ``ValueError``。数据问题一律是 MatrixIntegrityError，所以连同
    ``decimal.InvalidOperation`` 一起在此收口，绝不让数值域异常漏给调用方。
    """
    if isinstance(val, bool) or val is None:
        raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 赔率缺失或非法: {val!r}")
    if not isinstance(val, (int, float, str)):
        raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 赔率类型非法: {type(val).__name__}")
    try:
        num = float(val)
        # 放在同一个 try 里：转换和有限性判定任何一步的数值域异常都统一收口。
        finite = math.isfinite(num)
    except (TypeError, ValueError, OverflowError, InvalidOperation) as exc:
        raise MatrixIntegrityError(
            f"[矩阵熔断] '{label}' 赔率无法解析为有限数值（溢出或非数值）: {val!r}"
        ) from exc
    if not finite:
        raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 赔率数值非法: {num!r}")
    if num <= 1.0:
        raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 赔率必须 > 1.0: {num!r}")
    return round(num, 4)


def _check_heat_seats(val: Any, label: str) -> int:
    """严格解析热度席位：接受无小数部分的 int / float / 数字字符串。

    ``1`` / ``1.0`` / ``"1"`` / ``"1.0"`` 都收下并转 int；``1.5`` / ``-1`` /
    ``"abc"`` / ``True`` 抛 MatrixIntegrityError。绝不 ``int(x)`` 硬截断
    （``1.9`` 会被静默截成 1）。
    """
    if isinstance(val, bool) or val is None:
        raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 热度席位非法: {val!r}")
    if isinstance(val, int):
        num = val
    elif isinstance(val, float):
        if not math.isfinite(val) or not float(val).is_integer():
            raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 热度席位必须为整数: {val!r}")
        num = int(val)
    elif isinstance(val, str):
        try:
            dec = Decimal(val.strip())
        except (InvalidOperation, ValueError) as exc:
            raise MatrixIntegrityError(
                f"[矩阵熔断] '{label}' 热度席位无法解析为整数: {val!r}"
            ) from exc
        if not dec.is_finite() or dec != dec.to_integral_value():
            raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 热度席位必须为整数: {val!r}")
        num = int(dec)
    else:
        raise MatrixIntegrityError(
            f"[矩阵熔断] '{label}' 热度席位类型非法: {type(val).__name__}"
        )
    if num < 0:
        raise MatrixIntegrityError(f"[矩阵熔断] '{label}' 热度席位不得为负: {num!r}")
    return num


def _check_match_id(match_id: Any) -> str:
    """match_id 会进 Markdown 与 TG：非空、<=64 字符、不含换行与竖线。"""
    if not isinstance(match_id, str):
        raise MatrixIntegrityError(
            f"[矩阵熔断] match_id 必须为字符串: {type(match_id).__name__}"
        )
    text = match_id.strip()
    if not text:
        raise MatrixIntegrityError("[矩阵熔断] match_id 不能为空")
    if len(text) > 64:
        raise MatrixIntegrityError(f"[矩阵熔断] match_id 长度 {len(text)} 超过上限 64")
    if "\n" in text or "\r" in text:
        raise MatrixIntegrityError(f"[矩阵熔断] match_id 不得包含换行: {text!r}")
    if "|" in text:
        raise MatrixIntegrityError(f"[矩阵熔断] match_id 不得包含竖线: {text!r}")
    return text


def _mean(values: Sequence[float], label: str) -> float:
    """均值前断言非空；空样本抛 MatrixIntegrityError 并点名池/方向。

    ``statistics.mean([])`` 抛的是 ``StatisticsError``，调用方拦不住，
    所以这里先拦一道，错误类型统一为 MatrixIntegrityError。
    """
    if not values:
        raise MatrixIntegrityError(
            f"[矩阵熔断] '{label}' 样本为空，均值无定义（拒绝默认值兜底）"
        )
    return statistics.mean(values)


def _validate_tactical_pool_definitions(
    definitions: Sequence[tuple[str, str, tuple[str, ...]]],
) -> None:
    """入口自校验 15 战术池定义表：覆盖 31 项、无重复、每池非空、池 id 唯一。"""
    if len(definitions) != TACTICAL_POOL_COUNT:
        raise MatrixIntegrityError(
            f"[矩阵熔断] 战术池定义表应有 {TACTICAL_POOL_COUNT} 个池，实际 {len(definitions)} 个"
        )
    expected = set(THIRTY_ONE_SCORES)
    seen: dict[str, str] = {}
    pool_ids: set[str] = set()
    for pool_id, _pool_name, pool_scores in definitions:
        if pool_id in pool_ids:
            raise MatrixIntegrityError(f"[矩阵熔断] 战术池 id 重复: '{pool_id}'")
        pool_ids.add(pool_id)
        if not pool_scores:
            raise MatrixIntegrityError(f"[矩阵熔断] 战术池 '{pool_id}' 为空，未包含任何比分")
        for score in pool_scores:
            if score not in expected:
                raise MatrixIntegrityError(
                    f"[矩阵熔断] 战术池 '{pool_id}' 含未知比分 '{score}'，不在 31 项全集内"
                )
            if score in seen:
                raise MatrixIntegrityError(
                    f"[矩阵熔断] 比分 '{score}' 同时出现在 '{seen[score]}' 与 '{pool_id}'，违反互斥"
                )
            seen[score] = pool_id
    missing = [score for score in THIRTY_ONE_SCORES if score not in seen]
    if missing:
        raise MatrixIntegrityError(
            f"[矩阵熔断] 战术池定义表漏项，未覆盖比分: {missing}"
        )


def _top8_sort_key(row: ScoreMatrixRow) -> tuple[float, int, int, int, float, int]:
    """最低前 8 的确定性排序键（并列时逐级打破平手）。

    1. abs_delta 升序（主键）
    2. 核心受保护优先
    3. 尾部杀穿靠后
    4. heat_seats 降序
    5. lottery_odds 升序
    6. THIRTY_ONE_SCORES 原始下标（确定性来自固定全集顺序，不用字符串比较）
    """
    return (
        row.abs_delta,
        0 if row.is_core_protected else 1,
        1 if row.is_overkill_tail else 0,
        -row.heat_seats,
        row.lottery_odds,
        _SCORE_INDEX[row.score],
    )


def build_tactical_pools(
    row_by_score: Mapping[str, ScoreMatrixRow],
    definitions: Sequence[tuple[str, str, tuple[str, ...]]] | None = None,
) -> list[TacticalPoolRow]:
    """把 31 个比分行聚成 15 个战术池行。

    池级 ``avg_delta`` 由 ``avg_market_odds - avg_lottery_odds`` **派生**（不再二次
    round），这是唯一真相：读者自己减一遍永远逐位对得上，不再与「每行 delta 先
    round 再平均」的另一条舍入路径并存。展示层统一用 ``:.4f`` 格式化。
    """
    defs = TACTICAL_POOL_DEFINITIONS if definitions is None else definitions
    _validate_tactical_pool_definitions(defs)

    pools: list[TacticalPoolRow] = []
    for pool_id, pool_name, pool_scores in defs:
        pool_rows = [row_by_score[score] for score in pool_scores]
        label = f"战术池 {pool_id}"
        avg_m = round(_mean([r.market_avg_odds for r in pool_rows], f"{label} avg_market_odds"), 4)
        avg_j = round(_mean([r.lottery_odds for r in pool_rows], f"{label} avg_lottery_odds"), 4)
        avg_d = avg_m - avg_j
        total_heat = sum(r.heat_seats for r in pool_rows)
        core_rows = [r for r in pool_rows if r.is_core_protected]
        core_heat = sum(r.heat_seats for r in core_rows)
        pools.append(
            TacticalPoolRow(
                pool_id=pool_id,
                pool_name=pool_name,
                scores=tuple(pool_scores),
                avg_market_odds=avg_m,
                avg_lottery_odds=avg_j,
                avg_delta=avg_d,
                abs_avg_delta=abs(avg_d),
                total_heat_seats=total_heat,
                core_count=len(core_rows),
                core_ratio=len(core_rows) / len(pool_rows),
                overkill_count=sum(1 for r in pool_rows if r.is_overkill_tail),
                core_heat_seats=core_heat,
                core_heat_ratio=(core_heat / total_heat) if total_heat else None,
            )
        )
    return pools


def build_score_matrices(
    match_id: str,
    market_avg_scores: dict[str, Any],
    jc_scores_data: dict[str, dict[str, Any]],
) -> CompleteScoreMatrices:
    """基于完整 31 项比分数据构建：
    1) 31 个比分矩阵
    2) 15 战术池比分矩阵
    3) 最低前 8 均差矩阵
    严格要求 31 项比分全部齐备，缺失任何一项立即抛错，禁止默认值兜底。
    """
    clean_match_id = _check_match_id(match_id)
    if not isinstance(market_avg_scores, dict) or not isinstance(jc_scores_data, dict):
        raise MatrixIntegrityError("[矩阵熔断] market_avg_scores 与 jc_scores_data 必须为字典")

    # 入口先校验 15 战术池定义表，避免漏项/重复/未知比分拖到中途才炸。
    _validate_tactical_pool_definitions(TACTICAL_POOL_DEFINITIONS)

    # 严格校验 31 个比分全集无缺失、无多余未知项
    expected_set = set(THIRTY_ONE_SCORES)
    actual_m_set = set(market_avg_scores.keys())
    actual_j_set = set(jc_scores_data.keys())
    if actual_m_set != expected_set:
        missing = sorted(expected_set - actual_m_set)
        extra = sorted(actual_m_set - expected_set)
        raise MatrixIntegrityError(
            f"[矩阵熔断] market_avg_scores 31 比分不完整，缺失={missing}，多余={extra}"
        )
    if actual_j_set != expected_set:
        missing = sorted(expected_set - actual_j_set)
        extra = sorted(actual_j_set - expected_set)
        raise MatrixIntegrityError(
            f"[矩阵熔断] jc_scores_data 31 比分不完整，缺失={missing}，多余={extra}"
        )

    # 1. 构建 31 个比分矩阵
    matrix_31: list[ScoreMatrixRow] = []
    row_by_score: dict[str, ScoreMatrixRow] = {}

    for sc in THIRTY_ONE_SCORES:
        m_odd = _parse_odds(market_avg_scores[sc], f"market_avg_scores.{sc}")
        j_item = jc_scores_data[sc]
        if not isinstance(j_item, dict):
            raise MatrixIntegrityError(f"[矩阵熔断] jc_scores_data['{sc}'] 非字典")
        j_odd = _parse_odds(j_item.get("odds"), f"jc_scores_data.{sc}.odds")
        heat = _check_heat_seats(j_item.get("heat_seats"), f"jc_scores_data.{sc}.heat_seats")

        delta = round(m_odd - j_odd, 4)
        row = ScoreMatrixRow(
            score=sc,
            direction=SCORE_DIRECTION_MAP[sc],
            market_avg_odds=m_odd,
            lottery_odds=j_odd,
            delta=delta,
            abs_delta=round(abs(delta), 4),
            heat_seats=heat,
            is_core_protected=sc in CORE_PROTECTED_SET,
            is_overkill_tail=sc in OVERKILL_TAIL_SET,
        )
        matrix_31.append(row)
        row_by_score[sc] = row

    # 2. 构建 15 战术池矩阵
    matrix_tactical_pools = build_tactical_pools(row_by_score, TACTICAL_POOL_DEFINITIONS)

    # 3. 构建最低前 8 均差矩阵（按 abs_delta 升序取前 8，并列时逐级打破平手）
    sorted_by_abs = sorted(matrix_31, key=_top8_sort_key)
    top8_raw = sorted_by_abs[:8]
    rank1_abs = top8_raw[0].abs_delta
    if rank1_abs == 0.0:
        # 31 项 |Δ| 最小值为 0（全零时更是全部为 0），除数为 0，比值无定义。
        raise MatrixIntegrityError(
            f"[矩阵熔断] 31 项 |Δ| 最小值为 0（{top8_raw[0].score}），"
            "第 1 名倍数与第 1/第 2 名比值均无定义，拒绝 1e-9 兜底"
        )

    top_8_min_abs_delta: list[Top8DeltaRow] = [
        Top8DeltaRow(
            rank=idx + 1,
            score=item.score,
            direction=item.direction,
            delta=item.delta,
            abs_delta=item.abs_delta,
            lottery_odds=item.lottery_odds,
            heat_seats=item.heat_seats,
            ratio_to_rank1=round(item.abs_delta / rank1_abs, 4),
        )
        for idx, item in enumerate(top8_raw)
    ]

    # rank1_rank2_ratio 直接取 TOP-8 第 2 行的 ratio_to_rank1：
    # 与表格里印出来的数字完全一致，均势判定不可能与表打架。
    rank1_rank2_ratio = top_8_min_abs_delta[1].ratio_to_rank1
    is_even_match = rank1_rank2_ratio <= EQUAL_MATCH_RATIO_THRESHOLD

    # 三向通道均差绝对值均值汇总
    by_direction: dict[str, list[ScoreMatrixRow]] = {d: [] for d in DIRECTION_ORDER}
    for row in matrix_31:
        by_direction[row.direction].append(row)
    counts = {d: len(by_direction[d]) for d in DIRECTION_ORDER}
    for direction in DIRECTION_ORDER:
        if counts[direction] != EXPECTED_DIRECTION_COUNTS[direction]:
            raise MatrixIntegrityError(
                f"[矩阵熔断] {direction} 方向比分数量应为 {EXPECTED_DIRECTION_COUNTS[direction]}，"
                f"实际 {counts[direction]}，31 项方向树不完整"
            )
    channel_means = {
        direction: round(
            _mean(
                [r.abs_delta for r in by_direction[direction]],
                f"{direction} 方向 abs_delta",
            ),
            4,
        )
        for direction in DIRECTION_ORDER
    }
    min_channel = min(DIRECTION_ORDER, key=lambda d: (channel_means[d], DIRECTION_ORDER.index(d)))
    channel_summary = ChannelAbsSummary(
        win_mean_abs_delta=channel_means["WIN"],
        draw_mean_abs_delta=channel_means["DRAW"],
        loss_mean_abs_delta=channel_means["LOSS"],
        win_count=counts["WIN"],
        draw_count=counts["DRAW"],
        loss_count=counts["LOSS"],
        min_channel=min_channel,
        channel_spread=max(channel_means.values()) - min(channel_means.values()),
    )

    return CompleteScoreMatrices(
        match_id=clean_match_id,
        matrix_31=matrix_31,
        matrix_tactical_pools=matrix_tactical_pools,
        top_8_min_abs_delta=top_8_min_abs_delta,
        rank1_rank2_ratio=rank1_rank2_ratio,
        is_even_match=is_even_match,
        channel_summary=channel_summary,
    )


def _cell(value: Any) -> str:
    """Markdown 单元格转义：裸竖线会被当成列分隔符，必须写成 \\|。"""
    return str(value).replace("|", "\\|")


def format_matrices_markdown(bundle: CompleteScoreMatrices) -> str:
    """将三套矩阵格式化为清晰的 Markdown 表格输出。"""
    lines: list[str] = [f"# 赛事 {_cell(bundle.match_id)} 三套比分与均差矩阵\n"]

    lines.append("## 一、 31 个比分矩阵\n")
    lines.append("| 比分 | 方向 | 市均赔率 | 竞彩赔率 | 均差(Δ) | 绝对均差 | 热度席位 | 核心保护 | 尾部杀穿 |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for r in bundle.matrix_31:
        lines.append(
            f"| {_cell(r.score)} | {_cell(r.direction)} | {r.market_avg_odds:.2f} | {r.lottery_odds:.2f} | "
            f"{r.delta:+.4f} | {r.abs_delta:.4f} | {r.heat_seats} | "
            f"{'是' if r.is_core_protected else '否'} | {'是' if r.is_overkill_tail else '否'} |"
        )

    lines.append("\n## 二、 15 战术池比分矩阵\n")
    lines.append(
        "| 池编号 | 池名称 | 包含比分 | 池均市均 | 池均竞彩 | 池均差(Δ) | 绝对池均差 | "
        "合计热度 | 核心数 | 核心占比 | 尾部数 | 核心热度 | 核心热占比 |"
    )
    lines.append(
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | "
        ":---: | :---: | :---: | :---: | :---: |"
    )
    for p in bundle.matrix_tactical_pools:
        heat_ratio = "无定义(池内零热度)" if p.core_heat_ratio is None else f"{p.core_heat_ratio:.4f}"
        lines.append(
            f"| {_cell(p.pool_id)} | {_cell(p.pool_name)} | {_cell(', '.join(p.scores))} | "
            f"{p.avg_market_odds:.2f} | {p.avg_lottery_odds:.2f} | "
            f"{p.avg_delta:+.4f} | {p.abs_avg_delta:.4f} | {p.total_heat_seats} | "
            f"{p.core_count} | {p.core_ratio:.4f} | {p.overkill_count} | "
            f"{p.core_heat_seats} | {heat_ratio} |"
        )

    lines.append("\n## 三、 最低前 8 均差矩阵（按 |Δ| 升序）\n")
    lines.append("| 排名 | 比分 | 方向 | 均差(Δ) | 最小均差 | 竞彩赔率 | 热度席位 | 第1名倍数 |")
    lines.append("| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for t in bundle.top_8_min_abs_delta:
        lines.append(
            f"| #{t.rank} | {_cell(t.score)} | {_cell(t.direction)} | {t.delta:+.4f} | "
            f"{t.abs_delta:.4f} | {t.lottery_odds:.2f} | {t.heat_seats} | {t.ratio_to_rank1:.2f}x |"
        )

    cs = bundle.channel_summary
    lines.append(
        f"\n> 三向通道 |Δ| 均值：WIN=`{cs.win_mean_abs_delta:.4f}`（{cs.win_count} 项），"
        f"DRAW=`{cs.draw_mean_abs_delta:.4f}`（{cs.draw_count} 项），"
        f"LOSS=`{cs.loss_mean_abs_delta:.4f}`（{cs.loss_count} 项）；"
        f"最小通道=`{cs.min_channel}`，通道极差=`{cs.channel_spread:.4f}`。"
    )
    lines.append(
        f"> 第 1/第 2 名比值=`{bundle.rank1_rank2_ratio:.4f}`，"
        f"1.35 均势阈值判定：{'均势' if bundle.is_even_match else '非均势'}。"
    )
    return "\n".join(lines)


def paginate_tg_sections(
    sections: Sequence[tuple[Sequence[str], Sequence[str]]],
    max_len: int = 4096,
) -> list[str]:
    """按行分页到 Telegram 单条上限内，每页重挂所属小节的表头。

    - 绝不切断一行；单行超过上限直接抛 MatrixIntegrityError，不截断。
    - 每个分页块都以所属小节的头行开头，读者任何时候都知道在看哪张表。
    """
    if isinstance(max_len, bool) or not isinstance(max_len, int) or max_len <= 0:
        raise MatrixIntegrityError(f"[矩阵熔断] Telegram 分页上限非法: {max_len!r}")

    chunks: list[str] = []
    for headers, rows in sections:
        header_lines = list(headers)
        if not header_lines:
            raise MatrixIntegrityError("[矩阵熔断] Telegram 小节缺少表头行")
        for line in header_lines:
            if len(line) > max_len:
                raise MatrixIntegrityError(
                    f"[矩阵熔断] 表头单行长度 {len(line)} 超过 Telegram 上限 {max_len}，拒绝截断"
                )
        current: list[str] = []
        for line in rows:
            if len(line) > max_len:
                raise MatrixIntegrityError(
                    f"[矩阵熔断] 单行长度 {len(line)} 超过 Telegram 上限 {max_len}，"
                    f"拒绝截断: {line[:60]!r}"
                )
            if not current:
                if len("\n".join(header_lines + [line])) > max_len:
                    raise MatrixIntegrityError(
                        f"[矩阵熔断] 表头+单行已超过 Telegram 上限 {max_len}，无法分页且拒绝截断"
                    )
                current.append(line)
            elif len("\n".join(header_lines + current + [line])) > max_len:
                chunks.append("\n".join(header_lines + current))
                if len("\n".join(header_lines + [line])) > max_len:
                    raise MatrixIntegrityError(
                        f"[矩阵熔断] 表头+单行已超过 Telegram 上限 {max_len}，无法分页且拒绝截断"
                    )
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append("\n".join(header_lines + current))
        elif not rows:
            chunks.append("\n".join(header_lines))
    if not chunks:
        raise MatrixIntegrityError("[矩阵熔断] Telegram 小节为空，无可发送内容")
    return chunks


def format_matrices_telegram(bundle: CompleteScoreMatrices, max_len: int = 4096) -> list[str]:
    """把三套矩阵排成可直接发给 Telegram 的消息列表（按行分页，永不截断）。"""
    m31_rows = [
        f"{r.score:<5s} {r.direction:<4s} 市均{r.market_avg_odds:>7.2f} 竞彩{r.lottery_odds:>7.2f} "
        f"Δ{r.delta:+.4f} 热{r.heat_seats}{' 核' if r.is_core_protected else ''}"
        f"{' 尾' if r.is_overkill_tail else ''}"
        for r in bundle.matrix_31
    ]
    pool_rows = [
        f"{p.pool_id:<22s} 均Δ{p.avg_delta:+.4f} 热{p.total_heat_seats:>3d} "
        f"核{p.core_count}/{len(p.scores)} "
        f"核热{'无定义' if p.core_heat_ratio is None else format(p.core_heat_ratio, '.2f')}"
        for p in bundle.matrix_tactical_pools
    ]
    top8_rows = [
        f"#{t.rank} {t.score:<5s} {t.direction:<4s} |Δ|{t.abs_delta:.4f} "
        f"竞彩{t.lottery_odds:>6.2f} 热{t.heat_seats} {t.ratio_to_rank1:.2f}x"
        for t in bundle.top_8_min_abs_delta
    ]
    cs = bundle.channel_summary
    summary_rows = [
        f"WIN  {cs.win_mean_abs_delta:.4f}（{cs.win_count} 项）",
        f"DRAW {cs.draw_mean_abs_delta:.4f}（{cs.draw_count} 项）",
        f"LOSS {cs.loss_mean_abs_delta:.4f}（{cs.loss_count} 项）",
        f"最小通道 {cs.min_channel}，极差 {cs.channel_spread:.4f}",
        (
            f"第1/第2名比值 {bundle.rank1_rank2_ratio:.4f}，"
            f"{'均势' if bundle.is_even_match else '非均势'}（阈值 {EQUAL_MATCH_RATIO_THRESHOLD}）"
        ),
    ]
    sections = [
        (
            [f"📊 赛事 {bundle.match_id} 三套比分矩阵", "【一、31 项比分矩阵】"],
            m31_rows,
        ),
        (
            ["【二、15 战术池矩阵】"],
            pool_rows,
        ),
        (
            ["【三、最低前 8 均差矩阵】"],
            top8_rows,
        ),
        (
            ["【四、三向 |Δ| 摘要】"],
            summary_rows,
        ),
    ]
    return paginate_tg_sections(sections, max_len=max_len)


if __name__ == "__main__":
    # 构造完整 31 项比分测试样本
    sample_market_avg = {sc: round(6.5 + idx * 1.4, 2) for idx, sc in enumerate(THIRTY_ONE_SCORES)}
    sample_jc = {
        sc: {
            "odds": round(sample_market_avg[sc] + ((idx % 5) - 2) * 0.35, 2),
            "heat_seats": (idx % 6) + 1,
        }
        for idx, sc in enumerate(THIRTY_ONE_SCORES)
    }
    res = build_score_matrices("M20261126_31_15_8", sample_market_avg, sample_jc)
    print(format_matrices_markdown(res))
    print()
    print(f"Telegram 分页块数: {len(format_matrices_telegram(res))}")
