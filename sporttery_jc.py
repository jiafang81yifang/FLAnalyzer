"""Sporttery/Jingcai merge-table helpers."""
from __future__ import annotations

from typing import Any

_FIXED_CACHE: dict[str, Any] = {}


def get(key: Any, default: Any = None) -> Any:
    """Compatibility helper for module-level cache lookups."""
    return _FIXED_CACHE.get(str(key), default)


def _odds_value(value: Any) -> float | None:
    try:
        if isinstance(value, dict): value = value.get('odds') or value.get('sp') or value.get('value')
        f=float(value)
        return f if f > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_merged_market_heat_jc_table(heat: Any, jc: Any) -> dict[str, Any]:
    rows=[]
    if isinstance(heat, dict):
        for row in heat.get('ranking') or heat.get('rows') or []:
            if isinstance(row, dict): rows.append(dict(row))
    if isinstance(jc, dict):
        for score, val in jc.items():
            odd=_odds_value(val)
            if odd is None: continue
            found=False
            for row in rows:
                if str(row.get('score')).replace(':','-') == str(score).replace(':','-'):
                    row['jc_odds']=odd; found=True; break
            if not found:
                rows.append({'score': str(score).replace(':','-'), 'jc_odds': odd})
    return {'available': bool(rows), 'rows': rows, 'cross_table': rows}


def format_merged_heat_jc_tg_lines(table: Any) -> list[str]:
    t=table if isinstance(table, dict) else {}
    rows=t.get('rows') or []
    lines=['📊【市均***竞彩对比表】']
    if not rows:
        lines.append('  暂无可合并比分数据')
    else:
        for row in rows[:10]:
            if isinstance(row, dict): lines.append(f"  {row.get('score','?')} 市热={row.get('heat','-')} 竞彩={row.get('jc_odds','-')}")
    return lines
