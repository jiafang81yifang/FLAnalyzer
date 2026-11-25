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


def resolve_direction(scores: Any, *args: Any, **kwargs: Any) -> str:
    counts = {'胜': 0, '平': 0, '负': 0}
    for s in scores or []:
        parsed = _parse(s)
        if not parsed:
            continue
        h, a = parsed
        counts['胜' if h > a else ('平' if h == a else '负')] += 1
    return max(counts, key=lambda direction: counts[direction]) if any(counts.values()) else ''


def resolve_cores(*args: Any, **kwargs: Any) -> dict[str, Any]:
    scores = []
    for item in args:
        if isinstance(item, (list, tuple)):
            scores.extend(item)
    return {'available': True, 'direction': resolve_direction(scores), 'ou': ou_from_scores(scores)}
