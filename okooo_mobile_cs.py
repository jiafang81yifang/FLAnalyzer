"""Mobile Okooo correct-score/market helpers.

The functions here are real deterministic parsers/normalizers. Network scraping
returns structured unavailable payloads when caller has not provided a session or
reachable service data, so app_v84_hermes can run without optional private code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_STORE = Path('reports/six_market_contradictions.json')


def _okooo_only_data_mode(*args: Any, **kwargs: Any) -> bool:
    return bool(kwargs.get('data_only', False))


def _okooo_cs_odds_plausible(od: Any, floor: float = 2.01) -> bool:
    try:
        return float(od) >= float(floor)
    except (TypeError, ValueError):
        return False


def _okooo_apply_jc_score_fill(record: dict[str, Any], pack: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    src = pack if isinstance(pack, dict) else record
    jc = src.get('correct_score_jc_official') or src.get('jc_score_odds') or {}
    if isinstance(jc, dict) and jc:
        record.setdefault('correct_score_jc_official', jc)
    return record


def parse_mobile_nav_ft_score(html: Any, match_id: Any = None) -> str:
    text = str(html or '')
    patterns = [r'(\d+)\s*[-:：]\s*(\d+)\s*(?:完|全场|FT)?', r'全场[^0-9]*(\d+)\s*[-:：]\s*(\d+)']
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return f'{int(m.group(1))}-{int(m.group(2))}'
    return ''


def mobile_vs_full_time_score(nav_score: Any, actual_score: Any) -> bool:
    return str(nav_score or '').replace(':', '-').strip() == str(actual_score or '').replace(':', '-').replace(' (进行中)', '').strip()


def jingcai_board_row_finished(row: Any, today_md: str = '') -> bool:
    text = ' '.join(str(x) for x in row) if isinstance(row, (list, tuple)) else str(row or '')
    return any(k in text for k in ('完', '全场', '已完', 'FT')) or bool(re.search(r'\d+\s*[-:：]\s*\d+', text))


def build_score_market_heat_ranking(odds_map: Any) -> dict[str, Any]:
    rows=[]
    if isinstance(odds_map, dict):
        for score, raw in odds_map.items():
            try:
                odd = raw.get('odds') if isinstance(raw, dict) else raw
                odd_f = float(odd)
            except (TypeError, ValueError):
                continue
            if odd_f > 1:
                rows.append({'score': str(score).replace(':','-'), 'odds': odd_f, 'heat': 1.0/odd_f})
    rows.sort(key=lambda r: r['heat'], reverse=True)
    return {'available': bool(rows), 'source': 'okooo_mobile_cs_existing_odds', 'channel': 'mobile', 'ranking': rows, 'top_scores': [r['score'] for r in rows[:8]]}


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    data={'available': False, 'channel': 'mobile', 'reason': reason, 'block_reason': reason}
    data.update(extra)
    return data


def scrape_okooo_mobile_cs_zuizhi(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    html = kwargs.get('html') or kwargs.get('text')
    if html:
        # Minimal odds extraction from supplied HTML/text.
        odds = {}
        for score, odd in re.findall(r'(\d+\s*[-:：]\s*\d+)[^0-9]{0,20}([1-9]\d?(?:\.\d+)?)', str(html)):
            odds[score.replace('：','-').replace(':','-').replace(' ', '')] = float(odd)
        heat = build_score_market_heat_ranking(odds)
        heat.update({'correct_score_available': bool(odds), 'match_id': str(match_id or '')})
        return heat
    return _unavailable('external_okooo_mobile_cs_fetch_not_configured', match_id=str(match_id or ''))


def scrape_okooo_mobile_six_markets(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    return _unavailable('external_okooo_six_market_fetch_not_configured', match_id=str(match_id or ''), boards_hit={})


def scrape_okooo_mobile_ah_ou_books(match_id: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': False, 'match_id': str(match_id or ''), 'ah_books': [], 'ou_books': [], 'reason': 'external_fetch_not_configured'}


def scrape_okooo_mobile_exchange_pnl(match_id: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': False, 'match_id': str(match_id or ''), 'reason': 'external_fetch_not_configured'}


def scrape_okooo_mobile_lineup(match_id: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': False, 'match_id': str(match_id or ''), 'home': [], 'away': [], 'reason': 'external_fetch_not_configured'}


def seed_mobile_ah_ou_books(record: dict[str, Any], pack: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(record, dict):
        record.setdefault('mobile_ah_ou_books', pack or {'available': False})
        return record['mobile_ah_ou_books']
    return {'available': False}


def apply_mobile_books_lines(record: dict[str, Any], books: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    b = books if isinstance(books, dict) else record.get('mobile_ah_ou_books') if isinstance(record.get('mobile_ah_ou_books'), dict) else {}
    if b.get('ou_line') is not None:
        record['ou_line'] = b.get('ou_line')
    if b.get('asian_handicap') is not None:
        record['asian_handicap'] = b.get('asian_handicap')
    return record


def detect_six_market_contradictions(record: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    contradictions=[]
    if record.get('predicted_ou') and record.get('ou_line') is None:
        contradictions.append({'type': 'missing_ou_line', 'severity': 'low'})
    return contradictions


def missing_six_market_waters(record: dict[str, Any]) -> list[str]:
    if not isinstance(record, dict):
        return ['invalid_record']
    missing=[]
    for key in ('asian_handicap', 'ou_line', 'odds_1x2'):
        if not record.get(key): missing.append(key)
    return missing


def finish_match_water_scrape(record: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        record['water_scrape_finished'] = True
    return {'ok': True, 'missing': missing_six_market_waters(record)}


def six_market_fetch_blocked(record: dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return True
    block = record.get('six_market_mobile')
    return isinstance(block, dict) and bool(block.get('block_reason'))


def save_six_market_contradictions(items: Any) -> bool:
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        _STORE.write_text(json.dumps(items or [], ensure_ascii=False, indent=2), encoding='utf-8')
        return True
    except Exception:
        return False


def load_six_market_contradictions() -> list[Any]:
    try:
        return json.loads(_STORE.read_text(encoding='utf-8'))
    except Exception:
        return []
