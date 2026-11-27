"""Small convergence arbitration helpers."""
from __future__ import annotations

from typing import Any


def _to_float(value: Any, default: float = 2.5) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse(score: Any) -> tuple[int, int] | None:
    parts = str(score or '').replace(':', '-').split('-', 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def ou_from_scores(scores: Any, *extra_scores: Any, line: Any = None) -> str:
    if len(extra_scores) >= 2 and line is None:
        try:
            vals = [int(scores) + int(extra_scores[0])]
            line = extra_scores[1]
        except (TypeError, ValueError):
            vals = []
        if vals:
            ln = _to_float(line)
            avg = sum(vals) / len(vals)
            return '大' if avg > ln else ('小' if avg < ln else '走')
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
    if isinstance(score_values, str):
        iterable = [score_values]
    elif isinstance(score_values, (list, tuple, set)):
        iterable = list(score_values)
    else:
        iterable = [score_values]
    vals = []
    for s in iterable:
        parsed = _parse(s)
        if parsed:
            vals.append(sum(parsed))
    if not vals:
        return ''
    avg = sum(vals) / len(vals)
    ln = _to_float(line)
    return '大' if avg > ln else ('小' if avg < ln else '走')


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
    lock_direction = max(counts, key=lambda d: counts[d]) if any(counts.values()) else ''
    module_norm = _DIRECTION_ALIASES.get(module_direction.strip().lower(), '')
    direction = lock_direction or module_norm
    resolution = {
        'lock_direction': lock_direction,
        'module_direction': module_direction,
        'counts': dict(counts),
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
    return {'available': True, 'direction': resolve_direction(scores), 'ou': ou_from_scores(scores)}
