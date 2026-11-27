"""四道护栏 + 五阶段清算中已定义的两个阶段：纯函数实现。

本模块只做判定，不碰 IO、不做网络、不引入 streamlit / pandas / requests / torch。
输入是结构化字典，输出是判定结果字典。方向语义的铁律写在下面。

方向铁律（用户 2026-11-26 裁决）
--------------------------------
1. ``direction`` 永远单一（``'H'`` / ``'D'`` / ``'A'`` 之一）。
2. 护栏 2（2 倍邻近 / 1.35 均势）与护栏 4（ΔW 降级）**只输出风险标记**：
   不修改 ``direction``，不在任何推荐集合里塞入第二个胜平负方向。
3. 护栏 1 是宪法级否决（``一票否决，覆盖微观``），它是唯一允许改写
   ``direction`` 的护栏，且只改写成**一个**方向。
4. 护栏 3 只调权重并剥夺首选资格，从不改方向。
5. Overkill 只在比分方向与已锁定 ``direction`` 一致时激活候选池，
   方向不一致时只记录不采纳。

量纲（务必先读）
----------------
* **Δ（渠道均差）**：按规范 §0.1 的定义 ``Δ = 市均赔率 − 竞彩官方赔率``，
  单位是**赔率**，不是百分点。本模块所有 Δ 入参（``abs_deltas`` 的三向均值、
  ``score_deltas`` 的逐比分有符号值）都按这个量纲读取，
  ``+0.50`` / ``-0.50`` / ``+20.0`` 这些阈值也都在这个量纲上比较。
* **ΔW（护栏 4 的首/次权差）**：来自引擎 ``_score_weight`` 的 ``total``，
  那是 **0–1 尺度**（``theory/100 + market/100`` 再加 0.01/0.02 步长修正）。
  规范写的 ``ΔW < 5.0`` 是**百分点**，所以在 0–1 尺度上必须
  ``delta_w_pp = delta_w_raw * 100`` 之后再与 5.0 比较。
  直接拿 0–1 的 ``delta_w_raw`` 比 5.0 会恒真（实测 400 组有效 payload
  全部命中），每一场都会被钉到 0.60 置信度。

五阶段清算的范围
----------------
规范 §0.3 逐字给出的只有「阶段 1 ``MIN_ABS_DIFF_LOCK``」与
「阶段 2 ``TRUE PROTECTION``（含 Overkill Protocol）」两个阶段；
阶段 3–5 在本轮规范里没有任何文字定义，因此本模块**不发明**它们，
只实现有定义的两个阶段。
"""
from __future__ import annotations

import dataclasses
import math
import re
from typing import Any

# --------------------------------------------------------------------------- #
# 常量表（阈值一律具名，不得内联魔数）
# --------------------------------------------------------------------------- #
#: 护栏 1 的一票否决档位：|tier_diff| 达到该值即锁定对应方向。
TIER_DOMINANCE_THRESHOLD = 2

#: 规范要求的两个具名否决标记（逐字，供下游按字符串识别）。
VETO_TIER_DOMINANCE_WIN = 'V16_31_Fundamental_Tier_Dominance_Win'
VETO_TIER_DOMINANCE_LOSS = 'V16_31_Fundamental_Tier_Dominance_Loss'

#: 未命中任何否决时的 ``veto_action``（沿用引擎既有取值，不得改动）。
VETO_NONE = 'NONE'

#: 护栏 2 的 2 倍邻近阈值：第二小 / 第一小 < 2.0 → 非单边碾压。
NEIGHBORHOOD_RATIO_THRESHOLD = 2.0

#: 护栏 2 的均势熔断阈值：|Δ_胜| / |Δ_负| <= 1.35 → 均势盘。
EVEN_MATCH_ABS_RATIO_THRESHOLD = 1.35

#: 高熵联赛的放宽阈值。**本轮不接电**（见 ``is_high_entropy_league``）。
HIGH_ENTROPY_ABS_RATIO_THRESHOLD = 1.80

#: 联赛放宽不生效的原因码（C7 指定）。
HIGH_ENTROPY_RELAX_REASON = 'league_vocabulary_unreliable'

#: 联赛压力权重缺省时的来源标记（C4 唯一允许「取不到就当 0」的地方）。
LEAGUE_PRESSURE_SOURCE_UNAVAILABLE = 'unavailable'

#: 联赛 → 压力权重。默认空字典：没有可靠来源，查不到就如实报缺。
LEAGUE_PRESSURE_WEIGHTS: dict[str, float] = {}

#: 护栏 3 的「席位」窗口：热度榜单前 N 行。
#: 席位定义见 ``guardrail_3_inducing_trap`` 的 docstring。
HEAT_SEAT_WINDOW = 8

#: 护栏 3 的席位阈值：某方向在窗口内占 >= 4 席即为散户热度集中。
HEAT_SEAT_THRESHOLD = 4

#: 护栏 3 的均差阈值：中低赔常见比分 Δ > +0.50（赔率量纲）。
TRAP_DELTA_THRESHOLD = 0.50

#: 护栏 3 命中后的权重乘子。
TRAP_WEIGHT_FACTOR = 0.20

#: 护栏 4 的降级阈值，单位百分点。
CONFIDENCE_DELTA_W_THRESHOLD_PP = 5.0

#: 护栏 4 命中后的目标置信度。
DECAYED_CONFIDENCE = 0.60

#: 阶段 2 的基础保护乘子：``2.50 + w_league_pressure``。
PROTECTION_MULTIPLIER_BASE = 2.50

#: 阶段 2 的保护触发阈值（赔率量纲，有符号）。
PROTECTION_DELTA_THRESHOLD = -0.50

#: 阶段 2 的核心低赔/零封比分。
PROTECTION_CORE_SCORES = ('1-0', '2-0', '0-1', '0-2', '1-1')

#: Overkill 协议的尾部风险阈值（赔率量纲）。
OVERKILL_DELTA_THRESHOLD = 20.0

#: Overkill 协议只认「胜其它 / 负其它」这两种聚合比分（两种写法都认）。
OVERKILL_SCORES = ('胜其它', '胜其他', '负其它', '负其他')

#: PAR-9 已落地的禁用词表：风险陈述里不得出现任何选号指引。
PICK_LANGUAGE = ('首选', '次选', '双选', '强推', '推荐')

#: 护栏 3 命中时对外的那句风险陈述。
#:
#: 刻意不含 PAR-9 的五个词里的任何一个字，也不靠近义替换绕过：这里说的是
#: 「优先资格」，即这些比分的排队位置被降级，而不是「换一个选号建议」。
#: 渲染侧（报告 / TG 段落）有独立扫描，靠本模块的否定式豁免躲不过去。
TRAP_RISK_NOTE = '热度与通道均差背离，相关比分已失去优先资格'

#: 方向 → 赛果桶，以及桶 → 方向。
BUCKET_OF_DIRECTION = {'H': 'home', 'D': 'draw', 'A': 'away'}
DIRECTION_OF_BUCKET = {'home': 'H', 'draw': 'D', 'away': 'A'}
DIRECTIONS = ('H', 'D', 'A')

#: 通道名：规范写 Home/Away，引擎写 WIN/LOSS，矩阵写 胜/平/负。
#: ``|ΔHome| = |Δ_WIN| = |Δ_胜|``，``|ΔAway| = |Δ_LOSS| = |Δ_负|``，平局不参与比值。
HOME_CHANNEL_LABEL = '胜'
DRAW_CHANNEL_LABEL = '平'
AWAY_CHANNEL_LABEL = '负'

#: ``abs_deltas`` 接受的写法 -> 通道。中文是主口径。
#:
#: 三种非中文写法里，**只有小写那一组是矩阵模块的真实字段名**：
#: ``score_matrices.ChannelAbsSummary`` 声明的是 ``win_mean_abs_delta`` /
#: ``draw_mean_abs_delta`` / ``loss_mean_abs_delta``（全小写）。集成契约 §1.1
#: 要求直接接受注入的 ``ChannelAbsSummary``，所以这三个必须认。
#:
#: ``WIN`` / ``DRAW`` / ``LOSS`` 与首字母大写的 ``WIN_mean_abs_delta`` 一组是
#: **本模块自定义的别名**，对应矩阵模块 ``CompleteScoreMatrices.channel_abs_summary``
#: 那个旧字典形状的便利属性 —— 它不是 ``ChannelAbsSummary`` 本体。
#:
#: 同一通道给了多种写法且数值不一致时**报冲突**，不静默挑一个。
CHANNEL_ALIASES: dict[str, str] = {
    # 中文主口径
    '胜': HOME_CHANNEL_LABEL,
    '平': DRAW_CHANNEL_LABEL,
    '负': AWAY_CHANNEL_LABEL,
    # 矩阵模块 ChannelAbsSummary 的真实字段名（小写，dataclass 本体与 asdict）
    'win_mean_abs_delta': HOME_CHANNEL_LABEL,
    'draw_mean_abs_delta': DRAW_CHANNEL_LABEL,
    'loss_mean_abs_delta': AWAY_CHANNEL_LABEL,
    # 本模块自定义别名：矩阵模块 channel_abs_summary 的旧字典形状
    'WIN_mean_abs_delta': HOME_CHANNEL_LABEL,
    'DRAW_mean_abs_delta': DRAW_CHANNEL_LABEL,
    'LOSS_mean_abs_delta': AWAY_CHANNEL_LABEL,
    # 本模块自定义别名：裸方向名
    'WIN': HOME_CHANNEL_LABEL,
    'DRAW': DRAW_CHANNEL_LABEL,
    'LOSS': AWAY_CHANNEL_LABEL,
}

#: 聚合比分两种写法 -> 赛果桶（与引擎 ``AGGREGATE_ALIASES`` 同义）。
AGGREGATE_BUCKETS = {
    '胜其它': 'home', '胜其他': 'home',
    '平其它': 'draw', '平其他': 'draw',
    '负其它': 'away', '负其他': 'away',
}

_SCORE_PATTERN = re.compile(r'(\d+)\s*-\s*(\d+)')


class GuardrailInputError(ValueError):
    """比值类护栏的分母不可用。

    单独定义异常类型，是为了让调用方能区分「输入缺项」与「数学上无定义」，
    而不是靠一个 0.0 兜底糊过去。
    """


# --------------------------------------------------------------------------- #
# 基础工具（不猜测、不补默认值）
# --------------------------------------------------------------------------- #
def _finite(value: Any) -> float | None:
    """把输入转成有限浮点；不接受 bool / 非数值 / NaN / inf，失败返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(result):
        return None
    return result


def _cs_key(score: Any) -> str:
    """比分归一：``2:1`` / ``2 - 1`` / ``2-1`` 都成 ``2-1``。"""
    return str(score).replace('：', '-').replace(':', '-').replace(' ', '').strip()


def _parse_score(score: Any) -> tuple[int, int] | None:
    match = _SCORE_PATTERN.fullmatch(_cs_key(score))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _score_bucket(score: Any) -> str:
    """比分 -> ``'home'`` / ``'draw'`` / ``'away'``；无法判定返回空串。

    与引擎 ``_score_bucket`` 同义（结构性判定 + 聚合别名），但**不 import** 引擎：
    引擎所在分支已冻结并 repin，跨分支 import 会让那条 pin 失效。
    """
    text = _cs_key(score)
    aggregate = AGGREGATE_BUCKETS.get(text)
    if aggregate:
        return aggregate
    parsed = _parse_score(text)
    if parsed is None:
        return ''
    home_goals, away_goals = parsed
    if home_goals > away_goals:
        return 'home'
    if home_goals == away_goals:
        return 'draw'
    return 'away'


def _is_aggregate(score: Any) -> bool:
    return _cs_key(score) in AGGREGATE_BUCKETS


def _missing(field: str, reason: str) -> dict[str, Any]:
    return {'kind': 'missing', 'field': field, 'reason': reason}


def _invalid(field: str, reason: str) -> dict[str, Any]:
    return {'kind': 'invalid', 'field': field, 'reason': reason}


def _route(block: dict[str, Any], issue: dict[str, Any]) -> None:
    """把 ``_missing`` / ``_invalid`` 的返回值投进对应列表。

    「字段没给」与「字段给了但数学上无定义」是两件事，调用方需要分开处理：
    前者说明上游还没接，后者说明数据本身有问题。混在一个列表里会让
    「分母为 0」看起来像「还没接数据」。
    """
    key = 'missing' if issue.get('kind') == 'missing' else 'invalid'
    block[key].append({'field': issue.get('field', ''), 'reason': issue.get('reason', '')})


def _channel_pairs(abs_deltas: Any) -> tuple[list[tuple[str, Any]], dict[str, Any] | None]:
    """把入参摊平成 ``[(键写法, 原始值)]``，只取通道相关字段。

    支持三种真实交付方式：

    * ``dict``（键可以是中文 / 小写字段名 / 大写别名）
    * ``score_matrices.ChannelAbsSummary`` 这类 **dataclass 实例**
    * 任意带同名属性的对象

    dataclass 走 ``dataclasses.fields`` 逐字段 ``getattr``，**不用** ``dataclasses.asdict``：
    asdict 会把 ``min_channel: str`` / ``win_count: int`` / ``channel_spread: float``
    一并倒进判定路径，那些字段与三向 |Δ| 无关，靠「未知键忽略」放行属于运气而不是设计。
    """
    if isinstance(abs_deltas, dict):
        return [(str(key), value) for key, value in abs_deltas.items()], None
    if abs_deltas is None:
        return [], _missing('abs_deltas', 'abs_deltas 缺失')
    if dataclasses.is_dataclass(abs_deltas) and not isinstance(abs_deltas, type):
        names = [field.name for field in dataclasses.fields(abs_deltas)]
        return [(name, getattr(abs_deltas, name, None)) for name in names], None
    # 非 dataclass 但带同名属性的普通对象：只取已知的通道字段，不扫 __dict__。
    present = [name for name in CHANNEL_ALIASES if hasattr(abs_deltas, name)]
    if present:
        return [(name, getattr(abs_deltas, name, None)) for name in present], None
    return [], _missing('abs_deltas', 'abs_deltas 缺失或类型不受支持（需要字典或 ChannelAbsSummary）')


def _positive_channel_deltas(abs_deltas: Any) -> tuple[dict[str, float], dict[str, Any] | None]:
    """校验三向 |Δ| 并返回 ``{胜,平,负: 正值}``；不合格时返回问题描述。

    比值类护栏要求三向都 **> 0**：分母为 0 的比值只能靠 ``1e-9`` 之类的隐含
    兜底才算得出来，那正是零兜底铁律禁止的东西。这里与引擎 ``pairwise_vote``
    的处理保持一致——宁可直接拒绝，也不编一个比值。

    输入既可以是字典，也可以是 ``score_matrices.ChannelAbsSummary`` dataclass 实例
    （集成契约 §1.1 要求直接接受注入的它）。两条路径汇到同一套校验与冲突检测。
    """
    pairs, issue = _channel_pairs(abs_deltas)
    if issue:
        return {}, issue
    # 先按别名把各种写法归到三个通道；同一通道出现互相矛盾的写法就报冲突。
    resolved: dict[str, float] = {}
    for raw_key, raw_value in pairs:
        channel = CHANNEL_ALIASES.get(str(raw_key).strip())
        if channel is None:
            continue
        value = _finite(raw_value)
        if value is None:
            return {}, _invalid(f'abs_deltas.{raw_key}', f'abs_deltas.{raw_key} 非有限数值: {raw_value!r}')
        if channel in resolved and resolved[channel] != value:
            return {}, _invalid(
                f'abs_deltas.{raw_key}',
                f'通道 {channel} 被多种写法赋了不同数值（{resolved[channel]!r} vs {value!r}）',
            )
        resolved[channel] = value
    values: dict[str, float] = {}
    for label in (HOME_CHANNEL_LABEL, DRAW_CHANNEL_LABEL, AWAY_CHANNEL_LABEL):
        if label not in resolved:
            return {}, _missing(f'abs_deltas.{label}', f'abs_deltas.{label} 缺失')
        value = resolved[label]
        if value <= 0:
            return {}, _invalid(
                f'abs_deltas.{label}',
                f'abs_deltas.{label} 必须 > 0（0 分母的比值只能靠兜底算出来）: {value!r}',
            )
        values[label] = value
    return values, None


def _signed_deltas(score_deltas: Any) -> tuple[dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    """把逐比分 Δ 表归一成 ``{score: float}``，同时回收缺项与不可解析项。"""
    values: dict[str, float] = {}
    missing: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    if not isinstance(score_deltas, dict):
        missing.append({'field': 'score_deltas', 'reason': 'score_deltas 缺失或不是字典'})
        return {}, missing, invalid
    for raw_score, raw_delta in score_deltas.items():
        score = _cs_key(raw_score)
        delta = _finite(raw_delta)
        if not score or delta is None:
            invalid.append({'field': f'score_deltas.{raw_score}', 'reason': '比分或 Δ 不可解析'})
            continue
        values[score] = delta
    return values, missing, invalid


#: 「否定—剥夺」结构：这些子句在**剥夺**资格，与「不做推荐」同类，
#: 扫描前整段剔除，避免把合法的否定陈述误判成选号指引。
#:
#: 只在自检层当安全网用 —— 面向用户的文案本身仍不该含被禁词字
#: （PAR-9 的输出扫描在渲染侧还有一道，靠豁免躲不过去）。
#: 每一条都以子句终止符为右边界，避免 `不得` 之类吞掉同一句后半段真正的指引。
NEGATION_CLAUSE_PATTERNS = (
    r'不具备[^，。；,;！!？?]*?资格',
    r'不满足[^，。；,;！!？?]*?条件',
    r'失去[^，。；,;！!？?]*?资格',
    r'不得[^，。；,;！!？?]*',
    r'禁止[^，。；,;！!？?]*',
    r'排除[^，。；,;！!？?]*',
)
#: 「拒绝给建议」的几个固定整串，同样是合法用法。
NEGATION_PHRASES = ('不做任何选择建议', '不做推荐', '不推荐')


def pick_language_violations(text: Any) -> list[str]:
    """返回文本中出现的 PAR-9 禁用词；空列表表示这是一句纯风险陈述。

    「否定—剥夺」结构（``不具备…资格`` / ``不满足…条件`` / ``失去…资格`` /
    ``不得…`` / ``禁止…`` / ``排除…``）与 ``不做推荐`` / ``不推荐`` 一样，
    是在**剥夺**资格而不是给予指引，扫描前整段剔除。

    剔除是按匹配区间挖掉，不是整句丢弃：``1-0 不具备首选资格，建议首选 2-0``
    会剩下后半句，``建议首选`` 照样被抓住 —— 豁免不能变成把扫描关掉。
    """
    cleaned = str(text or '')
    for pattern in NEGATION_CLAUSE_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned)
    for phrase in NEGATION_PHRASES:
        cleaned = cleaned.replace(phrase, '')
    return [word for word in PICK_LANGUAGE if word in cleaned]


# --------------------------------------------------------------------------- #
# 联赛字段可靠性（C7：本轮按 check_then_decide 判为不可靠）
# --------------------------------------------------------------------------- #
def resolve_league_pressure(league_name: str | None) -> tuple[float, str]:
    """查联赛压力权重，返回 ``(weight, source)``。

    ``LEAGUE_PRESSURE_WEIGHTS`` 默认空字典 —— 全仓库没有任何 ``league_pressure``
    数据源，所以这里恒返回 ``(0.0, "unavailable")``。这是全模块**唯一**允许
    「取不到就当 0」的地方：``M_Protection = 2.50 + 0 = 2.50`` 正是规范给出的
    基础乘子，不构成信息伪造，但调用方必须能从 ``source`` 看到它是缺省值。
    """
    if not isinstance(league_name, str) or not league_name.strip():
        return 0.0, LEAGUE_PRESSURE_SOURCE_UNAVAILABLE
    weight = _finite(LEAGUE_PRESSURE_WEIGHTS.get(league_name.strip()))
    if weight is None:
        return 0.0, LEAGUE_PRESSURE_SOURCE_UNAVAILABLE
    return weight, 'table'


def is_high_entropy_league(league_name: str | None) -> tuple[bool, str]:
    """判断是否为高熵联赛（规范里的 1.80 放宽分支）。

    **恒返回 ``(False, "unavailable")``**，1.80 分支不接电。

    原因（实测）：``league_name`` 在本仓库是自由中文文本，写入时取
    ``match_record.get('league', match_record.get('league_name', ''))``，
    缺省填充 ``'未知联赛'`` / ``'竞彩赛事'``，不是受控词表。没有可信词表就
    没法把某个联赛稳定识别成「高熵」，因此按用户裁定的 ``check_then_decide``
    如实报缺，而不是凭队名硬匹配。

    这条函数同时是**防回退护栏**：任何人想「顺手」把荷甲加进白名单，
    都必须先改掉这里的返回值，测试会立刻红。
    """
    del league_name  # 参数保留是为了钉住未来接线时的签名形状
    return False, LEAGUE_PRESSURE_SOURCE_UNAVAILABLE


# --------------------------------------------------------------------------- #
# 护栏 1：宏观基本面宪法级边界
# --------------------------------------------------------------------------- #
def guardrail_1_macro_dominance(tier_diff: Any = None) -> dict[str, Any]:
    """``tier_diff`` 一票否决：>= 2 锁主胜，<= -2 锁客胜，``|tier_diff| < 2`` 放行微观。

    ``tier_diff`` 必须由调用方显式提供（C6）：本模块不从排名差、赔率差或任何
    其它间接量推算它。缺失时护栏不生效并写入 ``missing``。
    """
    result: dict[str, Any] = {
        'guardrail': 'FUNDAMENTAL_TIER_DOMINANCE',
        'status': 'skipped',
        'applies': False,
        'tier_diff': None,
        'threshold': TIER_DOMINANCE_THRESHOLD,
        'veto_action': VETO_NONE,
        'locked_direction': '',
        'reason': '',
        'missing': [],
        'invalid': [],
    }
    if tier_diff is None:
        result['reason'] = 'tier_diff 缺失：宏观护栏不生效（不从排名差/赔率差反推）'
        result['missing'].append({'field': 'tier_diff', 'reason': result['reason']})
        return result
    value = _finite(tier_diff)
    if value is None:
        result['status'] = 'invalid'
        result['reason'] = f'tier_diff 非有限数值: {tier_diff!r}'
        result['invalid'].append({'field': 'tier_diff', 'reason': result['reason']})
        return result
    if not float(value).is_integer():
        result['status'] = 'invalid'
        result['reason'] = f'tier_diff 必须是整数档位，收到 {value!r}'
        result['invalid'].append({'field': 'tier_diff', 'reason': result['reason']})
        return result
    tiers = int(value)
    result['tier_diff'] = tiers
    if tiers >= TIER_DOMINANCE_THRESHOLD:
        result.update({
            'status': 'ok',
            'applies': True,
            'veto_action': VETO_TIER_DOMINANCE_WIN,
            'locked_direction': 'H',
            'reason': f'tier_diff={tiers} >= {TIER_DOMINANCE_THRESHOLD}：基本面锁定主胜，覆盖微观',
        })
    elif tiers <= -TIER_DOMINANCE_THRESHOLD:
        result.update({
            'status': 'ok',
            'applies': True,
            'veto_action': VETO_TIER_DOMINANCE_LOSS,
            'locked_direction': 'A',
            'reason': f'tier_diff={tiers} <= -{TIER_DOMINANCE_THRESHOLD}：基本面锁定客胜，覆盖微观',
        })
    else:
        result.update({
            'status': 'ok',
            'applies': False,
            'veto_action': VETO_NONE,
            'reason': f'|tier_diff|={abs(tiers)} < {TIER_DOMINANCE_THRESHOLD}：放行微观',
        })
    return result


# --------------------------------------------------------------------------- #
# 护栏 2：均差邻近 + 均势熔断（只出标记，绝不改方向）
# --------------------------------------------------------------------------- #
def channel_neighborhood_ratio(abs_deltas: Any) -> float:
    """三向 |Δ| 的「第二小 / 第一小」。分母为 0 抛 ``GuardrailInputError``。"""
    values, issue = _positive_channel_deltas(abs_deltas)
    if issue:
        raise GuardrailInputError(str(issue['reason']))
    ordered = sorted(values.values())
    return ordered[1] / ordered[0]


def home_away_abs_ratio(abs_deltas: Any) -> float:
    """``|ΔHome| / |ΔAway|`` = ``|Δ_胜| / |Δ_负|``。平局通道不参与。

    这是护栏 2 的 1.35 口径，**不是**矩阵模块的 ``rank1_rank2_ratio``
    （Top-8 前两名赔率比）。两者独立并存、分别命名，不得互相顶替。

    分母 ``|Δ_负|`` 为 0（或非正值）时抛 ``GuardrailInputError`` ——
    不返回 0.0，也不用 ``or 0.0`` / ``1e-9`` 之类的兜底把「无定义」伪装成数值。
    """
    values, issue = _positive_channel_deltas(abs_deltas)
    if issue:
        raise GuardrailInputError(str(issue['reason']))
    return values[HOME_CHANNEL_LABEL] / values[AWAY_CHANNEL_LABEL]


def guardrail_2_neighborhood(abs_deltas: Any = None) -> dict[str, Any]:
    """2 倍邻近规则：第二小 / 第一小 < 2.0 → 非单边碾压。

    命中时**只**输出 ``neighborhood_flag = "NON_SINGLE_SIDED"`` 与比值，
    ``direction`` 一个字节都不动，也不产出任何跨方向集合。
    """
    result: dict[str, Any] = {
        'guardrail': 'NEIGHBORHOOD_2X',
        'status': 'skipped',
        'triggered': False,
        'neighborhood_flag': '',
        'neighborhood_ratio': None,
        'threshold': NEIGHBORHOOD_RATIO_THRESHOLD,
        'first_min': None,
        'second_min': None,
        'direction_modified': False,
        'risk_note': '',
        'missing': [],
        'invalid': [],
    }
    values, issue = _positive_channel_deltas(abs_deltas)
    if issue:
        result['reason'] = issue['reason']
        _route(result, issue)
        return result
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    first, second = ordered[0][1], ordered[1][1]
    ratio = second / first
    result.update({
        'status': 'ok',
        'neighborhood_ratio': round(ratio, 6),
        'first_min': round(first, 6),
        'second_min': round(second, 6),
    })
    if ratio < NEIGHBORHOOD_RATIO_THRESHOLD:
        note = (
            f'胜平负三向均差第二小/第一小={ratio:.4f} < {NEIGHBORHOOD_RATIO_THRESHOLD}，'
            '非单边碾压，尾部冷门风险上升'
        )
        result.update({
            'triggered': True,
            'neighborhood_flag': 'NON_SINGLE_SIDED',
            'risk_note': note,
        })
    return result


def guardrail_2_even_match(abs_deltas: Any = None, league_name: str | None = None) -> dict[str, Any]:
    """1.35 均势熔断：``|Δ_胜| / |Δ_负| <= 1.35`` → 均势盘。

    命中时**只**输出 ``fusion_flag = "EVEN_MATCH"``、比值与一句 ``draw_note``；
    绝不把 ``D`` 追加进任何方向集合，``direction`` 不变。

    1.80 的联赛放宽分支不接电：``is_high_entropy_league`` 恒返回
    ``(False, "unavailable")``，结果里如实给出
    ``high_entropy_relax_applied=False`` 与 ``HIGH_ENTROPY_RELAX_REASON``。
    """
    result: dict[str, Any] = {
        'guardrail': 'EVEN_MATCH_FUSION',
        'status': 'skipped',
        'triggered': False,
        'fusion_flag': '',
        'home_away_abs_ratio': None,
        'threshold': EVEN_MATCH_ABS_RATIO_THRESHOLD,
        'threshold_used': EVEN_MATCH_ABS_RATIO_THRESHOLD,
        'high_entropy_relax_applied': False,
        'high_entropy_relax_reason': HIGH_ENTROPY_RELAX_REASON,
        'high_entropy_league': False,
        'high_entropy_league_source': LEAGUE_PRESSURE_SOURCE_UNAVAILABLE,
        'direction_modified': False,
        'draw_note': '',
        'missing': [],
        'invalid': [],
    }
    high_entropy, high_entropy_source = is_high_entropy_league(league_name)
    result['high_entropy_league'] = high_entropy
    result['high_entropy_league_source'] = high_entropy_source
    if high_entropy:
        # 1.80 分支只在词表可靠时才会走到这里；当前不可能发生。
        result['threshold_used'] = HIGH_ENTROPY_ABS_RATIO_THRESHOLD
        result['high_entropy_relax_applied'] = True
        result['high_entropy_relax_reason'] = 'applied'
    try:
        ratio = home_away_abs_ratio(abs_deltas)
    except GuardrailInputError as exc:
        result['reason'] = str(exc)
        _, issue = _positive_channel_deltas(abs_deltas)
        if issue:
            _route(result, issue)
        else:
            result['invalid'].append({'field': 'abs_deltas', 'reason': str(exc)})
        return result
    result.update({
        'status': 'ok',
        'home_away_abs_ratio': round(ratio, 6),
        'threshold_used': result['threshold_used'],
    })
    if ratio <= result['threshold_used']:
        result.update({
            'triggered': True,
            'fusion_flag': 'EVEN_MATCH',
            'draw_note': (
                f'主客通道均差比值 {ratio:.4f} <= {result["threshold_used"]}，属均势盘'
            ),
        })
    return result


# --------------------------------------------------------------------------- #
# 护栏 3：诱多蜜罐
# --------------------------------------------------------------------------- #
def guardrail_3_inducing_trap(
    heat_ranking: Any = None,
    score_deltas: Any = None,
) -> dict[str, Any]:
    """诱多蜜罐：某方向散户热度 >= 4 席，且其中低赔常见比分 Δ > +0.50。

    **「席位」的定义（写死，不允许二次解释）**：某方向在热度榜单**前
    ``HEAT_SEAT_WINDOW`` (=8) 行**中出现的**比分个数**。不是排名加和、
    不是赔率权重、也不是行数按方向加权。

    **「中低赔常见比分」的定义**：落在同一个前 8 行窗口内的**非聚合**比分。
    依据是引擎的热榜由 ``heat = 1/odds`` 降序排（``build_score_market_heat_ranking``
    就是这么排的），所以前 8 行天然就是赔率最低的那批（中低赔）；
    ``胜其它 / 平其它 / 负其它`` 属于聚合桶，不算「常见比分」，不参与蜜罐判定。

    席位缺失（榜单为空 / 行里没有可解析的 ``score``）时护栏**不生效**并写入
    ``missing``，**不按 0 席处理** —— 0 席会让护栏静默「通过」，看起来像查过。
    """
    result: dict[str, Any] = {
        'guardrail': 'INDUCING_TRAP',
        'status': 'skipped',
        'triggered': False,
        'flag': '',
        'seat_window': HEAT_SEAT_WINDOW,
        'seat_threshold': HEAT_SEAT_THRESHOLD,
        'delta_threshold': TRAP_DELTA_THRESHOLD,
        'weight_factor': TRAP_WEIGHT_FACTOR,
        'seats': {},
        'hot_directions': [],
        'trap_scores': [],
        'weight_multipliers': {},
        'first_pick_eligible': {},
        'missing': [],
        'invalid': [],
    }
    rows = heat_ranking if isinstance(heat_ranking, (list, tuple)) else []
    if not rows:
        reason = 'heat_ranking 为空或缺失：护栏不生效（不按 0 席处理）'
        result['reason'] = reason
        result['missing'].append({'field': 'heat_ranking', 'reason': reason})
        return result
    window = rows[:HEAT_SEAT_WINDOW]
    seats: dict[str, int] = {'H': 0, 'D': 0, 'A': 0}
    seat_scores: dict[str, list[str]] = {'H': [], 'D': [], 'A': []}
    parsed_rows = 0
    for item in window:
        if isinstance(item, dict):
            raw_score = item.get('score') or item.get('name') or ''
        elif isinstance(item, (list, tuple)) and item:
            raw_score = item[0]
        else:
            raw_score = ''
        score = _cs_key(raw_score)
        bucket = _score_bucket(score)
        if not score or not bucket:
            continue
        parsed_rows += 1
        direction = DIRECTION_OF_BUCKET[bucket]
        seats[direction] += 1
        seat_scores[direction].append(score)
    if parsed_rows == 0:
        reason = 'heat_ranking 前 8 行没有可解析的 score：护栏不生效（不按 0 席处理）'
        result['reason'] = reason
        result['missing'].append({'field': 'heat_ranking[].score', 'reason': reason})
        return result

    hot = [direction for direction in DIRECTIONS if seats[direction] >= HEAT_SEAT_THRESHOLD]
    result.update({'status': 'ok', 'seats': dict(seats), 'hot_directions': hot})

    # 没有方向达到席位阈值时，逐比分 Δ 根本不参与判定，不必去解析它
    # （否则会在 missing 里报一条与本次结论无关的缺项）。
    if not hot:
        return result
    deltas, missing, invalid = _signed_deltas(score_deltas)
    result['missing'].extend(missing)
    result['invalid'].extend(invalid)
    if not deltas:
        result['reason'] = 'score_deltas 缺失：无法判定蜜罐 Δ'
        return result

    trap_scores: list[str] = []
    multipliers: dict[str, float] = {}
    eligible: dict[str, bool] = {}
    for direction in hot:
        for score in seat_scores[direction]:
            if _is_aggregate(score):
                continue
            delta = deltas.get(score)
            if delta is None or delta <= TRAP_DELTA_THRESHOLD:
                continue
            trap_scores.append(score)
            multipliers[score] = TRAP_WEIGHT_FACTOR
            eligible[score] = False
            result.setdefault('trap_detail', []).append({
                'score': score,
                'direction': direction,
                'delta': round(delta, 6),
                'seats_in_direction': seats[direction],
                'reason': f'Δ={delta:.4f} > +{TRAP_DELTA_THRESHOLD} 且该方向 {seats[direction]} 席热度',
            })
    if trap_scores:
        result.update({
            'triggered': True,
            'flag': 'INDUCING_TRAP',
            'trap_scores': trap_scores,
            'weight_multipliers': multipliers,
            'first_pick_eligible': eligible,
        })
    return result


# --------------------------------------------------------------------------- #
# 护栏 4：置信度衰减（只改置信度，不改方向）
# --------------------------------------------------------------------------- #
def guardrail_4_confidence_decay(
    primary_weight: Any = None,
    secondary_weight: Any = None,
    multi_signal_conflict: bool = False,
) -> dict[str, Any]:
    """``ΔW = |W_Top1 − W_Top2|``，``ΔW < 5.0`` 或存在多向信号冲突 → 置信度降到 0.60。

    **量纲**：引擎 ``_score_weight`` 的 ``total`` 是 0–1 尺度，规范的 ``5.0`` 是
    百分点，所以判定前必须 ``delta_w_raw * 100``。直接拿 0–1 的原始值比 5.0
    会恒真（实测有效 payload 全部命中），那样每一场都会被钉到 0.60。

    未命中降级时 ``system_confidence`` 返回 ``None``（调用方保留自己的置信度），
    **不编一个基准值**。
    """
    result: dict[str, Any] = {
        'guardrail': 'CONFIDENCE_DECAY',
        'status': 'skipped',
        'triggered': False,
        'delta_w_raw': None,
        'delta_w_pp': None,
        'threshold_pp': CONFIDENCE_DELTA_W_THRESHOLD_PP,
        'multi_signal_conflict': bool(multi_signal_conflict),
        'system_confidence': None,
        'direction_modified': False,
        'missing': [],
        'invalid': [],
    }
    primary = _finite(primary_weight)
    secondary = _finite(secondary_weight)
    absent = [
        name
        for name, value in (('primary_weight', primary), ('secondary_weight', secondary))
        if value is None
    ]
    if absent:
        result['reason'] = f'权重缺失或非数值: {absent}'
        result['missing'].extend(
            {'field': name, 'reason': '缺失或非有限数值'} for name in absent
        )
        return result
    if primary is None or secondary is None:
        return result
    if primary < 0 or secondary < 0:
        result['status'] = 'invalid'
        result['reason'] = f'权重不得为负: primary={primary!r} secondary={secondary!r}'
        result['invalid'].append({'field': 'weight', 'reason': result['reason']})
        return result
    raw = abs(primary - secondary)
    pp = raw * 100.0
    triggered = pp < CONFIDENCE_DELTA_W_THRESHOLD_PP or bool(multi_signal_conflict)
    result.update({
        'status': 'ok',
        'triggered': triggered,
        'delta_w_raw': round(raw, 6),
        'delta_w_pp': round(pp, 6),
    })
    if triggered:
        result['system_confidence'] = DECAYED_CONFIDENCE
        if pp < CONFIDENCE_DELTA_W_THRESHOLD_PP:
            result['reason'] = (
                f'ΔW={pp:.4f}pp < {CONFIDENCE_DELTA_W_THRESHOLD_PP}pp：首/次权差不足，置信度降级'
            )
        else:
            result['reason'] = '多向信号冲突：置信度降级'
    return result


# --------------------------------------------------------------------------- #
# 阶段 1：MIN_ABS_DIFF_LOCK
# --------------------------------------------------------------------------- #
def stage1_min_abs_diff_lock(
    abs_deltas: Any = None,
    direction: str | None = None,
    macro_locked_direction: str | None = None,
) -> dict[str, Any]:
    """阶段 1：取 |Δ| 最小通道为基准方向，叠加 2 倍邻近与 1.35 均势。

    方向优先级：宏观否决（护栏 1）> 调用方已定方向 > 本阶段的 |Δ| 最小通道。
    ``|Δ| 最小`` 只是**兜底方向来源**（引擎里叫 ``absolute_fallback``），
    它绝不覆盖前两级；也绝不因为邻近/均势命中就把方向换成「双选」。
    """
    result: dict[str, Any] = {
        'stage': 'MIN_ABS_DIFF_LOCK',
        'status': 'skipped',
        'min_abs_channel': '',
        'min_abs_delta': None,
        'baseline_direction': '',
        'direction_source': '',
        'applied_macro_lock': False,
        'neighborhood': {},
        'even_match': {},
        'missing': [],
        'invalid': [],
    }
    values, reason = _positive_channel_deltas(abs_deltas)
    if reason:
        result['reason'] = reason
        result['missing'].append({'field': 'abs_deltas', 'reason': reason})
        return result
    label_to_direction = {
        HOME_CHANNEL_LABEL: 'H',
        DRAW_CHANNEL_LABEL: 'D',
        AWAY_CHANNEL_LABEL: 'A',
    }
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    min_label, min_value = ordered[0]
    result.update({
        'status': 'ok',
        'min_abs_channel': min_label,
        'min_abs_delta': round(min_value, 6),
    })
    macro = str(macro_locked_direction or '')
    supplied = str(direction or '')
    if macro in DIRECTIONS:
        baseline, source = macro, 'macro_tier_dominance'
        result['applied_macro_lock'] = True
    elif supplied in DIRECTIONS:
        baseline, source = supplied, 'upstream'
    else:
        baseline, source = label_to_direction[min_label], 'absolute_fallback'
    result['baseline_direction'] = baseline
    result['direction_source'] = source
    return result


# --------------------------------------------------------------------------- #
# 阶段 2：TRUE PROTECTION + Overkill Protocol
# --------------------------------------------------------------------------- #
def stage2_true_protection(
    score_deltas: Any = None,
    direction: str | None = None,
    league_name: str | None = None,
) -> dict[str, Any]:
    """阶段 2：核心低赔/零封比分 Δ < −0.50 → 乘子 ``2.50 + w_league_pressure``。

    ``w_league_pressure`` 没有系统内来源，``resolve_league_pressure`` 恒给出
    ``(0.0, "unavailable")``；结果里必须带 ``league_pressure_source``，
    让调用方看得见这是缺省值而不是真算出来的 0。

    五个核心比分跨三个方向（1-0/2-0 主胜、0-1/0-2 客胜、1-1 平局）。乘子本身
    与方向无关，但本模块只把**与锁定方向一致**的比分放进 ``protection_candidates``；
    跨方向的命中单独列在 ``protection_rejected``（原因 ``direction_mismatch``），
    信息不丢，但绝不会拼出一个跨方向的候选池。
    """
    result: dict[str, Any] = {
        'stage': 'TRUE_PROTECTION',
        'status': 'skipped',
        'base_multiplier': PROTECTION_MULTIPLIER_BASE,
        'w_league_pressure': 0.0,
        'league_pressure_source': LEAGUE_PRESSURE_SOURCE_UNAVAILABLE,
        'protection_multiplier': PROTECTION_MULTIPLIER_BASE,
        'delta_threshold': PROTECTION_DELTA_THRESHOLD,
        'protection_candidates': [],
        'protection_rejected': [],
        'multipliers': {},
        'missing': [],
        'invalid': [],
    }
    deltas, missing, invalid = _signed_deltas(score_deltas)
    result['missing'].extend(missing)
    result['invalid'].extend(invalid)
    if not deltas:
        result['reason'] = 'score_deltas 缺失：TRUE PROTECTION 不生效'
        return result
    weight, source = resolve_league_pressure(league_name)
    multiplier = PROTECTION_MULTIPLIER_BASE + weight
    result.update({
        'status': 'ok',
        'w_league_pressure': weight,
        'league_pressure_source': source,
        'protection_multiplier': round(multiplier, 6),
    })
    locked = str(direction or '')
    for score in PROTECTION_CORE_SCORES:
        delta = deltas.get(score)
        if delta is None:
            result['missing'].append({
                'field': f'score_deltas.{score}',
                'reason': '核心保护比分缺 Δ，不按 0 处理',
            })
            continue
        if delta >= PROTECTION_DELTA_THRESHOLD:
            continue
        bucket = _score_bucket(score)
        aligned = bool(locked) and BUCKET_OF_DIRECTION.get(locked, '') == bucket
        result['multipliers'][score] = round(multiplier, 6)
        entry = {
            'score': score,
            'delta': round(delta, 6),
            'multiplier': round(multiplier, 6),
            'bucket': bucket,
            'direction_aligned': aligned,
        }
        if aligned:
            result['protection_candidates'].append(entry)
        else:
            entry['reason'] = 'direction_mismatch'
            result['protection_rejected'].append(entry)
    return result


def overkill_protocol(score_deltas: Any = None, direction: str | None = None) -> dict[str, Any]:
    """Overkill Protocol：胜其它/负其它 Δ >= +20.0 的尾部风险保护。

    只在**该比分方向与已锁定 direction 一致**时激活候选池；
    方向不一致时只记录（``overkill_rejected``，原因 ``direction_mismatch``），
    绝不逆转 ``direction``。
    """
    result: dict[str, Any] = {
        'protocol': 'OVERKILL',
        'status': 'skipped',
        'overkill_activated': False,
        'overkill_candidates': [],
        'overkill_rejected': [],
        'delta_threshold': OVERKILL_DELTA_THRESHOLD,
        'missing': [],
        'invalid': [],
    }
    deltas, missing, invalid = _signed_deltas(score_deltas)
    result['missing'].extend(missing)
    result['invalid'].extend(invalid)
    if not deltas:
        result['reason'] = 'score_deltas 缺失：Overkill 不生效'
        return result
    locked = str(direction or '')
    result['status'] = 'ok'
    for score in OVERKILL_SCORES:
        delta = deltas.get(score)
        if delta is None:
            continue
        if delta < OVERKILL_DELTA_THRESHOLD:
            continue
        bucket = _score_bucket(score)
        aligned = bool(locked) and BUCKET_OF_DIRECTION.get(locked, '') == bucket
        entry = {
            'score': score,
            'delta': round(delta, 6),
            'bucket': bucket,
            'direction_aligned': aligned,
        }
        if aligned:
            result['overkill_candidates'].append(entry)
        else:
            entry['reason'] = 'direction_mismatch'
            result['overkill_rejected'].append(entry)
    result['overkill_activated'] = bool(result['overkill_candidates'])
    return result


# --------------------------------------------------------------------------- #
# 方向契约：任何推荐集合里都不许出现第二个胜平负方向
# --------------------------------------------------------------------------- #
def _recommendation_sets(result: dict[str, Any]) -> dict[str, list[Any]]:
    """收集结果里所有「会被下游当成可选比分池」的集合。"""
    protection = result.get('stage2_true_protection') or {}
    overkill = result.get('overkill_protocol') or {}
    return {
        'locked_scores': list(result.get('locked_scores') or []),
        'protection_candidates': list(protection.get('protection_candidates') or []),
        'overkill_candidates': list(overkill.get('overkill_candidates') or []),
    }


def check_single_direction(
    direction: str | None,
    locked_scores: Any = None,
) -> dict[str, Any]:
    """校验「恰 2 条、互不相同、与 ``direction`` 同向」。

    与引擎 ``assert_locked_contract`` 同义，但这里**返回报告**而不是抛错：
    护栏是旁路判定，不应该把主流水线打崩；调用方读 ``ok`` 自己决定怎么处置。
    任何越界都不静默裁剪——原样留在 ``offending`` 里。
    """
    direction_text = str(direction or '')
    report: dict[str, Any] = {
        'ok': False,
        'direction': direction_text,
        'expected_bucket': BUCKET_OF_DIRECTION.get(direction_text, ''),
        'count': 0,
        'scores': [],
        'offending': [],
        'reasons': [],
    }
    items = [item for item in (locked_scores or []) if isinstance(item, dict)]
    report['count'] = len(items)
    report['scores'] = [_cs_key(item.get('score') or '') for item in items]
    if direction_text not in DIRECTIONS:
        report['reasons'].append(f'direction 非法: {direction_text!r}')
        return report
    if len(items) != 2:
        report['reasons'].append(f'locked_scores 必须恰 2 条，实际 {len(items)} 条')
    expected = BUCKET_OF_DIRECTION[direction_text]
    seen: set[str] = set()
    for item in items:
        score = _cs_key(item.get('score') or '')
        if score in seen:
            report['reasons'].append(f'locked_scores 出现重复比分 {score}')
        seen.add(score)
        bucket = _score_bucket(score)
        if bucket != expected:
            report['offending'].append({
                'score': score,
                'bucket': bucket,
                'reason': 'hedge_direction_in_locked' if bucket else 'unparsable_score',
            })
    if report['offending']:
        report['reasons'].append('locked_scores 与 direction 不同向（禁止对冲/跨方向）')
    report['ok'] = not report['reasons']
    return report


# --------------------------------------------------------------------------- #
# 总入口
# --------------------------------------------------------------------------- #
def evaluate_market_guardrails(payload: Any = None) -> dict[str, Any]:
    """跑四道护栏 + 两个已定义阶段，返回一份完整判定。

    ``payload`` 支持的键（缺什么报什么，绝不补默认值）：

    ``direction``         调用方（引擎 Step1）已定的方向 ``H/D/A``
    ``locked_scores``     引擎 Step6 的锁定比分（本模块只回显 + 校验，不改写）
    ``tier_diff``         宏观档位差；只能显式传入（C6）
    ``abs_deltas``        三向 |Δ| 均值 ``{胜,平,负}``，赔率量纲
    ``score_deltas``      逐比分有符号 Δ ``{比分: Δ}``，赔率量纲
    ``heat_ranking``      热度榜（按 heat 降序，前 8 行即中低赔窗口）
    ``primary_weight``    Step6 首选的 ``total``（0–1 尺度）
    ``secondary_weight``  Step6 次选的 ``total``（0–1 尺度）
    ``multi_signal_conflict`` 多向信号是否冲突（bool）
    ``league_name``       联赛名（自由文本，仅用于如实报「不可用」）

    返回结果里 ``direction`` 只可能被护栏 1 改写（宪法级否决），
    护栏 2/3/4 对方向的影响恒为 0，并分别用 ``direction_modified=False`` 自证。
    """
    data = dict(payload) if isinstance(payload, dict) else {}
    result: dict[str, Any] = {
        'schema': 'blade.market_guardrails.v1',
        'status': 'ok',
        'direction': '',
        'direction_before': '',
        'direction_after': '',
        'direction_source': '',
        'direction_changed_by': '',
        'locked_scores': [],
        'locked_contract': {},
        'self_check': {'ok': True, 'violations': [], 'checked_notes': 0},
        'risk_flags': [],
        'risk_notes': [],
        'high_entropy_relax_applied': False,
        'high_entropy_relax_reason': HIGH_ENTROPY_RELAX_REASON,
        'league_pressure_source': LEAGUE_PRESSURE_SOURCE_UNAVAILABLE,
        'overkill_activated': False,
        'overkill_candidates': [],
        'overkill_rejected': [],
        'missing': [],
        'invalid': [],
        'guardrail_1_macro_dominance': {},
        'guardrail_2_neighborhood': {},
        'guardrail_2_even_match': {},
        'guardrail_3_inducing_trap': {},
        'guardrail_4_confidence_decay': {},
        'stage1_min_abs_diff_lock': {},
        'stage2_true_protection': {},
        'overkill_protocol': {},
    }

    direction_in = str(data.get('direction') or '')
    result['direction_before'] = direction_in

    macro = guardrail_1_macro_dominance(data.get('tier_diff'))
    neighborhood = guardrail_2_neighborhood(data.get('abs_deltas'))
    even_match = guardrail_2_even_match(data.get('abs_deltas'), data.get('league_name'))
    trap = guardrail_3_inducing_trap(data.get('heat_ranking'), data.get('score_deltas'))
    decay = guardrail_4_confidence_decay(
        data.get('primary_weight'),
        data.get('secondary_weight'),
        bool(data.get('multi_signal_conflict')),
    )

    direction = direction_in
    direction_source = 'upstream'
    if macro.get('applies'):
        direction = str(macro.get('locked_direction') or '')
        direction_source = 'macro_tier_dominance'
        result['direction_changed_by'] = 'guardrail_1_macro_dominance'
        result['risk_flags'].append('TIER_DOMINANCE_VETO')

    stage1 = stage1_min_abs_diff_lock(
        data.get('abs_deltas'),
        direction=direction,
        macro_locked_direction=macro.get('locked_direction') if macro.get('applies') else '',
    )
    if not direction and stage1.get('baseline_direction'):
        direction = str(stage1['baseline_direction'])
        direction_source = str(stage1.get('direction_source') or '')

    stage2 = stage2_true_protection(
        data.get('score_deltas'),
        direction=direction,
        league_name=data.get('league_name'),
    )
    overkill = overkill_protocol(data.get('score_deltas'), direction=direction)

    result['direction'] = direction
    result['direction_after'] = direction
    result['direction_source'] = direction_source
    result['locked_scores'] = [
        dict(item) for item in (data.get('locked_scores') or []) if isinstance(item, dict)
    ]
    result['guardrail_1_macro_dominance'] = macro
    result['guardrail_2_neighborhood'] = neighborhood
    result['guardrail_2_even_match'] = even_match
    result['guardrail_3_inducing_trap'] = trap
    result['guardrail_4_confidence_decay'] = decay
    result['stage1_min_abs_diff_lock'] = stage1
    result['stage2_true_protection'] = stage2
    result['overkill_protocol'] = overkill
    result['league_pressure_source'] = stage2.get('league_pressure_source', LEAGUE_PRESSURE_SOURCE_UNAVAILABLE)
    result['overkill_activated'] = bool(overkill.get('overkill_activated'))
    result['overkill_candidates'] = list(overkill.get('overkill_candidates') or [])
    result['overkill_rejected'] = list(overkill.get('overkill_rejected') or [])

    if neighborhood.get('triggered'):
        result['risk_flags'].append('NON_SINGLE_SIDED')
        result['risk_notes'].append(str(neighborhood.get('risk_note') or ''))
    if even_match.get('triggered'):
        result['risk_flags'].append('EVEN_MATCH')
        result['risk_notes'].append(str(even_match.get('draw_note') or ''))
    if trap.get('triggered'):
        result['risk_flags'].append('INDUCING_TRAP')
        # 面向用户的文案里不得出现 PAR-9 的五个词（首选/次选/双选/强推/推荐），
        # 渲染侧还有一道独立扫描。这里用「优先资格」表达同一件事，不靠否定式豁免兜。
        result['risk_notes'].append(TRAP_RISK_NOTE)
    if decay.get('triggered'):
        result['risk_flags'].append('LOW_CONFIDENCE')
    if result['overkill_activated']:
        result['risk_flags'].append('OVERKILL_TAIL_PROTECTION')

    for block in (macro, neighborhood, even_match, trap, decay, stage1, stage2, overkill):
        for entry in block.get('missing') or []:
            result['missing'].append({'guardrail': block.get('guardrail') or block.get('stage') or block.get('protocol'), **entry})
        for entry in block.get('invalid') or []:
            result['invalid'].append({'guardrail': block.get('guardrail') or block.get('stage') or block.get('protocol'), **entry})
    if result['missing']:
        result['status'] = 'degraded'

    contract = check_single_direction(direction, result['locked_scores'])
    result['locked_contract'] = contract
    # locked_scores 为空不是违规：本模块只回显并校验上游 Step6 的锁定，
    # 没有任何待校验集合时标 not_applicable，而不是给一个看起来像违规的 ok=false。
    if not result['locked_scores']:
        result['locked_contract']['status'] = 'not_applicable'
        result['locked_contract']['ok'] = None
        result['locked_contract']['reasons'] = []
    elif not contract['ok']:
        result['locked_contract']['status'] = 'violated'
        result['status'] = 'invalid'
        result['invalid'].append({'guardrail': 'SINGLE_DIRECTION_CONTRACT', 'reason': '; '.join(contract['reasons'])})
    else:
        result['locked_contract']['status'] = 'ok'

    # 输出语言自检：**不**并入 status。status 只反映输入是否可用、判定是否成立；
    # 一句措辞不合规不是「结论不可用」。违规走独立的 self_check 字段。
    apply_language_self_check(result)
    return result


def apply_language_self_check(result: dict[str, Any]) -> dict[str, Any]:
    """把 ``risk_notes`` 过一遍 PAR-9 禁用词扫描，结果写进 ``result['self_check']``。

    刻意**不碰** ``result['status']``：一句措辞不合规说明文案要改，不说明这次判定
    的结论不可用。status 的三种取值（ok / degraded / invalid）只由输入缺项、
    数据无效、方向契约违约决定。

    单独成函数是为了让「自检失败会不会翻 status」这件事可以被直接断言。
    """
    self_check = result.get('self_check')
    if not isinstance(self_check, dict):
        self_check = {'ok': True, 'violations': [], 'checked_notes': 0}
        result['self_check'] = self_check
    for note in result.get('risk_notes') or []:
        violations = pick_language_violations(note)
        if violations:
            self_check['ok'] = False
            self_check['violations'].append({
                'guardrail': 'PICK_LANGUAGE',
                'reason': f'风险陈述含选号指引: {note}',
                'matched': violations,
            })
    self_check['checked_notes'] = len(result.get('risk_notes') or [])
    return result
