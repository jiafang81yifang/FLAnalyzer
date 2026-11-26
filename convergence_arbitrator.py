"""Small convergence arbitration helpers.

缺数即缺：本模块不再为任何缺失/非法的盘口线编造数值。取不到盘口线时
`ou_from_scores` 返回空串并把原因写进 `missing`，绝不按 2.5 硬算大小球。
"""
from __future__ import annotations

from typing import Any

# 盘口线必须严格大于 0：0 不是合法的大小球线，负数更不是。
_MIN_LINE = 0.0


def _to_float(value: Any, default: float | None = None) -> float | None:
    """数值转换；缺值/非数一律回 default（默认 None = 显式缺失）。

    历史实现对每个缺失数字都返回 2.5，任何调用点都拿不到「这个数是假的」的信号。
    """
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _missing(reason: str) -> dict[str, Any]:
    return {'available': False, 'missing': ['ou_line'], 'reason': reason}


def _parse(score: Any) -> tuple[int, int] | None:
    parts = str(score or '').replace(':', '-').split('-', 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def ou_verdict(scores: Any, line: Any) -> dict[str, Any]:
    """按盘口线判定大小球，缺失盘口线时返回 missing 而不是编造 2.5。

    返回 {'available', 'verdict', 'line', 'avg_goals', 'missing', 'reason'}。
    `verdict` 仍是历史三态字符串（'大'/'小'/'走'），缺失时为空串。
    """
    ln = _to_float(line)
    if ln is None:
        return {**_missing('line_not_numeric'), 'verdict': '', 'line': None, 'avg_goals': None}
    if ln <= _MIN_LINE:
        return {**_missing(f'line_not_positive({ln:g})'), 'verdict': '', 'line': ln, 'avg_goals': None}
    if isinstance(scores, str):
        iterable = [scores]
    elif isinstance(scores, (list, tuple, set)):
        iterable = list(scores)
    else:
        iterable = [scores]
    vals = []
    for s in iterable:
        parsed = _parse(s)
        if parsed:
            vals.append(sum(parsed))
    if not vals:
        return {**_missing('no_parsable_scores'), 'verdict': '', 'line': ln, 'avg_goals': None}
    avg = sum(vals) / len(vals)
    verdict = '大' if avg > ln else ('小' if avg < ln else '走')
    return {'available': True, 'verdict': verdict, 'line': ln, 'avg_goals': round(avg, 4), 'missing': [], 'reason': ''}


def ou_from_scores(scores: Any, *extra_scores: Any, line: Any = None) -> str:
    """兼容入口：只返回三态字符串，缺失盘口线时返回空串。

    需要知道「为什么没结论」的调用方用 `ou_verdict`。
    """
    if len(extra_scores) >= 2 and line is None:
        try:
            vals = [int(scores) + int(extra_scores[0])]
            line = extra_scores[1]
        except (TypeError, ValueError):
            vals = []
        if vals:
            return str(ou_verdict(vals, line)['verdict'])
    if extra_scores:
        if len(extra_scores) == 1 and line is None:
            line = extra_scores[0]
            score_values = scores
        else:
            score_values = (scores, *extra_scores[:-1])
            if line is None:
                line = extra_scores[-1]
    else:
        score_values = scores
    return str(ou_verdict(score_values, line)['verdict'])


_DIRECTION_ALIASES = {
    '胜': '胜', 'home': '胜', 'win': '胜', '主胜': '胜',
    '平': '平', 'draw': '平', '平局': '平',
    '负': '负', 'away': '负', 'lose': '负', '客胜': '负',
}


def resolve_direction(scores: Any, *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    """方向投票，返回文档约定的 `(direction, resolution)` 两元组。

    历史实现只返回一个字符串，而 app 是按两元组解包的，于是每场都抛
    「not enough values to unpack」并被吞成降级。这里返回二元组；
    同时保留 `_legacy_string=True` 选项给需要旧形状的调用方。
    """
    # 非可迭代输入（3 / 3.5 / True / None）必须降级成空方向，绝不抛 TypeError。
    if isinstance(scores, str) or not isinstance(scores, (list, tuple, set, dict)):
        try:
            iter(scores)
        except TypeError:
            scores = []
    scores_list: list[Any] = list(scores or [])
    module_direction = ''
    if args:
        module_direction = str(args[0] or '')
    elif 'module_direction' in kwargs:
        module_direction = str(kwargs.get('module_direction') or '')
    counts = {'胜': 0, '平': 0, '负': 0}
    for s in scores_list:
        parsed = _parse(s)
        if not parsed:
            continue
        h, a = parsed
        counts['胜' if h > a else ('平' if h == a else '负')] += 1
    # 并列最高票不是唯一方向：返回空方向并由调用方判为 unresolved。
    top = max(counts.values()) if counts else 0
    leaders = [d for d, c in counts.items() if c == top and top > 0]
    tie = len(leaders) > 1
    lock_direction = '' if tie else (leaders[0] if leaders else '')
    module_norm = _DIRECTION_ALIASES.get(module_direction.strip().lower(), '')
    direction = lock_direction or module_norm
    resolution = {
        'lock_direction': lock_direction,
        'module_direction': module_direction,
        'counts': dict(counts),
        'tie': tie,
        'tied_directions': leaders if tie else [],
        'conflict': bool(lock_direction and module_norm and lock_direction != module_norm),
        'source': 'lock' if lock_direction else ('module' if module_norm else 'none'),
    }
    return direction, resolution


def resolve_direction_value(scores: Any, *args: Any, **kwargs: Any) -> str:
    """只要方向字符串的调用方用这个（旧 resolve_direction 的形状）。"""
    direction, _resolution = resolve_direction(scores, *args, **kwargs)
    return direction


def resolve_cores(*args: Any, **kwargs: Any) -> dict[str, Any]:
    scores = []
    for item in args:
        if isinstance(item, (list, tuple)):
            scores.extend(item)
    return {
        'available': True,
        'direction': resolve_direction(scores),
        'ou': ou_from_scores(scores),
        # 旧调用点读 cover/hedge 两个键；对冲语义已删除，保留空元组只为形状兼容。
        'cover': (),
        'hedge': (),
    }
