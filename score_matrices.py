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
import unicodedata
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
    """31 个比分矩阵单行条目。

    ``market_avg_odds`` / ``delta`` / ``abs_delta`` 允许为 ``None``，含义是**市均
    对该比分没有报价**（不是「市均等于竞彩」）。生产记录里 merge_cs31 会用竞彩
    官方赔率填城市均覆盖不到的 9 项，值与竞彩完全相等；若照单全收，这 9 项的
    Δ 恒为 0，那是恒等式而非盘口贴近，会把 31 项表与 Top-8 一起带偏。
    此时 ``market_avg_odds=None``、``has_market_price=False``，该行照常列出，
    但不参与 Δ 统计与 Top-8 排序。
    """

    score: str
    direction: str
    market_avg_odds: float | None
    lottery_odds: float
    delta: float | None  # 市均赔率 - 竞彩赔率；市均无报价时 None
    abs_delta: float | None
    heat_seats: int
    is_core_protected: bool
    is_overkill_tail: bool
    has_market_price: bool = True


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
    avg_market_odds: float | None
    avg_lottery_odds: float
    avg_delta: float | None
    abs_avg_delta: float | None
    total_heat_seats: int
    core_count: int
    core_ratio: float
    overkill_count: int
    core_heat_seats: int
    core_heat_ratio: float | None
    priced_count: int = 0
    """池内**有真实市均报价**的比分个数。池均市均/池均差只对这些比分求值；
    为 0 时 ``avg_market_odds`` / ``avg_delta`` / ``abs_avg_delta`` 均为 ``None``。"""


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
    """三向通道 |Δ| 摘要（WIN/DRAW/LOSS）。

    均值只对**有真实市均报价**的行求值；``*_priced_count`` 是各方向参与均值
    的行数，``*_count`` 是该方向的比分总数（固定 13/5/13，用来校验方向树）。
    """

    win_mean_abs_delta: float | None
    draw_mean_abs_delta: float | None
    loss_mean_abs_delta: float | None
    win_count: int
    draw_count: int
    loss_count: int
    min_channel: str
    channel_spread: float | None
    win_priced_count: int = 0
    draw_priced_count: int = 0
    loss_priced_count: int = 0
    min_channel_partial: bool = False
    """``min_channel`` 是否在「某方向无有价行、均值缺项」的情况下选出的。
    True 表示这个「最小通道」只在剩余方向里比较过，读者需要知道。"""


@dataclass(frozen=True)
class CompleteScoreMatrices:
    """三套矩阵完整输出容器。"""

    match_id: str
    matrix_31: list[ScoreMatrixRow]
    matrix_tactical_pools: list[TacticalPoolRow]
    top_8_min_abs_delta: list[Top8DeltaRow]
    rank1_rank2_ratio: float | None
    is_even_match: bool | None
    channel_summary: ChannelAbsSummary
    market_priced_count: int = 31
    """31 项里有真实市均报价的项数（生产记录通常 22，其余 9 项为竞彩填充）。
    ``None`` 不参与任何均值/排序，本字段让读者一眼看出分母是多少。"""

    @property
    def channel_abs_summary(self) -> dict[str, float | None]:
        """旧字典形状的只读别名（键名与原版一致），主输出仍是 channel_summary。

        某方向整向无可比价时该键为 ``None``——不是 0，0 会被读成「完全贴合盘口」。
        """
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

    只对**有市均报价**的行调用（``build_score_matrices`` 已先筛掉无价行），
    所以 ``abs_delta`` 在这里必然非 None；为 None 直接熔断，而不是拿 0 排序。

    1. abs_delta 升序（主键）
    2. 核心受保护优先
    3. 尾部杀穿靠后
    4. heat_seats 降序
    5. lottery_odds 升序
    6. THIRTY_ONE_SCORES 原始下标（确定性来自固定全集顺序，不用字符串比较）
    """
    if row.abs_delta is None:
        raise MatrixIntegrityError(
            f"[矩阵熔断] 无市均报价的比分 {row.score!r} 不得参与 Top-8 排序"
        )
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

    **分母只数有真实市均报价的行**（``has_market_price``）。竞彩填充项市均为
    ``None``，若按 0 参与平均会静默拉低池均；这里直接把它们排除在
    ``avg_market_odds`` / ``avg_delta`` 之外，并记 ``priced_count``。整池没有
    任何有价行时三个字段均为 ``None``——「没有可比价」不等于「均差为 0」。
    ``avg_lottery_odds`` 仍按池内全部行求值（竞彩赔率对这 31 项都有值）。
    """
    defs = TACTICAL_POOL_DEFINITIONS if definitions is None else definitions
    _validate_tactical_pool_definitions(defs)

    pools: list[TacticalPoolRow] = []
    for pool_id, pool_name, pool_scores in defs:
        pool_rows = [row_by_score[score] for score in pool_scores]
        priced = [r for r in pool_rows if r.has_market_price]
        label = f"战术池 {pool_id}"
        if priced:
            # priced 只含 has_market_price 的行，其 market_avg_odds 必然非 None
            priced_odds = [
                r.market_avg_odds for r in priced if r.market_avg_odds is not None
            ]
            avg_m: float | None = round(
                _mean(priced_odds, f"{label} avg_market_odds"), 4
            )
        else:
            avg_m = None
        avg_j = round(_mean([r.lottery_odds for r in pool_rows], f"{label} avg_lottery_odds"), 4)
        if avg_m is None:
            avg_d: float | None = None
            abs_d: float | None = None
        else:
            avg_d = avg_m - avg_j
            abs_d = abs(avg_d)
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
                abs_avg_delta=abs_d,
                total_heat_seats=total_heat,
                core_count=len(core_rows),
                core_ratio=len(core_rows) / len(pool_rows),
                overkill_count=sum(1 for r in pool_rows if r.is_overkill_tail),
                core_heat_seats=core_heat,
                core_heat_ratio=(core_heat / total_heat) if total_heat else None,
                priced_count=len(priced),
            )
        )
    return pools


def build_score_matrices(
    match_id: str,
    market_avg_scores: dict[str, Any],
    jc_scores_data: dict[str, dict[str, Any]],
    *,
    unpriced_scores: Sequence[str] | None = None,
) -> CompleteScoreMatrices:
    """基于完整 31 项比分数据构建：
    1) 31 个比分矩阵
    2) 15 战术池比分矩阵
    3) 最低前 8 均差矩阵

    严格要求 31 项比分全部齐备，缺失任何一项立即抛错，禁止默认值兜底。

    ``unpriced_scores``：这些比分**没有真实市均报价**（生产里是 merge_cs31 用
    竞彩赔率填充的那 9 项）。它们仍然必须出现在 ``market_avg_scores`` 里（31 项
    齐备是对「抓全」的校验），但：
      * ``market_avg_odds`` 记 ``None``，**不采用**传进来的那个等于竞彩的值；
      * ``delta`` / ``abs_delta`` 记 ``None``（不是 0.0：0 意为「贴着盘口」）；
      * 不参与池均市均、池均差、Top-8 排序、三向 |Δ| 均值。
    这些比分在 ``market_avg_scores`` 里的值必须是能解析的赔率或 ``None``；
    传 ``None`` 也接受（表示「这个键在，但没有报价」）。
    """
    clean_match_id = _check_match_id(match_id)
    if not isinstance(market_avg_scores, dict) or not isinstance(jc_scores_data, dict):
        raise MatrixIntegrityError("[矩阵熔断] market_avg_scores 与 jc_scores_data 必须为字典")

    unpriced = {str(s).replace(':', '-').strip() for s in (unpriced_scores or ())}
    unknown_unpriced = sorted(unpriced - set(THIRTY_ONE_SCORES))
    if unknown_unpriced:
        raise MatrixIntegrityError(
            f"[矩阵熔断] unpriced_scores 含未知比分: {unknown_unpriced}"
        )

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
        j_item = jc_scores_data[sc]
        if not isinstance(j_item, dict):
            raise MatrixIntegrityError(f"[矩阵熔断] jc_scores_data['{sc}'] 非字典")
        j_odd = _parse_odds(j_item.get("odds"), f"jc_scores_data.{sc}.odds")
        heat = _check_heat_seats(j_item.get("heat_seats"), f"jc_scores_data.{sc}.heat_seats")

        is_priced = sc not in unpriced
        if is_priced:
            m_odd: float | None = _parse_odds(market_avg_scores[sc], f"market_avg_scores.{sc}")
        else:
            # 无市均报价：不采用传进来的值（生产里它就等于竞彩价），记 None。
            # 若给了 None 也照收；给了别的值只做一次可解析性校验，绝不使用。
            raw = market_avg_scores[sc]
            if raw is not None:
                _parse_odds(raw, f"market_avg_scores.{sc}")
            m_odd = None
        if m_odd is None:
            delta: float | None = None
            abs_delta: float | None = None
        else:
            delta = round(m_odd - j_odd, 4)
            abs_delta = round(abs(delta), 4)
        row = ScoreMatrixRow(
            score=sc,
            direction=SCORE_DIRECTION_MAP[sc],
            market_avg_odds=m_odd,
            lottery_odds=j_odd,
            delta=delta,
            abs_delta=abs_delta,
            heat_seats=heat,
            is_core_protected=sc in CORE_PROTECTED_SET,
            is_overkill_tail=sc in OVERKILL_TAIL_SET,
            has_market_price=is_priced,
        )
        matrix_31.append(row)
        row_by_score[sc] = row

    priced_rows = [r for r in matrix_31 if r.has_market_price]
    if not priced_rows:
        raise MatrixIntegrityError(
            "[矩阵熔断] 31 项全部无市均报价，三套矩阵无可比价（拒绝全 None 出表）"
        )

    # 2. 构建 15 战术池矩阵
    matrix_tactical_pools = build_tactical_pools(row_by_score, TACTICAL_POOL_DEFINITIONS)

    # 3. 构建最低前 8 均差矩阵（按 abs_delta 升序取前 8，并列时逐级打破平手）。
    #    只在有市均报价的行里选：竞彩填充项的 Δ 是恒等式，混进来会占满 Top-8。
    sorted_by_abs = sorted(priced_rows, key=_top8_sort_key)
    top8_raw = sorted_by_abs[:8]
    rank1_abs = top8_raw[0].abs_delta
    if rank1_abs is None:  # pragma: no cover - _top8_sort_key 已拒绝 None
        raise MatrixIntegrityError("[矩阵熔断] Top-8 首行无市均报价，比值无定义")
    if rank1_abs == 0.0:
        # 有价行里 |Δ| 最小值为 0，除数为 0，比值无定义。
        raise MatrixIntegrityError(
            f"[矩阵熔断] 有市均报价的项里 |Δ| 最小值为 0（{top8_raw[0].score}），"
            "第 1 名倍数与第 1/第 2 名比值均无定义，拒绝 1e-9 兜底"
        )

    top_8_min_abs_delta: list[Top8DeltaRow] = []
    for idx, item in enumerate(top8_raw):
        # 排序键已保证有价行的 delta / abs_delta 非 None
        if item.delta is None or item.abs_delta is None:  # pragma: no cover - 同上
            raise MatrixIntegrityError(
                f"[矩阵熔断] Top-8 行 {item.score!r} 缺市均均差，无法出表"
            )
        top_8_min_abs_delta.append(
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
        )

    # rank1_rank2_ratio 直接取 TOP-8 第 2 行的 ratio_to_rank1：
    # 与表格里印出来的数字完全一致，均势判定不可能与表打架。
    # 有价行不足 2 项时「第 1/第 2 名比值」无定义——不编一个 1.0 出来。
    if len(top_8_min_abs_delta) >= 2:
        rank1_rank2_ratio: float | None = top_8_min_abs_delta[1].ratio_to_rank1
        is_even_match: bool | None = rank1_rank2_ratio <= EQUAL_MATCH_RATIO_THRESHOLD
    else:
        rank1_rank2_ratio = None
        is_even_match = None

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
    # 均值分母只数有市均报价的行；整向无可比价时该方向均值记 None，
    # 不拿 0 顶上去（0 会被误读成「完全贴合盘口」）。
    channel_means: dict[str, float] = {}
    channel_missing: set[str] = set()
    priced_counts: dict[str, int] = {}
    for direction in DIRECTION_ORDER:
        priced = [r for r in by_direction[direction] if r.has_market_price]
        priced_counts[direction] = len(priced)
        abs_values = [r.abs_delta for r in priced if r.abs_delta is not None]
        if abs_values:
            channel_means[direction] = round(
                _mean(abs_values, f"{direction} 方向 abs_delta"), 4
            )
        else:
            channel_missing.add(direction)

    defined = [d for d in DIRECTION_ORDER if d in channel_means]
    min_channel_partial = len(defined) < len(DIRECTION_ORDER)
    spread: float | None = None
    if defined:
        means = [channel_means[d] for d in defined]
        min_channel = min(defined, key=lambda d: (channel_means[d], DIRECTION_ORDER.index(d)))
        spread = max(means) - min(means)
    else:  # pragma: no cover - priced_rows 非空已在上面保证至少一条有价行
        min_channel = ""

    channel_summary = ChannelAbsSummary(
        win_mean_abs_delta=channel_means.get("WIN"),
        draw_mean_abs_delta=channel_means.get("DRAW"),
        loss_mean_abs_delta=channel_means.get("LOSS"),
        win_count=counts["WIN"],
        draw_count=counts["DRAW"],
        loss_count=counts["LOSS"],
        min_channel=min_channel,
        channel_spread=spread,
        win_priced_count=priced_counts["WIN"],
        draw_priced_count=priced_counts["DRAW"],
        loss_priced_count=priced_counts["LOSS"],
        min_channel_partial=min_channel_partial,
    )

    return CompleteScoreMatrices(
        match_id=clean_match_id,
        matrix_31=matrix_31,
        matrix_tactical_pools=matrix_tactical_pools,
        top_8_min_abs_delta=top_8_min_abs_delta,
        rank1_rank2_ratio=rank1_rank2_ratio,
        is_even_match=is_even_match,
        channel_summary=channel_summary,
        market_priced_count=len(priced_rows),
    )


def _cell(value: Any) -> str:
    """Markdown 单元格转义：裸竖线会被当成列分隔符，必须写成 \\|。"""
    return str(value).replace("|", "\\|")


NO_PRICE_MARK = "—"  # 无报价：与「赔率为 0」区分开，0 是贴着盘口的极端值


def _fmt_odds(value: float | None, spec: str = ".2f") -> str:
    return NO_PRICE_MARK if value is None else format(value, spec)


def _fmt_delta(value: float | None) -> str:
    return NO_PRICE_MARK if value is None else f"{value:+.4f}"


def _fmt_abs(value: float | None) -> str:
    return NO_PRICE_MARK if value is None else f"{value:.4f}"


def format_matrices_markdown(bundle: CompleteScoreMatrices) -> str:
    """将三套矩阵格式化为清晰的 Markdown 表格输出。

    市均无报价的行（竞彩填充项）在「市均赔率 / 均差 / 绝对均差」三列显示 ``—``，
    绝不显示 0.0000——那会被读成「完全贴合盘口」。
    """
    lines: list[str] = [f"# 赛事 {_cell(bundle.match_id)} 三套比分与均差矩阵\n"]

    lines.append("## 一、 31 个比分矩阵\n")
    lines.append("| 比分 | 方向 | 市均赔率 | 竞彩赔率 | 均差(Δ) | 绝对均差 | 热度席位 | 核心保护 | 尾部杀穿 |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for r in bundle.matrix_31:
        lines.append(
            f"| {_cell(r.score)} | {_cell(r.direction)} | {_fmt_odds(r.market_avg_odds)} | "
            f"{r.lottery_odds:.2f} | {_fmt_delta(r.delta)} | {_fmt_abs(r.abs_delta)} | "
            f"{r.heat_seats} | "
            f"{'是' if r.is_core_protected else '否'} | {'是' if r.is_overkill_tail else '否'} |"
        )

    lines.append("\n## 二、 15 战术池比分矩阵\n")
    lines.append(
        "| 池编号 | 池名称 | 包含比分 | 池均市均 | 池均竞彩 | 池均差(Δ) | 绝对池均差 | "
        "合计热度 | 核心数 | 核心占比 | 尾部数 | 核心热度 | 核心热占比 | 有价项 |"
    )
    lines.append(
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | "
        ":---: | :---: | :---: | :---: | :---: | :---: |"
    )
    for p in bundle.matrix_tactical_pools:
        heat_ratio = "无定义(池内零热度)" if p.core_heat_ratio is None else f"{p.core_heat_ratio:.4f}"
        lines.append(
            f"| {_cell(p.pool_id)} | {_cell(p.pool_name)} | {_cell(', '.join(p.scores))} | "
            f"{_fmt_odds(p.avg_market_odds)} | {p.avg_lottery_odds:.2f} | "
            f"{_fmt_delta(p.avg_delta)} | {_fmt_abs(p.abs_avg_delta)} | {p.total_heat_seats} | "
            f"{p.core_count} | {p.core_ratio:.4f} | {p.overkill_count} | "
            f"{p.core_heat_seats} | {heat_ratio} | {p.priced_count}/{len(p.scores)} |"
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

    def _chan(mean: float | None, priced: int, total: int) -> str:
        body = NO_PRICE_MARK if mean is None else f"{mean:.4f}"
        return f"{body}（{priced}/{total} 项有价）"

    spread = NO_PRICE_MARK if cs.channel_spread is None else f"{cs.channel_spread:.4f}"
    lines.append(
        f"\n> 三向通道 |Δ| 均值：WIN=`{_chan(cs.win_mean_abs_delta, cs.win_priced_count, cs.win_count)}`，"
        f"DRAW=`{_chan(cs.draw_mean_abs_delta, cs.draw_priced_count, cs.draw_count)}`，"
        f"LOSS=`{_chan(cs.loss_mean_abs_delta, cs.loss_priced_count, cs.loss_count)}`；"
        f"最小通道=`{cs.min_channel}`"
        f"{'（有方向无可比价，仅在其余方向中比较）' if cs.min_channel_partial else ''}，"
        f"通道极差=`{spread}`。"
    )
    ratio = NO_PRICE_MARK if bundle.rank1_rank2_ratio is None else f"{bundle.rank1_rank2_ratio:.4f}"
    verdict = (
        "无定义（有价项不足 2 个）"
        if bundle.is_even_match is None
        else ("均势" if bundle.is_even_match else "非均势")
    )
    lines.append(
        f"> 第 1/第 2 名比值=`{ratio}`，1.35 均势阈值判定：{verdict}。"
        f" 有市均报价项数=`{bundle.market_priced_count}/31`。"
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
    """把三套矩阵排成可直接发给 Telegram 的消息列表（按行分页，永不截断）。

    市均无报价的行显示 ``—``，不显示 0.00；池均差同理。
    """
    m31_rows = [
        (
            f"{r.score:<5s} {r.direction:<4s} "
            f"市均{_fmt_odds(r.market_avg_odds, '>7.2f') if r.market_avg_odds is not None else '      —'} "
            f"竞彩{r.lottery_odds:>7.2f} "
            f"Δ{(' ' + _fmt_delta(r.delta)) if r.delta is not None else '      —'} "
            f"热{r.heat_seats}{' 核' if r.is_core_protected else ''}"
            f"{' 尾' if r.is_overkill_tail else ''}"
            f"{'' if r.has_market_price else ' 市均无报价(竞彩填充)'}"
        )
        for r in bundle.matrix_31
    ]
    pool_rows = [
        f"{p.pool_id:<22s} 均Δ{_fmt_delta(p.avg_delta):>8s} 热{p.total_heat_seats:>3d} "
        f"核{p.core_count}/{len(p.scores)} 有价{p.priced_count}/{len(p.scores)} "
        f"核热{'无定义' if p.core_heat_ratio is None else format(p.core_heat_ratio, '.2f')}"
        for p in bundle.matrix_tactical_pools
    ]
    top8_rows = [
        f"#{t.rank} {t.score:<5s} {t.direction:<4s} |Δ|{t.abs_delta:.4f} "
        f"竞彩{t.lottery_odds:>6.2f} 热{t.heat_seats} {t.ratio_to_rank1:.2f}x"
        for t in bundle.top_8_min_abs_delta
    ]
    cs = bundle.channel_summary

    def _chan(mean: float | None, priced: int, total: int) -> str:
        body = NO_PRICE_MARK if mean is None else f"{mean:.4f}"
        return f"{body}（{priced}/{total} 有价）"

    spread = NO_PRICE_MARK if cs.channel_spread is None else f"{cs.channel_spread:.4f}"
    ratio = NO_PRICE_MARK if bundle.rank1_rank2_ratio is None else f"{bundle.rank1_rank2_ratio:.4f}"
    verdict = (
        "无定义(有价项不足2)"
        if bundle.is_even_match is None
        else ("均势" if bundle.is_even_match else "非均势")
    )
    summary_rows = [
        f"WIN  {_chan(cs.win_mean_abs_delta, cs.win_priced_count, cs.win_count)}",
        f"DRAW {_chan(cs.draw_mean_abs_delta, cs.draw_priced_count, cs.draw_count)}",
        f"LOSS {_chan(cs.loss_mean_abs_delta, cs.loss_priced_count, cs.loss_count)}",
        (
            f"最小通道 {cs.min_channel or '—'}"
            f"{'(有方向无可比价,仅其余方向比较)' if cs.min_channel_partial else ''}"
            f"，极差 {spread}"
        ),
        f"第1/第2名比值 {ratio}，{verdict}（阈值 {EQUAL_MATCH_RATIO_THRESHOLD}）",
        (
            f"有市均报价项数 {bundle.market_priced_count}/31"
            f"（其余 {31 - bundle.market_priced_count} 项市均无报价，为竞彩填充）"
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


# --------------------------------------------------------------------------- #
# 生产记录适配层（app_v84_hermes 的记录形状 -> build_score_matrices 入参）
# --------------------------------------------------------------------------- #
# 三段来源在记录上的真实键名（cursor/fix-live-log-gaps 上确认的生产形状）：
#   市均 31 项  rec['correct_score_page_avg']（回退 correct_score_site_avg）
#                —— 这是 merge_cs31 的 31 键合并结果，值是裸赔率 float。
#   竞彩赔率     rec['correct_score_jc_official']（与 rec['jc_score_odds'] 同源）
#                —— 值可能是 {score: odds}，也可能是 {score: {'odds': ...}}。
#   热度席位     rec['score_market_heat']['ranking'] —— 见 heat_seats 访问器。
MATRIX_MARKET_KEYS: tuple[str, ...] = ('correct_score_page_avg', 'correct_score_site_avg')
MATRIX_JC_KEYS: tuple[str, ...] = ('correct_score_jc_official', 'jc_score_odds')
MATRIX_HEAT_KEY = 'score_market_heat'

# merge_cs31 会把「市均没有报价」的 9 项用竞彩官方赔率填进市均向量，值完全相等。
# 这 9 项若参与 Δ 比较，Δ 恒等于 0 —— 那是恒等式，不是「盘口贴近」。实测四份
# 真实抓包全部如此：zeros == jc_filled，各 9 项（见报告里的运行结果）。
# 所以按 cs31_provenance 把它们从「市均 vs 竞彩」的比较里排除，与
# blade_deepseek_pipeline 的 _implied_vec「有出处时先把竞彩那一路摘出去」同一口径。
JC_PROVENANCE_LABELS = frozenset({
    'jc_official', 'jc_score_odds', 'jc', '竞彩',
})


def _provenance_of(rec: Mapping[str, Any] | None) -> dict[str, str]:
    """取 cs31_provenance（{score: desktop_cs|jc_official}），形状不合规就是空表。"""
    record = rec if isinstance(rec, Mapping) else {}
    raw = record.get('cs31_provenance')
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(k).replace(':', '-').strip(): str(v or '').strip()
        for k, v in raw.items()
    }


def _jc_filled_scores(rec: Mapping[str, Any] | None) -> list[str]:
    """记录里「市均其实来自竞彩官方」的比分（merge_cs31 的填充项）。

    出处来源三层，与 ``blade_deepseek_pipeline.resolve_cs31_provenance`` 同口径：
      1. ``rec['cs31_provenance']`` 显式出处（新记录）；
      2. 没有出处表时从 ``rec['correct_score_books_avg']``（原始市均页的键集）
         反推：在键集里 = desktop_cs，不在 = jc_official（老记录）；
      3. 两者都没有 —— 无从判断，返回空列表（按「全部有市均」处理，不猜）。

    不反推的后果是实测出来的：老记录把合并后的 31 键整表当成市均，那 9 项的
    Δ 恒为 0，直接触发 D1 熔断，三套矩阵在老比赛上永远出不来。
    """
    record = rec if isinstance(rec, Mapping) else {}
    provenance = _provenance_of(record)
    if provenance:
        return sorted(
            score for score, label in provenance.items()
            if label in JC_PROVENANCE_LABELS and score in set(THIRTY_ONE_SCORES)
        )
    books = record.get('correct_score_books_avg')
    if isinstance(books, Mapping) and books:
        page_keys: set[str] = set()
        for score, odd in books.items():
            key = str(score).replace(':', '-').strip()
            raw = odd.get('odds') if isinstance(odd, Mapping) else odd
            try:
                value = float(raw) if raw is not None else None
            except (TypeError, ValueError, OverflowError):
                value = None
            if key and value is not None and value > 1:
                page_keys.add(key)
        if page_keys:
            return sorted(
                s for s in THIRTY_ONE_SCORES if s not in page_keys
            )
    return []


def _jc_odds_of(value: Any) -> Any:
    """竞彩比分表的值可能是裸赔率，也可能是 {'odds': ...}：两种都取赔率。

    返回 None 表示「有这个键但没有可用赔率」——由 _parse_odds 照常熔断，
    绝不在这里补默认值。
    """
    if isinstance(value, dict):
        return value.get('odds')
    return value


def _heat_ranking_records(rec: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """取出 score_market_heat.ranking，形状不合规时返回空列表（不补 0）。"""
    record = rec if isinstance(rec, Mapping) else {}
    heat = record.get(MATRIX_HEAT_KEY)
    if not isinstance(heat, Mapping):
        return []
    ranking = heat.get('ranking')
    if not isinstance(ranking, (list, tuple)):
        return []
    return [row for row in ranking if isinstance(row, Mapping)]


def _matrix_heat_seats_from_rec(rec: Mapping[str, Any] | None) -> dict[str, int]:
    """把 score_market_heat.ranking 的「出现与否」映射成 31 项 heat_seats。

    口径（**唯一定义点，只此一处可改**）：某比分出现在
    ``rec['score_market_heat']['ranking']`` 里 = ``1``，没出现 = ``0``。

    这是**热度榜单出现次数**，不是注单数、不是席位金额、不是下注人数。
    ``build_score_market_heat_ranking`` 按比分去重（``seen`` 集合），所以
    「出现次数」在真实数据上恒为 0/1，这里如实按计数累计——哪天上游允许
    同一比分多行，计数会自动跟着变成 2、3，不需要改这里。

    定义尚未经用户裁决（契约 §6.1 记录在案）。改口径只能改这一个函数，
    并在 TG 段脚注同步改文案；矩阵本体不感知 heat_seats 从哪来。

    ``score_market_heat`` 缺失、不是字典、``ranking`` 不是列表或为空 —— 一律
    抛 MatrixIntegrityError，**绝不返回全 0**：没有热度榜单就是没有这一维数据，
    拿 0 顶上去会让 core_heat_ratio 看起来像「算过」。
    """
    record = rec if isinstance(rec, Mapping) else {}
    heat = record.get(MATRIX_HEAT_KEY)
    if not isinstance(heat, Mapping):
        raise MatrixIntegrityError(
            f"[矩阵熔断] 热度席位缺失：记录无 {MATRIX_HEAT_KEY}（无法按热度榜单出现次数取 heat_seats）"
        )
    rows = _heat_ranking_records(record)
    if not rows:
        raise MatrixIntegrityError(
            f"[矩阵熔断] 热度席位缺失：{MATRIX_HEAT_KEY}.ranking 为空"
        )
    counts: dict[str, int] = {}
    for row in rows:
        score = str(row.get('score') or '').replace(':', '-').strip()
        if score:
            counts[score] = counts.get(score, 0) + 1
    return {score: counts.get(score, 0) for score in THIRTY_ONE_SCORES}


def market_odds_from_rec(rec: Mapping[str, Any] | None) -> dict[str, Any]:
    """从记录取市均 31 项（不补齐、不造数）。

    返回的是记录里那条**合并后**的 31 键向量（merge_cs31 的产物）。其中由竞彩
    填充的比分，值等于竞彩价——调用方必须同时取 :func:`_jc_filled_scores` 并
    把它们作为 ``unpriced_scores`` 传给 :func:`build_score_matrices`，否则这 9 项
    的 Δ 会恒为 0，把 Top-8 占满（实测四份真实抓包全部如此）。
    """
    record = rec if isinstance(rec, Mapping) else {}
    for key in MATRIX_MARKET_KEYS:
        value = record.get(key)
        if isinstance(value, Mapping) and value:
            return {
                str(score).replace(':', '-'): (
                    odd.get('odds') if isinstance(odd, Mapping) else odd
                )
                for score, odd in value.items()
            }
    raise MatrixIntegrityError(
        "[矩阵熔断] 市均 31 项缺失：记录无 "
        + ' / '.join(MATRIX_MARKET_KEYS)
    )


def jc_scores_from_rec(rec: Mapping[str, Any] | None) -> dict[str, float]:
    """从记录取竞彩 31 项赔率（裸赔率与 {'odds': ...} 两种形状都吃）。"""
    record = rec if isinstance(rec, Mapping) else {}
    for key in MATRIX_JC_KEYS:
        value = record.get(key)
        if isinstance(value, Mapping) and value:
            return {
                str(score).replace(':', '-'): _jc_odds_of(odd)
                for score, odd in value.items()
            }
    raise MatrixIntegrityError(
        "[矩阵熔断] 竞彩比分赔率缺失：记录无 " + ' / '.join(MATRIX_JC_KEYS)
    )


def build_matrices_from_rec(
    rec: Mapping[str, Any] | None,
    match_id: str | None = None,
) -> CompleteScoreMatrices:
    """用一条生产记录直接建三套矩阵。

    缺任何一项都由 build_score_matrices / _parse_odds 熔断，本函数不补齐、
    不给默认值、不吞异常。

    ``cs31_provenance`` 里标为竞彩来源的比分作为 ``unpriced_scores`` 传入：
    它们在市均向量里只是竞彩价的副本，没有真实市均价，因此不参与 Δ/Top-8/池均。
    记录没有 ``cs31_provenance`` 时无从判断，按「全部有市均」处理（老记录）。
    """
    record = rec if isinstance(rec, Mapping) else {}
    resolved_id = match_id
    if not resolved_id:
        resolved_id = str(
            record.get('id') or record.get('match') or record.get('cn_name') or ''
        )
    market = market_odds_from_rec(record)
    jc = jc_scores_from_rec(record)
    heat = _matrix_heat_seats_from_rec(record)
    jc_payload: dict[str, Any] = {}
    for score in THIRTY_ONE_SCORES:
        jc_payload[score] = {
            'odds': jc.get(score),
            'heat_seats': heat[score],
        }
    return build_score_matrices(
        resolved_id,
        market,
        jc_payload,
        unpriced_scores=_jc_filled_scores(record),
    )


HEAT_SEATS_DEFINITION_NOTE = (
    "热度席位=该比分在 score_market_heat.ranking 的出现次数（真实数据上为 0/1）；"
    "定义待用户裁决，仅一处可改"
)

GOAL_MATRIX_TOP_N = 16
"""市均前 16：按真实市均赔率升序（赔率最低 = 最被看好）取前 16 项。"""

GOAL_MATRIX_PAGE_SPECS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ('0球', '1球', '2球', '3球')),
    (2, ('4球', '5球', '6球', '7+')),
)
"""两页四列：第 1 页 0-3 球，第 2 页 4/5/6/7+。"""

OTHER_SCORES = ('胜其它', '平其它', '负其它')
"""三个「其它」项一律归 7+ 列：它们是「未列出的高比分」，语义属于 7+。
**不得**按数值硬算——`胜其它` 没有进球数，硬算会把它丢进 6 球或直接崩。
"""

GOAL_BUCKET_7PLUS = '7+'
GOAL_BUCKET_6 = '6球'


def _numeric_goals(score: str) -> int | None:
    """数字比分的主客进球数之和；「其它」项返回 None。"""
    text = str(score or '').replace(':', '-').strip()
    if text in OTHER_SCORES:
        return None
    head, _, tail = text.partition('-')
    if not head.strip().isdigit() or not tail.strip().isdigit():
        return None
    return int(head) + int(tail)


def goal_bucket_of(score: str) -> str | None:
    """比分 -> 列名（'0球'..'6球' / '7+'）。超出 31 项全集或无法解析返回 None。

    规则（用户口径，勿改）：
      * 进球数 0..5 直接映射成对应的「N球」；
      * 进球数恰为 6 → ``6球``（如 4-2 / 2-4 / 3-3 / 5-1 / 1-5）；
      * 进球数 >= 7 → ``7+``（如 4-3 不在 31 项内，但 5-2 是 7、4-4 是 8）；
      * ``胜其它`` / ``平其它`` / ``负其它`` → 一律 ``7+``。

    最后一条是这里最容易写错的地方：三个「其它」是「未列出的高比分」，
    语义上属于 7+，不是 6。按数值硬算会把它们丢掉或塞进 6 球列。
    """
    text = str(score or '').replace(':', '-').strip()
    if text in OTHER_SCORES:
        return GOAL_BUCKET_7PLUS
    goals = _numeric_goals(text)
    if goals is None:
        return None
    if goals >= 7:
        return GOAL_BUCKET_7PLUS
    return f'{goals}球'


def direction_of(score: str) -> str:
    """比分 -> 胜/平/负（其它项按字面，数字比分按主客大小）。"""
    text = str(score or '').replace(':', '-').strip()
    if text == '胜其它':
        return '胜'
    if text == '负其它':
        return '负'
    if text == '平其它':
        return '平'
    head, _, tail = text.partition('-')
    if not head.strip().isdigit() or not tail.strip().isdigit():
        raise MatrixIntegrityError(f"[矩阵熔断] 无法判断比分方向: {score!r}")
    home, away = int(head), int(tail)
    if home > away:
        return '胜'
    if home < away:
        return '负'
    return '平'


@dataclass(frozen=True)
class GoalMatrixCell:
    """一格：一个列 x 一个方向。``scores`` 与 ``odds`` 等长且一一对应。"""

    scores: tuple[str, ...]
    odds: tuple[float, ...]

    @property
    def is_empty(self) -> bool:
        return not self.scores


@dataclass(frozen=True)
class GoalMatrixRow:
    """一行：某方向在四列上的格子。"""

    direction: str
    cells: tuple[GoalMatrixCell, ...]


@dataclass(frozen=True)
class GoalMatrixPage:
    """一页：四列 + 三行 + 整列的「均」。

    ``column_means`` 是**整列**（跨三个方向）的均值，不是某一行的均值：
    版式里每列只有一个「均」。
    """

    page_no: int
    columns: tuple[str, ...]
    rows: tuple[GoalMatrixRow, ...]
    column_means: tuple[float | None, ...] = ()


@dataclass(frozen=True)
class GoalOddsMatrix:
    """【市均前16·进球赔率矩阵】完整数据。"""

    match_id: str
    home: str
    away: str
    selected: tuple[tuple[str, float], ...]
    """入围的 (比分, 市均赔率)，按赔率升序。``len`` 可能 < 16（市均不足）。"""
    pages: tuple[GoalMatrixPage, ...]
    available_market_count: int
    """该场有真实市均报价的项数（分母，用来判断「前 16 不足」）。"""
    jc_keys: frozenset[str] = frozenset()
    """竞彩侧有价的比分集合（只做完整性提示，不参与排名）。"""

    @property
    def is_short(self) -> bool:
        return len(self.selected) < GOAL_MATRIX_TOP_N


def build_goal_odds_matrix(
    market_odds: Mapping[str, Any],
    jc_odds: Mapping[str, Any],
    *,
    match_id: str = '',
    home: str = '',
    away: str = '',
    top_n: int = GOAL_MATRIX_TOP_N,
) -> GoalOddsMatrix:
    """按真实市均赔率取前 N 项，装进两页四列的目标版式。

    ``market_odds`` 必须是**只有真实市均报价**的那些项（竞彩填充项已在调用方
    按 provenance 剔除）；本函数再兜一层：值必须能解析成 > 1 的有限数，
    解析不了直接熔断，绝不跳过或补 0。

    ``jc_odds`` 目前只用于校验该比分在竞彩侧也有价（列里要显示市均赔率，
    不需要竞彩价）；缺失不影响排名。
    """
    clean_market: dict[str, float] = {}
    for score, raw in (market_odds or {}).items():
        key = str(score).replace(':', '-').strip()
        if key not in set(THIRTY_ONE_SCORES):
            continue
        clean_market[key] = _parse_odds(raw, f"market_odds.{key}")
    if not clean_market:
        raise MatrixIntegrityError(
            "[矩阵熔断] 市均前16：该场无任何真实市均报价（竞彩填充项不计入）"
        )
    # 赔率升序 = 最被看好在前；同赔率按 31 项全集顺序，保证确定性
    ordered = sorted(clean_market.items(), key=lambda kv: (kv[1], _SCORE_INDEX[kv[0]]))
    selected = tuple(ordered[:top_n])

    buckets: dict[str, list[tuple[str, float]]] = {}
    for score, odd in selected:
        bucket = goal_bucket_of(score)
        if bucket is None:  # pragma: no cover - 31 项全集都有桶
            raise MatrixIntegrityError(
                f"[矩阵熔断] 市均前16：比分 {score!r} 无法归入任何进球数列"
            )
        buckets.setdefault(bucket, []).append((score, odd))

    pages: list[GoalMatrixPage] = []
    for page_no, columns in GOAL_MATRIX_PAGE_SPECS:
        # 列均 = 该列**所有**方向的格子里实际出现的比分赔率的均值
        # （不是某一行自己的均值——整列只有一个「均」）。
        column_means: list[float | None] = []
        for column in columns:
            entries = buckets.get(column, ())
            column_means.append(
                round(sum(o for _s, o in entries) / len(entries), 2) if entries else None
            )
        rows: list[GoalMatrixRow] = []
        for direction in ('胜', '平', '负'):
            cells: list[GoalMatrixCell] = []
            for column in columns:
                entries = [
                    (s, o) for s, o in buckets.get(column, ())
                    if direction_of(s) == direction
                ]
                # 同格内按赔率升序，与「最被看好在前」一致
                entries.sort(key=lambda so: (so[1], _SCORE_INDEX[so[0]]))
                cells.append(
                    GoalMatrixCell(
                        scores=tuple(s for s, _o in entries),
                        odds=tuple(o for _s, o in entries),
                    )
                )
            rows.append(GoalMatrixRow(direction=direction, cells=tuple(cells)))
        pages.append(
            GoalMatrixPage(
                page_no=page_no,
                columns=tuple(columns),
                rows=tuple(rows),
                column_means=tuple(column_means),
            )
        )

    return GoalOddsMatrix(
        match_id=match_id,
        home=home,
        away=away,
        selected=selected,
        pages=tuple(pages),
        available_market_count=len(clean_market),
        jc_keys=frozenset(str(k).replace(':', '-') for k in (jc_odds or {})),
    )


GOAL_MATRIX_EMPTY = '—'
"""空格标记：该格没有入围比分。是「无」，不是 0，绝不填赔率。"""

_GOAL_COL_MIN_WIDTH = 9
"""每列最小宽度。用户样例里最挤的一格是 ``2-1 / 3-0``（9 字符），
所以宽度不能写死 9——按全表最长单元格取，保证「不截断」而不是「硬塞」。"""


def _display_width(text: str) -> int:
    """终端显示宽度：CJK 全角字符占 2 列，其余占 1 列。

    等宽对齐必须按显示宽度算，不能按 ``len``——「球」在 len 里是 1，
    在终端里占 2 列，混排时整张表会歪。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)


def _fit_display(text: str, width: int) -> str:
    """按**显示宽度**把内容居中到列宽；放不下就抛错，绝不截断。"""
    pad = width - _display_width(text)
    if pad < 0:
        raise MatrixIntegrityError(
            f"[矩阵熔断] 进球赔率矩阵单元格超宽 显示{_display_width(text)}>{width}，"
            f"拒绝截断: {text!r}"
        )
    left = pad // 2
    right = pad - left
    return ' ' * left + text + ' ' * right


def _fit(text: str, width: int) -> str:
    """把单格内容居中到列宽；放不下就抛错，绝不截断（截断会伪造数据）。"""
    return _fit_display(text, width)


def _pad_right_display(text: str, width: int) -> str:
    """按显示宽度右补空格到固定宽度（用于【胜】这类前缀，保证竖线对齐）。"""
    pad = width - _display_width(text)
    if pad < 0:
        raise MatrixIntegrityError(
            f"[矩阵熔断] 行前缀超宽 显示{_display_width(text)}>{width}: {text!r}"
        )
    return text + ' ' * pad


def _goal_column_width(matrix: GoalOddsMatrix) -> int:
    """按全表最长单元格的**显示宽度**定列宽（至少 ``_GOAL_COL_MIN_WIDTH``）。"""
    longest = 0
    for page in matrix.pages:
        for row in page.rows:
            for cell in row.cells:
                if cell.is_empty:
                    continue
                longest = max(longest, _display_width(' / '.join(cell.scores)))
                longest = max(
                    longest, _display_width('/'.join(format(o, '.2f') for o in cell.odds))
                )
    return max(_GOAL_COL_MIN_WIDTH, longest + 2)


def format_goal_odds_matrix_tg(matrix: GoalOddsMatrix, max_len: int = 4096) -> list[str]:
    """渲染【市均前16·进球赔率矩阵】，两页用 ``\\x0c`` 分隔。

    版式严格照用户给的样子：每页一行标题、一行列头、一行「均」，
    然后【胜】【平】【负】三行，每行两行（比分行 + 赔率行）。
    纯文本等宽，不用 markdown 表格（TG 不渲染）。

    【关键：空格是 ``—``，不是 0】
    某格没有入围比分时，比分行与赔率行都显示 ``—``；列均也显示 ``—``。
    绝不用 0 或 0.00 表示「无」。
    """
    if isinstance(max_len, bool) or not isinstance(max_len, int) or max_len <= 0:
        raise MatrixIntegrityError(f"[矩阵熔断] Telegram 分页上限非法: {max_len!r}")

    header_name = f"{matrix.home or '主队'} 对 {matrix.away or '客队'}"
    title = f"【市均前16·进球赔率矩阵】 {header_name}".rstrip()
    width = _goal_column_width(matrix)

    pages: list[str] = []
    for page in matrix.pages:
        span = f"{page.columns[0].rstrip('球')}-{page.columns[-1]}"
        lines = [title, f"第{page.page_no}页 {span}"]
        # 列头行
        head = _pad_right_display('  进球', 6) + '|' + '|'.join(_fit(col, width) for col in page.columns) + '|'
        lines.append(head)
        # 「均」行
        mean_cells = []
        for mean in page.column_means:
            text = GOAL_MATRIX_EMPTY if mean is None else f'均{mean:.2f}'
            mean_cells.append(_fit(text, width))
        lines.append(_pad_right_display('    均', 6) + '|' + '|'.join(mean_cells) + '|')
        # 三行 x 两行
        for row in page.rows:
            score_cells = []
            odds_cells = []
            for cell in row.cells:
                if cell.is_empty:
                    score_cells.append(_fit(GOAL_MATRIX_EMPTY, width))
                    # 空格的下半行留空：上半行的「—」已经说明了「无」，
                    # 下半行再写一个「—」会让人以为这里有个赔率值叫「—」。
                    odds_cells.append(' ' * width)
                else:
                    score_cells.append(_fit(' / '.join(cell.scores), width))
                    odds_cells.append(
                        _fit('/'.join(format(o, '.2f') for o in cell.odds), width)
                    )
            lines.append(_pad_right_display(f'【{row.direction}】', 6) + '|'
                         + '|'.join(score_cells) + '|')
            lines.append(' ' * 6 + '|' + '|'.join(odds_cells) + '|')
        pages.append('\n'.join(lines))

    if matrix.is_short:
        note = (
            f"  注：真实市均仅 {matrix.available_market_count} 项，前 16 不足"
            f"（已按实际 {len(matrix.selected)} 项出表）"
        )
        pages[-1] = pages[-1] + '\n' + note
    for page in pages:
        for line in page.splitlines():
            if len(line) > max_len:
                raise MatrixIntegrityError(
                    f"[矩阵熔断] 进球赔率矩阵单行超长 {len(line)}>{max_len}，拒绝截断"
                )
    return pages


def format_goal_odds_matrix_messages(
    rec: Mapping[str, Any] | None,
    match_id: str | None = None,
    max_len: int = 4096,
) -> list[str]:
    """生产入口：记录 -> 两页进球赔率矩阵（``\\x0c`` 分隔，send_tg 会切成两条）。"""
    record = rec if isinstance(rec, Mapping) else {}
    resolved_id = match_id or str(
        record.get('id') or record.get('match') or record.get('cn_name') or ''
    )
    try:
        market = market_odds_from_rec(record)
    except MatrixIntegrityError as exc:
        return [f"【市均前16·进球赔率矩阵】\n  未形成（{exc}）"]
    unpriced = set(_jc_filled_scores(record))
    real_market = {s: v for s, v in market.items() if s not in unpriced}
    jc = {}
    try:
        jc = jc_scores_from_rec(record)
    except MatrixIntegrityError:
        jc = {}
    home = str(record.get('home') or record.get('home_team') or '')
    away = str(record.get('away') or record.get('away_team') or '')
    try:
        matrix = build_goal_odds_matrix(
            real_market, jc, match_id=resolved_id, home=home, away=away,
        )
    except MatrixIntegrityError as exc:
        return [f"【市均前16·进球赔率矩阵】\n  未形成（{exc}）"]
    pages = format_goal_odds_matrix_tg(matrix, max_len=max_len)
    return ['\x0c'.join(pages)]


def format_matrices_tg_messages(
    rec: Mapping[str, Any] | None,
    match_id: str | None = None,
    max_len: int = 4096,
) -> list[str]:
    """生产入口：记录 -> 可发送的 TG 消息列表；失败时给一段「未形成（原因）」。

    这是报告层唯一的调用点。矩阵层的 MatrixIntegrityError 在这里被翻译成
    报告已有的「未形成（原因）」口径，其余段落不受影响；绝不打印半张表。

    每个分页块末尾追加 heat_seats 口径脚注（``HEAT_SEATS_DEFINITION_NOTE``），
    读者不必翻代码就知道这列是什么、以及它还没被裁决。
    """
    try:
        bundle = build_matrices_from_rec(rec, match_id=match_id)
    except MatrixIntegrityError as exc:
        return [f"🎯【三套比分矩阵】\n  未形成（{exc}）"]
    pages = format_matrices_telegram(bundle, max_len=max_len)
    return [f"{page}\n\n  · {HEAT_SEATS_DEFINITION_NOTE}" for page in pages]


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
