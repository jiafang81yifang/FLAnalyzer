"""Odds/index math helpers used by app_v84_hermes."""
from __future__ import annotations

import math
from typing import Any


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def normalize_return_rate(value: Any) -> float | None:
    n = _f(value)
    if n is None:
        return None
    return n / 100.0 if n > 1.5 else n


def return_rate_pct(value: Any) -> float | None:
    n = normalize_return_rate(value)
    return None if n is None else n * 100.0


def calc_return_rate(*odds: Any) -> float | None:
    vals = []
    for odd in odds:
        value = _f(odd)
        if value is not None and value > 1:
            vals.append(value)
    if not vals:
        return None
    inv = sum(1.0 / v for v in vals)
    return (1.0 / inv) if inv > 0 else None


def calc_instant_probs(win: Any, draw: Any, lose: Any) -> dict[str, Any]:
    win_f = _f(win)
    draw_f = _f(draw)
    lose_f = _f(lose)
    if win_f is None or draw_f is None or lose_f is None or win_f <= 1 or draw_f <= 1 or lose_f <= 1:
        return {'valid': False, 'home': None, 'draw': None, 'away': None, 'return_rate': None}
    inv_sum = (1.0 / win_f) + (1.0 / draw_f) + (1.0 / lose_f)
    rr = 1.0 / inv_sum if inv_sum > 0 else None
    return {
        'valid': rr is not None,
        'home': (rr / win_f) if rr else None,
        'draw': (rr / draw_f) if rr else None,
        'away': (rr / lose_f) if rr else None,
        'return_rate': rr,
        'return_rate_pct': rr * 100.0 if rr else None,
    }


def calc_kelly_index(prob: Any, odds: Any) -> float | None:
    p = _f(prob)
    o = _f(odds)
    if p is None or o is None or o <= 1:
        return None
    return p * o


def apply_six_cs_settlement_v2_to_rec(rec: dict[str, Any]) -> dict[str, Any]:
    """六盘口×市均清算 v2。

    只有真的拿到输入才算 available。空记录曾返回 available=True 且每个字段都是
    None（日志里 `status=None ah=None ou=None cs=None cos=None` 还打了 ✅），
    属于「对空输入宣称成功」。现在缺什么就在 block_reason 里点名。
    """
    if not isinstance(rec, dict):
        return {'available': False, 'reason': 'invalid_rec', 'block_reason': 'invalid_rec'}
    ou_line = rec.get('ou_line')
    asian_handicap = rec.get('asian_handicap') or rec.get('ah_line')
    cs_books = rec.get('correct_score_page_avg') or rec.get('correct_score_books_avg')
    cs_available = bool(isinstance(cs_books, dict) and cs_books) or bool(
        isinstance(rec.get('odds_correct_score'), dict) and rec.get('odds_correct_score')
    )
    missing = []
    if ou_line in (None, ''):
        missing.append('ou_line')
    if asian_handicap in (None, ''):
        missing.append('asian_handicap')
    if not cs_available:
        missing.append('correct_score')
    board: dict[str, Any] = {
        'available': not missing,
        'ou_line': ou_line,
        'asian_handicap': asian_handicap,
        'correct_score_available': cs_available,
        'cs_market_count': len(cs_books) if isinstance(cs_books, dict) else 0,
    }
    if missing:
        board['block_reason'] = 'missing_inputs:' + '/'.join(missing)
    rec['six_cs_settlement_v2'] = board
    return board
