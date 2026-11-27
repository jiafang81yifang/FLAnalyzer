"""竞彩 31 项比分盘 与 市场 26/25 项波胆盘 的机器可读口径与对比工具。

术语（本模块、报告、注释一律照此，不再混用「波胆/比分盘/境外盘」）：

    竞彩 —— 中国竞彩足球 31 项比分盘。
    市场 —— 境外主流公司 25/26 项波胆盘。

两套盘的基数恒等式（`assert_score_book_invariants` 逐条断言，测试另配反证）：

    竞彩 31 = 12 主胜精确格 + 胜其他 + 4 平局精确格 + 平其他
              + 12 客胜精确格 + 负其他
            = 13 + 5 + 13
    市场 26 = 22 精确格（主胜 9 + 客胜 9 + 平局 4）
              + 4 个桶（主胜其他、平其他、客胜其他、任何其他）
    市场 25 = 22 精确格 + 3 个桶（不单设平其他，把它并入任何其他）

两条核心差距，只有这两条：

  1. 单边进球**独立覆盖上限**不同：竞彩 5 球封顶，所以竞彩独立列出 5:0 5:1 5:2
     共 3 项、0:5 1:5 2:5 共 3 项；市场 4 球封顶，这 6 个比分没有独立格，被并进
     主胜其他／客胜其他／任何其他。
  2. 「其他」的**归类方式**不同：竞彩拆成 胜其他/平其他/负其他 三桶，且已把上述
     6 个 5 球格单独拆走；市场「其他」桶覆盖的比分集合不一样，26 项盘面还多一个
     总「任何其他」。两边「其他」不可直接相减，必须先重分桶到同一口径。

本模块只做集合、分桶、映射与数值工具：不抓数据、不发请求、不读文件。市场各家做市
商的桶边界不一样，所以边界一律由显式的 `MarketBucketPolicy` 给出，绝不默认猜一个。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# 竞彩 31 项
# --------------------------------------------------------------------------- #
JC_HOME_OTHER = "胜其他"
JC_DRAW_OTHER = "平其他"
JC_AWAY_OTHER = "负其他"
JC_OTHER_KEYS: tuple[str, ...] = (JC_HOME_OTHER, JC_DRAW_OTHER, JC_AWAY_OTHER)

JC_HOME_SCORES: tuple[str, ...] = (
    "1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2", "5:0", "5:1", "5:2",
)
JC_DRAW_SCORES: tuple[str, ...] = ("0:0", "1:1", "2:2", "3:3")
JC_AWAY_SCORES: tuple[str, ...] = (
    "0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4", "0:5", "1:5", "2:5",
)

#: 竞彩 31 项全表，顺序即竞彩官方选项顺序（13 + 5 + 13）。
JC_31_KEYS: tuple[str, ...] = (
    JC_HOME_SCORES + (JC_HOME_OTHER,)
    + JC_DRAW_SCORES + (JC_DRAW_OTHER,)
    + JC_AWAY_SCORES + (JC_AWAY_OTHER,)
)

JC_31_COUNT = 31
JC_DIRECTION_COUNTS: dict[str, int] = {"home": 13, "draw": 5, "away": 13}

#: 竞彩把单队进球上限定在 5 球，这 6 个比分因此在竞彩侧是独立格、在市场侧不是。
JC_FIVE_GOAL_CELLS: tuple[str, ...] = ("5:0", "5:1", "5:2", "0:5", "1:5", "2:5")

#: 同一写法在仓库里两种都有（其它/其他），读既有记录时两种都要认。
#: 市场桶名一并放在这里：它们也是「键」，不能当成无法识别的比分丢掉。
_OTHER_ALIASES: dict[str, str] = {
    "胜其它": JC_HOME_OTHER, "胜其他": JC_HOME_OTHER,
    "平其它": JC_DRAW_OTHER, "平其他": JC_DRAW_OTHER,
    "负其它": JC_AWAY_OTHER, "负其他": JC_AWAY_OTHER,
    "主胜其它": "主胜其他", "主队其他": "主胜其他", "主队其它": "主胜其他",
    "平局其它": "平局其他",
    "客胜其它": "客胜其他", "客队其他": "客胜其他", "客队其它": "客胜其他",
    "任何其它": "任何其他", "其它": "任何其他", "其他": "任何其他",
    "任何其他比分": "任何其他", "anyother": "任何其他", "any_other": "任何其他",
    "any other score": "任何其他", "any other": "任何其他",
}

#: 市场盘面上的桶名（归一后的写法）。
_MARKET_BUCKET_NAMES: frozenset[str] = frozenset({
    "主胜其他", "平局其他", "客胜其他", "任何其他",
})

# --------------------------------------------------------------------------- #
# 市场 26 / 25 项
# --------------------------------------------------------------------------- #
MARKET_HOME_OTHER = "主胜其他"
MARKET_DRAW_OTHER = "平局其他"
MARKET_AWAY_OTHER = "客胜其他"
MARKET_ANY_OTHER = "任何其他"

MARKET_HOME_SCORES: tuple[str, ...] = ("1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2")
MARKET_AWAY_SCORES: tuple[str, ...] = ("0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4")
MARKET_DRAW_SCORES: tuple[str, ...] = ("0:0", "1:1", "2:2", "3:3")

MARKET_PRECISE_SCORES: tuple[str, ...] = MARKET_HOME_SCORES + MARKET_DRAW_SCORES + MARKET_AWAY_SCORES

#: 26 项盘面的 4 个桶；25 项盘面没有独立平局其他，它并入任何其他。
MARKET_BUCKET_KEYS_26: tuple[str, ...] = (
    MARKET_HOME_OTHER, MARKET_DRAW_OTHER, MARKET_AWAY_OTHER, MARKET_ANY_OTHER,
)
MARKET_BUCKET_KEYS_25: tuple[str, ...] = (
    MARKET_HOME_OTHER, MARKET_AWAY_OTHER, MARKET_ANY_OTHER,
)

MARKET_26_KEYS: tuple[str, ...] = (
    MARKET_HOME_SCORES + (MARKET_HOME_OTHER,)
    + MARKET_DRAW_SCORES + (MARKET_DRAW_OTHER,)
    + MARKET_AWAY_SCORES + (MARKET_AWAY_OTHER, MARKET_ANY_OTHER)
)
MARKET_25_KEYS: tuple[str, ...] = (
    MARKET_HOME_SCORES + (MARKET_HOME_OTHER,)
    + MARKET_DRAW_SCORES
    + MARKET_AWAY_SCORES + (MARKET_AWAY_OTHER, MARKET_ANY_OTHER)
)

#: 4:3 / 3:4 在保留它们的盘面里是独立格；竞彩侧它们落在胜其他/负其他。
MARKET_EXTRA_GOAL_CELLS: tuple[str, ...] = ("4:3", "3:4")

MARKET_26_COUNT = 26
MARKET_25_COUNT = 25


def _score_sort_key(score: str) -> tuple[int, int]:
    parts = _split_score(score)
    return parts if parts else (99, 99)


# --------------------------------------------------------------------------- #
# 比分键归一
# --------------------------------------------------------------------------- #
def _split_score(score: Any) -> tuple[int, int] | None:
    head, sep, tail = str(score or "").partition(":")
    if sep and head.isdigit() and tail.isdigit():
        return int(head), int(tail)
    return None


def normalize_score_key(key: Any) -> str:
    """把 `1-0` / `1：0` / `1:0` / `胜其它` 归一成本模块的写法；认不出返回空串。

    市场盘面的桶名（主胜其他/平局其他/客胜其他/任何其他）也是合法键，
    原样返回——它们必须能进归一化与分桶，否则市场盘面会被悄悄削掉 4 项。
    """
    text = str(key or "").replace("：", ":").replace("-", ":").replace(" ", "").strip()
    if not text:
        return ""
    alias = _OTHER_ALIASES.get(text) or _OTHER_ALIASES.get(text.lower())
    if alias:
        return alias
    if text in _MARKET_BUCKET_NAMES:
        return text
    parts = _split_score(text)
    return f"{parts[0]}:{parts[1]}" if parts else ""


def jc31_keys(sep: str = ":") -> list[str]:
    """竞彩 31 项键表，分隔符可换成仓库里既有的 `-` 写法。"""
    return [k.replace(":", sep) for k in JC_31_KEYS]


# --------------------------------------------------------------------------- #
# 市场盘面模型
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketBook:
    """一个市场盘面的键表：25 项或 26 项。"""

    size: int
    keys: tuple[str, ...]
    buckets: tuple[str, ...]

    @property
    def precise(self) -> tuple[str, ...]:
        return tuple(k for k in self.keys if k not in self.buckets)

    def has_draw_other(self) -> bool:
        return MARKET_DRAW_OTHER in self.buckets


MARKET_BOOK_26 = MarketBook(MARKET_26_COUNT, MARKET_26_KEYS, MARKET_BUCKET_KEYS_26)
MARKET_BOOK_25 = MarketBook(MARKET_25_COUNT, MARKET_25_KEYS, MARKET_BUCKET_KEYS_25)
MARKET_BOOKS: dict[int, MarketBook] = {26: MARKET_BOOK_26, 25: MARKET_BOOK_25}


def market_book(size: Any) -> MarketBook:
    """按项数取盘面。只有 25 与 26 两种，别的一律抛错而不是就近凑。"""
    try:
        n = int(size)
    except (TypeError, ValueError):
        raise ValueError(f"市场盘面项数非法: {size!r}（只有 25 / 26）") from None
    book = MARKET_BOOKS.get(n)
    if book is None:
        raise ValueError(f"市场盘面项数非法: {n}（只有 25 / 26）")
    return book


# --------------------------------------------------------------------------- #
# 桶边界口径（各家做市商不同，绝不默认猜一个）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketBucketPolicy:
    """市场盘的桶边界。开关可组合，输出里必须打印本次采用的组合。

    ``five_goals_in_team_other``
        单队 5 球及以上并入**方向桶**（主胜其他 / 客胜其他）。
    ``five_goals_in_any_other``
        单队 5 球及以上并入总「任何其他」。
    ``keep_43_34``
        4:3 / 3:4 是独立精确格（从方向桶里摘出来）。关闭时它们留在方向桶里，
        与竞彩「胜其他/负其他」的边界一致。

    两个 ``five_goals_*`` 互斥：同时为真没有唯一答案，直接抛错而不是挑一个。
    """

    five_goals_in_team_other: bool = False
    five_goals_in_any_other: bool = True
    keep_43_34: bool = False

    def __post_init__(self) -> None:
        if self.five_goals_in_team_other and self.five_goals_in_any_other:
            raise ValueError(
                "市场桶边界冲突：单队 5 球及以上不能同时并入方向桶和总「任何其他」，"
                "five_goals_in_team_other / five_goals_in_any_other 只能选一个"
            )

    def label(self) -> str:
        """一行口径组合说明，CLI 与报告原样打印。"""
        if self.five_goals_in_team_other:
            five = "单队5球及以上→方向桶"
        elif self.five_goals_in_any_other:
            five = "单队5球及以上→任何其他"
        else:
            five = "单队5球及以上→盘面外"
        extra = "独立格" if self.keep_43_34 else "并入方向桶"
        return f"{five}; 4:3/3:4={extra}"

    def as_dict(self) -> dict[str, bool]:
        return {
            "five_goals_in_team_other": self.five_goals_in_team_other,
            "five_goals_in_any_other": self.five_goals_in_any_other,
            "keep_43_34": self.keep_43_34,
        }


#: 默认口径 = 经覆盖集比对确认**唯一无损**的那一套（`five_goals_in_team_other`）。
#:
#: 理由只有一条，而且是算出来的、不是声明的：逐个市场桶比对「竞彩侧覆盖集」与
#: 「市场桶覆盖集」，只有这一套全等（`verify_lossless()` 给出 lossless=True）。
#: 它的构造是「多对一」：竞彩的胜其他/负其他/6 个 5 球格全部落进方向桶，
#: 而市场方向桶的覆盖集恰好等于它们的并集，聚合不丢也不串。
#:
#: `five_goals_in_any_other`（即把 5 球及以上划进总「任何其他」）**不是**无损：
#: 竞彩的「胜其他」本身横跨两个市场桶——4:3 归主胜其他，5:3/6:0 归任何其他，
#: 而竞彩只有一个「胜其他」选项，无论如何都拆不开。把它整个塞进主胜其他，
#: 会让主胜其他与任何其他两个桶的差额互相抵消，Δ 读起来像定价错位，
#: 实际是口径伪差。同分布两边喂、Δ 应全 0 的自检实测 max|Δ|=0.01451。
DEFAULT_MARKET_POLICY = MarketBucketPolicy(
    five_goals_in_team_other=True,
    five_goals_in_any_other=False,
    keep_43_34=False,
)

#: 压测与对照用的另外三种可组合口径。
POLICY_TEAM_OTHER = DEFAULT_MARKET_POLICY
POLICY_ANY_OTHER = MarketBucketPolicy(
    five_goals_in_team_other=False, five_goals_in_any_other=True, keep_43_34=False,
)
POLICY_KEEP_43_34 = MarketBucketPolicy(keep_43_34=True)
POLICY_TEAM_OTHER_KEEP = MarketBucketPolicy(
    five_goals_in_team_other=True, five_goals_in_any_other=False, keep_43_34=True,
)
POLICY_ANY_OTHER_KEEP = MarketBucketPolicy(
    five_goals_in_team_other=False, five_goals_in_any_other=True, keep_43_34=True,
)
ALL_POLICIES: tuple[MarketBucketPolicy, ...] = (
    DEFAULT_MARKET_POLICY, POLICY_ANY_OTHER, POLICY_KEEP_43_34,
    POLICY_TEAM_OTHER_KEEP, POLICY_ANY_OTHER_KEEP,
)


# --------------------------------------------------------------------------- #
# 每个桶覆盖的比分全集
# --------------------------------------------------------------------------- #
#: 枚举上界：单队 15 球以外的比分对任何真实盘面都没有意义，
#: 但桶的定义必须是一个封闭集合，所以给一个显式的上界而不是「无限」。
COVERAGE_MAX_GOALS = 15


def market_bucket_coverage(
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> dict[str, tuple[str, ...]]:
    """市场每个桶覆盖的比分集合（不含桶自身）。

    规则：先取该盘的精确格；剩下的比分按 主胜/平局/客胜 分流。单队 5 球及以上
    按 ``policy`` 决定去处；平局的其他比分：26 项盘面落平局其他，25 项盘面落
    任何其他。4:3/3:4 在 ``keep_43_34`` 下是独立格，因此不出现在任何桶里。
    """
    precise = set(book.precise)
    if policy.keep_43_34:
        precise.update(MARKET_EXTRA_GOAL_CELLS)

    coverage: dict[str, list[str]] = {bucket: [] for bucket in book.buckets}
    for home in range(COVERAGE_MAX_GOALS + 1):
        for away in range(COVERAGE_MAX_GOALS + 1):
            score = f"{home}:{away}"
            if score in precise:
                continue
            if home > away:
                if home >= 5 and policy.five_goals_in_any_other:
                    coverage[MARKET_ANY_OTHER].append(score)
                else:
                    coverage[MARKET_HOME_OTHER].append(score)
            elif home < away:
                if away >= 5 and policy.five_goals_in_any_other:
                    coverage[MARKET_ANY_OTHER].append(score)
                else:
                    coverage[MARKET_AWAY_OTHER].append(score)
            elif book.has_draw_other():
                coverage[MARKET_DRAW_OTHER].append(score)
            else:
                coverage[MARKET_ANY_OTHER].append(score)
    return {b: tuple(sorted(s, key=_score_sort_key)) for b, s in coverage.items()}


def bucket_of_market_score(
    score: Any,
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> str:
    """一个市场比分（精确格或某个桶的成员）落在哪个市场键上；认不出返回空串。"""
    key = normalize_score_key(score)
    if not key:
        return ""
    if key in book.keys:
        return key
    parts = _split_score(key)
    if parts is None:
        return ""
    home, away = parts
    if home > away:
        if home >= 5 and policy.five_goals_in_any_other:
            return MARKET_ANY_OTHER
        return MARKET_HOME_OTHER
    if home < away:
        if away >= 5 and policy.five_goals_in_any_other:
            return MARKET_ANY_OTHER
        return MARKET_AWAY_OTHER
    return MARKET_DRAW_OTHER if book.has_draw_other() else MARKET_ANY_OTHER


# --------------------------------------------------------------------------- #
# 竞彩 31 -> 市场桶（档 A：无损可比）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class JcToMarketMap:
    """竞彩 31 项到市场盘面的映射，附带**算出来的**可用性结论。

    ``mapping``            竞彩键 -> 市场键（31 项齐全）
    ``unresolved``         无法唯一落位的竞彩键
    ``lossless``           逐桶比对覆盖集后的结论（全等才为 True）；不是声明出来的
    ``one_to_many_buckets`` 口径不可无损时，哪些桶的覆盖集与竞彩侧并集不等
    ``bucket_gaps``        逐桶给出「竞彩多出 / 市场多出 / 双方都多出」的比分集合
    """

    mapping: dict[str, str]
    unresolved: tuple[str, ...] = ()
    lossless: bool = True
    policy_label: str = ""
    book_size: int = MARKET_26_COUNT
    one_to_many_buckets: tuple[str, ...] = ()
    bucket_gaps: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)


def _jc_side_bucket_coverage(
    mapping: Mapping[str, str],
    policy: MarketBucketPolicy,
    book: MarketBook,
) -> dict[str, tuple[str, ...]]:
    """竞彩侧每个市场桶实际覆盖的比分集合。

    「竞彩键 X 落到市场桶 B」意味着 X 覆盖的所有比分都进了 B。所以 B 在竞彩侧的
    覆盖集 = 所有映射到 B 的竞彩键的覆盖集之并。

    竞彩键的覆盖集：精确格就是它自己；胜其他/负其他/平其他是该方向上**不在
    31 项精确格里的全部比分**，上界与 `market_bucket_coverage` 相同。
    """
    per_key: dict[str, set[str]] = {key: {key} for key in book.precise if key in JC_31_KEYS}

    precise_home = {s for s in JC_HOME_SCORES}
    precise_draw = {s for s in JC_DRAW_SCORES}
    precise_away = {s for s in JC_AWAY_SCORES}
    for home in range(COVERAGE_MAX_GOALS + 1):
        for away in range(COVERAGE_MAX_GOALS + 1):
            score = f"{home}:{away}"
            if home > away:
                if score not in precise_home:
                    per_key.setdefault(JC_HOME_OTHER, set()).add(score)
            elif home < away:
                if score not in precise_away:
                    per_key.setdefault(JC_AWAY_OTHER, set()).add(score)
            elif score not in precise_draw:
                per_key.setdefault(JC_DRAW_OTHER, set()).add(score)

    out: dict[str, set[str]] = {bucket: set() for bucket in book.buckets}
    for jc_key, market_key in mapping.items():
        if market_key in out:
            out[market_key] |= per_key.get(jc_key, {jc_key})
    return {bucket: tuple(sorted(scores, key=_score_sort_key)) for bucket, scores in out.items()}


def verify_lossless(
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
    mapping: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """逐桶比对「竞彩侧覆盖集」与「市场桶覆盖集」，算出这条口径是否无损。

    无损的判据是**集合全等**：某个市场桶在竞彩侧覆盖的比分集合，必须与
    `market_bucket_coverage` 给它的覆盖集逐元素相同。只要有一个桶不等，
    把竞彩分布聚合到该桶上就不再是无损聚合，Δ 里必然混着口径伪差。

    这不是从 policy 标志位推断的：`five_goals_in_team_other` 恰好无损、
    `five_goals_in_any_other` 恰好不是，是覆盖集比对出来的结论，不是设定。
    """
    table = jc_to_market_map(policy, book) if mapping is None else mapping
    pairs = table.mapping if isinstance(table, JcToMarketMap) else dict(table)
    market_cov = market_bucket_coverage(policy, book)
    jc_cov = _jc_side_bucket_coverage(pairs, policy, book)

    gaps: dict[str, dict[str, tuple[str, ...]]] = {}
    one_to_many: list[str] = []
    for bucket in book.buckets:
        jc_side = set(jc_cov.get(bucket, ()))
        mk_side = set(market_cov.get(bucket, ()))
        if jc_side == mk_side:
            continue
        one_to_many.append(bucket)
        only_jc = tuple(sorted(jc_side - mk_side, key=_score_sort_key))
        only_mk = tuple(sorted(mk_side - jc_side, key=_score_sort_key))
        gaps[bucket] = {
            "only_in_jc": only_jc,
            "only_in_market": only_mk,
            "both": ("only_in_jc" if only_jc else "") + (
                "+only_in_market" if only_mk else ""
            ) or "",
        }
    return {
        "lossless": not one_to_many,
        "one_to_many_buckets": tuple(one_to_many),
        "bucket_gaps": gaps,
        "policy": policy.as_dict(),
        "policy_label": policy.label(),
        "book_size": book.size,
    }


def jc_to_market_map(
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> JcToMarketMap:
    """把竞彩 31 项逐个投到市场盘面的桶上，并**算出**这条口径是否无损。

    未识别的键不会进 ``mapping``；`assert_score_book_invariants` 保证 31 项齐全。

    `lossless` 不许由 policy 标志位推断——它必须来自覆盖集比对。
    历史实现写死的 `not policy.keep_43_34` 在默认口径（5 球→任何其他）下
    是错的：竞彩「胜其他」横跨「主胜其他」与「任何其他」两个市场桶，
    一对一聚合会把两个桶的差额互相抵消成一堆假 Δ。
    """
    mapping: dict[str, str] = {}
    for jc_key in JC_31_KEYS:
        target = jc_to_market_key(jc_key, policy=policy, book=book)
        if target:
            mapping[jc_key] = target
    verdict = verify_lossless(policy=policy, book=book, mapping=mapping)
    unresolved: tuple[str, ...] = ()
    if verdict["one_to_many_buckets"]:
        # 不可无损时，点名是哪些竞彩键横跨了多个市场桶。
        crossers = tuple(
            key for key in (JC_HOME_OTHER, JC_AWAY_OTHER, JC_DRAW_OTHER)
            if jc_key_spans_buckets(key, policy, book)
        )
        unresolved = crossers
    return JcToMarketMap(
        mapping=mapping,
        unresolved=unresolved,
        lossless=verdict["lossless"],
        policy_label=policy.label(),
        book_size=book.size,
        one_to_many_buckets=verdict["one_to_many_buckets"],
        bucket_gaps=verdict["bucket_gaps"],
    )


def jc_key_spans_buckets(
    jc_key: Any,
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> bool:
    """这个竞彩键覆盖的比分是否落在**不止一个**市场桶里。

    胜其他就是典型：4:3 归主胜其他，5:3/6:0 归任何其他，一个竞彩选项
    横跨两个市场桶。这种情况下「把它整个塞进一个桶」必然产生口径伪差。
    """
    key = normalize_score_key(jc_key)
    if not key:
        return False
    targets = {bucket_of_market_score(score, policy=policy, book=book) for score in _jc_key_coverage(key)}
    targets.discard("")
    return len(targets) > 1


def _jc_key_coverage(jc_key: str) -> tuple[str, ...]:
    """一个竞彩键覆盖的比分集合（精确格是它自己）。"""
    if jc_key in JC_HOME_SCORES or jc_key in JC_DRAW_SCORES or jc_key in JC_AWAY_SCORES:
        return (jc_key,)
    out: list[str] = []
    for home in range(COVERAGE_MAX_GOALS + 1):
        for away in range(COVERAGE_MAX_GOALS + 1):
            score = f"{home}:{away}"
            if jc_key == JC_HOME_OTHER:
                matched = home > away and score not in JC_HOME_SCORES
            elif jc_key == JC_AWAY_OTHER:
                matched = home < away and score not in JC_AWAY_SCORES
            elif jc_key == JC_DRAW_OTHER:
                matched = home == away and score not in JC_DRAW_SCORES
            else:
                matched = False
            if matched:
                out.append(score)
    return tuple(out)


def jc_to_market_key(
    score: Any,
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> str:
    """竞彩 31 项里的一个键投到哪个市场键（精确格返回自己）。认不出返回空串。

    只有市场盘面真实存在的精确格才回到自己；其余按 policy 落到桶上。
    """
    key = normalize_score_key(score)
    if not key:
        return ""
    if key == JC_HOME_OTHER:
        return MARKET_HOME_OTHER
    if key == JC_AWAY_OTHER:
        return MARKET_AWAY_OTHER
    if key == JC_DRAW_OTHER:
        return MARKET_DRAW_OTHER if book.has_draw_other() else MARKET_ANY_OTHER

    parts = _split_score(key)
    if parts is None:
        return ""
    home, away = parts
    if key in book.precise:
        return key
    if home > away:
        if key == "4:3" and policy.keep_43_34:
            return key
        if home >= 5 and policy.five_goals_in_any_other:
            return MARKET_ANY_OTHER
        return MARKET_HOME_OTHER
    if home < away:
        if key == "3:4" and policy.keep_43_34:
            return key
        if away >= 5 and policy.five_goals_in_any_other:
            return MARKET_ANY_OTHER
        return MARKET_AWAY_OTHER
    return MARKET_DRAW_OTHER if book.has_draw_other() else MARKET_ANY_OTHER


def project_jc31_onto_market(
    jc_probs: Mapping[str, Any],
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> dict[str, Any]:
    """档 A：把竞彩 31 维分布聚合到市场盘面的桶上。

    未识别的键与非法值一律跳过（不参与、不当 0），并在 ``skipped`` 里点名。
    """
    mapping = jc_to_market_map(policy, book)
    agg: dict[str, float] = {key: 0.0 for key in book.keys}
    skipped: list[str] = []
    used: dict[str, list[str]] = {}
    for score, prob in (jc_probs or {}).items():
        key = normalize_score_key(score)
        value = _finite_float(prob)
        if not key or value is None:
            skipped.append(str(score))
            continue
        target = mapping.mapping.get(key)
        if not target:
            skipped.append(str(score))
            continue
        agg[target] = agg.get(target, 0.0) + value
        used.setdefault(target, []).append(key)
    return {
        "book_size": book.size,
        "policy": policy.as_dict(),
        "policy_label": policy.label(),
        "probs": agg,
        "used": {k: tuple(sorted(v)) for k, v in used.items()},
        "skipped": tuple(skipped),
        "lossless": mapping.lossless,
        "unresolved": mapping.unresolved,
        "one_to_many_buckets": mapping.one_to_many_buckets,
        "bucket_gaps": mapping.bucket_gaps,
    }


# --------------------------------------------------------------------------- #
# 数值守卫
# --------------------------------------------------------------------------- #
def _finite_float(value: Any) -> float | None:
    """可用的数：有限、非 bool。非法一律 None（调用方跳过，绝不当 0）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _valid_odd(value: Any) -> float | None:
    """可用的赔率：有限且 > 1（赔率 1.0 及以下没有隐含概率意义）。"""
    number = _finite_float(value)
    if number is None or number <= 1.0:
        return None
    return number


# --------------------------------------------------------------------------- #
# 去水与返还率
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NormalizedOdds:
    """一次去水的结果。

    ``rtp``            返还率估计 = 1/sum(raw)。样本不完整、有别名重复键、
                       或算出来 >= 1.0（现实中不存在）时**必须**是 None。
    ``probs``          去水概率（raw/sum(raw)），只含 ``required`` 里的键。
    ``complete``       要求的键是否全部给出且全部合法，且没有任何样本错误。
    ``missing``        该有的键没给。
    ``skipped``        该有的键给了坏值（0 / 负数 / nan / inf / 超长数字串）。
    ``rejected``       根本不属于这个盘口的键（给了 ``required`` 时），不进分母。
    ``duplicate_inputs`` 归一后撞到同一比分键的多份输入 —— 不求和，交给调用方裁决。
    """

    rtp: float | None
    probs: dict[str, float]
    complete: bool
    missing: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    raw_sum: float | None = None
    rejected: tuple[str, ...] = ()
    duplicate_inputs: tuple[tuple[str, tuple[str, ...]], ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "rtp": self.rtp,
            "probs": dict(self.probs),
            "complete": self.complete,
            "missing": list(self.missing),
            "skipped": list(self.skipped),
            "raw_sum": self.raw_sum,
            "rejected": list(self.rejected),
            "duplicate_inputs": [[k, list(v)] for k, v in self.duplicate_inputs],
            "warnings": list(self.warnings),
        }


def normalize_odds(
    odds_map: Mapping[str, Any] | None,
    required: Iterable[str] | None = None,
) -> NormalizedOdds:
    """把 `{比分键: 赔率}` 去水。

    ``raw = 1/odds``（0、负数、nan、inf、超长数字串跳过且不进分母）；
    ``rtp = 1/sum(raw)``，``norm = raw/sum(raw)``。

    给了 ``required`` 时有三条硬约束（每一条都是修掉的真实缺陷）：

    1. **缺任何一个键 → rtp 为 None**：缺项的倒数不在分母里，RTP 会被算高。
    2. **别名重复键不算两份**：`{'1:0': 3.0, '1-0': 3.0}` 归一后是同一个比分，
       累加会把分母做大、RTP 算小，甚至算出 1.15 这种不存在的返还率。
       不求和、不择一，记进 ``duplicate_inputs``，`complete=False`、`rtp=None`，
       让调用方决定信哪一份（可能是两个不同来源）。
    3. **表外键不进分母**：给了 ``required`` 时只有 ``required`` 里的键能进 ``raw``；
       其余进 ``rejected``。否则多给一个 `9:9` 会拉偏分母，把所有 Δ 整体推移。

    再加一条样本自洽检查：``complete`` 且 ``rtp >= 1.0`` 说明这份赔率互相矛盾
    （返还率 ≥ 100% 不是真实盘口），同样置 None 并告警。
    """
    required_keys: tuple[str, ...] = tuple(
        k for k in (normalize_score_key(x) for x in (required or ())) if k
    )
    universe = set(required_keys) if required_keys else None

    raw: dict[str, float] = {}
    # 归一后的比分 -> 贡献它的原始键（用于识别别名重复）
    contributors: dict[str, list[str]] = {}
    skipped: list[str] = []
    rejected: list[str] = []
    warnings: list[str] = []

    for key, odd in (odds_map or {}).items():
        score = normalize_score_key(key)
        value = _valid_odd(odd)
        if not score or value is None:
            skipped.append(str(key))
            continue
        if universe is not None and score not in universe:
            rejected.append(str(key))
            continue
        contributors.setdefault(score, []).append(str(key))
        raw[score] = raw.get(score, 0.0) + 1.0 / value

    duplicate_inputs: list[tuple[str, tuple[str, ...]]] = [
        (score, tuple(keys)) for score, keys in sorted(contributors.items()) if len(keys) > 1
    ]
    if duplicate_inputs:
        warnings.append(
            f"样本有 {len(duplicate_inputs)} 个别名重复键，已拒绝估 RTP："
            f"{[score for score, _ in duplicate_inputs[:4]]} —— 不求和、不择一，由调用方裁决"
        )
    if rejected:
        warnings.append(
            f"有 {len(rejected)} 个键不属于本盘口，已排除出分母：{rejected[:4]}"
        )

    total = sum(raw.values())
    probs = {k: v / total for k, v in raw.items()} if total > 0 else {}

    if required_keys:
        missing = tuple(k for k in required_keys if k not in raw)
        complete = not missing
    else:
        missing = ()
        complete = bool(raw)

    # 样本错误优先于完整性：有别名重复时连「完整」都不成立。
    if duplicate_inputs:
        complete = False

    rtp: float | None = None
    if total > 0 and complete:
        candidate = 1.0 / total
        if candidate >= 1.0:
            warnings.append(
                f"样本自相矛盾（返还率 {candidate:.4f} ≥ 100%），不是真实盘口："
                "按样本错误处理，RTP 置空"
            )
        else:
            rtp = candidate
    elif total > 0 and not complete:
        # 不完整时 raw_sum 仍如实给出（它是现有键的和），但绝不据此算 RTP。
        pass

    return NormalizedOdds(
        rtp=rtp,
        probs=probs,
        complete=complete,
        missing=missing,
        skipped=tuple(skipped),
        raw_sum=total if total > 0 else None,
        rejected=tuple(rejected),
        duplicate_inputs=tuple(duplicate_inputs),
        warnings=tuple(warnings),
    )


def normalize_jc_odds(odds_map: Mapping[str, Any] | None) -> NormalizedOdds:
    """竞彩 31 项去水。缺任何一项 → ``rtp=None, complete=False, missing=[...]``。"""
    return normalize_odds(odds_map, required=JC_31_KEYS)


def normalize_market_odds(odds_map: Mapping[str, Any] | None, book: MarketBook | int = 26) -> NormalizedOdds:
    """市场盘面去水。缺任何一项（按 25/26 的键表）→ ``rtp=None``。"""
    target = book if isinstance(book, MarketBook) else market_book(book)
    return normalize_odds(odds_map, required=target.keys)


# --------------------------------------------------------------------------- #
# 双变量泊松 / Dixon-Coles
# --------------------------------------------------------------------------- #
def _poisson_pmf(lam: float, goals: int) -> float:
    """泊松概率，对数域计算以避开大 λ 下的上溢/下溢。"""
    if goals < 0:
        return 0.0
    if lam <= 0.0:
        return 1.0 if goals == 0 else 0.0
    log_p = -lam + goals * math.log(lam) - math.lgamma(goals + 1)
    return math.exp(log_p) if log_p > -745.0 else 0.0


def dc_tau(home: int, away: int, lam_h: float, lam_a: float, rho: float) -> tuple[float, bool]:
    """Dixon-Coles 低比分修正系数，返回 ``(tau, clamped)``。

    ``clamped`` 为真表示原始修正值被截断到下限——rho 在这个格上越界了。
    调用方必须把截断计数上报，不许静默压扁负值。
    """
    if home == 0 and away == 0:
        raw = 1.0 - lam_h * lam_a * rho
    elif home == 0 and away == 1:
        raw = 1.0 + lam_h * rho
    elif home == 1 and away == 0:
        raw = 1.0 + lam_a * rho
    elif home == 1 and away == 1:
        raw = 1.0 - rho
    else:
        return 1.0, False
    if raw < DC_TAU_FLOOR:
        return DC_TAU_FLOOR, True
    return raw, False


DC_TAU_FLOOR = 1e-6

#: 摊薄回不到原桶的质量超过这个量就在输出里点名（低于它属于数值噪声）。
_ROUNDTRIP_WARN = 1e-5


@dataclass(frozen=True)
class DcGrid:
    """Dixon-Coles 比分网格（已在该网格上归一）。"""

    probs: dict[str, float]
    lam_h: float
    lam_a: float
    rho: float
    tau_clamped_cells: int = 0
    max_goals: int = 10

    @property
    def total(self) -> float:
        return sum(self.probs.values())


def dc_grid(lam_h: float, lam_a: float, rho: float = 0.0, max_goals: int = 10) -> DcGrid:
    """生成 Dixon-Coles 比分网格并按网格归一（尾部质量留在网格内）。

    网格是有限截断，所以归一化在这里是必要的：截断丢掉的尾部质量若不在这里
    处理，投影到 31 维后必然对不上 1。归一之后 `dixon_coles_to_jc31` 不再归一，
    分桶漏项就会直接让它的断言变红。
    """
    lh = _finite_float(lam_h)
    la = _finite_float(lam_a)
    r = _finite_float(rho)
    if lh is None or la is None or r is None or lh <= 0 or la <= 0:
        raise ValueError(f"Dixon-Coles 参数非法: lam_h={lam_h!r} lam_a={lam_a!r} rho={rho!r}")
    cells: dict[str, float] = {}
    clamped = 0
    for home in range(max_goals + 1):
        ph = _poisson_pmf(lh, home)
        for away in range(max_goals + 1):
            tau, was_clamped = dc_tau(home, away, lh, la, r)
            clamped += 1 if was_clamped else 0
            value = tau * ph * _poisson_pmf(la, away)
            if value > 0.0:
                cells[f"{home}:{away}"] = value
    total = sum(cells.values())
    if total <= 0.0:
        raise ValueError(f"Dixon-Coles 网格总质量为 0: lam_h={lh} lam_a={la} rho={r}")
    return DcGrid(
        probs={k: v / total for k, v in cells.items()},
        lam_h=lh, lam_a=la, rho=r,
        tau_clamped_cells=clamped, max_goals=max_goals,
    )


def dc_score_to_jc_key(score: Any) -> str:
    """任意比分落到竞彩 31 项的唯一一格；认不出返回空串。"""
    key = normalize_score_key(score)
    parts = _split_score(key)
    if parts is None:
        return ""
    home, away = parts
    if home > away:
        return key if key in JC_HOME_SCORES else JC_HOME_OTHER
    if home < away:
        return key if key in JC_AWAY_SCORES else JC_AWAY_OTHER
    return key if key in JC_DRAW_SCORES else JC_DRAW_OTHER


def dixon_coles_to_jc31(
    lam_h: float,
    lam_a: float,
    rho: float = 0.0,
    max_goals: int = 10,
) -> dict[str, Any]:
    """把 Dixon-Coles 网格投影到竞彩 31 项。

    网格已在 `dc_grid` 内归一，分桶保序，所以这里**不再归一**：末尾那次
    `total = sum(...)` 的再归一化会把「分桶漏了某一格」悄悄吸收掉，改成一个
    断言——漏项直接抛错。
    """
    grid = dc_grid(lam_h, lam_a, rho=rho, max_goals=max_goals)
    out: dict[str, float] = {key: 0.0 for key in JC_31_KEYS}
    unmapped: list[str] = []
    for score, prob in grid.probs.items():
        target = dc_score_to_jc_key(score)
        if not target:
            unmapped.append(score)
            continue
        out[target] += prob
    if unmapped:
        raise AssertionError(f"Dixon-Coles 投影有 {len(unmapped)} 格落不进竞彩 31 项: {unmapped[:5]}")
    total = sum(out.values())
    if abs(total - 1.0) >= 1e-9:
        raise AssertionError(f"Dixon-Coles 投影到 31 维后总质量 {total!r} != 1（分桶漏项）")
    return {
        "probs": out,
        "lam_h": grid.lam_h,
        "lam_a": grid.lam_a,
        "rho": grid.rho,
        "tau_clamped_cells": grid.tau_clamped_cells,
        "max_goals": grid.max_goals,
    }


# --------------------------------------------------------------------------- #
# 从 1X2 + 大小球反推 (λh, λa)
# --------------------------------------------------------------------------- #
DEFAULT_FIT_HALF_WIDTH = 0.8
DEFAULT_FIT_ROUNDS = 2
DEFAULT_FIT_LOSS_TOL = 2e-3
_FIT_GRID = 17


@dataclass(frozen=True)
class LambdaFit:
    """λ 拟合结果。

    ``accepted``    是否可当结果用。为假时**不许**下游拿 lh/la 当拟合值。
    ``hit_boundary`` 最后用的搜索框是否仍在边界上命中（真 = 最优点可能落在框外）。
    ``loss``        四项（主/平/客/大 2.5）平方误差和，必须打印。
    """

    lam_h: float | None
    lam_a: float | None
    loss: float | None
    hit_boundary: bool
    accepted: bool
    rounds: int
    half_width: float
    reason: str = ""
    tau_clamped_cells: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lam_h": self.lam_h, "lam_a": self.lam_a, "loss": self.loss,
            "hit_boundary": self.hit_boundary, "accepted": self.accepted,
            "rounds": self.rounds, "half_width": self.half_width,
            "reason": self.reason, "tau_clamped_cells": self.tau_clamped_cells,
        }


def _model_1x2_ou(lam_h: float, lam_a: float, line: float = 2.5,
                  rho: float = 0.0, max_goals: int = 10) -> tuple[float, float, float, float, int]:
    """模型的主/平/客与大球（严格大于 line）概率。"""
    grid = dc_grid(lam_h, lam_a, rho=rho, max_goals=max_goals)
    home = draw = away = over = 0.0
    for score, prob in grid.probs.items():
        parts = _split_score(score)
        if parts is None:
            continue
        h, a = parts
        if h > a:
            home += prob
        elif h == a:
            draw += prob
        else:
            away += prob
        if h + a > line:
            over += prob
    return home, draw, away, over, grid.tau_clamped_cells


def _fit_loss(lam_h: float, lam_a: float, targets: tuple[float, float, float, float],
              line: float, rho: float) -> tuple[float, int]:
    home, draw, away, over, clamped = _model_1x2_ou(lam_h, lam_a, line=line, rho=rho)
    loss = ((home - targets[0]) ** 2 + (draw - targets[1]) ** 2
            + (away - targets[2]) ** 2 + (over - targets[3]) ** 2)
    return loss, clamped


def fit_lambda_from_market_1x2_and_ou(
    p_home: Any,
    p_draw: Any,
    p_away: Any,
    over_25: Any,
    *,
    line: float = 2.5,
    rho: float = 0.0,
    half_width: float = DEFAULT_FIT_HALF_WIDTH,
    max_rounds: int = DEFAULT_FIT_ROUNDS,
    loss_tol: float = DEFAULT_FIT_LOSS_TOL,
    center: tuple[float, float] | None = None,
) -> LambdaFit:
    """用市场去水后的 1X2 + 大小球反推 (λh, λa)。

    搜索是在一个方框里做的，所以「最优点落在框边上」就意味着真实最优可能在框外。
    旧实现只在中心 ±0.8 的框里搜一轮，然后把边界上的点当结果返回——坏拟合被
    静默当成答案。现在：触边界就扩框重搜，最多 ``max_rounds`` 轮；仍然触边界、
    或 loss 超过 ``loss_tol``，就**拒绝输出**（``accepted=False`` 且给出原因）。
    """
    vals = [_finite_float(v) for v in (p_home, p_draw, p_away, over_25)]
    if any(v is None for v in vals):
        return LambdaFit(None, None, None, False, False, 0, half_width,
                         reason="1X2/大小球概率缺失或非有限，无法拟合 λ")
    targets = (vals[0], vals[1], vals[2], vals[3])  # type: ignore[assignment]
    if any(t is None or t < 0.0 or t > 1.0 for t in targets):
        return LambdaFit(None, None, None, False, False, 0, half_width,
                         reason="1X2/大小球概率越界，无法拟合 λ")

    if center is not None:
        base_h, base_a = float(center[0]), float(center[1])
    else:
        total = targets[0] + targets[2]
        base_total = 2.2 + 1.2 * (targets[3] - 0.5) * 2.0
        base_total = min(max(base_total, 0.6), 6.0)
        share = (targets[0] / total) if total > 0 else 0.5
        base_h = max(0.15, base_total * share)
        base_a = max(0.15, base_total - base_h)

    width = float(half_width)
    best_loss = math.inf
    best: tuple[float, float] = (base_h, base_a)
    clamped_cells = 0
    hit = False
    rounds = 0
    for round_index in range(1, max(1, int(max_rounds)) + 1):
        rounds = round_index
        best_loss, best, hit, clamped_cells = _box_search(
            base_h, base_a, width, targets, line, rho,
        )
        if not hit:
            break
        width *= 2.0

    if hit:
        return LambdaFit(None, None, best_loss, True, False, rounds, width,
                         reason=f"扩展 {rounds} 轮后最优点仍在搜索框边界上（±{width:.2f}），拒绝用作拟合值")

    # 粗搜只能把最优解定位到一个格子内；不细化就报出的 loss 是格距决定的，
    # 与真实拟合优度无关。这里在最优格附近做等比收缩细化，直到步长足够小。
    best, best_loss, clamped_cells = _refine(best, best_loss, clamped_cells, targets, line, rho, width)

    if best_loss > loss_tol:
        return LambdaFit(None, None, best_loss, False, False, rounds, width,
                         reason=f"拟合残差 {best_loss:.6f} 超过容差 {loss_tol:.6f}，拒绝用作拟合值")
    return LambdaFit(best[0], best[1], best_loss, False, True, rounds, width,
                     reason="", tau_clamped_cells=clamped_cells)


def _refine(
    start: tuple[float, float], start_loss: float, start_clamped: int,
    targets: tuple[float, float, float, float], line: float, rho: float,
    width: float,
) -> tuple[tuple[float, float], float, int]:
    """在最优点周围等比收缩细化，直到步长 <= `_REFINE_STEP` 或不再变好。

    粗搜的格距是 `2*width/(grid-1)`，直接拿格子中心的 loss 当拟合优度，
    报出来的数只反映格距、不反映模型。细化到 1e-4 量级之后 loss 才有意义。
    """
    step = 2.0 * width / (_FIT_GRID - 1)
    best, best_loss, best_clamped = start, start_loss, start_clamped
    while step > _REFINE_STEP:
        improved = False
        for dh in (-step, 0.0, step):
            for da in (-step, 0.0, step):
                if dh == 0.0 and da == 0.0:
                    continue
                lam_h = max(0.01, best[0] + dh)
                lam_a = max(0.01, best[1] + da)
                loss, clamped = _fit_loss(lam_h, lam_a, targets, line, rho)
                if loss < best_loss - 1e-15:
                    best, best_loss, best_clamped = (lam_h, lam_a), loss, clamped
                    improved = True
        step *= 0.5
        if not improved and step <= _REFINE_STEP * 4:
            break
    return best, best_loss, best_clamped


_REFINE_STEP = 1e-4


def _box_search(
    base_h: float, base_a: float, width: float,
    targets: tuple[float, float, float, float], line: float, rho: float,
) -> tuple[float, tuple[float, float], bool, int]:
    """在 [base±width] 的方框里粗搜，返回 (loss, (lh, la), 是否触边界, clamp 计数)。"""
    lo_h = max(0.05, base_h - width)
    hi_h = base_h + width
    lo_a = max(0.05, base_a - width)
    hi_a = base_a + width
    grid = _FIT_GRID
    best_loss = math.inf
    best_pair = (base_h, base_a)
    best_clamped = 0
    best_index = (0, 0)
    for i in range(grid):
        lam_h = lo_h + (hi_h - lo_h) * i / (grid - 1)
        for j in range(grid):
            lam_a = lo_a + (hi_a - lo_a) * j / (grid - 1)
            loss, clamped = _fit_loss(lam_h, lam_a, targets, line, rho)
            if loss < best_loss:
                best_loss, best_pair, best_clamped = loss, (lam_h, lam_a), clamped
                best_index = (i, j)
    hit = best_index[0] in (0, grid - 1) or best_index[1] in (0, grid - 1)
    return best_loss, best_pair, hit, best_clamped


# --------------------------------------------------------------------------- #
# 残差与 KL
# --------------------------------------------------------------------------- #
def kl_divergence(p: Mapping[str, float], q: Mapping[str, float]) -> tuple[float, tuple[str, ...]]:
    """KL(p‖q)，跳过 p=0 的项（`max(p,1e-12)` 会让 KL 虚高）。

    返回 (kl, zero_prob_keys)。q=0 而 p>0 时该方向无定义，记为正无穷。
    """
    kl = 0.0
    zeros: list[str] = []
    for key, pv in p.items():
        value = _finite_float(pv)
        if value is None:
            continue
        if value <= 0.0:
            zeros.append(key)
            continue
        qv = _finite_float(q.get(key))
        if qv is None or qv <= 0.0:
            return math.inf, tuple(zeros)
        kl += value * math.log(value / qv)
    return kl, tuple(zeros)


def caliber_self_check(
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
    *,
    lam_h: float = 1.9,
    lam_a: float = 0.7,
    rho: float = 0.0,
) -> dict[str, Any]:
    """口径自检：同一份 Dixon-Coles 分布同时喂给两边，Δ 应当全为 0。

    做法：把同一份 DC 网格 (a) 投影到竞彩 31 维再按口径聚合，(b) 直接按每个
    市场桶的**真实覆盖集**求和，然后逐桶做差。口径真的对齐时两边是同一个数，
    `max_abs_delta` 应为 0（浮点噪声量级）。

    它给出的数字是「这个口径下 Δ 里有多少是纯口径伪差」的下界：
    `DEFAULT`（5 球→任何其他）实测 max|Δ|≈0.0145，`five_goals_in_team_other`
    实测 0.0。这就是为什么只有后者能当定价错位读。
    """
    grid = dc_grid(lam_h, lam_a, rho=rho)
    jc_probs = dixon_coles_to_jc31(lam_h, lam_a, rho=rho)["probs"]
    projected = project_jc31_onto_market(jc_probs, policy=policy, book=book)

    coverage = market_bucket_coverage(policy, book)
    market_side: dict[str, float] = {}
    for key in book.keys:
        if key in coverage:
            market_side[key] = sum(grid.probs.get(s, 0.0) for s in coverage[key])
        else:
            market_side[key] = grid.probs.get(key, 0.0)

    deltas = {
        key: projected["probs"].get(key, 0.0) - market_side.get(key, 0.0)
        for key in book.keys
    }
    max_abs = max((abs(v) for v in deltas.values()), default=0.0)
    return {
        "policy": policy.as_dict(),
        "policy_label": policy.label(),
        "book_size": book.size,
        "lam_h": lam_h,
        "lam_a": lam_a,
        "rho": rho,
        "deltas": deltas,
        "max_abs_delta": max_abs,
        "clean": max_abs < 1e-12,
        "lossless": jc_to_market_map(policy, book).lossless,
    }


def bucket_deltas(
    jc_probs: Mapping[str, float],
    market_probs: Mapping[str, float],
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    book: MarketBook = MARKET_BOOK_26,
) -> dict[str, Any]:
    """档 A：把竞彩分布聚合到市场桶上，逐桶做差。

    这是唯一可以直接当「定价错位」读的差额——**仅当口径无损时**。
    口径不可无损时，`caliber_self_check` 会把「同分布两边喂应得 0」的自检
    数字一并交出来，读者据此知道 Δ 里有多少是纯口径伪差，而不是定价差。
    """
    projected = project_jc31_onto_market(jc_probs, policy=policy, book=book)
    rows: list[dict[str, Any]] = []
    for key in book.keys:
        jc_value = projected["probs"].get(key, 0.0)
        mk_value = _finite_float(market_probs.get(key))
        rows.append({
            "bucket": key,
            "jc": jc_value,
            "market": mk_value,
            "delta": None if mk_value is None else jc_value - mk_value,
            "is_other": key in book.buckets,
        })
    other_keys = set(book.buckets)
    other_jc = sum(r["jc"] for r in rows if r["is_other"])
    other_mk_values = [r["market"] for r in rows if r["is_other"] and r["market"] is not None]
    other_mk = sum(other_mk_values) if len(other_mk_values) == len(book.buckets) else None

    self_check = caliber_self_check(policy=policy, book=book)
    warnings: list[str] = []
    if not projected["lossless"]:
        buckets = ", ".join(projected["unresolved"] and list(projected["unresolved"]) or [])
        warnings.append(
            f"口径不可无损比较（横跨多个市场桶的竞彩键：{buckets or '见 one_to_many_buckets'}）。"
            f"自检：同一份分布同时喂两边，Δ 应全为 0，实测 max|Δ|={self_check['max_abs_delta']:.5f}——"
            "这部分 Δ 是口径伪差，不是定价错位。"
        )

    return {
        "book_size": book.size,
        "policy": policy.as_dict(),
        "policy_label": policy.label(),
        "lossless": projected["lossless"],
        "unresolved": list(projected["unresolved"]),
        "one_to_many_buckets": list(projected["one_to_many_buckets"]),
        "bucket_gaps": dict(projected["bucket_gaps"]),
        "caliber_self_check": self_check,
        "rows": rows,
        "other_jc": other_jc,
        "other_market": other_mk,
        "other_delta": None if other_mk is None else other_jc - other_mk,
        "skipped": list(projected["skipped"]),
        "other_keys": sorted(other_keys),
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# 档 B：市场桶摊回 31 维（必须显式标注 modeled=True）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketExpansion:
    """把市场桶摊回 31 维的结果。

    ``modeled`` 恒为 True：桶内每个比分都是模型给的，不是市场报的。
    ``residual`` 桶内模型分布与「桶概率全压在既有精确格上」这一退化假设的
    残差（L1），用于告诉读者这次摊薄有多可信。
    """

    probs: dict[str, float]
    model: dict[str, Any]
    modeled: bool = True
    bucket_models: dict[str, dict[str, float]] = field(default_factory=dict)
    residual: float = 0.0
    roundtrip_loss: dict[str, float] = field(default_factory=dict)
    bucket_reconciliation: dict[str, dict[str, float]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "probs": dict(self.probs),
            "model": dict(self.model),
            "modeled": self.modeled,
            "bucket_models": {k: dict(v) for k, v in self.bucket_models.items()},
            "residual": self.residual,
            "roundtrip_loss": dict(self.roundtrip_loss),
            "bucket_reconciliation": {k: dict(v) for k, v in self.bucket_reconciliation.items()},
            "warnings": list(self.warnings),
        }


def bucket_probability_mass(
    bucket: str,
    grid: DcGrid,
    book: MarketBook = MARKET_BOOK_26,
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
) -> dict[str, float]:
    """网格里落进某个市场桶的比分 → 该桶内的条件概率（和为 1）。

    桶内没有任何质量时返回空字典（调用方必须当成「无法摊薄」而不是 0）。
    """
    coverage = market_bucket_coverage(policy, book)
    members = coverage.get(bucket)
    if not members:
        return {}
    inside = {score: grid.probs.get(score, 0.0) for score in members}
    total = sum(inside.values())
    if total <= 0.0:
        return {}
    return {score: value / total for score, value in inside.items() if value > 0.0}


def expand_market_to_jc31(
    market_probs: Mapping[str, Any],
    lam_h: Any,
    lam_a: Any,
    rho: Any = 0.0,
    *,
    book: MarketBook = MARKET_BOOK_26,
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    max_goals: int = 10,
) -> MarketExpansion:
    """档 B：把市场盘面摊回竞彩 31 维。

    精确格的概率原样保留；每个桶的概率按 Dixon-Coles 在该桶覆盖集上的条件
    分布摊开。**没有模型参数就直接拒绝**：不许拿平均分摊冒充。

    结果永远 ``modeled=True``，调用方必须把它当模型推断而不是市场报价。
    """
    if lam_h is None or lam_a is None:
        raise ValueError("档 B 需要模型参数 (λh, λa)：没有模型参数时拒绝摊薄，不用平均分摊冒充")
    # 传错形状（例如把 dixon_coles_to_jc31 的整个返回 dict 当 market_probs）时，
    # 逐个 key 取不到值就会静默返回全 0、零告警。这里直接拒绝。
    overlap = set(market_probs or {}) & set(book.keys)
    if not overlap:
        raise ValueError(
            f"market_probs 与市场 {book.size} 项盘面的键没有任何交集，拒绝摊薄"
            f"（传入 {len(market_probs or {})} 个键，例如 {list(market_probs or {})[:4]}）"
        )
    grid = dc_grid(float(lam_h), float(lam_a), rho=float(rho or 0.0), max_goals=max_goals)

    out: dict[str, float] = {key: 0.0 for key in JC_31_KEYS}
    bucket_models: dict[str, dict[str, float]] = {}
    roundtrip_loss: dict[str, float] = {}
    bucket_reconciliation: dict[str, dict[str, float]] = {}
    warnings: list[str] = []
    residual = 0.0

    for market_key in book.keys:
        mass = _finite_float(market_probs.get(market_key))
        if mass is None or mass < 0.0:
            continue
        if market_key not in book.buckets:
            out[market_key] = out.get(market_key, 0.0) + mass
            continue
        conditional = bucket_probability_mass(market_key, grid, book=book, policy=policy)
        if not conditional:
            if mass <= 0.0:
                # 该桶本来就没概率：不是结构差，也不必告警。空桶告警曾经对着
                # mass=0.0 也发一条，把「没有这份钱」误报成「这份钱丢了」。
                bucket_models[market_key] = {}
                continue
            # 模型在这个桶上完全没有质量：桶里的比分在给定 λ 下概率都下溢了。
            # 这不是「该桶概率为 0」，而是模型无法表达它——必须整笔记为结构差，
            # 否则这部分市场概率会从输出里静默消失。
            bucket_reconciliation[market_key] = {
                "market_mass": mass,
                "returned_to_same_bucket": 0.0,
                "unrepresentable": mass,
            }
            roundtrip_loss[market_key] = mass
            warnings.append(
                f"桶 {market_key} 在模型下没有质量（λh={grid.lam_h} λa={grid.lam_a} "
                f"rho={grid.rho}），该桶 {mass:.3e} 的概率无法摊薄，整笔计入结构差"
            )
            continue
        bucket_models[market_key] = conditional
        residual += abs(sum(conditional.values()) - 1.0)
        lost = 0.0
        kept = 0.0
        for score, share in conditional.items():
            target = dc_score_to_jc_key(score)
            if not target:
                warnings.append(f"桶 {market_key} 的比分 {score} 落不进竞彩 31 项")
                lost += share
                continue
            out[target] = out.get(target, 0.0) + mass * share
            # 摊到 31 维之后还能不能回到同一个市场桶？回不去，说明这一段
            # 质量在竞彩 31 维里根本无法表达（例：6:0 在市场的「任何其他」，
            # 但竞彩只有「胜其他」这一个槽）。这不是实现缺陷，是两套盘的结构差，
            # 必须量化上报，不能悄悄消失。
            if jc_to_market_key(target, policy=policy, book=book) == market_key:
                kept += share
            else:
                lost += share
        bucket_reconciliation[market_key] = {
            "market_mass": mass,
            "returned_to_same_bucket": mass * kept,
            "unrepresentable": mass * lost,
        }
        if lost > 0.0:
            roundtrip_loss[market_key] = mass * lost

    for bucket, lost_mass in roundtrip_loss.items():
        if lost_mass > _ROUNDTRIP_WARN:
            warnings.append(
                f"桶 {bucket} 有 {lost_mass:.3e} 的概率在竞彩 31 维里无法表达"
                f"（摊薄后回不到该桶），这部分是两套盘的结构差，不是定价差"
            )
    if sum(roundtrip_loss.values()) > _ROUNDTRIP_WARN:
        # 点名来源：读者要能分清「实现没做好」与「两套盘本来就不一样」。
        warnings.append(
            "结构差的主要来源：单队 5 球及以上（竞彩有独立格 / 市场并入其他）"
            "与 4:3/3:4 的归并口径不同 —— 这是两套盘的固有差异，不是实现缺陷"
        )

    return MarketExpansion(
        probs=out,
        model={
            "lam_h": grid.lam_h, "lam_a": grid.lam_a, "rho": grid.rho,
            "max_goals": grid.max_goals, "tau_clamped_cells": grid.tau_clamped_cells,
        },
        modeled=True,
        bucket_models=bucket_models,
        residual=residual,
        roundtrip_loss=roundtrip_loss,
        bucket_reconciliation=bucket_reconciliation,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------- #
# 残差分析（结构化返回，CLI 只负责打印）
# --------------------------------------------------------------------------- #
def analyze_residuals(
    jc_odds: Mapping[str, Any],
    market_odds: Mapping[str, Any],
    *,
    book_size: int = MARKET_26_COUNT,
    policy: MarketBucketPolicy = DEFAULT_MARKET_POLICY,
    model: Mapping[str, Any] | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """四维框架里的「去水概率 / 其他项 / 31 维残差与 KL」三块，结构化返回。

    档 A 永远给（把竞彩聚到市场桶上做差）；档 B 只在 ``model`` 给了
    ``lam_h``/``lam_a`` 时才给，且结果带 ``modeled=True``。

    没有模型参数不是错误：这时 ``tier_b`` 为 None，``warnings`` 里点名原因。
    """
    book = market_book(book_size)
    jc_norm = normalize_jc_odds(jc_odds)
    market_norm = normalize_market_odds(market_odds, book)

    warnings: list[str] = []
    if not jc_norm.complete:
        warnings.append(
            f"竞彩样本不完整（缺 {len(jc_norm.missing)} 项：{list(jc_norm.missing)[:6]}），RTP 不可估"
        )
    # 去水阶段的每一条告警都必须上行：别名重复键、表外键、返还率 ≥ 100%
    # 都是「这份样本不能直接信」的理由，埋在 jc/market 里读者看不到。
    warnings.extend(jc_norm.warnings)
    warnings.extend(market_norm.warnings)

    tier_a = bucket_deltas(jc_norm.probs, market_norm.probs, policy=policy, book=book)
    five_goal_cells = {
        key: jc_norm.probs.get(key)
        for key in JC_FIVE_GOAL_CELLS
    }
    five_goal_total = sum(v for v in five_goal_cells.values() if v is not None)

    tier_b: dict[str, Any] | None = None
    if model and model.get("lam_h") is not None and model.get("lam_a") is not None:
        expansion = expand_market_to_jc31(
            market_norm.probs, model.get("lam_h"), model.get("lam_a"),
            model.get("rho", 0.0), book=book, policy=policy,
        )
        residuals = [
            {
                "score": key,
                "jc": jc_norm.probs.get(key),
                "model": expansion.probs.get(key),
                "residual": (
                    None if jc_norm.probs.get(key) is None or expansion.probs.get(key) is None
                    else jc_norm.probs[key] - expansion.probs[key]
                ),
            }
            for key in JC_31_KEYS
        ]
        kl, zero_keys = kl_divergence(jc_norm.probs, expansion.probs)
        tier_b = {
            "modeled": True,
            "model": expansion.model,
            "residuals": residuals,
            "kl": kl,
            "zero_prob_keys": list(zero_keys),
            "bucket_models": expansion.bucket_models,
            "residual_l1": expansion.residual,
            "roundtrip_loss": dict(expansion.roundtrip_loss),
            "roundtrip_loss_total": sum(expansion.roundtrip_loss.values()),
            "bucket_reconciliation": {k: dict(v) for k, v in expansion.bucket_reconciliation.items()},
            "warnings": list(expansion.warnings) + [w for w in warnings if w not in expansion.warnings],
        }
    else:
        warnings.append("档 B 未运行：没有模型参数 (λh, λa)，拒绝用平均分摊冒充")

    ranked = [r for r in tier_a["rows"] if r["delta"] is not None]
    ranked.sort(key=lambda r: abs(r["delta"]), reverse=True)

    # 口径伪差的自检数字也必须在顶层可见：lossless=False 时读者要能一眼看到
    # 「同分布两边喂应得 0、实测 max|Δ|=X」，否则会把这些 Δ 当定价错位。
    warnings.extend(tier_a.get("warnings") or [])
    if tier_b is not None:
        # tier_b 的告警（结构性质量损失、桶无质量）合并到顶层并去重；
        # 之前只在 tier_b 分支内拼给自己，顶层 warnings 仍是空的。
        warnings.extend(tier_b.get("warnings") or [])

    seen: set[str] = set()
    merged: list[str] = []
    for warning in warnings:
        text = str(warning or '').strip()
        if text and text not in seen:
            seen.add(text)
            merged.append(text)

    return {
        "book_size": book.size,
        "policy": policy.as_dict(),
        "policy_label": policy.label(),
        "lossless": tier_a.get("lossless"),
        "jc": jc_norm.as_dict(),
        "market": market_norm.as_dict(),
        "tier_a": tier_a,
        "top_deltas": ranked[:max(1, int(top_n))],
        "five_goal_cells": five_goal_cells,
        "five_goal_total": five_goal_total,
        "tier_b": tier_b,
        "warnings": merged,
    }


# --------------------------------------------------------------------------- #
# 文本渲染
# --------------------------------------------------------------------------- #
def _pct(value: Any, digits: int = 2) -> str:
    """分数（0..1）→ 百分比文本；缺失打印「-」而不是 0。"""
    from number_text import pct_text

    number = _finite_float(value)
    return pct_text(number * 100.0, digits) if number is not None else "-"


def format_analysis_text(analysis: Mapping[str, Any], top_n: int = 10) -> str:
    """把 `analyze_residuals` 的结果渲染成可读文本；数字一律走 number_text 守卫。"""
    from number_text import assert_finite_text, num_text

    lines: list[str] = []
    lines.append("竞彩 31 项 × 市场 26/25 项 口径对比")
    lines.append(f"  市场盘面={analysis.get('book_size')} 项 口径={analysis.get('policy_label')}")
    jc = analysis.get("jc") or {}
    market = analysis.get("market") or {}
    lines.append(f"  RTP：竞彩={_pct(jc.get('rtp'))} 市场={_pct(market.get('rtp'))}")
    for side, block in (("竞彩", jc), ("市场", market)):
        if not block.get("complete"):
            lines.append(
                f"  ⚠️ {side}样本不完整（缺 {len(block.get('missing') or [])} 项），RTP 不可估"
            )

    lines.append("【档 A｜无损可比：竞彩聚合到市场桶上做差】")
    tier_a = analysis.get("tier_a") or {}
    if not tier_a.get("lossless"):
        check = tier_a.get("caliber_self_check") or {}
        lines.append(
            "  ⚠️ 本口径不可无损比较：Δ 含口径伪差，不可当定价错位读。"
            f"自检（同分布两边喂应得 0）：max|Δ|={num_text(check.get('max_abs_delta'), '.5f')}"
        )
        if tier_a.get("one_to_many_buckets"):
            lines.append(f"     横跨多个市场桶的桶：{', '.join(tier_a['one_to_many_buckets'])}")
        gaps = tier_a.get("bucket_gaps") or {}
        for bucket, gap in list(gaps.items())[:3]:
            only_jc = list(gap.get("only_in_jc") or [])[:4]
            lines.append(
                f"     {bucket}: 竞彩多出 {len(gap.get('only_in_jc') or [])} 个比分"
                f"{('（如 ' + ', '.join(only_jc) + '）') if only_jc else ''}"
            )
    for row in (analysis.get("top_deltas") or [])[:top_n]:
        lines.append(
            f"  {row['bucket']:<6} 竞彩{_pct(row['jc']):>8} "
            f"市场{_pct(row['market']):>8} Δ{num_text((row['delta'] or 0) * 100, '+.2f')}pp"
        )
    lines.append(
        f"  「其他」合计：竞彩{_pct(tier_a.get('other_jc'))} 市场{_pct(tier_a.get('other_market'))}"
        f" Δ{num_text((tier_a.get('other_delta') or 0) * 100, '+.2f')}pp"
    )
    lines.append(
        f"  6 个 5 球格在竞彩侧合计 {_pct(analysis.get('five_goal_total'))}"
        "（市场侧无独立格）"
    )

    tier_b = analysis.get("tier_b")
    lines.append("【档 B｜模型摊薄：市场桶按 Dixon-Coles 摊回 31 维】")
    if not tier_b:
        for warning in analysis.get("warnings") or []:
            lines.append(f"  ⚠️ {warning}")
    else:
        model = tier_b.get("model") or {}
        lines.append(
            f"  参数 λh={num_text(model.get('lam_h'), '.4f')} λa={num_text(model.get('lam_a'), '.4f')} "
            f"rho={num_text(model.get('rho'), '+.4f')} modeled={tier_b.get('modeled')}"
        )
        lines.append(
            f"  31 维 KL={num_text(tier_b.get('kl'), '.6f')} "
            f"桶内残差L1={num_text(tier_b.get('residual_l1'), '.3e')}"
        )
        zeros = tier_b.get("zero_prob_keys") or []
        if zeros:
            lines.append(f"  p=0 跳过 {len(zeros)} 项：{', '.join(zeros[:6])}")
        loss_total = tier_b.get("roundtrip_loss_total") or 0.0
        if loss_total > 0.0:
            lines.append(
                f"  结构差：{_pct(loss_total)} 的市场概率在竞彩 31 维里无法表达"
                f"（摊薄后回不到原桶，逐桶见 warnings）"
            )
        worst = sorted(
            (r for r in tier_b.get("residuals") or [] if r.get("residual") is not None),
            key=lambda r: abs(r["residual"]), reverse=True,
        )[:top_n]
        for row in worst:
            lines.append(
                f"  {row['score']:<5} 竞彩{_pct(row['jc']):>8} "
                f"模型{_pct(row['model']):>8} 残差{num_text((row['residual'] or 0) * 100, '+.2f')}pp"
            )
    text = "\n".join(lines)
    assert_finite_text(text)
    return text


# --------------------------------------------------------------------------- #
# 全量不变量
# --------------------------------------------------------------------------- #
class ScoreBookInvariantError(AssertionError):
    """两套盘的口径不变量被破坏。"""


def assert_score_book_invariants() -> None:
    """逐条断言两套盘的口径。任何一条红都说明模块被改错了。

    基数与集合逐项比较，不看「长度对得上」就过——`len()` 相等掩盖不了
    「5:2 跑进胜其他、另一个格子跑出来」这种互换。
    """
    # 竞彩 31 = 13 + 5 + 13
    if len(JC_31_KEYS) != JC_31_COUNT:
        raise ScoreBookInvariantError(f"竞彩基数不是 31: {len(JC_31_KEYS)}")
    if (len(JC_HOME_SCORES) + 1, len(JC_DRAW_SCORES) + 1, len(JC_AWAY_SCORES) + 1) != (13, 5, 13):
        raise ScoreBookInvariantError("竞彩 13+5+13 三方向计数不对")
    if tuple(JC_31_KEYS) != tuple(JC_HOME_SCORES + (JC_HOME_OTHER,)
                                + JC_DRAW_SCORES + (JC_DRAW_OTHER,)
                                + JC_AWAY_SCORES + (JC_AWAY_OTHER,)):
        raise ScoreBookInvariantError("竞彩 31 项顺序/集合与 12+1+4+1+12+1 展开不相等")
    if len(set(JC_31_KEYS)) != JC_31_COUNT:
        raise ScoreBookInvariantError("竞彩 31 项里有重复键")

    # 市场 26 = 22 精确格 + 4 桶；25 = 22 + 3 桶。键表按**逐项相等**核对，
    # 不是只看长度：把 25 项盘面填成 26 项的键表，长度检查会过，集合检查不会。
    if tuple(MARKET_BOOK_26.keys) != tuple(MARKET_26_KEYS):
        raise ScoreBookInvariantError("26 项盘面键表与 MARKET_26_KEYS 不逐项相等")
    if tuple(MARKET_BOOK_25.keys) != tuple(MARKET_25_KEYS):
        raise ScoreBookInvariantError("25 项盘面键表与 MARKET_25_KEYS 不逐项相等")
    if MARKET_DRAW_OTHER in MARKET_BOOK_25.keys:
        raise ScoreBookInvariantError("25 项盘面不该有独立平局其他")
    if MARKET_DRAW_OTHER not in MARKET_BOOK_26.keys:
        raise ScoreBookInvariantError("26 项盘面缺独立平局其他")

    # 注册表里的盘面（含被测试临时换进去的）也必须自洽。
    for size, book in MARKET_BOOKS.items():
        if book.size != size:
            raise ScoreBookInvariantError(f"MARKET_BOOKS[{size}] 自报 {book.size} 项")
        if len(book.precise) + len(book.buckets) != book.size:
            raise ScoreBookInvariantError(
                f"市场 {book.size} 项 = 精确格 + 桶 恒等式不成立："
                f"{len(book.precise)} + {len(book.buckets)} != {book.size}"
            )
        if len(book.keys) != book.size:
            raise ScoreBookInvariantError(f"市场 {book.size} 项键表长度不对: {len(book.keys)}")
        if len(set(book.keys)) != book.size:
            raise ScoreBookInvariantError(f"市场 {book.size} 项键表里有重复键")
        if not set(book.buckets) <= set(book.keys):
            raise ScoreBookInvariantError(f"市场 {book.size} 项桶不在键表里")
        if not set(book.precise) <= set(book.keys):
            raise ScoreBookInvariantError(f"市场 {book.size} 项精确格不在键表里")
        if (MARKET_DRAW_OTHER in book.buckets) != (MARKET_DRAW_OTHER in book.keys):
            raise ScoreBookInvariantError(f"市场 {book.size} 项的平局其他：桶表与键表不一致")
        if book.has_draw_other() and MARKET_DRAW_OTHER not in book.keys:
            raise ScoreBookInvariantError(f"市场 {book.size} 项声明有平局其他但键表里没有")

    for book, precise_n, bucket_n in (
        (MARKET_BOOK_26, 22, 4), (MARKET_BOOK_25, 22, 3),
    ):
        if len(book.precise) != precise_n:
            raise ScoreBookInvariantError(f"市场 {book.size} 项盘面精确格不是 {precise_n}: {len(book.precise)}")
        if len(book.buckets) != bucket_n:
            raise ScoreBookInvariantError(f"市场 {book.size} 项盘面桶数不是 {bucket_n}: {len(book.buckets)}")

    if set(MARKET_BOOK_25.keys) - set(MARKET_BOOK_26.keys):
        raise ScoreBookInvariantError("25 项盘面出现了 26 项盘面没有的键")
    if set(MARKET_BOOK_26.keys) - set(MARKET_BOOK_25.keys) != {MARKET_DRAW_OTHER}:
        raise ScoreBookInvariantError("26 与 25 项的差集必须正好是 {平局其他}")

    if tuple(MARKET_PRECISE_SCORES) != (
        tuple(MARKET_HOME_SCORES) + tuple(MARKET_DRAW_SCORES) + tuple(MARKET_AWAY_SCORES)
    ):
        raise ScoreBookInvariantError("市场精确格集合与主胜9+平局4+客胜9 展开不相等")

    # 每个比分有且仅有一个归属桶（对每个 policy × book 组合）
    for policy in ALL_POLICIES:
        for size, book in MARKET_BOOKS.items():
            coverage = market_bucket_coverage(policy, book)
            if set(coverage) != set(book.buckets):
                raise ScoreBookInvariantError(
                    f"覆盖表的桶集合与盘面不符（{size} 项）：{sorted(set(coverage) ^ set(book.buckets))}"
                )
            seen: dict[str, str] = {}
            for bucket, scores in coverage.items():
                if bucket not in book.buckets:
                    raise ScoreBookInvariantError(f"覆盖表出现非法桶 {bucket}（{size} 项）")
                for score in scores:
                    if score in seen:
                        raise ScoreBookInvariantError(
                            f"比分 {score} 同时落在 {seen[score]} 与 {bucket}（policy={policy.label()}）"
                        )
                    seen[score] = bucket
            for score in book.precise:
                if score in seen:
                    raise ScoreBookInvariantError(
                        f"精确格 {score} 又出现在桶 {seen[score]} 里（{size} 项）"
                    )

    # 6 个 5 球格：竞彩侧是独立格，市场侧不是（按 policy 落桶）
    independent_jc = [s for s in JC_FIVE_GOAL_CELLS if s in JC_31_KEYS]
    if tuple(independent_jc) != tuple(JC_FIVE_GOAL_CELLS):
        raise ScoreBookInvariantError("6 个 5 球格没有全部成为竞彩独立格")
    for size, book in MARKET_BOOKS.items():
        for score in JC_FIVE_GOAL_CELLS:
            if score in book.keys:
                raise ScoreBookInvariantError(f"市场 {size} 项盘面把 {score} 列成了独立格")
    for policy in ALL_POLICIES:
        for size, book in MARKET_BOOKS.items():
            mapping = jc_to_market_map(policy, book)
            for score in JC_FIVE_GOAL_CELLS:
                target = mapping.mapping.get(score)
                if not target:
                    raise ScoreBookInvariantError(f"{score} 在市场 {size} 项（{policy.label()}）下没有归属")
                if target in book.precise:
                    raise ScoreBookInvariantError(f"{score} 被投到了市场精确格 {target}")

    # 竞彩 31 -> 市场：每个键有且仅有一个目标
    for policy in ALL_POLICIES:
        for size, book in MARKET_BOOKS.items():
            mapping = jc_to_market_map(policy, book)
            if set(mapping.mapping) != set(JC_31_KEYS):
                missing = sorted(set(JC_31_KEYS) - set(mapping.mapping))
                raise ScoreBookInvariantError(f"竞彩 31 项映射缺项: {missing}")
            for jc_key, market_key in mapping.mapping.items():
                if market_key not in book.keys:
                    raise ScoreBookInvariantError(f"{jc_key} 投到了市场盘面外的键 {market_key}")
