"""Small convergence arbitration helpers."""
from __future__ import annotations
from typing import Any


def _parse(score: Any):
    try:
        h, a = str(score or '').replace(':', '-').split('-', 1)
        return int(h), int(a)
    except Exception:
        return None


def ou_from_scores(scores: Any, line: Any = None) -> str:
    vals=[]
    for s in scores or []:
        p=_parse(s)
        if p: vals.append(sum(p))
    if not vals:
        return ''
    avg=sum(vals)/len(vals)
    try: ln=float(line)
    except Exception: ln=2.5
    return '大' if avg > ln else ('小' if avg < ln else '走')


def resolve_direction(scores: Any, *args: Any, **kwargs: Any) -> str:
    counts={'胜':0,'平':0,'负':0}
    for s in scores or []:
        p=_parse(s)
        if not p: continue
        h,a=p
        counts['胜' if h>a else ('平' if h==a else '负')]+=1
    return max(counts, key=counts.get) if any(counts.values()) else ''


def resolve_cores(*args: Any, **kwargs: Any) -> dict[str, Any]:
    scores=[]
    for a in args:
        if isinstance(a, (list, tuple)):
            scores.extend(a)
    return {'available': True, 'direction': resolve_direction(scores), 'ou': ou_from_scores(scores)}
