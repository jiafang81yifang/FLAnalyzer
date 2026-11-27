"""六步比分收敛引擎（v1.0 规范 + 矩阵/宏观/全方向预判附加契约）。

纯 Python：不依赖任何 UI 框架或 DataFrame 库，返回纯 dict / list，
渲染交给 UI 与报告层。被 pytest 直接 import，也被
``scripts/build_single_file.py`` 内嵌进单文件构建。

链路（铁律 1，单向因果，禁止对冲）：
    1X2 定向 -> 大小球(O/U) 路径 -> 31 项完备树过滤 -> 市热/市短与盘口联动
    -> 六盘口互洽 -> 锁定 2 个比分

铁律 2（零兜底）：每个输入先做存在性与有效性检查；缺失写 `missing`、越界写
`invalid`，依赖它的步骤 `status="aborted"` 并带原因，下游不产出 locked_scores。
本模块内不做任何静默默认值填充：没有兜底赔率表、没有兜底盘口线、
没有「猜一个平局比分」的常量。

铁律 3（时间边界）：常规时间 + 补时；加时/点球即使存在也被排除并计数。

三段历史代码（矩阵配额 / 宏观裁决 / 全方向预判）已并入本模块，各自只作为
某个步骤的输入或校验，不另建第二套比分空间，也不产生第二个入口。
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------- #
# 常量（铁律 3：口径集中在模块顶部）
# --------------------------------------------------------------------------- #
REGULAR_TIME_MINUTES = 90
STOPPAGE_MINUTES_MAX = 15
REGULAR_TIME_ONLY = True

# 竞彩 31 项：主胜 13 + 平局 5 + 客胜 13（与 blade_deepseek_pipeline 同一张表）。
JCZQ_HOME_WIN_SCORES = [
    '1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2', '5-0', '5-1', '5-2',
]
JCZQ_DRAW_SCORES = ['0-0', '1-1', '2-2', '3-3']
JCZQ_AWAY_WIN_SCORES = [
    '0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4', '0-5', '1-5', '2-5',
]
JCZQ_OTHER_OPTIONS = ['胜其它', '平其它', '负其它']
JCZQ_ALL_OPTIONS = JCZQ_HOME_WIN_SCORES + JCZQ_DRAW_SCORES + JCZQ_AWAY_WIN_SCORES + JCZQ_OTHER_OPTIONS

# 聚合桶的两种写法都存在于项目里（平其它 / 平其他），必须同时识别。
AGGREGATE_ALIASES = {
    '胜其它': 'home', '胜其他': 'home',
    '平其它': 'draw', '平其他': 'draw',
    '负其它': 'away', '负其他': 'away',
}

DIRECTIONS = ('H', 'D', 'A')
DIRECTION_SOURCES = ('directive', 'model_market', 'pairwise', 'absolute_fallback')
_CN_TO_DIRECTION = {'胜': 'H', '平': 'D', '负': 'A'}
_DIRECTION_TO_CN = {'H': '胜', 'D': '平', 'A': '负'}
_DIRECTION_CN = {'H': '主胜', 'D': '平局', 'A': '客胜'}
_BUCKET_OF_DIRECTION = {'H': 'home', 'D': 'draw', 'A': 'away'}
_DIRECTION_OF_BUCKET = {'home': 'H', 'draw': 'D', 'away': 'A'}
_OU_CN = {'OVER': '大球', 'UNDER': '小球'}

# 盘口枚举：唯一的一份中英文别名映射。
HANDICAP_SIDE_ALIASES = {
    'home': 'home', '主让': 'home', '让胜': 'home', '主': 'home', 'h': 'home',
    'away': 'away', '客让': 'away', '受让': 'away', '客': 'away', 'a': 'away',
    'none': 'none', 'level': 'none', '平手': 'none', 'n': 'none',
}
HANDICAP_SIDES = ('home', 'away', 'none')

# Step5 六个维度（顺序即报告顺序）。
CONSISTENCY_DIMENSIONS = (
    ('euro_1x2', '欧赔'),
    ('asian', '亚盘'),
    ('ou', '大小球'),
    ('half_1x2', '半场胜平负'),
    ('half_ou', '半场大小球'),
    ('tempo', '节奏与盘口演变'),
)
DIMENSION_WEIGHTS = {
    'euro_1x2': 20.0,
    'asian': 20.0,
    'ou': 20.0,
    'half_1x2': 15.0,
    'half_ou': 15.0,
    'tempo': 10.0,
}

# Step6 综合权重分解键（顺序固定）。前五项是加法项；`veto_scale` 是乘法修正
# （Step4 降权 / 宏观 conf_mod / 熔断 / 审计因子）的加法补项，
# 保证「各项之和 == 总分」恒成立（含熔断）。
WEIGHT_COMPONENTS = (
    'theory', 'market_prob', 'heat', 'short', 'linkage', 'veto_scale',
)

# 两两投票（Step1 第三档）的两个具名阈值：比值 2 倍为「显著更近」，
# 绝对差 1.0 个赔率均差以内视为并列。量纲 = 赔率均差（越小越贴近）。
PAIRWISE_RATIO_THRESHOLD = 2.0
PAIRWISE_ABS_GAP = 1.0
# 双方差值都小于此下界时视为「无信息」，判为无结论（None），绝不判平。
PAIRWISE_MIN_INFORMATIVE_DELTA = 0.01

# Step4：最低赔落在平局通道时的降权系数（clamp 到 [0.5, 1.0]）。
LOWEST_ODDS_DRAW_DOWNWEIGHT = 0.85
LOWEST_ODDS_DRAW_DOWNWEIGHT_MIN = 0.5
LOWEST_ODDS_DRAW_DOWNWEIGHT_MAX = 1.0

# C 段宏观裁决枚举（统一大写蛇形）。
VETO_NONE = 'NONE'
VETO_HARD_BLOCK = 'HARD_BLOCK'
VETO_STRONG_FADE = 'STRONG_FADE'
VETO_SOFT_FADE = 'SOFT_FADE'
VETO_BOOST = 'BOOST'
_VETO_WEIGHT_FACTOR = {
    VETO_NONE: 1.0,
    VETO_HARD_BLOCK: 0.0,
    VETO_STRONG_FADE: 0.5,
    VETO_SOFT_FADE: 0.75,
    VETO_BOOST: 1.25,
}

# 滚球事件类型（主规范 §3）。
LIVE_EVENT_TYPES = ('goal', 'red_card', 'penalty', 'var')

_ERROR_TEXT = {
    'step1_missing_inputs': 'Step1 中止：四档方向判定全部无结论',
    'step2_missing_inputs': 'Step2 中止：大小球路径所需输入缺失',
    'step3_missing_inputs': 'Step3 中止：31 项完备树所需输入缺失或非法',
    'step4_missing_inputs': 'Step4 中止：联动与市热/市短所需输入缺失',
    'step5_missing_inputs': 'Step5 中止：六盘口维度输入缺失',
    'step6_missing_inputs': 'Step6 中止：候选子集不足 2 个比分',
    'upstream_aborted': '上游步骤中止，本步不产出',
    'step3_direction_conflict': 'Step3 自洽断言失败：候选比分与 Step1/Step2 结论冲突',
    'invalid_handicap_side': '盘口方向取值未识别',
    'invalid_goal_expect': '进球预期取值未识别',
    'invalid_handicap_line': '让球盘口线无法归一化到 0.25 刻度',
    'pairwise_tie': '两两投票并列，不构成平局证据',
}


class ConvergenceContractError(ValueError):
    """铁律 1 的自洽断言失败：内部错误码，不静默裁剪后当成功。"""

    def __init__(self, code: str, detail: Any = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f'{code}: {detail!r}')


# --------------------------------------------------------------------------- #
# 基础工具：数字判定（零兜底：只判定，不填数）
# --------------------------------------------------------------------------- #
def _finite(value: Any) -> float | None:
    """有限实数则返回 float，否则 None。绝不用默认值顶替。"""
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _cs_key(score: Any) -> str:
    """比分键归一：优先复用 okooo_mobile_cs.normalize_cs_key（冒号→横杠）。"""
    try:
        from okooo_mobile_cs import normalize_cs_key as _normalize
    except Exception:  # noqa: BLE001 - 纯字符串归一的等价实现，模块缺席不影响语义
        return str(score or '').replace('：', '-').replace(':', '-').replace(' ', '').strip()
    return _normalize(score)


def _parse_score(score: Any) -> tuple[int, int] | None:
    text = _cs_key(score)
    match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _score_bucket(score: Any) -> str:
    """比分 -> 'home' / 'draw' / 'away'。

    结构性判定：`h == a` 即平局；聚合桶按别名表（两种写法都认）。
    不依赖任何 5 字面量名单，也不默认平。
    """
    text = _cs_key(score)
    alias = AGGREGATE_ALIASES.get(text)
    if alias:
        return alias
    parsed = _parse_score(text)
    if parsed is None:
        return ''
    home_goals, away_goals = parsed
    if home_goals > away_goals:
        return 'home'
    if home_goals == away_goals:
        return 'draw'
    return 'away'


def _is_draw_score(score: Any) -> bool:
    return _score_bucket(score) == 'draw'


def _score_total_goals(score: Any) -> int | None:
    parsed = _parse_score(score)
    if parsed is None:
        return None
    return parsed[0] + parsed[1]


def _is_other_bucket(score: Any) -> bool:
    return _cs_key(score) in AGGREGATE_ALIASES


# 聚合桶的最小总进球（结构性推导，不是猜）：
#   胜其它/负其它 = 31 项之外的同向比分，最小的是 4-3 / 3-4 → 7 球；
#   平其它         = 31 项之外的平局，最小的是 4-4        → 8 球。
# 因此聚合桶在任何现实盘口线（< 6）下都只可能是 OVER。
AGGREGATE_MIN_TOTAL_GOALS = {'home': 7, 'draw': 8, 'away': 7}


def normalized_entropy(probabilities: Any) -> float | None:
    """归一化香农熵（0..1）。用于复盘口径的置信度参考。"""
    if not isinstance(probabilities, dict) or not probabilities:
        return None
    values = [v for v in (_finite(x) for x in probabilities.values()) if v is not None and v > 0]
    if not values:
        return None
    total = sum(values)
    if total <= 0:
        return None
    shares = [v / total for v in values]
    entropy = -sum(p * math.log(p) for p in shares if p > 0)
    denominator = math.log(len(shares)) if len(shares) > 1 else 0.0
    if denominator <= 0:
        return 0.0
    return entropy / denominator


# --------------------------------------------------------------------------- #
# 盘口线归一化
# --------------------------------------------------------------------------- #
def normalize_handicap_line(value: Any) -> dict[str, Any]:
    """把各种盘口线写法统一到 quarter-ball（0.25 的整数倍），并保留符号。

    支持 ``0.75`` / ``-0.75`` / ``'0/0.5'`` / ``'0.5/1'`` 等；无法解析、
    非 0.25 倍数、NaN 一律 ``valid=False``，不做"就近取整"。
    """
    raw: float | None = None
    if isinstance(value, str):
        text = value.strip().replace('−', '-')
        if not text:
            return {'valid': False, 'raw': value, 'reason': '盘口线为空'}
        parts = [p for p in re.split(r'[/／]', text) if p.strip()]
        if len(parts) == 2:
            left, right = _finite(parts[0]), _finite(parts[1])
            if left is None or right is None:
                return {'valid': False, 'raw': value, 'reason': f'盘口线双段无法解析: {value!r}'}
            raw = (left + right) / 2.0
        else:
            raw = _finite(text)
    else:
        raw = _finite(value)
    if raw is None:
        return {'valid': False, 'raw': value, 'reason': f'盘口线非数值: {value!r}'}
    quarters = raw / 0.25
    nearest = round(quarters)
    if abs(quarters - nearest) > 1e-9:
        return {'valid': False, 'raw': value, 'reason': f'盘口线不是 0.25 倍数: {raw!r}'}
    line = round(nearest * 0.25, 2)
    if line < 0:
        side = 'home_gives'
    elif line > 0:
        side = 'away_gives'
    else:
        side = 'level'
    return {
        'valid': True,
        'raw': value,
        'line': line,
        'quarters': int(nearest),
        'side': side,
        'reason': '',
    }


def resolve_handicap_side(value: Any) -> str:
    """把盘口方向别名解析成 'home' / 'away' / 'none'；未识别抛 ValueError。"""
    text = str(value or '').strip().lower()
    side = HANDICAP_SIDE_ALIASES.get(text)
    if side is None:
        raise ValueError(
            f'未识别的盘口方向 {value!r}；接受取值: {sorted(HANDICAP_SIDE_ALIASES)}'
        )
    return side


# --------------------------------------------------------------------------- #
# 结构化审计日志
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


class AuditLog:
    """结构化审计日志：{'ts', 'step', 'code', 'detail'}。"""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(self, step: str, code: str, detail: Any = None) -> None:
        self.records.append({
            'ts': _now_iso(),
            'step': step,
            'code': code,
            'detail': detail if detail is not None else {},
        })

    def to_list(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.records]


# --------------------------------------------------------------------------- #
# Step 1：1X2 定向（唯一方向来源；四档顺序见附加契约二 §2）
# --------------------------------------------------------------------------- #
def _de_vig(odds: Any) -> dict[str, Any]:
    """三项欧赔去水。任一赔率 ≤1.0 或缺失即 invalid，不做修正。"""
    result: dict[str, Any] = {'valid': False, 'probs': {}, 'overround': None, 'missing': []}
    if not isinstance(odds, dict) or not odds:
        result['missing'] = ['odds_1x2']
        return result
    picks: dict[str, float] = {}
    for key in ('home', 'draw', 'away'):
        value = None
        for candidate in (key, key.capitalize(), key.upper()):
            if odds.get(candidate) is not None:
                value = _finite(odds.get(candidate))
                break
        if value is None:
            result['missing'].append(f'odds_1x2.{key}')
            continue
        if value <= 1.0:
            result['invalid_reason'] = f'{key} 赔率 ≤1.0: {value!r}'
            result['missing'].append(f'odds_1x2.{key}')
            continue
        picks[key] = value
    if result['missing']:
        return result
    inverse = {k: 1.0 / v for k, v in picks.items()}
    overround = sum(inverse.values())
    if overround <= 0:
        result['invalid_reason'] = '去水前概率和 ≤0'
        return result
    result.update({
        'valid': True,
        'overround': round(overround, 6),
        'probs': {k: v / overround for k, v in inverse.items()},
        'odds': picks,
    })
    return result


def _model_wdl_probs(lam_home: Any, lam_away: Any) -> dict[str, Any]:
    """独立泊松（λ 冻结）推 1X2；λ 缺失/非正即 invalid（不填默认 λ）。"""
    lh, la = _finite(lam_home), _finite(lam_away)
    missing: list[str] = []
    if lh is None or lh <= 0:
        missing.append('lam_home')
    if la is None or la <= 0:
        missing.append('lam_away')
    if missing or lh is None or la is None:
        return {'valid': False, 'probs': {}, 'missing': missing}
    max_goals = 10
    home = draw = away = 0.0
    for h in range(max_goals + 1):
        ph = math.exp(-lh) * lh ** h / math.factorial(h)
        for a in range(max_goals + 1):
            pa = math.exp(-la) * la ** a / math.factorial(a)
            joint = ph * pa
            if h > a:
                home += joint
            elif h == a:
                draw += joint
            else:
                away += joint
    total = home + draw + away
    if total <= 0:
        return {'valid': False, 'probs': {}, 'missing': [], 'invalid_reason': 'λ 概率矩阵合计 ≤0'}
    return {
        'valid': True,
        'probs': {'home': home / total, 'draw': draw / total, 'away': away / total},
    }


def compare_pair(
    val_a: Any,
    val_b: Any,
    ratio: float = PAIRWISE_RATIO_THRESHOLD,
    abs_gap: float = PAIRWISE_ABS_GAP,
) -> int | None:
    """两两比较：值越小越贴近（赔率均差量纲）。

    返回 1（a 优）/ -1（b 优）/ 0（并列）/ None（无信息）。
    两个阈值都是具名常量：
      * ``PAIRWISE_RATIO_THRESHOLD = 2.0``：一方小于另一方的一半即「显著更近」；
      * ``PAIRWISE_ABS_GAP = 1.0``：绝对差在此以内视为并列。
    单调性：固定 b、a 递增时输出只会是 1 -> 0 -> -1，不回头。
    """
    a, b = _finite(val_a), _finite(val_b)
    if a is None or b is None:
        return None
    if a < 0 or b < 0:
        return None
    if a < PAIRWISE_MIN_INFORMATIVE_DELTA and b < PAIRWISE_MIN_INFORMATIVE_DELTA:
        return None
    if a < b / ratio:
        return 1
    if b < a / ratio:
        return -1
    if abs(a - b) < abs_gap:
        return 0
    return 1 if a < b else -1


def pairwise_vote(abs_deltas: Any) -> dict[str, Any]:
    """三项两两投票（Step1 第三档）：胜/平/负各得一票。

    并列不构成平局证据：无唯一最大时返回 ``direction=None``，
    交给下一档位（附加契约二 §1.3）。
    """
    out: dict[str, Any] = {
        'available': False,
        'scores': {},
        'direction': None,
        'direction_cn': '',
        'reason': '',
        'pairs': [],
    }
    if not isinstance(abs_deltas, dict):
        out['reason'] = 'abs_deltas 缺失'
        return out
    values: dict[str, float] = {}
    for label in ('胜', '平', '负'):
        value = _finite(abs_deltas.get(label))
        if value is None:
            out['reason'] = f'abs_deltas.{label} 缺失或非数值'
            return out
        if value <= 0:
            out['reason'] = f'abs_deltas.{label} 必须 >0（0 与 1e-6 的比值是隐式兜底）: {value!r}'
            return out
        values[label] = value
    scores = {'胜': 0, '平': 0, '负': 0}
    pairs: list[dict[str, Any]] = []
    labels = ('胜', '平', '负')
    undecided = 0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            left, right = labels[i], labels[j]
            verdict = compare_pair(values[left], values[right])
            if verdict is None:
                undecided += 1
                pairs.append({'left': left, 'right': right, 'verdict': None,
                              'detail': '双方差值都低于有效下界，无信息'})
                continue
            if verdict > 0:
                scores[left] += 1
                scores[right] -= 1
            elif verdict < 0:
                scores[right] += 1
                scores[left] -= 1
            pairs.append({
                'left': left, 'right': right, 'verdict': verdict,
                'left_value': values[left], 'right_value': values[right],
                'ratio_bound': round(values[right] / PAIRWISE_RATIO_THRESHOLD, 6),
            })
    best = max(scores.values())
    winners = [k for k, v in scores.items() if v == best]
    out.update({
        'available': True,
        'abs_deltas': {k: round(v, 6) for k, v in values.items()},
        'scores': scores,
        'pairs': pairs,
        'undecided_pairs': undecided,
    })
    if len(winners) != 1:
        out['direction'] = None
        out['reason'] = _ERROR_TEXT['pairwise_tie']
        return out
    out['direction'] = _CN_TO_DIRECTION[winners[0]]
    out['direction_cn'] = winners[0]
    return out


def abs_deltas_from_tree(tree: Any) -> dict[str, Any]:
    """从 31 项矩阵算出每个赛果桶的「赔率均差」（越小越贴近）。

    量纲定义：桶内 |市均% − 理论%| 的算术平均（单位 pp）。理论缺失的项不计入，
    全部缺失的桶 ``available=False`` —— 不补 0，也不编数。
    """
    buckets: dict[str, list[float]] = {'home': [], 'draw': [], 'away': []}
    for row in (tree or []):
        if not isinstance(row, dict):
            continue
        bucket = _score_bucket(row.get('score'))
        if bucket not in buckets:
            continue
        theory = _finite(row.get('theory'))
        market = _finite(row.get('market'))
        if theory is None or market is None:
            continue
        buckets[bucket].append(abs(market - theory))
    deltas: dict[str, float] = {}
    missing: list[str] = []
    for bucket, values in buckets.items():
        label = {'home': '胜', 'draw': '平', 'away': '负'}[bucket]
        if not values:
            missing.append(label)
            continue
        deltas[label] = round(sum(values) / len(values), 6)
    return {
        'available': not missing,
        'abs_deltas': deltas,
        'missing': missing,
        'source': 'mean_abs(market_pct - theory_pct) per bucket over pool31',
    }


def _directive_direction(payload: dict[str, Any]) -> dict[str, Any]:
    """第一档：外部显式指定方向（必须带 override_audit）。"""
    raw = payload.get('direction_directive')
    if raw is None or str(raw).strip() == '':
        return {'hit': False}
    text = str(raw).strip()
    direction = text.upper() if text.upper() in DIRECTIONS else _CN_TO_DIRECTION.get(text, '')
    if not direction:
        return {
            'hit': False,
            'invalid': {
                'field': 'direction_directive',
                'reason': f'未识别的方向指定 {raw!r}；接受 H/D/A 或 胜/平/负',
            },
        }
    return {
        'hit': True,
        'direction': direction,
        'override_audit': {
            'source': str(payload.get('direction_directive_source') or 'payload.direction_directive'),
            'value': raw,
            'acknowledged': True,
        },
    }


def _tree_direction_counts(tree: Any) -> dict[str, int]:
    """按方向统计「有真实市场值」的项数（31 项键恒在，缺值不算候选）。"""
    counts = {'home': 0, 'draw': 0, 'away': 0}
    for row in (tree or []):
        if not isinstance(row, dict):
            continue
        bucket = _score_bucket(row.get('score'))
        market = _finite(row.get('market'))
        if bucket in counts and market is not None and market > 0:
            counts[bucket] += 1
    return counts


def step1_direction(payload: dict[str, Any], log: AuditLog) -> dict[str, Any]:
    """Step 1：四档定方向，输出唯一 H/D/A + ``direction_source``。

    档位顺序（附加契约二 §2）：directive -> model_market -> pairwise ->
    absolute_fallback；全部无唯一结论 -> aborted，绝不默认平。
    """
    result: dict[str, Any] = {
        'step': 'DIRECTION',
        'status': 'ok',
        'reason': '',
        'direction': '',
        'direction_cn': '',
        'direction_source': '',
        'tier': 0,
        'override_audit': None,
        'model_probs': {},
        'market_probs': {},
        'diff': {},
        'confidence': None,
        'pairwise': {},
        'abs_deltas': {},
        'abs_deltas_source': '',
        'conflicts': [],
        'missing': [],
        'invalid': [],
    }

    directive = _directive_direction(payload)
    if directive.get('invalid'):
        result['invalid'].append(directive['invalid'])
    if directive.get('hit'):
        direction = directive['direction']
        counts = _tree_direction_counts(payload.get('pool31_rows'))
        if any(counts.values()):
            bucket = _BUCKET_OF_DIRECTION[direction]
            if counts[bucket] == 0:
                result['conflicts'].append({
                    'code': 'DIRECTIVE_DIRECTION_HAS_NO_CANDIDATE',
                    'detail': f'显式指定 {direction}，但 31 项树该方向 0 个候选',
                })
                result.update({
                    'status': 'aborted',
                    'reason': 'Step1 中止：显式指定方向在 31 项树中无候选',
                    'override_audit': directive['override_audit'],
                })
                log.add('DIRECTION', 'DIRECTIVE_REJECTED', {
                    'direction': direction, 'counts': counts,
                })
                return result
        result.update({
            'direction': direction,
            'direction_cn': _DIRECTION_CN[direction],
            'direction_source': 'directive',
            'tier': 1,
            'override_audit': directive['override_audit'],
        })
        log.add('DIRECTION', 'DIRECTIVE_USED', directive['override_audit'])
        return result

    market = _de_vig(payload.get('odds_1x2'))
    model = _model_wdl_probs(payload.get('lam_home'), payload.get('lam_away'))
    result['market_probs'] = market.get('probs') or {}
    result['model_probs'] = model.get('probs') or {}
    result['market_overround'] = market.get('overround')
    for item in market.get('missing') or []:
        result['missing'].append({'field': item, 'reason': '1X2 赔率缺失或 ≤1.0'})
    if market.get('invalid_reason'):
        result['invalid'].append({'field': 'odds_1x2', 'reason': market['invalid_reason']})
    for item in model.get('missing') or []:
        result['missing'].append({'field': item, 'reason': 'λ 缺失或非正，无法形成模型概率'})
    if model.get('invalid_reason'):
        result['invalid'].append({'field': 'lam', 'reason': model['invalid_reason']})

    declared = payload.get('abs_deltas')
    if isinstance(declared, dict) and declared:
        result['abs_deltas'] = {k: v for k, v in declared.items()}
        result['abs_deltas_source'] = str(payload.get('abs_deltas_source') or 'payload.abs_deltas')
    else:
        tree = payload.get('pool31_rows')
        if isinstance(tree, (list, tuple)) and tree:
            computed = abs_deltas_from_tree(tree)
            result['abs_deltas'] = computed['abs_deltas']
            result['abs_deltas_source'] = computed['source']
        else:
            result['abs_deltas_source'] = ''

    # 第二档：模型概率 + 去水市场隐含概率
    if market.get('valid') and model.get('valid'):
        keys = ('home', 'draw', 'away')
        result['diff'] = {k: round(model['probs'][k] - market['probs'][k], 6) for k in keys}
        blended = {k: 0.5 * model['probs'][k] + 0.5 * market['probs'][k] for k in keys}
        ordered = sorted(keys, key=lambda k: blended[k], reverse=True)
        leader = ordered[0]
        result.update({
            'blended_probs': {k: round(v, 6) for k, v in blended.items()},
            'confidence': round(blended[leader] - blended[ordered[1]], 6),
            'direction': {'home': 'H', 'draw': 'D', 'away': 'A'}[leader],
            'direction_source': 'model_market',
            'tier': 2,
        })
        result['direction_cn'] = _DIRECTION_CN[result['direction']]
        log.add('DIRECTION', 'DIRECTION_LOCKED', {
            'direction': result['direction'], 'source': 'model_market',
            'blended': result['blended_probs'], 'confidence': result['confidence'],
        })
        return result

    # 第三档：两两投票
    vote = pairwise_vote(result['abs_deltas'])
    result['pairwise'] = vote
    if vote.get('available') and vote.get('direction'):
        result.update({
            'direction': vote['direction'],
            'direction_cn': _DIRECTION_CN[vote['direction']],
            'direction_source': 'pairwise',
            'tier': 3,
            'confidence': None,
        })
        log.add('DIRECTION', 'DIRECTION_LOCKED', {
            'direction': result['direction'], 'source': 'pairwise',
            'scores': vote['scores'], 'abs_deltas': vote.get('abs_deltas'),
        })
        return result
    if vote.get('reason'):
        result['conflicts'].append({'code': 'PAIRWISE_NO_UNIQUE_DIRECTION',
                                    'detail': vote['reason']})

    # 第四档：绝对值回退（取最小赔率均差；并列仍无解）
    raw_deltas = result['abs_deltas'] if isinstance(result['abs_deltas'], dict) else {}
    deltas: dict[str, float] = {}
    for label, value in raw_deltas.items():
        number = _finite(value)
        if number is not None and number > 0:
            deltas[str(label)] = number
    if len(deltas) == 3:
        ordered = sorted(deltas.items(), key=lambda kv: kv[1])
        if ordered[0][1] < ordered[1][1]:
            label = ordered[0][0]
            result.update({
                'direction': _CN_TO_DIRECTION[label],
                'direction_cn': _DIRECTION_CN[_CN_TO_DIRECTION[label]],
                'direction_source': 'absolute_fallback',
                'tier': 4,
                'confidence': None,
            })
            log.add('DIRECTION', 'DIRECTION_LOCKED', {
                'direction': result['direction'], 'source': 'absolute_fallback',
                'abs_deltas': deltas,
            })
            return result
        result['conflicts'].append({
            'code': 'ABSOLUTE_FALLBACK_TIED',
            'detail': f'绝对值回退并列: {ordered}',
        })

    # 五：仍无唯一方向 -> aborted，不产生比分
    result['status'] = 'aborted'
    result['reason'] = _ERROR_TEXT['step1_missing_inputs']
    log.add('DIRECTION', 'STEP_ABORTED', {
        'missing': result['missing'], 'invalid': result['invalid'],
        'conflicts': result['conflicts'],
    })
    return result


# --------------------------------------------------------------------------- #
# Step 2：O/U 路径（xG 修正 + 上下盘隐含倾向）
# --------------------------------------------------------------------------- #
def _poisson_over_prob(expected_goals: float, line: float) -> float:
    """泊松下总进球超过盘口线的概率（quarter-ball 盘口按两段各半）。"""
    lines = [line]
    quarters = line / 0.25
    if abs(quarters - round(quarters)) < 1e-9 and round(quarters) % 2 == 1 and line >= 0.5:
        lines = [round(line - 0.25, 2), round(line + 0.25, 2)]
    lam = expected_goals
    max_goals = 15
    total = 0.0
    for single_line in lines:
        prob_over = 0.0
        for goals in range(max_goals + 1):
            p = math.exp(-lam) * lam ** goals / math.factorial(goals)
            if goals > single_line:
                prob_over += p
        total += prob_over
    return total / len(lines)


def step2_ou_path(payload: dict[str, Any], step1: dict[str, Any], log: AuditLog) -> dict[str, Any]:
    """Step 2：依附 Step1 方向与盘口基准线，输出唯一 OVER / UNDER。"""
    result: dict[str, Any] = {
        'step': 'OU_PATH',
        'status': 'ok',
        'reason': '',
        'ou_path': '',
        'ou_path_cn': '',
        'ou_line': None,
        'expected_goals_base': None,
        'expected_goals_adjusted': None,
        'xg_adjustment': None,
        'over_prob': None,
        'under_prob': None,
        'market_over_prob': None,
        'model_path': '',
        'market_path': '',
        'path_conflict': None,
        'missing': [],
        'invalid': [],
    }
    if step1.get('status') != 'ok':
        result.update({'status': 'aborted', 'reason': _ERROR_TEXT['upstream_aborted']})
        return result

    ou_line_raw = payload.get('ou_line')
    line = _finite(ou_line_raw)
    if ou_line_raw is None:
        result['missing'].append({'field': 'ou_line', 'reason': '大小球盘口线缺失'})
    elif line is None:
        result['invalid'].append({'field': 'ou_line', 'reason': f'盘口线非数值: {ou_line_raw!r}'})
    elif line < 0:
        result['invalid'].append({'field': 'ou_line', 'reason': f'大小球盘口线为负: {line!r}'})

    lam_home, lam_away = _finite(payload.get('lam_home')), _finite(payload.get('lam_away'))
    if lam_home is None or lam_home <= 0:
        result['missing'].append({'field': 'lam_home', 'reason': 'λ 缺失或非正'})
    if lam_away is None or lam_away <= 0:
        result['missing'].append({'field': 'lam_away', 'reason': 'λ 缺失或非正'})

    over_odds = _finite(payload.get('ou_over_odds'))
    under_odds = _finite(payload.get('ou_under_odds'))
    if over_odds is None or over_odds <= 1.0:
        result['missing'].append({'field': 'ou_over_odds', 'reason': '上盘水位缺失或 ≤1.0'})
    if under_odds is None or under_odds <= 1.0:
        result['missing'].append({'field': 'ou_under_odds', 'reason': '下盘水位缺失或 ≤1.0'})

    if result['missing'] or result['invalid']:
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step2_missing_inputs']
        log.add('OU_PATH', 'STEP_ABORTED', {'missing': result['missing'], 'invalid': result['invalid']})
        return result
    # 走到这里四个输入都已通过有效性检查；pyright 看不到 dict 成员收窄，
    # 这里显式再判一次，缺任何一个都按 Step2 中止返回（行为不变）。
    if line is None or lam_home is None or lam_away is None or over_odds is None or under_odds is None:
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step2_missing_inputs']
        return result

    base_eg = lam_home + lam_away
    adjusted = base_eg
    xg_used = None
    xg_home, xg_away = _finite(payload.get('xg_home')), _finite(payload.get('xg_away'))
    if xg_home is not None and xg_away is not None and xg_home >= 0 and xg_away >= 0:
        # xG 修正：λ 合计与 xG 合计各占一半（权重固定 0.5，方向可复算）。
        adjusted = 0.5 * base_eg + 0.5 * (xg_home + xg_away)
        xg_used = {'xg_home': xg_home, 'xg_away': xg_away, 'weight': 0.5}
    result.update({
        'ou_line': line,
        'expected_goals_base': round(base_eg, 6),
        'expected_goals_adjusted': round(adjusted, 6),
        'xg_adjustment': xg_used,
    })

    over_prob = _poisson_over_prob(adjusted, line)
    result['over_prob'] = round(over_prob, 6)
    result['under_prob'] = round(1.0 - over_prob, 6)
    inverse = 1.0 / over_odds + 1.0 / under_odds
    market_over = (1.0 / over_odds) / inverse
    result['market_over_prob'] = round(market_over, 6)

    # 路径 = xG 修正后的模型口径；市场口径不同向时记冲突，方向仍取模型口径。
    model_path = 'OVER' if adjusted > line else 'UNDER'
    market_path = 'OVER' if market_over >= 0.5 else 'UNDER'
    conflict = model_path != market_path
    result['model_path'] = model_path
    result['market_path'] = market_path
    result['path_conflict'] = conflict
    result['ou_path'] = model_path
    result['ou_path_cn'] = _OU_CN[model_path]
    if conflict:
        log.add('OU_PATH', 'PATH_CONFLICT_MODEL_WINS', {
            'model_path': model_path, 'market_path': market_path,
            'adjusted_goals': result['expected_goals_adjusted'], 'line': line,
        })
    log.add('OU_PATH', 'OU_PATH_LOCKED', {
        'ou_path': model_path, 'line': line,
        'adjusted_goals': result['expected_goals_adjusted'], 'over_prob': result['over_prob'],
    })
    return result


# --------------------------------------------------------------------------- #
# Step 3：31 项完备树过滤
# --------------------------------------------------------------------------- #
def build_complete_tree_31(rows: Any = None, *, extra_rows: Any = None) -> list[dict[str, Any]]:
    """31 项完备树：三个聚合桶恒在（永不被结构性剔除）。

    优先复用调用方传入的 31 项行（``build_pool31_option_rows`` /
    ``build_theo_mkt_31_pack`` 的产物）；缺项用 ``extra_rows`` 补；都没有的项
    保留行但字段为 None（不编造）。
    """
    by_score: dict[str, dict[str, Any]] = {}
    for source in (rows, extra_rows):
        for row in (source or []):
            if not isinstance(row, dict):
                continue
            score = _cs_key(row.get('score') or row.get('name') or '')
            if score not in JCZQ_ALL_OPTIONS or score in by_score:
                continue
            by_score[score] = dict(row)
    tree: list[dict[str, Any]] = []
    for option in JCZQ_ALL_OPTIONS:
        row = by_score.get(option) or {'score': option}
        theory = _finite(row.get('theory'))
        market = _finite(row.get('market'))
        tree.append({
            'score': option,
            'bucket': _score_bucket(option),
            'is_aggregate': _is_other_bucket(option),
            'theory': theory,
            'market': market,
            'market_source': str(row.get('market_source') or ''),
            'jc_odds_pct': _finite(row.get('jc_odds_pct')),
            'delta': (round(market - theory, 4) if theory is not None and market is not None else None),
        })
    return tree


def score_matrix_from_pack(pack: Any) -> dict[str, Any]:
    """把 ``build_theo_mkt_31_pack`` 的产物接成 31 项矩阵（Step3/6 的唯一来源）。

    不改写来源语义：``market_source`` 逐行透传，出处（recorded / derived）由
    ``cs31_provenance_source`` 原样带出；市短/市热矩阵不回退。
    """
    p = pack if isinstance(pack, dict) else {}
    rows = [r for r in (p.get('rows') or []) if isinstance(r, dict)]
    out: dict[str, Any] = {
        'available': bool(rows),
        'rows': rows,
        'row_count': len(rows),
        'provenance': dict(p.get('cs31_provenance') or {}),
        'provenance_source': str(p.get('cs31_provenance_source') or ''),
        'counts': dict(p.get('cs31_counts') or {}),
        'pos_rows': [r for r in (p.get('pos_rows') or []) if isinstance(r, dict)],
        'reason': '' if rows else 'pack 内没有 31 项行',
    }
    return out


def resolve_pool31_provenance(record: Any, payload: Any = None) -> dict[str, Any]:
    """逐键出处：调用 ``DeepSeekAnalyzer.resolve_cs31_provenance``，不另开一套推导。

    返回 ``{'available', 'provenance', 'source'}``；source ∈
    recorded / derived / None，与既有语义逐字一致。
    """
    try:
        from blade_deepseek_pipeline import DeepSeekAnalyzer
    except Exception as exc:  # noqa: BLE001 - 模块缺席时如实报缺，不自行推导
        return {
            'available': False, 'provenance': {}, 'source': None,
            'reason': f'blade_deepseek_pipeline 不可用: {type(exc).__name__}: {exc}',
        }
    out = DeepSeekAnalyzer.resolve_cs31_provenance(record, payload)
    provenance = out.get('provenance') if isinstance(out, dict) else None
    return {
        'available': bool(provenance),
        'provenance': dict(provenance or {}),
        'source': (out or {}).get('source'),
    }


def _direction_matches(score: str, direction: str) -> bool:
    return _score_bucket(score) == _BUCKET_OF_DIRECTION[direction]


def _ou_matches(score: str, ou_path: str, line: float) -> bool:
    bucket = _score_bucket(score)
    if _is_other_bucket(score):
        # 聚合桶用「最小总进球」做结构性判定（见 AGGREGATE_MIN_TOTAL_GOALS）。
        goals = AGGREGATE_MIN_TOTAL_GOALS.get(bucket)
    else:
        goals = _score_total_goals(score)
    if goals is None:
        return False
    if ou_path == 'OVER':
        return goals > line
    return goals < line


def step3_candidate_tree(
    payload: dict[str, Any],
    step1: dict[str, Any],
    step2: dict[str, Any],
    log: AuditLog,
) -> dict[str, Any]:
    """Step 3：31 项内剔除与 Step1/Step2 冲突的赛果，输出候选子集与剔除理由。"""
    result: dict[str, Any] = {
        'step': 'CANDIDATE_TREE',
        'status': 'ok',
        'reason': '',
        'universe_size': 0,
        'candidates': [],
        'rejected': [],
        'candidate_count': 0,
        'aggregate_in_tree': 0,
        'missing': [],
        'invalid': [],
    }
    if step1.get('status') != 'ok' or step2.get('status') != 'ok':
        result.update({
            'status': 'aborted', 'reason': _ERROR_TEXT['upstream_aborted'],
            'upstream': {'step1': step1.get('status'), 'step2': step2.get('status')},
        })
        return result

    direction = step1['direction']
    ou_path = step2['ou_path']
    line = step2['ou_line']

    # goal_expect / handicap_side 只在给了的情况校验；不认识就 invalid 并中止。
    if payload.get('goal_expect') is not None or payload.get('handicap_side') is not None:
        if payload.get('handicap_side') is not None:
            try:
                resolve_handicap_side(payload.get('handicap_side'))
            except ValueError as exc:
                result['invalid'].append({'field': 'handicap_side', 'reason': str(exc)})
        if payload.get('goal_expect') is not None:
            normalized = str(payload.get('goal_expect')).strip().lower()
            if normalized not in {'high', '高', 'medium', '中', 'low', '低'}:
                result['invalid'].append({
                    'field': 'goal_expect',
                    'reason': f'{_ERROR_TEXT["invalid_goal_expect"]}: {payload.get("goal_expect")!r}',
                })
    if result['invalid']:
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step3_missing_inputs']
        log.add('CANDIDATE_TREE', 'STEP_ABORTED', {'invalid': result['invalid']})
        return result

    tree = build_complete_tree_31(
        payload.get('pool31_rows'), extra_rows=payload.get('pool31_extra_rows')
    )
    result['universe_size'] = len(tree)
    result['aggregate_in_tree'] = sum(1 for r in tree if r['is_aggregate'])
    if len(tree) != len(JCZQ_ALL_OPTIONS) or result['aggregate_in_tree'] != len(JCZQ_OTHER_OPTIONS):
        result['invalid'].append({
            'field': 'pool31_rows',
            'reason': (
                f'完备树项数 {len(tree)}（聚合桶 {result["aggregate_in_tree"]}）'
                f' != 31（聚合桶 3）'
            ),
        })
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step3_missing_inputs']
        log.add('CANDIDATE_TREE', 'STEP_ABORTED', {'invalid': result['invalid']})
        return result

    # 铁律 2：31 项市场值/理论概率是必填输入，缺一项就如实报缺并中止本步，
    # 绝不拿 0 当默认值让缺数据的比分在 Step6 里胜出。
    for row in tree:
        if row['market'] is None or row['market'] <= 0:
            result['missing'].append({
                'field': f'pool31_rows.{row["score"]}.market',
                'reason': '31 项市场值缺失或 ≤0',
            })
        if row['theory'] is None or row['theory'] <= 0:
            result['missing'].append({
                'field': f'pool31_rows.{row["score"]}.theory',
                'reason': '31 项理论概率缺失或 ≤0',
            })
    if result['missing']:
        result['status'] = 'aborted'
        result['reason'] = (
            f'{_ERROR_TEXT["step3_missing_inputs"]}'
            f'（31项值缺 {len(result["missing"])} 处）'
        )
        log.add('CANDIDATE_TREE', 'STEP_ABORTED', {'missing': len(result['missing'])})
        return result

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in tree:
        score = row['score']
        reason = ''
        if not _direction_matches(score, direction):
            reason = f'赛果与 Step1 方向 {direction}（{_DIRECTION_CN[direction]}）冲突'
        elif not _ou_matches(score, ou_path, line):
            reason = f'总进球与 Step2 路径 {ou_path}（盘口 {line:g}）冲突'
        if reason:
            rejected.append({'score': score, 'bucket': row['bucket'], 'reason': reason})
            continue
        candidates.append(row)

    # 铁律 1 显式断言：候选每一项都必须与 Step1/Step2 自洽（不静默裁剪）。
    for row in candidates:
        if not _direction_matches(row['score'], direction):
            raise ConvergenceContractError('step3_direction_conflict', row)
        if not _ou_matches(row['score'], ou_path, line):
            raise ConvergenceContractError('step3_ou_conflict', row)

    result['candidates'] = candidates
    result['rejected'] = rejected
    result['candidate_count'] = len(candidates)
    log.add('CANDIDATE_TREE', 'TREE_FILTERED', {
        'universe': result['universe_size'],
        'candidates': len(candidates),
        'rejected': len(rejected),
        'direction': direction,
        'ou_path': ou_path,
    })
    return result


# --------------------------------------------------------------------------- #
# Step 3 之后的矩阵收敛 / 方向自洽校验（只校验，不产生方向）
# --------------------------------------------------------------------------- #
def direction_consistency_check(
    pool31_rows: Any,
    direction: str,
    handicap_side: str = 'none',
) -> dict[str, Any]:
    """对 31 项池做「方向密度」自检，只回报与 Step1 的一致/冲突。

    去水口径：每个赛果桶内各自归一 ——
    ``share[bucket] = sum(market% in bucket) / sum(market% over 31 options)``。
    旧实现乘的「选项个数比」密度系数已彻底删除：把「选项多」当成「概率低」的
    补偿是量纲错误，会让主队热门的盘口被判成平局主导。
    """
    rows = [r for r in (pool31_rows or []) if isinstance(r, dict)]
    buckets: dict[str, float] = {'home': 0.0, 'draw': 0.0, 'away': 0.0}
    counts: dict[str, int] = {'home': 0, 'draw': 0, 'away': 0}
    missing: list[str] = []
    for row in rows:
        score = _cs_key(row.get('score') or '')
        bucket = _score_bucket(score)
        if bucket not in buckets:
            missing.append(score or '<empty>')
            continue
        market = _finite(row.get('market'))
        if market is None or market <= 0:
            missing.append(score)
            continue
        buckets[bucket] += market
        counts[bucket] += 1
    total = sum(buckets.values())
    out: dict[str, Any] = {
        'available': total > 0 and not missing,
        'bucket_sums': {k: round(v, 6) for k, v in buckets.items()},
        'counts': counts,
        'share_pct': {},
        'dominant': '',
        'step1_direction': direction,
        'conflict': None,
        'missing': missing,
        'density_factor': None,
        'handicap_side': handicap_side,
    }
    if total <= 0:
        out['available'] = False
        return out
    shares = {k: v * 100.0 / total for k, v in buckets.items()}
    out['share_pct'] = {k: round(v, 2) for k, v in shares.items()}
    dominant = max(shares, key=lambda k: shares[k])
    out['dominant'] = {'home': 'H', 'draw': 'D', 'away': 'A'}[dominant]
    out['conflict'] = bool(direction) and out['dominant'] != direction
    if out['conflict']:
        out['detail'] = (
            f'31项池方向密度主导 {out["dominant"]}（{shares[dominant]:.2f}%）'
            f'与 Step1 {direction} 不一致；以 Step1 为准'
        )
    return out


def reduce_matrix_stratified(
    pool31_rows: Any,
    handicap_side: Any,
    goal_expect: Any = None,
) -> dict[str, Any]:
    """矩阵配额式收敛：按盘口方向与进球预期在 31 项内挑核心集。

    与历史实现的三处差异：盘口方向走显式枚举别名表（``'让胜'`` 正确归入
    home 分支）；``goal_expect`` 不认识时返回 ``invalid`` 而不静默回退；
    聚合桶不参与结构性剔除。
    """
    side = resolve_handicap_side(handicap_side)
    if side == 'home':
        allowed = ('home',)
    elif side == 'away':
        allowed = ('away',)
    else:
        allowed = ('home', 'draw', 'away')
    quota = {'high': 5, 'medium': 4, 'low': 3}
    out: dict[str, Any] = {
        'available': False,
        'side': side,
        'goal_expect': goal_expect,
        'quota': None,
        'core': [],
        'invalid': [],
        'universe_size': len(JCZQ_ALL_OPTIONS),
    }
    if goal_expect is None:
        out['invalid'].append({'field': 'goal_expect', 'reason': '缺少进球预期（high/medium/low）'})
        return out
    normalized = str(goal_expect).strip().lower()
    mapping = {'high': 'high', '高': 'high', 'medium': 'medium', '中': 'medium', 'low': 'low', '低': 'low'}
    if normalized not in mapping:
        out['invalid'].append({
            'field': 'goal_expect',
            'reason': f'{_ERROR_TEXT["invalid_goal_expect"]}: {goal_expect!r}',
        })
        return out
    level = mapping[normalized]
    out['quota'] = quota[level]
    scored: list[tuple[float, str]] = []
    for row in (pool31_rows or []):
        if not isinstance(row, dict):
            continue
        score = _cs_key(row.get('score') or '')
        if _score_bucket(score) not in allowed:
            continue
        market = _finite(row.get('market'))
        if market is None or market <= 0:
            continue
        scored.append((market, score))
    if not scored:
        out['invalid'].append({
            'field': 'pool31_rows', 'reason': '筛选后无可用行（market 缺失或方向不符）',
        })
        return out
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    out['core'] = [score for _, score in scored[:quota[level]]]
    out['available'] = True
    return out


# --------------------------------------------------------------------------- #
# 宏观裁决引擎（C 段 V16.36）
# --------------------------------------------------------------------------- #
def resolve_tier_diff(payload: dict[str, Any]) -> dict[str, Any]:
    """``tier_diff`` 只能来自系统内可复算的量，并带出来源。

    来源优先级：联赛名次差（6 名一档）> Elo 差（100 分一档）；都没有 -> invalid。
    """
    rank_home = _finite(payload.get('league_rank_home'))
    rank_away = _finite(payload.get('league_rank_away'))
    if rank_home is not None and rank_away is not None and rank_home > 0 and rank_away > 0:
        raw = rank_away - rank_home
        tiers = math.trunc(raw / 6.0)
        tiers = max(-3, min(3, tiers))
        return {
            'valid': True, 'tier_diff': tiers, 'source': 'league_rank',
            'raw': {
                'league_rank_home': rank_home, 'league_rank_away': rank_away,
                'rank_gap': raw, 'names_per_tier': 6,
            },
        }
    elo_home = _finite(payload.get('elo_home'))
    elo_away = _finite(payload.get('elo_away'))
    if elo_home is not None and elo_away is not None and elo_home > 0 and elo_away > 0:
        raw = elo_home - elo_away
        tiers = math.trunc(raw / 100.0)
        tiers = max(-3, min(3, tiers))
        return {
            'valid': True, 'tier_diff': tiers, 'source': 'elo',
            'raw': {
                'elo_home': elo_home, 'elo_away': elo_away,
                'elo_gap': raw, 'points_per_tier': 100,
            },
        }
    return {
        'valid': False, 'tier_diff': None, 'source': '',
        'reason': '缺少可复算的实力来源（league_rank_* / elo_*），tier_diff 不可用',
        'raw': {
            'league_rank_home': rank_home, 'league_rank_away': rank_away,
            'elo_home': elo_home, 'elo_away': elo_away,
        },
    }


def _tier_branch(line_q: int) -> str:
    """盘口档位：LEVEL / SHALLOW / MID(1.0-1.25) / DEEP_1_5 / DEEP_PLUS(>=1.75)。"""
    quarters = abs(line_q)
    if quarters == 0:
        return 'LEVEL'
    if quarters <= 3:
        return 'SHALLOW'
    if quarters <= 5:
        return 'MID'
    if quarters == 6:
        return 'DEEP_1_5'
    return 'DEEP_PLUS'


def macro_handicap_verdict(payload: dict[str, Any], step1: dict[str, Any], log: AuditLog) -> dict[str, Any]:
    """宏观裁决：带符号盘口 + 档位 + tier_diff -> veto_action / conf_mod。

    保留盘口符号（``abs()`` 已删）；线归一化到 quarter-ball，非法即 invalid；
    ``conf_mod`` clamp 到 [-0.5, +0.5]；1.25 / 1.5 / >=1.75 三档分开。
    """
    result: dict[str, Any] = {
        'step': 'MACRO_VETO',
        'status': 'ok',
        'reason': '',
        'handicap_line': None,
        'handicap_side': '',
        'tier_branch': '',
        'veto_action': VETO_NONE,
        'conf_mod': 0.0,
        'tier_diff': None,
        'tier_source': '',
        'tier_raw': {},
        'missing': [],
        'invalid': [],
        'conflicts': [],
    }
    if step1.get('status') != 'ok':
        result.update({'status': 'aborted', 'reason': _ERROR_TEXT['upstream_aborted']})
        return result
    normalized = normalize_handicap_line(payload.get('asian_handicap'))
    if not normalized.get('valid'):
        result['invalid'].append({'field': 'asian_handicap', 'reason': normalized.get('reason')})
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['invalid_handicap_line']
        log.add('MACRO_VETO', 'STEP_ABORTED', {'invalid': result['invalid']})
        return result
    tier = resolve_tier_diff(payload)
    if not tier.get('valid'):
        result['invalid'].append({'field': 'tier_diff', 'reason': tier.get('reason')})
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step4_missing_inputs']
        log.add('MACRO_VETO', 'STEP_ABORTED', {'invalid': result['invalid']})
        return result

    line = normalized['line']
    line_q = normalized['quarters']
    side = 'home_gives' if line < 0 else ('away_gives' if line > 0 else 'level')
    branch = _tier_branch(line_q)
    tier_diff = int(tier['tier_diff'])
    direction = step1['direction']
    result.update({
        'handicap_line': line,
        'handicap_side': side,
        'tier_branch': branch,
        'tier_diff': tier_diff,
        'tier_source': tier['source'],
        'tier_raw': tier['raw'],
    })

    handicap_favours = 'H' if side == 'home_gives' else ('A' if side == 'away_gives' else 'D')
    aligned = handicap_favours == direction
    strength_direction = 'H' if tier_diff > 0 else ('A' if tier_diff < 0 else 'D')
    strength_aligned = strength_direction == direction
    abs_tier = abs(tier_diff)

    if branch == 'LEVEL':
        if aligned and strength_aligned:
            veto, conf = VETO_BOOST, 0.20
        elif not aligned:
            veto, conf = VETO_SOFT_FADE, -0.15
        else:
            veto, conf = VETO_NONE, 0.0
    elif branch == 'SHALLOW':
        if aligned:
            veto, conf = (VETO_BOOST, 0.30) if abs_tier >= 1 else (VETO_NONE, 0.10)
        else:
            veto, conf = VETO_SOFT_FADE, -0.20
    elif branch == 'MID':
        if aligned:
            veto, conf = VETO_BOOST, 0.40
        elif line_q == 5:
            veto, conf = (VETO_HARD_BLOCK, -0.50) if not strength_aligned else (VETO_STRONG_FADE, -0.35)
        else:
            veto, conf = VETO_STRONG_FADE, -0.30
    elif branch == 'DEEP_1_5':
        if aligned:
            veto, conf = VETO_BOOST, 0.45
        else:
            veto, conf = VETO_HARD_BLOCK, -0.50
    else:  # DEEP_PLUS >= 1.75
        if aligned and abs_tier >= 2:
            veto, conf = VETO_BOOST, 0.50
        elif aligned:
            veto, conf = VETO_STRONG_FADE, -0.25
        elif abs_tier >= 2:
            veto, conf = VETO_HARD_BLOCK, -0.50
        else:
            veto, conf = VETO_STRONG_FADE, -0.40

    result['veto_action'] = veto
    result['conf_mod'] = clamp_conf_mod(conf)
    if not aligned:
        result['conflicts'].append({
            'code': 'HANDICAP_AGAINST_STEP1',
            'detail': f'盘口 {line:g}（{side}）利好 {handicap_favours}，Step1 方向 {direction}',
        })
    if not strength_aligned:
        result['conflicts'].append({
            'code': 'TIER_AGAINST_STEP1',
            'detail': (
                f'tier_diff={tier_diff}（{tier["source"]}）利好 {strength_direction}，'
                f'Step1 方向 {direction}'
            ),
        })
    log.add('MACRO_VETO', 'MACRO_VERDICT', {
        'veto_action': veto, 'conf_mod': result['conf_mod'], 'branch': branch,
        'line': line, 'side': side, 'tier_diff': tier_diff,
    })
    return result


def euro_micro_delta(payload: dict[str, Any], step1: dict[str, Any]) -> dict[str, Any]:
    """欧赔微观离散：初赔 -> 即时赔率的隐含概率迁移（互锁真的被读取）。"""
    out: dict[str, Any] = {'available': False, 'delta': {}, 'dominant': '', 'missing': []}
    open_odds = payload.get('odds_1x2_open')
    if not isinstance(open_odds, dict) or not open_odds:
        out['missing'].append({'field': 'odds_1x2_open', 'reason': '初赔缺失'})
        return out
    open_vo = _de_vig(open_odds)
    if not open_vo.get('valid'):
        out['missing'].append({'field': 'odds_1x2_open', 'reason': '初赔非法（缺失或 ≤1.0）'})
        return out
    instant = step1.get('market_probs') or {}
    if not instant:
        out['missing'].append({'field': 'odds_1x2', 'reason': '即时去水概率缺失'})
        return out
    delta = {
        key: round(instant.get(key, 0.0) - open_vo['probs'][key], 6)
        for key in ('home', 'draw', 'away')
    }
    dominant = max(delta, key=lambda k: abs(delta[k]))
    out.update({
        'available': True,
        'delta': delta,
        'dominant': {'home': 'H', 'draw': 'D', 'away': 'A'}[dominant],
        'dominant_delta': delta[dominant],
        'open_probs': {k: round(v, 6) for k, v in open_vo['probs'].items()},
    })
    return out


def apply_macro_interlock(
    macro: dict[str, Any],
    micro: dict[str, Any],
    step1: dict[str, Any],
    log: AuditLog,
) -> dict[str, Any]:
    """互锁：宏观 ``conf_mod`` 的作用方向必须与欧赔微观迁移方向一致。

    一致 -> 全额生效；冲突 -> 折半并把冲突写进 ``conflicts``；微观不可用 ->
    保留 ``conf_mod`` 并标记 ``interlock='micro_unavailable'``（不编造迁移方向）。
    """
    out: dict[str, Any] = {'interlock': 'not_applicable', 'conf_mod': 0.0, 'conflicts': []}
    if macro.get('status') != 'ok':
        return out
    conf = _finite(macro.get('conf_mod'))
    if conf is None:
        return out
    direction = step1.get('direction') or ''
    macro_favours = direction if conf > 0 else ''
    if not micro.get('available'):
        out.update({
            'interlock': 'micro_unavailable',
            'conf_mod': round(conf, 6),
            'conflicts': list(macro.get('conflicts') or []),
        })
        log.add('MACRO_VETO', 'INTERLOCK_MICRO_UNAVAILABLE', {'conf_mod': conf})
        return out
    micro_direction = micro.get('dominant') or ''
    micro_delta = _finite(micro.get('dominant_delta'))
    agrees = (
        conf == 0
        or (conf > 0 and micro_direction == direction)
        or (conf < 0 and micro_direction != direction)
    )
    conflicts = list(macro.get('conflicts') or [])
    if agrees:
        out.update({'interlock': 'aligned', 'conf_mod': round(conf, 6)})
        log.add('MACRO_VETO', 'INTERLOCK_ALIGNED', {
            'conf_mod': conf, 'micro_direction': micro_direction, 'micro_delta': micro_delta,
        })
    else:
        halved = round(conf * 0.5, 6)
        out.update({'interlock': 'conflict_halved', 'conf_mod': halved})
        conflicts.append({
            'code': 'MACRO_MICRO_INTERLOCK_CONFLICT',
            'detail': (
                f'宏观 conf_mod={conf}（利好 {macro_favours or "-"}）'
                f'与欧赔微观迁移 {micro_direction}({micro_delta:+.4f}) 冲突，'
                f'修正量折半为 {halved}'
            ),
        })
        log.add('MACRO_VETO', 'INTERLOCK_CONFLICT', {
            'conf_mod': conf, 'halved': halved, 'micro_direction': micro_direction,
        })
    out['conflicts'] = conflicts
    return out


# --------------------------------------------------------------------------- #
# Step 4：联动与市热/市短
# --------------------------------------------------------------------------- #
def _parse_heat_lines(lines: Any) -> dict[str, Any]:
    """从已渲染的市热 TG 行里读出「哪一方更热、条数」（不重算热度）。"""
    if not isinstance(lines, (list, tuple)) or not lines:
        return {'available': False, 'reason': 'heat_tg_lines 为空', 'counts': {}, 'dominant': ''}
    text = '\n'.join(str(x) for x in lines)
    counts: dict[str, int] = {}
    for label in ('胜', '平', '负'):
        found = re.findall(rf'{label}\s*[=:：]\s*(\d+)', text)
        if found:
            counts[label] = sum(int(v) for v in found)
    if not counts:
        return {'available': False, 'reason': 'heat_tg_lines 内无可解析的胜/平/负计数',
                'counts': {}, 'dominant': ''}
    dominant_cn = max(counts, key=lambda k: counts[k])
    return {
        'available': True,
        'counts': counts,
        'dominant': _CN_TO_DIRECTION[dominant_cn],
        'dominant_cn': dominant_cn,
        'total': sum(counts.values()),
    }


def _parse_short_lines(lines: Any) -> dict[str, Any]:
    """从市短矩阵 TG 行里读出「胜=n(...) 平=n(...) 负=n(...)」。"""
    if not isinstance(lines, (list, tuple)) or not lines:
        return {'available': False, 'reason': 'mkt_over_theo_tg_lines 为空',
                'counts': {}, 'dominant': ''}
    text = '\n'.join(str(x) for x in lines)
    counts: dict[str, int] = {}
    for label in ('胜', '平', '负'):
        found = re.findall(rf'{label}\s*=\s*(\d+)', text)
        if found:
            counts[label] = sum(int(v) for v in found)
    if not counts:
        return {'available': False,
                'reason': 'mkt_over_theo_tg_lines 内无可解析的胜平负计数',
                'counts': {}, 'dominant': ''}
    dominant_cn = max(counts, key=lambda k: counts[k])
    return {
        'available': True,
        'counts': counts,
        'dominant': _CN_TO_DIRECTION[dominant_cn],
        'dominant_cn': dominant_cn,
        'total': sum(counts.values()),
    }


def _heat_top8_rows(payload: dict[str, Any]) -> dict[str, Any]:
    """取热榜前 8（``heat_top8`` / ``heat_ranking`` / score_market_heat.ranking）。

    三个键都是同一份市热数据的不同落盘位置；按顺序取第一个可用的，
    不重算热度，也不编造赔率。
    """
    raw = None
    for key in ('heat_top8', 'heat_ranking'):
        candidate = payload.get(key)
        if isinstance(candidate, (list, tuple)) and candidate:
            raw = candidate
            break
    if raw is None:
        block = payload.get('score_market_heat')
        if isinstance(block, dict):
            raw = block.get('ranking')
    if not isinstance(raw, (list, tuple)) or not raw:
        return {'available': False,
                'reason': 'heat 榜为空或缺失（heat_top8/heat_ranking 均无）', 'rows': []}
    rows: list[dict[str, Any]] = []
    for item in raw[:8]:
        if isinstance(item, dict):
            score = _cs_key(item.get('score') or '')
            odds = _finite(item.get('odds'))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            score, odds = _cs_key(item[0]), _finite(item[1])
        else:
            continue
        if not score or odds is None:
            continue
        rows.append({'score': score, 'odds': odds})
    if not rows:
        return {'available': False, 'reason': 'heat 榜没有可解析的比分/赔率行', 'rows': []}
    return {'available': True, 'rows': rows, 'reason': ''}


def lowest_odds_draw_check(
    payload: dict[str, Any],
    direction: str,
    log: AuditLog,
) -> dict[str, Any]:
    """最低赔落在平局通道而方向为主胜/客胜 -> 冲突 + 降权（不双选、不改方向）。

    heat 为空 -> ``status='skipped'`` 且 ``missing=['heat_top8']``，绝不编造 ``1-1``。
    """
    out: dict[str, Any] = {
        'status': 'skipped',
        'reason': '',
        'lowest_score': '',
        'lowest_odds': None,
        'lowest_bucket': '',
        'conflict_code': '',
        'downweight': 1.0,
        'missing': [],
    }
    heat = _heat_top8_rows(payload)
    if not heat.get('available'):
        out['reason'] = heat.get('reason') or 'heat 榜缺失'
        out['missing'].append({'field': 'heat_top8', 'reason': out['reason']})
        log.add('LINKAGE', 'LOWEST_ODDS_CHECK_SKIPPED', {'reason': out['reason']})
        return out
    lowest = min(heat['rows'], key=lambda r: (r['odds'], r['score']))
    bucket = _score_bucket(lowest['score'])
    out.update({
        'status': 'ok',
        'lowest_score': lowest['score'],
        'lowest_odds': lowest['odds'],
        'lowest_bucket': bucket,
        'heat_rows': len(heat['rows']),
    })
    if bucket == 'draw' and direction in ('H', 'A'):
        code = (
            'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_HOME' if direction == 'H'
            else 'LOWEST_ODDS_IS_DRAW_WHILE_DIRECTION_AWAY'
        )
        factor = max(
            LOWEST_ODDS_DRAW_DOWNWEIGHT_MIN,
            min(LOWEST_ODDS_DRAW_DOWNWEIGHT_MAX, LOWEST_ODDS_DRAW_DOWNWEIGHT),
        )
        out.update({
            'conflict_code': code,
            'downweight': factor,
            'detail': (
                f'最低赔 {lowest["score"]}@{lowest["odds"]:g} 落在平局通道，'
                f'{_DIRECTION_CN[direction]}权重已下调至 {factor}，方向不变'
            ),
        })
        log.add('LINKAGE', code, {
            'lowest_score': lowest['score'], 'lowest_odds': lowest['odds'],
            'direction': direction, 'downweight': factor,
        })
    return out


def _bucket_counts_from_ranking(ranking: Any, limit: Any = None) -> dict[str, Any]:
    """从市热/市短榜单按赛果方向计数（结构化输入优先路径）。

    ``ranking`` 每行需带 ``score``；``odds``/``heat`` 可选（本函数只数方向）。
    聚合桶（胜其它/平其它/负其它 及别名写法）按前缀归入对应方向。
    ``limit`` 给出时只取前 N 行（市热榜惯用前 8）。
    """
    rows = ranking if isinstance(ranking, (list, tuple)) else []
    if limit is not None:
        rows = rows[:int(limit)]
    counts: dict[str, int] = {'胜': 0, '平': 0, '负': 0}
    skipped: list[str] = []
    used = 0
    for item in rows:
        if isinstance(item, dict):
            score = item.get('score') or item.get('name') or ''
        elif isinstance(item, (list, tuple)) and item:
            score = item[0]
        else:
            continue
        score_key = _cs_key(score)
        bucket = _score_bucket(score_key)
        if bucket not in ('home', 'draw', 'away'):
            skipped.append(str(score))
            continue
        counts[_DIRECTION_TO_CN[_DIRECTION_OF_BUCKET[bucket]]] += 1
        used += 1
    return {
        'available': used > 0,
        'counts': counts,
        'rows_used': used,
        'skipped': skipped,
        'total': used,
    }


def _counts_from_structured(value: Any) -> dict[str, Any]:
    """校验并归一调用方给的 ``{胜,平,负}`` 计数（不补 0、不猜缺项）。"""
    if not isinstance(value, dict):
        return {'available': False, 'reason': '不是 {胜,平,负} 字典'}
    counts: dict[str, int] = {}
    for label in ('胜', '平', '负'):
        number = _finite(value.get(label))
        if number is None or number < 0 or not float(number).is_integer():
            return {'available': False, 'reason': f'{label} 缺失或非非负整数: {value.get(label)!r}'}
        counts[label] = int(number)
    if sum(counts.values()) <= 0:
        return {'available': False, 'reason': '三项计数合计为 0（无有效信号）'}
    return {'available': True, 'counts': counts}


def _normalize_signal_counts(signal: dict[str, Any]) -> dict[str, Any]:
    """给方向信号补上 dominant / dominant_cn / total（统一口径）。"""
    out = dict(signal)
    counts = out.get('counts') or {}
    if not counts:
        out.setdefault('dominant', '')
        out.setdefault('dominant_cn', '')
        out.setdefault('total', 0)
        return out
    dominant_cn = max(counts, key=lambda k: counts[k])
    out['dominant_cn'] = dominant_cn
    out['dominant'] = _CN_TO_DIRECTION[dominant_cn]
    out['total'] = sum(counts.values())
    return out


def _wdl_signal(payload: dict[str, Any], kind: str, log: AuditLog) -> dict[str, Any]:
    """市热/市短的方向信号：结构化优先，渲染文本仅作兼容兜底。

    优先级（``kind='heat'``）：
      1. ``heat_ranking`` —— 由现有 ranking 逐行按方向计数（首选：数字来自数据本身）；
      2. ``heat_wdl_counts`` —— 调用方已算好的 {胜,平,负}；
      3. ``heat_tg_lines`` —— 旧调用方的渲染文本（正则解析，可能解析不出）；
    ``kind='short'`` 对应 ``short_rows`` / ``short_wdl_counts`` / ``mkt_over_theo_tg_lines``。
    三条都不可用时返回 available=False + 明确原因，绝不编造计数。
    """
    if kind == 'heat':
        ranking_key, counts_key, lines_key, parser = (
            'heat_ranking', 'heat_wdl_counts', 'heat_tg_lines', _parse_heat_lines,
        )
        limit = 8
    else:
        ranking_key, counts_key, lines_key, parser = (
            'short_rows', 'short_wdl_counts', 'mkt_over_theo_tg_lines', _parse_short_lines,
        )
        limit = None
    tried: list[str] = []

    ranking = payload.get(ranking_key)
    if isinstance(ranking, (list, tuple)) and ranking:
        from_ranking = _bucket_counts_from_ranking(ranking, limit=limit)
        if from_ranking['available']:
            log.add('LINKAGE', f'{kind.upper()}_FROM_RANKING', {
                'rows_used': from_ranking['rows_used'], 'counts': from_ranking['counts'],
            })
            return _normalize_signal_counts({
                'available': True,
                'source_path': 'ranking',
                'counts': from_ranking['counts'],
                'total': from_ranking['total'],
                'rows_used': from_ranking['rows_used'],
                'skipped': from_ranking['skipped'],
            })
        tried.append(f'{ranking_key}(无可用比分行)')

    structured = _counts_from_structured(payload.get(counts_key))
    if structured['available']:
        log.add('LINKAGE', f'{kind.upper()}_FROM_COUNTS', {'counts': structured['counts']})
        total = sum(structured['counts'].values())
        return _normalize_signal_counts({
            'available': True,
            'source_path': 'counts',
            'counts': structured['counts'],
            'total': total,
            'rows_used': total,
            'skipped': [],
        })
    if payload.get(counts_key) is not None:
        tried.append(f'{counts_key}({structured["reason"]})')

    parsed = parser(payload.get(lines_key))
    if parsed.get('available'):
        log.add('LINKAGE', f'{kind.upper()}_FROM_LINES', {'counts': parsed.get('counts')})
        return _normalize_signal_counts({
            'available': True,
            'source_path': 'lines',
            'counts': parsed['counts'],
            'total': parsed.get('total', sum(parsed['counts'].values())),
            'rows_used': parsed.get('total', sum(parsed['counts'].values())),
            'skipped': [],
        })
    tried.append(f'{lines_key}({parsed.get("reason") or "无可解析计数"})')

    return {
        'available': False,
        'source_path': 'none',
        'counts': {},
        'total': 0,
        'reason': '；'.join(tried) or f'{kind} 无结构化输入且无渲染文本',
        'tried': tried,
    }


def step4_linkage(
    payload: dict[str, Any],
    step1: dict[str, Any],
    step3: dict[str, Any],
    macro: dict[str, Any],
    micro: dict[str, Any],
    log: AuditLog,
) -> dict[str, Any]:
    """Step 4：让球/大小球顺局互殴 + 市热方向 + 市短偏向 + 最低赔冲突 -> 权重修正。"""
    result: dict[str, Any] = {
        'step': 'LINKAGE',
        'status': 'ok',
        'reason': '',
        'linkage': '',
        'linkage_weight': 1.0,
        'heat': {'available': False},
        'short': {'available': False},
        'heat_source_path': 'none',
        'short_source_path': 'none',
        'lowest_odds_check': {},
        'macro_conf_mod': 0.0,
        'macro_veto': VETO_NONE,
        'interlock': 'not_applicable',
        'weight_factors': {},
        'step4_multiplier': 1.0,
        'conflicts': [],
        'missing': [],
        'invalid': [],
    }
    if step1.get('status') != 'ok' or step3.get('status') != 'ok':
        result.update({'status': 'aborted', 'reason': _ERROR_TEXT['upstream_aborted']})
        return result

    heat = _wdl_signal(payload, 'heat', log)
    short = _wdl_signal(payload, 'short', log)
    result['heat'] = heat
    result['short'] = short
    result['heat_source_path'] = heat.get('source_path', 'none')
    result['short_source_path'] = short.get('source_path', 'none')
    if not heat.get('available'):
        result['missing'].append({'field': 'heat_ranking/heat_wdl_counts/heat_tg_lines',
                                  'reason': heat.get('reason')})
    if not short.get('available'):
        result['missing'].append({'field': 'short_rows/short_wdl_counts/mkt_over_theo_tg_lines',
                                  'reason': short.get('reason')})

    normalized = normalize_handicap_line(payload.get('asian_handicap'))
    if not normalized.get('valid'):
        result['invalid'].append({'field': 'asian_handicap', 'reason': normalized.get('reason')})
    ou_line = _finite(payload.get('ou_line'))
    if ou_line is None:
        result['missing'].append({'field': 'ou_line', 'reason': '大小球盘口线缺失'})
    direction = step1['direction']

    if result['missing'] or result['invalid']:
        result['status'] = 'aborted'
        result['reason'] = _ERROR_TEXT['step4_missing_inputs']
        log.add('LINKAGE', 'STEP_ABORTED', {
            'missing': result['missing'], 'invalid': result['invalid'],
        })
        return result

    side = 'home_gives' if normalized['line'] < 0 else (
        'away_gives' if normalized['line'] > 0 else 'level')
    handicap_favours = 'H' if side == 'home_gives' else ('A' if side == 'away_gives' else 'D')
    linkage = '顺局' if handicap_favours == direction else '互殴'
    linkage_weight = 1.10 if linkage == '顺局' else 0.90
    result['linkage'] = linkage
    result['linkage_weight'] = linkage_weight
    if linkage == '互殴':
        result['conflicts'].append({
            'code': 'LINKAGE_MUTUAL_ATTACK',
            'detail': (
                f'让球盘 {normalized["line"]:g} 利好 {handicap_favours}，'
                f'与 Step1 {direction} 相反'
            ),
        })

    lowest = lowest_odds_draw_check(payload, direction, log)
    result['lowest_odds_check'] = lowest
    if lowest.get('status') == 'skipped':
        result['missing'].extend(lowest.get('missing') or [])
    if lowest.get('conflict_code'):
        result['conflicts'].append({
            'code': lowest['conflict_code'],
            'detail': lowest.get('detail') or '',
        })

    heat_factor = 1.0
    if heat.get('available'):
        heat_factor = 1.05 if heat['dominant'] == direction else 0.97
    short_factor = 1.0
    if short.get('available'):
        short_factor = 1.05 if short['dominant'] == direction else 0.97
    draw_factor = _finite(lowest.get('downweight'))
    if draw_factor is None:
        draw_factor = 1.0
    draw_factor = max(LOWEST_ODDS_DRAW_DOWNWEIGHT_MIN,
                      min(LOWEST_ODDS_DRAW_DOWNWEIGHT_MAX, draw_factor))
    result['weight_factors'] = {
        'linkage': round(linkage_weight, 6),
        'heat': round(heat_factor, 6),
        'short': round(short_factor, 6),
        'lowest_odds_draw': round(draw_factor, 6),
    }
    result['step4_multiplier'] = round(
        linkage_weight * heat_factor * short_factor * draw_factor, 6
    )

    interlock = apply_macro_interlock(macro, micro, step1, log)
    result['interlock'] = interlock.get('interlock')
    result['macro_conf_mod'] = interlock.get('conf_mod', 0.0)
    result['macro_veto'] = macro.get('veto_action', VETO_NONE) if macro.get('status') == 'ok' else VETO_NONE
    result['conflicts'].extend(interlock.get('conflicts') or [])
    log.add('LINKAGE', 'LINKAGE_RESOLVED', {
        'linkage': linkage,
        'factors': result['weight_factors'],
        'step4_multiplier': result['step4_multiplier'],
        'macro_conf_mod': result['macro_conf_mod'],
        'macro_veto': result['macro_veto'],
    })
    return result


# --------------------------------------------------------------------------- #
# Step 5：六盘口互洽
# --------------------------------------------------------------------------- #
def _dimension_half_1x2(payload: dict[str, Any], direction: str) -> dict[str, Any]:
    vo = _de_vig(payload.get('half_time_1x2'))
    if not vo.get('valid'):
        return {'available': False, 'reason': '半场胜平负赔率缺失或非法',
                'missing': [{'field': 'half_time_1x2', 'reason': '缺失或非法'}]}
    leader = max(vo['probs'], key=lambda k: vo['probs'][k])
    half_dir = {'home': 'H', 'draw': 'D', 'away': 'A'}[leader]
    return {
        'available': True,
        'half_direction': half_dir,
        'probs': {k: round(v, 6) for k, v in vo['probs'].items()},
        'agree': half_dir == direction,
        'detail': {'half_direction': half_dir},
    }


def _dimension_half_ou(payload: dict[str, Any], step2: dict[str, Any]) -> dict[str, Any]:
    line = _finite(payload.get('half_time_ou_line'))
    if line is None:
        return {'available': False, 'reason': '半场大小球盘口线缺失',
                'missing': [{'field': 'half_time_ou_line', 'reason': '缺失或非数值'}]}
    lam_home, lam_away = _finite(payload.get('lam_home')), _finite(payload.get('lam_away'))
    if lam_home is None or lam_away is None:
        return {'available': False, 'reason': 'λ 缺失，无法推半场期望进球',
                'missing': [{'field': 'lam_home/lam_away', 'reason': '缺失'}]}
    half_eg = (lam_home + lam_away) / 2.0
    half_path = 'OVER' if half_eg > line else 'UNDER'
    return {
        'available': True,
        'half_time_line': line,
        'half_time_expected_goals': round(half_eg, 6),
        'half_ou_path': half_path,
        'agree': half_path == step2.get('ou_path'),
        'detail': {'half_ou_path': half_path, 'half_line': line},
    }


def immutable_factors_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """不可变因子审计（`borsa_signals_count` 不得默认 1）。

    缺失 -> invalid（该维度记 0 分）；存在时 confidence 真的参与 Step5 该维
    得分与 Step6 权重。
    """
    out: dict[str, Any] = {
        'available': False,
        'reason': '',
        'total_factors': None,
        'changed_factors': [],
        'confidence': None,
        'weighted_active': None,
        'missing': [],
        'invalid': [],
    }
    if 'borsa_signals_count' not in payload or payload.get('borsa_signals_count') is None:
        out['reason'] = 'borsa_signals_count 缺失（不默认 1）'
        out['invalid'].append({'field': 'borsa_signals_count', 'reason': out['reason']})
        return out
    count = _finite(payload.get('borsa_signals_count'))
    total = _finite(payload.get('immutable_factor_total'))
    changed_raw = payload.get('immutable_factors_changed')
    if count is None or count < 0 or not float(count).is_integer():
        out['reason'] = f'borsa_signals_count 非非负整数: {payload.get("borsa_signals_count")!r}'
        out['invalid'].append({'field': 'borsa_signals_count', 'reason': out['reason']})
        return out
    if total is None or total <= 0:
        out['reason'] = 'immutable_factor_total 缺失或非正'
        out['invalid'].append({'field': 'immutable_factor_total', 'reason': out['reason']})
        return out
    changed = list(changed_raw) if isinstance(changed_raw, (list, tuple, set)) else []
    if changed_raw is not None and not isinstance(changed_raw, (list, tuple, set)):
        out['reason'] = 'immutable_factors_changed 必须是列表'
        out['invalid'].append({'field': 'immutable_factors_changed', 'reason': out['reason']})
        return out
    if len(changed) > total:
        out['reason'] = f'变更因子数 {len(changed)} 超过总数 {total:g}'
        out['invalid'].append({'field': 'immutable_factors_changed', 'reason': out['reason']})
        return out
    confidence = (total - len(changed)) / total
    out.update({
        'available': True,
        'total_factors': int(total),
        'changed_factors': changed,
        'confidence': round(confidence, 6),
        'weighted_active': round(int(count) * confidence, 6),
    })
    return out


def _dimension_tempo(
    payload: dict[str, Any],
    step2: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    """节奏与盘口演变：大小球初盘 -> 即时盘口的移动 + 不可变因子审计。"""
    if not audit.get('available'):
        return {
            'available': False,
            'reason': audit.get('reason') or '不可变因子审计不可用',
            'missing': list(audit.get('missing') or []) + list(audit.get('invalid') or []),
        }
    normalized = normalize_handicap_line(payload.get('ou_open_line'))
    if not normalized.get('valid'):
        return {'available': False, 'reason': '大小球初盘缺失或非法',
                'missing': [{'field': 'ou_open_line', 'reason': normalized.get('reason')}]}
    open_line = normalized['line']
    now_line = _finite(payload.get('ou_line'))
    if now_line is None:
        return {'available': False, 'reason': '即时大小球盘口缺失',
                'missing': [{'field': 'ou_line', 'reason': '缺失'}]}
    delta = now_line - open_line
    tempo_path = 'OVER' if delta > 0 else ('UNDER' if delta < 0 else 'FLAT')
    return {
        'available': True,
        'ou_open_line': open_line,
        'ou_line': now_line,
        'line_delta': round(delta, 6),
        'tempo_path': tempo_path,
        'audit_confidence': audit['confidence'],
        'weighted_active': audit['weighted_active'],
        'agree': tempo_path == step2.get('ou_path'),
        'detail': {
            'tempo_path': tempo_path, 'line_delta': round(delta, 6),
            'audit_confidence': audit['confidence'],
        },
    }


def step5_consistency(
    payload: dict[str, Any],
    step1: dict[str, Any],
    step2: dict[str, Any],
    step4: dict[str, Any],
    log: AuditLog,
) -> dict[str, Any]:
    """Step 5：六大维度逐项打分 + 冲突明细 + 总互洽得分（0-100）。

    缺一维则该维 0 分并记冲突，绝不补分。
    """
    result: dict[str, Any] = {
        'step': 'CONSISTENCY',
        'status': 'ok',
        'reason': '',
        'dimensions': {},
        'total_score': 0.0,
        'conflicts': [],
        'missing_dimensions': [],
        'immutable_factors': {},
        'audit_factor': 1.0,
    }
    if step1.get('status') != 'ok' or step2.get('status') != 'ok':
        result.update({'status': 'aborted', 'reason': _ERROR_TEXT['upstream_aborted']})
        return result
    direction = step1['direction']
    dims: dict[str, dict[str, Any]] = {}

    # 1) 欧赔
    market = step1.get('market_probs') or {}
    leader = max(market, key=lambda k: market[k]) if market else ''
    euro_dir = {'home': 'H', 'draw': 'D', 'away': 'A'}.get(leader, '')
    dims['euro_1x2'] = {
        'available': bool(market),
        'agree': bool(euro_dir) and euro_dir == direction,
        'detail': {'market_leader': euro_dir},
        'reason': '' if market else '1X2 去水概率缺失',
        'missing': [] if market else [{'field': 'odds_1x2', 'reason': '缺失'}],
    }
    # 2) 亚盘
    normalized = normalize_handicap_line(payload.get('asian_handicap'))
    if normalized.get('valid'):
        ah_line = normalized['line']
        ah_dir = 'H' if ah_line < 0 else ('A' if ah_line > 0 else 'D')
        dims['asian'] = {'available': True, 'agree': ah_dir == direction,
                         'detail': {'line': ah_line, 'favours': ah_dir}}
    else:
        dims['asian'] = {'available': False, 'reason': normalized.get('reason'),
                         'missing': [{'field': 'asian_handicap', 'reason': normalized.get('reason')}]}
    # 3) 大小球：模型路径与市场路径是否一致
    dims['ou'] = {
        'available': step2.get('market_over_prob') is not None,
        'agree': not bool(step2.get('path_conflict')),
        'detail': {'model_path': step2.get('model_path'), 'market_path': step2.get('market_path')},
        'reason': '' if step2.get('market_over_prob') is not None else '大小球水位缺失',
        'missing': [] if step2.get('market_over_prob') is not None else [
            {'field': 'ou_over_odds/ou_under_odds', 'reason': '缺失'}],
    }
    # 4) 半场胜平负
    dims['half_1x2'] = _dimension_half_1x2(payload, direction)
    # 5) 半场大小球
    dims['half_ou'] = _dimension_half_ou(payload, step2)
    # 6) 节奏与盘口演变（含不可变因子审计）
    audit = immutable_factors_audit(payload)
    result['immutable_factors'] = audit
    dims['tempo'] = _dimension_tempo(payload, step2, audit)

    total = 0.0
    for key, label in CONSISTENCY_DIMENSIONS:
        dim = dims.get(key) or {'available': False, 'reason': '未采集'}
        weight = DIMENSION_WEIGHTS[key]
        if not dim.get('available'):
            dim['score'] = 0.0
            result['missing_dimensions'].append(key)
            result['conflicts'].append({
                'code': 'DIMENSION_MISSING',
                'dimension': key,
                'dimension_label': label,
                'detail': dim.get('reason') or '维度缺失，该维 0 分（不补分）',
            })
        elif dim.get('agree'):
            dim['score'] = weight
        else:
            dim['score'] = weight * 0.5
            result['conflicts'].append({
                'code': 'DIMENSION_CONFLICT',
                'dimension': key,
                'dimension_label': label,
                'detail': dim.get('detail') or '与 Step1/Step2 结论不一致',
            })
        # 不可变因子审计的 confidence 直接乘在 tempo 维度得分上（真的参与）。
        if key == 'tempo' and dim.get('available'):
            conf = _finite(dim.get('audit_confidence'))
            if conf is not None:
                dim['score'] = dim['score'] * max(0.0, min(1.0, conf))
                dim['audit_applied'] = round(conf, 6)
        dim['score'] = round(dim['score'], 4)
        dim['weight'] = weight
        total += dim['score']
        result['dimensions'][key] = dim
    result['total_score'] = round(total, 4)
    conf = _finite((audit or {}).get('confidence'))
    result['audit_factor'] = round(0.5 + 0.5 * max(0.0, min(1.0, conf)), 6) if conf is not None else 0.5
    for item in step4.get('conflicts') or []:
        result['conflicts'].append({
            'code': item.get('code'), 'dimension': 'linkage',
            'dimension_label': '联动', 'detail': item.get('detail'),
        })
    log.add('CONSISTENCY', 'SCORED', {
        'total_score': result['total_score'],
        'missing_dimensions': list(result['missing_dimensions']),
        'conflicts': len(result['conflicts']),
        'audit_factor': result['audit_factor'],
    })
    return result


# --------------------------------------------------------------------------- #
# Step 6：双比分锁定
# --------------------------------------------------------------------------- #
def _score_weight(
    row: dict[str, Any],
    step1: dict[str, Any],
    step2: dict[str, Any],
    step4: dict[str, Any],
    step5: dict[str, Any],
    macro: dict[str, Any],
) -> dict[str, Any]:
    """单个比分的综合权重分解。

    加法项（``WEIGHT_COMPONENTS`` 前五项）逐比分可复算：``theory`` / ``market_prob``
    是这一项自己的理论/市场概率（不是赛果桶级别的），市热/市短/联动是固定步长的
    修正。乘法项（Step4 降权 × 宏观 conf_mod × 审计因子 × 熔断）折成
    ``veto_scale`` 一个加法补项，因此 ``sum(components) == total`` 恒成立。
    """
    direction = step1['direction']
    theory_pct = _finite(row.get('theory'))
    market_pct = _finite(row.get('market'))
    missing_components: list[str] = []
    if theory_pct is None:
        missing_components.append('theory')
    if market_pct is None:
        missing_components.append('market')
    components: dict[str, float] = {
        'theory': round((theory_pct or 0.0) / 100.0, 6),
        'market_prob': round((market_pct or 0.0) / 100.0, 6),
        'heat': 0.0,
        'short': 0.0,
        'linkage': 0.0,
        'veto_scale': 0.0,
    }
    heat = step4.get('heat') or {}
    if heat.get('available'):
        components['heat'] = 0.01 if heat.get('dominant') == direction else -0.01
    short = step4.get('short') or {}
    if short.get('available'):
        components['short'] = 0.01 if short.get('dominant') == direction else -0.01
    components['linkage'] = 0.02 if step4.get('linkage') == '顺局' else -0.02
    base = sum(components[k] for k in WEIGHT_COMPONENTS if k != 'veto_scale')

    veto = str(macro.get('veto_action') or VETO_NONE) if macro.get('status') == 'ok' else VETO_NONE
    veto_factor = _VETO_WEIGHT_FACTOR.get(veto, 1.0)
    step4_factor = _finite(step4.get('step4_multiplier'))
    if step4_factor is None:
        step4_factor = 1.0
    audit_factor = _finite(step5.get('audit_factor'))
    if audit_factor is None:
        audit_factor = 1.0
    # conf_mod 已 clamp 到 [-0.5, +0.5]，所以 (1+conf_mod) ∈ [0.5, 1.5] 恒正。
    conf_mod = _finite(step4.get('macro_conf_mod'))
    if conf_mod is None:
        conf_mod = 0.0
    conf_factor = 1.0 + max(-0.5, min(0.5, conf_mod))
    if veto_factor == 0.0:
        total = 0.0
    else:
        total = base * step4_factor * conf_factor * veto_factor * audit_factor
    components['veto_scale'] = round(total - base, 6)
    out = {
        'score': row['score'],
        'bucket': row['bucket'],
        'is_aggregate': bool(row.get('is_aggregate')),
        'components': {k: round(v, 6) for k, v in components.items()},
        'base_weight': round(base, 6),
        'multipliers': {
            'step4': round(step4_factor, 6),
            'conf_mod': round(conf_factor, 6),
            'veto': veto_factor,
            'audit': round(audit_factor, 6),
        },
        'veto_action': veto,
        'missing_components': missing_components,
        'total': round(total, 6),
    }
    out['components_sum_check'] = round(sum(out['components'].values()), 6)
    return out


def step6_lock(
    step1: dict[str, Any],
    step2: dict[str, Any],
    step3: dict[str, Any],
    step4: dict[str, Any],
    step5: dict[str, Any],
    macro: dict[str, Any],
    log: AuditLog,
) -> dict[str, Any]:
    """Step 6：候选按综合权重排序，刚性输出前 2（同一方向，非对冲）。"""
    result: dict[str, Any] = {
        'step': 'LOCK',
        'status': 'ok',
        'reason': '',
        'locked_scores': [],
        'ranked': [],
        'tie_break': 'total desc -> market_prob desc -> score asc（确定性，可复现）',
        'missing': [],
        'invalid': [],
    }
    if any(x.get('status') != 'ok' for x in (step1, step2, step3, step4, step5)):
        result.update({'status': 'aborted', 'reason': _ERROR_TEXT['upstream_aborted']})
        return result
    candidates = [r for r in (step3.get('candidates') or []) if isinstance(r, dict)]
    if len(candidates) < 2:
        result['status'] = 'aborted'
        result['reason'] = f'{_ERROR_TEXT["step6_missing_inputs"]}（当前 {len(candidates)} 个）'
        result['missing'].append({
            'field': 'step3.candidates', 'reason': f'候选 {len(candidates)} < 2',
        })
        log.add('LOCK', 'STEP_ABORTED', {'candidates': len(candidates)})
        return result
    ranked = [_score_weight(row, step1, step2, step4, step5, macro) for row in candidates]
    ranked = [r for r in ranked if r['total'] > 0]
    if len(ranked) < 2:
        result['status'] = 'aborted'
        result['reason'] = f'宏观熔断后可用候选 {len(ranked)} 个 < 2'
        result['missing'].append({
            'field': 'macro.veto_action', 'reason': f'熔断后仅剩 {len(ranked)} 个候选',
        })
        log.add('LOCK', 'STEP_ABORTED', {'after_veto': len(ranked)})
        return result
    ranked.sort(key=lambda r: (-r['total'], -(r['components']['market_prob']), r['score']))
    result['ranked'] = ranked
    result['locked_scores'] = [ranked[0], ranked[1]]
    log.add('LOCK', 'LOCKED_TWO', {
        'primary': ranked[0]['score'], 'secondary': ranked[1]['score'],
        'primary_total': ranked[0]['total'], 'secondary_total': ranked[1]['total'],
    })
    return result


# --------------------------------------------------------------------------- #
# 滚球事件日志
# --------------------------------------------------------------------------- #
def live_event_log(payload: dict[str, Any], regular_time_only: bool = REGULAR_TIME_ONLY) -> dict[str, Any]:
    """滚球事件：时间戳 / 类型 / 变更前后状态；未接入真实源时 source=not_connected。"""
    raw = payload.get('live_events')
    out: dict[str, Any] = {
        'source': 'not_connected',
        'events': [],
        'excluded_count': 0,
        'excluded': [],
        'regular_time_only': bool(regular_time_only),
    }
    if not isinstance(raw, (list, tuple)) or not raw:
        return out
    out['source'] = str(payload.get('live_event_source') or 'provided')
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get('type') or '').strip().lower()
        minute = _finite(item.get('minute'))
        record = {
            'ts': str(item.get('ts') or ''),
            'minute': minute,
            'type': kind,
            'before': item.get('before'),
            'after': item.get('after'),
        }
        if kind not in LIVE_EVENT_TYPES:
            record['excluded_reason'] = f'未知事件类型 {kind!r}'
            out['excluded'].append(record)
            out['excluded_count'] += 1
            continue
        if regular_time_only and minute is not None and minute > REGULAR_TIME_MINUTES + STOPPAGE_MINUTES_MAX:
            record['excluded_reason'] = '超出常规时间+补时（加时/点球不计入）'
            out['excluded'].append(record)
            out['excluded_count'] += 1
            continue
        out['events'].append(record)
    return out


# --------------------------------------------------------------------------- #
# 入口：一次调用走完六步
# --------------------------------------------------------------------------- #
def clamp_conf_mod(value: Any) -> float:
    """宏观修正量 clamp 到 [-0.5, +0.5]（附加契约 §1.14）。非数值返回 0.0。"""
    number = _finite(value)
    if number is None:
        return 0.0
    return max(-0.5, min(0.5, number))


def assert_locked_contract(direction: str, locked: Any) -> list[dict[str, Any]]:
    """铁律 1 的结构性断言：恰 2 条、互不相同、同向（不跨 H/A、不对冲）。

    这是对 ``locked_scores`` 的最后一道闸门，独立于 Step6 的实现：
    任何上游 bug 让结果越界，都在这里抛 ``ConvergenceContractError``，
    绝不静默裁剪后当成成功。

    同向检查同时覆盖「禁止对冲」：H 方向里出现客胜比分即
    ``hedge_direction_in_locked``，反之亦然。
    """
    items = [item for item in (locked or []) if isinstance(item, dict)]
    if not items:
        return items
    if len(items) != 2:
        raise ConvergenceContractError('locked_scores_length', len(items))
    if items[0].get('score') == items[1].get('score'):
        raise ConvergenceContractError('locked_scores_identical', items[0].get('score'))
    expected_bucket = _BUCKET_OF_DIRECTION[direction]
    forbidden = {'H': 'away', 'A': 'home'}.get(direction, '')
    for item in items:
        bucket = _score_bucket(item.get('score'))
        if bucket != expected_bucket:
            code = (
                'hedge_direction_in_locked'
                if forbidden and bucket == forbidden
                else 'locked_score_against_direction'
            )
            raise ConvergenceContractError(code, item.get('score'))
    return items


def run_score_convergence(payload: Any = None, **kwargs: Any) -> dict[str, Any]:
    """六步收敛入口。

    ``payload`` 支持的键（全部先做存在性/有效性检查，缺什么报什么）：

    Step1   ``odds_1x2`` {home,draw,away}、``lam_home``、``lam_away``、
            ``direction_directive``（最优先档）、``abs_deltas`` / ``pool31_rows``
    Step2   ``ou_line``、``ou_over_odds``、``ou_under_odds``、可选 ``xg_home``/``xg_away``
    Step3   ``pool31_rows``（``build_pool31_option_rows`` 的产物）、
            ``pool31_extra_rows``、或 ``theo_mkt_31_pack`` / ``score_matrix``
    Step4   ``heat_tg_lines``、``mkt_over_theo_tg_lines``、``asian_handicap``、
            ``ou_line``、``heat_top8``、可选 ``odds_1x2_open``、
            ``league_rank_home``/``league_rank_away`` 或 ``elo_home``/``elo_away``
    Step5   ``half_time_1x2``、``half_time_ou_line``、``ou_open_line``、
            ``borsa_signals_count``、``immutable_factor_total``、
            ``immutable_factors_changed``
    附加    ``live_events``、``handicap_side``、``goal_expect``
    """
    data = dict(payload) if isinstance(payload, dict) else {}
    data.update(kwargs)

    # 31 项矩阵的唯一来源：显式 rows，或现成的 pack / score_matrix。
    if not data.get('pool31_rows'):
        pack = data.get('theo_mkt_31_pack') or data.get('score_matrix')
        if isinstance(pack, dict) and pack:
            matrix = score_matrix_from_pack(pack)
            if matrix['available']:
                data['pool31_rows'] = matrix['rows']
                data.setdefault('pool31_provenance', matrix['provenance'])
                data.setdefault('pool31_provenance_source', matrix['provenance_source'])
                data['pool31_matrix'] = matrix

    log = AuditLog()
    snapshot_inputs = _input_snapshot(data)

    step1 = step1_direction(data, log)
    step2 = step2_ou_path(data, step1, log)
    step3 = step3_candidate_tree(data, step1, step2, log)
    macro = macro_handicap_verdict(data, step1, log)
    micro = euro_micro_delta(data, step1)
    step4 = step4_linkage(data, step1, step3, macro, micro, log)
    step5 = step5_consistency(data, step1, step2, step4, log)
    step6 = step6_lock(step1, step2, step3, step4, step5, macro, log)

    steps = {
        'step1_direction': step1,
        'step2_ou_path': step2,
        'step3_candidate_tree': step3,
        'step4_linkage': step4,
        'step5_consistency': step5,
        'step6_lock': step6,
    }
    aborted = [name for name, step in steps.items() if step.get('status') != 'ok']
    direction = str(step1.get('direction') or '') if step1.get('status') == 'ok' else ''
    ou_path = str(step2.get('ou_path') or '') if step2.get('status') == 'ok' else ''
    locked_raw = step6.get('locked_scores') if not aborted else []
    locked: list[dict[str, Any]] = [
        item for item in (locked_raw or []) if isinstance(item, dict)
    ]
    # 铁律 1：没有 Step1/Step2 结论，或任一步 aborted，绝不产出 locked_scores。
    if aborted or not direction or not ou_path:
        locked = []
    if locked and direction:
        locked = assert_locked_contract(direction, locked)

    result: dict[str, Any] = {
        'available': bool(direction) and bool(ou_path) and len(locked) == 2,
        'status': 'ok' if not aborted else 'aborted',
        'direction': direction,
        'direction_cn': _DIRECTION_CN.get(direction, ''),
        'direction_source': step1.get('direction_source') or '',
        'ou_path': ou_path,
        'ou_path_cn': _OU_CN.get(ou_path, ''),
        'locked_scores': locked,
        'candidate_count': len(step3.get('candidates') or []),
        'consistency_score': step5.get('total_score'),
        'conflict_count': len(step5.get('conflicts') or []),
        'heat_source_path': step4.get('heat_source_path', 'none'),
        'short_source_path': step4.get('short_source_path', 'none'),
        'aborted_steps': aborted,
        'abort_reasons': {name: steps[name].get('reason') for name in aborted},
        'missing': _collect(('missing',), steps),
        'invalid': _collect(('invalid',), steps),
        'step1': step1,
        'step2': step2,
        'step3': step3,
        'step4': step4,
        'step5': step5,
        'step6': step6,
        'macro': macro,
        'micro': micro,
        'pool31_provenance': _pool31_provenance_payload(data),
        'live_events': live_event_log(data),
        'audit_logs': log.to_list(),
        'audit_snapshot': {
            'inputs': snapshot_inputs,
            'steps': {name: _step_summary(step) for name, step in steps.items()},
            'macro': {
                k: macro.get(k)
                for k in ('status', 'veto_action', 'conf_mod', 'tier_diff', 'tier_source')
            },
            'final': {
                'direction': direction,
                'direction_source': result_direction_source(step1),
                'ou_path': ou_path,
                'locked_scores': [item['score'] for item in locked],
                'available': bool(direction) and len(locked) == 2,
            },
            'regular_time_only': REGULAR_TIME_ONLY,
            'regular_time_minutes': REGULAR_TIME_MINUTES,
            'stoppage_minutes_max': STOPPAGE_MINUTES_MAX,
        },
    }
    return result


def result_direction_source(step1: dict[str, Any]) -> str:
    source = str((step1 or {}).get('direction_source') or '')
    return source if source in DIRECTION_SOURCES else ''


def _collect(keys: tuple[str, ...], steps: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, step in steps.items():
        for key in keys:
            for item in step.get(key) or []:
                entry = dict(item) if isinstance(item, dict) else {'reason': str(item)}
                entry['step'] = name
                out.append(entry)
    return out


def _step_summary(step: dict[str, Any]) -> dict[str, Any]:
    summary = {
        'status': step.get('status'),
        'reason': step.get('reason', ''),
    }
    for key in ('direction', 'direction_source', 'ou_path', 'candidate_count',
                'total_score', 'locked_scores', 'linkage', 'veto_action'):
        if key in step:
            value = step[key]
            if key == 'locked_scores':
                summary['locked_scores'] = [item['score'] for item in value or []]
            else:
                summary[key] = value
    return summary


def _input_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """输入快照：只留可复盘的最小集合，键名稳定。"""
    def _copy_dict(value: Any) -> Any:
        return dict(value) if isinstance(value, dict) else value

    def _copy_list(value: Any) -> Any:
        return list(value) if isinstance(value, (list, tuple)) else value

    return {
        'odds_1x2': _copy_dict(data.get('odds_1x2')),
        'odds_1x2_open': _copy_dict(data.get('odds_1x2_open')),
        'direction_directive': data.get('direction_directive'),
        'lam_home': data.get('lam_home'),
        'lam_away': data.get('lam_away'),
        'xg_home': data.get('xg_home'),
        'xg_away': data.get('xg_away'),
        'ou_line': data.get('ou_line'),
        'ou_open_line': data.get('ou_open_line'),
        'ou_over_odds': data.get('ou_over_odds'),
        'ou_under_odds': data.get('ou_under_odds'),
        'asian_handicap': data.get('asian_handicap'),
        'half_time_1x2': _copy_dict(data.get('half_time_1x2')),
        'half_time_ou_line': data.get('half_time_ou_line'),
        'heat_tg_lines': _copy_list(data.get('heat_tg_lines')),
        'mkt_over_theo_tg_lines': _copy_list(data.get('mkt_over_theo_tg_lines')),
        'heat_top8': _copy_list(data.get('heat_top8')),
        'league_rank_home': data.get('league_rank_home'),
        'league_rank_away': data.get('league_rank_away'),
        'elo_home': data.get('elo_home'),
        'elo_away': data.get('elo_away'),
        'borsa_signals_count': data.get('borsa_signals_count'),
        'immutable_factor_total': data.get('immutable_factor_total'),
        'immutable_factors_changed': _copy_list(data.get('immutable_factors_changed')),
        'pool31_rows_count': len(data.get('pool31_rows') or [])
        if isinstance(data.get('pool31_rows'), (list, tuple)) else None,
    }


def _pool31_provenance_payload(data: dict[str, Any]) -> dict[str, Any]:
    """31 项逐键出处：只透传调用方给的 recorded/derived，不在这里重推。"""
    provenance = data.get('pool31_provenance')
    source = str(data.get('pool31_provenance_source') or '')
    if not isinstance(provenance, dict):
        return {'provenance': {}, 'source': '', 'counts': {}}
    counts: dict[str, int] = {}
    for label in provenance.values():
        key = str(label or '')
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {'provenance': dict(provenance), 'source': source, 'counts': counts}


# --------------------------------------------------------------------------- #
# 报告/UI 渲染（纯文本；不 import streamlit）
# --------------------------------------------------------------------------- #
def format_convergence_tg_lines(result: Any) -> list[str]:
    """把结果渲染成 Telegram「六步收敛」段（逐字给出规范要求的六项）。"""
    res = result if isinstance(result, dict) else {}
    head = '🧭【六步收敛】'
    lines = [head]
    if not res:
        return [head, '  未形成（引擎未运行：结果为空）']
    direction = res.get('direction') or ''
    ou_path = res.get('ou_path') or ''
    direction_cn = res.get('direction_cn') or _DIRECTION_CN.get(direction, '')
    ou_cn = res.get('ou_path_cn') or _OU_CN.get(ou_path, '')
    source = res.get('direction_source') or ''
    aborted = res.get('aborted_steps') or []
    reasons = res.get('abort_reasons') or {}

    def _why(step_name: str) -> str:
        reason = reasons.get(step_name) or ''
        return f'（{step_name} 中止：{reason}）' if reason else f'（{step_name} 中止）'

    lines.append(
        f'  方向: {direction or ("未形成" + _why("step1_direction"))}'
        f'{("（" + direction_cn + "）") if direction_cn else ""}'
        f'{(" 来源=" + source) if source else ""}'
    )
    lines.append(
        f'  O/U 路径: {ou_path or ("未形成" + _why("step2_ou_path"))}'
        f'{("（" + ou_cn + "）") if ou_cn else ""}'
    )
    lines.append(f'  候选集大小: {res.get("candidate_count", 0)}/31')
    score = res.get('consistency_score')
    lines.append(
        f'  互洽得分: {score if score is not None else "未形成"}{"/100" if score is not None else ""}'
    )
    lines.append(f'  冲突明细条数: {res.get("conflict_count", 0)}')
    locked = res.get('locked_scores') or []
    if len(locked) == 2:
        first, second = locked
        lines.append(
            f'  锁定2个比分: 主选 {first.get("score")}（权重 {first.get("total")}）/ '
            f'次选 {second.get("score")}（权重 {second.get("total")}）'
        )
        for item in locked:
            comp = item.get('components') or {}
            detail = ' '.join(f'{k}={comp.get(k)}' for k in WEIGHT_COMPONENTS)
            lines.append(f'    {item.get("score")}: {detail} 合计={item.get("total")}')
        check = res.get('step4') or {}
        if (check.get('lowest_odds_check') or {}).get('conflict_code'):
            lines.append(f'  降权说明: {(check.get("lowest_odds_check") or {}).get("detail")}')
    else:
        lines.append(f'  锁定2个比分: 未形成{_why("step6_lock") if "step6_lock" in aborted else "（候选不足 2 个）"}')
    if aborted:
        for name in aborted:
            lines.append(f'  中止: {name} — {reasons.get(name) or "原因未记录"}')
    missing = res.get('missing') or []
    invalid = res.get('invalid') or []
    if missing:
        shown = '; '.join(
            f'{m.get("step")}.{m.get("field")}: {m.get("reason")}' for m in missing[:8]
        )
        more = f' 等{len(missing)}项' if len(missing) > 8 else ''
        lines.append(f'  缺失: {shown}{more}')
    if invalid:
        shown = '; '.join(
            f'{m.get("step")}.{m.get("field")}: {m.get("reason")}' for m in invalid[:8]
        )
        more = f' 等{len(invalid)}项' if len(invalid) > 8 else ''
        lines.append(f'  异常: {shown}{more}')
    conflicts = (res.get('step5') or {}).get('conflicts') or []
    if conflicts:
        lines.append(f'  冲突明细: {len(conflicts)} 条')
        for item in conflicts[:6]:
            label = item.get('dimension_label') or item.get('dimension')
            lines.append(f'    - {label}: {item.get("detail")}')
    return lines
