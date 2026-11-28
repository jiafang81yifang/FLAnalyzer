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


_BONUS_META_KEYS = frozenset({
    'available', 'match_id', 'home', 'away', 'latest_odds', 'opening_odds',
    'source', 'source_note', 'rows', 'cross_table',
})

#: 比分键的形状：`1-0` / `1:0` / `胜其它`。合并表的竞彩侧只认这些键。
_SCORE_KEY_RE = None


def _score_key_re():
    global _SCORE_KEY_RE
    if _SCORE_KEY_RE is None:
        import re as _re
        _SCORE_KEY_RE = _re.compile(r'^(?:\d+-\d+|(?:胜|平|负)其[它他])$')
    return _SCORE_KEY_RE


def _jc_odds_map(jc: Any) -> dict[str, float]:
    """从两种真实形状里取出「比分 → 竞彩赔率」。

    生产里竞彩侧传进来的是 `_fixed_bonus_from_okooo_jc_official` 的产物
    （`{'available', 'match_id', 'home', 'away', 'latest_odds', 'opening_odds',
    'source', ...}`），只有 `latest_odds` 才是比分→赔率。把整只包当映射遍历，
    `available` / `match_id` 会被 `float()` 成 1.0 和 1338958.0，然后作为
    「比分」印进合并表——正文里因此出现 `available 市热=- 竞彩=1.0`。
    这里按形状分派：有 `latest_odds` 就只读它，否则才当作裸映射。
    """
    if not isinstance(jc, dict):
        return {}
    inner = jc.get('latest_odds')
    source = inner if isinstance(inner, dict) else jc
    out: dict[str, float] = {}
    for score, value in source.items():
        if not isinstance(inner, dict) and str(score) in _BONUS_META_KEYS:
            continue
        key = str(score or '').replace(':', '-').replace('：', '-').strip()
        if not _score_key_re().match(key):
            continue
        odd = _odds_value(value)
        if odd is not None:
            out[key] = odd
    return out


def build_merged_market_heat_jc_table(heat: Any, jc: Any) -> dict[str, Any]:
    rows=[]
    if isinstance(heat, dict):
        for row in heat.get('ranking') or heat.get('rows') or []:
            if isinstance(row, dict): rows.append(dict(row))
    for score, odd in _jc_odds_map(jc).items():
        found=False
        for row in rows:
            if str(row.get('score')).replace(':','-') == score:
                row['jc_odds']=odd; found=True; break
        if not found:
            rows.append({'score': score, 'jc_odds': odd})
    return {'available': bool(rows), 'rows': rows, 'cross_table': rows}


def format_merged_heat_jc_tg_lines(table: Any) -> list[str]:
    """市均×竞彩合并表（段名保留用户指定的 `市均***竞彩对比表`）。

    **段名不改**：`📊【市均***竞彩对比表】` 里的三个半角星号是用户指定的
    分隔符，不是脱敏残留，也不是 `31项市均×竞彩对比表` 那一节的 `×`。
    这两段是不同的小节，各自的标题字符都必须原样保留。

    本函数只做两件事，都不触碰段名：
      * 没有可合并的行时写一句中文说明，不再只印英文状态键；
      * 行内 `heat` 可能是市热榜单的整行 dict（带 `available`/`match_id`
        等英文字段）。这类行**只印真正有值的比分/热度/竞彩三列**：把 dict
        原样格式化出来就是 `available 市热=- 竞彩=1.0` 这种既非中文也非
        数据的噪声，那才是要修的英文泄漏。
    """
    t=table if isinstance(table, dict) else {}
    rows=t.get('rows') or []
    lines=['📊【市均***竞彩对比表】']
    if not rows:
        lines.append('  暂无可以合并的比分数据（市均榜与竞彩比分赔率都不为空时才成表）')
    else:
        for row in rows[:10]:
            if not isinstance(row, dict):
                continue
            score = _merged_score_text(row.get('score'))
            if not score:
                continue
            lines.append(
                f"  {score} 市热={_merged_scalar(row.get('heat'))} 竞彩={_merged_scalar(row.get('jc_odds'))}"
            )
    return lines


def _merged_score_text(score: Any) -> str:
    """比对表第一列：只要像比分的键。`available`/`match_id` 这类非比分键不印。"""
    text = str(score or '').strip().replace(':', '-').replace('：', '-')
    if not text or text in ('-', 'None'):
        return ''
    return text


def _merged_scalar(value: Any) -> str:
    """一个格子的显示值：数字原样、字典里取热度/赔率真值、缺就写「-」。

    字典形状来自市热榜单行（`{'score', 'odds', 'heat', ...}`）。取不到
    单一数字时写「-」，绝不把整只 dict 的键名印进正文。
    """
    if value is None or value == '':
        return '-'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{value:g}'
    if isinstance(value, dict):
        for key in ('heat', 'value', 'odds', 'pct', 'share_pct'):
            inner = value.get(key)
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return f'{inner:g}'
        return '-'
    text = str(value).strip()
    return text[:20] or '-'
