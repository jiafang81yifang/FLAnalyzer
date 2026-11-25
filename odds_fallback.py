"""Fallback odds helpers.

Network scrapers return structured unavailable results unless the caller supplies
already-fetched data. This avoids missing-module failures without pretending that
external 500.com data exists.
"""
from __future__ import annotations

import os
from typing import Any


def odds_fallback_enabled() -> bool:
    return os.environ.get('ODDS_FALLBACK_DISABLED', '').lower() not in {'1','true','yes'}


def build_heat_from_odds_map(odds: Any, source: str = 'fallback_odds_map', channel: str = 'fallback') -> dict[str, Any]:
    rows=[]
    if isinstance(odds, dict):
        for score, odd in odds.items():
            try:
                raw_value = odd.get('odds') if isinstance(odd, dict) else odd
                if raw_value is None:
                    continue
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if value > 1:
                rows.append({'score': str(score).replace(':','-'), 'odds': value, 'heat': 1.0/value})
    rows.sort(key=lambda r: r['heat'], reverse=True)
    return {
        'available': bool(rows),
        'source': source,
        'heat_source': source,
        'channel': channel,
        'ranking': rows,
        'top_scores': [r['score'] for r in rows[:8]],
    }


def scrape_500_detail(fixture_id_500: Any, proxies: Any = None) -> dict[str, Any]:
    return {'available': False, 'source': '500_detail', 'reason': 'external_fetch_not_configured', 'fixture_id_500': str(fixture_id_500 or '')}


def apply_fallback_cs_to_rec(rec: dict[str, Any], m_obj: Any = None, **kwargs: Any) -> dict[str, Any]:
    odds = rec.get('odds_correct_score') if isinstance(rec, dict) else {}
    heat = build_heat_from_odds_map(odds, source='fallback_existing_odds')
    if isinstance(rec, dict):
        rec['fallback_cs_heat'] = heat
    return heat


def attach_500_shuju_to_rec(rec: dict[str, Any], force: bool = False) -> dict[str, Any]:
    pack={'available': False, 'reason': 'external_500_shuju_not_configured'}
    if isinstance(rec, dict): rec['fallback_500_shuju']=pack
    return pack


def ensure_dual_source_before_judgment(rec: dict[str, Any], extra_sources: Any = None) -> dict[str, Any]:
    """把手机市均与 500 兜底合成一个可审计的整合包。

    返回值里每个字段都有明确类型（不再出现 ready=None / reason=None）：
      ready              是否足够进研判
      mobile_cs_entered  手机市均是否真的进了整合包
      cs_books_n         手机市均公司数
      filled_from_500    从 500 补进来的槽位
      kept_mobile        保留的手机市均槽位
      reason             未就绪时的具体原因（就绪时为空串）
    """
    if not isinstance(rec, dict):
        return {
            'available': False, 'ready': False, 'mobile_cs_entered': False,
            'cs_books_n': 0, 'filled_from_500': [], 'kept_mobile': [],
            'reason': 'invalid_record',
        }
    facts = rec.setdefault('dual_source_facts', {})
    if not isinstance(facts, dict):
        facts = {}
        rec['dual_source_facts'] = facts
    if isinstance(extra_sources, dict):
        for key, value in extra_sources.items():
            facts[key] = value

    books_avg = rec.get('correct_score_books_avg')
    page_avg = rec.get('correct_score_page_avg')
    if not isinstance(books_avg, dict) or not books_avg:
        books_avg = page_avg if isinstance(page_avg, dict) else {}
    cs_books_n = len(books_avg) if isinstance(books_avg, dict) else 0
    mobile_channel = str(rec.get('correct_score_channel') or '').lower()
    mobile_entered = bool(cs_books_n and mobile_channel in {'mobile', 'desktop_cs'})

    fallback_500 = facts.get('fallback_500_fenxi') if isinstance(facts.get('fallback_500_fenxi'), dict) else {}
    filled_from_500: list[str] = []
    if isinstance(fallback_500, dict) and fallback_500.get('available'):
        for slot in ('ah_line', 'ou_line', 'euro_1x2'):
            if fallback_500.get(slot) and not rec.get(slot):
                filled_from_500.append(slot)

    kept_mobile: list[str] = []
    for slot, key in (('asian_handicap', 'asian_handicap'), ('ou_line', 'ou_line'), ('odds_1x2', 'odds_1x2')):
        if rec.get(key) not in (None, '', {}, []):
            kept_mobile.append(slot)

    cs_block = str(rec.get('cs_block_reason') or '').strip()
    reasons: list[str] = []
    if not mobile_entered:
        reasons.append(f'手机市均未进入整合包(cs_n={cs_books_n}, channel={mobile_channel or "-"})')
        if cs_block:
            reasons.append(f'cs_block={cs_block}')
    water_gaps = rec.get('water_gaps') if isinstance(rec.get('water_gaps'), list) else []
    if water_gaps:
        reasons.append('水位缺' + '/'.join(str(g) for g in water_gaps))
    ready = bool(mobile_entered) and not water_gaps
    facts['judgment_ready'] = ready
    facts['correct_score'] = {
        'mobile_entered': mobile_entered,
        'books_n': cs_books_n,
        'books_avg': dict(books_avg) if isinstance(books_avg, dict) else {},
    }
    for slot in filled_from_500:
        facts[f'filled_500_{slot}'] = fallback_500.get(slot)
    return {
        'available': True,
        'ready': ready,
        'mobile_cs_entered': mobile_entered,
        'cs_books_n': cs_books_n,
        'filled_from_500': filled_from_500,
        'kept_mobile': kept_mobile,
        'reason': '' if ready else '；'.join(reasons) or '数据不足且未给出原因',
    }
