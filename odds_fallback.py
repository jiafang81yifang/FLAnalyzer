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


def build_heat_from_odds_map(odds: Any, source: str = 'fallback_odds_map') -> dict[str, Any]:
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
    return {'available': bool(rows), 'source': source, 'ranking': rows, 'top_scores': [r['score'] for r in rows[:8]]}


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


def ensure_dual_source_before_judgment(rec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rec, dict):
        return {'available': False, 'judgment_ready': False}
    facts = rec.setdefault('dual_source_facts', {})
    if not isinstance(facts, dict):
        facts = {}; rec['dual_source_facts']=facts
    facts.setdefault('judgment_ready', bool(rec.get('prob_dict') or rec.get('odds_correct_score') or rec.get('correct_score_page_avg')))
    facts.setdefault('correct_score', {'mobile_entered': bool(rec.get('correct_score_page_avg') or rec.get('odds_correct_score'))})
    return facts
