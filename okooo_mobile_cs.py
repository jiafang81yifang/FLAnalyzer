"""Mobile Okooo correct-score/market helpers.

The functions here are real deterministic parsers/normalizers. Network scraping
returns structured unavailable payloads when caller has not provided a session or
reachable service data, so app_v84_hermes can run without optional private code.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

_STORE = Path('reports/six_market_contradictions.json')


# ---- HTTP/cache helpers ----
def _cache_dir() -> Path:
    d = Path(os.environ.get('OKOOO_CACHE_DIR', 'reports/okooo_cache'))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(value: Any) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value or 'unknown'))


def _cache_path(kind: str, match_id: Any) -> Path:
    return _cache_dir() / f'{kind}_{_safe_id(match_id)}.json'


def _proxy_dict(proxies: Any = None) -> dict[str, str] | None:
    if isinstance(proxies, dict):
        return {k: v for k, v in proxies.items() if isinstance(v, str) and v}
    proxy = str(
        proxies
        or os.environ.get('OKOOO_PROXY')
        or os.environ.get('HTTPS_PROXY')
        or os.environ.get('HTTP_PROXY')
        or ''
    ).strip()
    return {'http': proxy, 'https': proxy} if proxy else None


def _http_get(url: str, proxies: Any = None, session: Any = None, retries: int = 2, timeout: int = 12) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Mobile Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    last = ''
    for attempt in range(max(1, retries)):
        try:
            sess = session or requests
            resp = sess.get(url, headers=headers, proxies=_proxy_dict(proxies), timeout=timeout)
            if resp.status_code == 200 and resp.text:
                resp.encoding = getattr(resp, 'apparent_encoding', None) or 'utf-8'
                return resp.text
            last = f'HTTP {resp.status_code}'
        except requests.RequestException as exc:
            last = f'{type(exc).__name__}: {str(exc)[:120]}'
        time.sleep(min(4, 2**attempt))
    raise RuntimeError(last or 'empty response')


def _read_json_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"   ⚠️ [澳客手机CS] 缓存读取跳过 {path.name}: {type(exc).__name__}")
        return None
    return data if isinstance(data, dict) else None


def _parse_score_odds_from_text(text: str) -> dict[str, float]:
    odds = {}
    # Handles compact HTML/text containing score followed by decimal odds.
    for score, odd in re.findall(r'(?:>|\b)(\d+\s*[-:：]\s*\d+)(?:<|\b)[^\d]{0,80}([1-9]\d?(?:\.\d+)?)', text):
        try:
            odds[score.replace('：', '-').replace(':', '-').replace(' ', '')] = float(odd)
        except ValueError:
            continue
    return odds


def _okooo_only_data_mode(*args: Any, **kwargs: Any) -> bool:
    return bool(kwargs.get('data_only', False))


def _okooo_cs_odds_plausible(od: Any, floor: float = 2.01) -> bool:
    try:
        return float(od) >= float(floor)
    except (TypeError, ValueError):
        return False


def _okooo_apply_jc_score_fill(
    record: dict[str, Any],
    pack: dict[str, Any] | None = None,
    source_note: str = '',
    overwrite: bool = False,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    src = pack if isinstance(pack, dict) else record
    jc = src.get('correct_score_jc_official') or src.get('jc_score_odds') or {}
    if isinstance(jc, dict) and jc:
        if overwrite or not record.get('correct_score_jc_official'):
            record['correct_score_jc_official'] = jc
        if source_note:
            record['correct_score_jc_source_note'] = source_note
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


def _looks_like_final_score(text: Any) -> bool:
    match = re.fullmatch(r'\s*(\d{1,2})\s*[-:：]\s*(\d{1,2})\s*', str(text or ''))
    if not match:
        return False
    left, right = int(match.group(1)), int(match.group(2))
    return 0 <= left <= 15 and 0 <= right <= 15


def jingcai_board_row_finished(
    row: Any = None,
    today_md: str = '',
    *,
    day_closed: bool = False,
    end: Any = None,
    inside_end_hide: bool = False,
) -> bool:
    if day_closed or inside_end_hide:
        return True
    end_text = str(end or '').strip().lower()
    if end_text in {'1', 'true', 'yes', 'y', 'end', 'ended', 'closed', 'finish', 'finished', 'done', '完', '已完', '全场'}:
        return True
    if end_text and _looks_like_final_score(end_text):
        return True
    try:
        zvs = row.select_one('.zVS') if row is not None and hasattr(row, 'select_one') else None
        zvs_text = zvs.get_text(strip=True) if zvs is not None else ''
        if _looks_like_final_score(zvs_text):
            return True
    except (AttributeError, TypeError):
        pass
    text = ' '.join(str(x) for x in row) if isinstance(row, (list, tuple)) else str(row or '')
    return any(k in text for k in ('完', '全场', '已完', 'FT')) or _looks_like_final_score(text)


def build_score_market_heat_ranking(
    odds_map: Any = None,
    correct_score_rows: Any = None,
    pool_enriched: Any = None,
    page_avg_odds: Any = None,
    heat_source: str = 'okooo_mobile_cs_existing_odds',
    channel: str = 'mobile',
) -> dict[str, Any]:
    rows = []
    odds_sources: list[tuple[str, Any]] = []
    if isinstance(odds_map, dict):
        odds_sources.extend((str(score), raw) for score, raw in odds_map.items())
    if isinstance(page_avg_odds, dict):
        odds_sources.extend((str(score), raw) for score, raw in page_avg_odds.items())
    if isinstance(correct_score_rows, dict):
        odds_sources.extend((str(score), raw) for score, raw in correct_score_rows.items())
    elif isinstance(correct_score_rows, (list, tuple)):
        for item in correct_score_rows:
            if isinstance(item, dict):
                score = item.get('score') or item.get('name') or item.get('cs')
                odds_sources.append((str(score or ''), item))
            elif isinstance(item, (list, tuple)) and item:
                odds_sources.append((str(item[0]), item[1] if len(item) > 1 else None))
            else:
                odds_sources.append((str(item), None))
    enriched_by_score: dict[str, Any] = {}
    if isinstance(pool_enriched, dict):
        enriched_by_score = {str(k).replace(':', '-'): v for k, v in pool_enriched.items()}
    elif isinstance(pool_enriched, (list, tuple)):
        for item in pool_enriched:
            if isinstance(item, dict):
                score = str(item.get('score') or item.get('name') or item.get('cs') or '').replace(':', '-')
                if score:
                    enriched_by_score[score] = item
    seen: set[str] = set()
    for score, raw in odds_sources:
        score_key = str(score or '').replace(':', '-').strip()
        if not score_key or score_key.lower() == 'none' or score_key in seen:
            continue
        seen.add(score_key)
        try:
            odd = raw.get('odds') or raw.get('avg') or raw.get('value') if isinstance(raw, dict) else raw
            if odd is None and score_key in enriched_by_score and isinstance(enriched_by_score[score_key], dict):
                odd = enriched_by_score[score_key].get('odds') or enriched_by_score[score_key].get('avg')
            odd_f = float(odd) if odd is not None else 0.0
        except (TypeError, ValueError):
            odd_f = 0.0
        enriched = enriched_by_score.get(score_key)
        row = {'score': score_key, 'odds': odd_f, 'heat': (1.0 / odd_f if odd_f > 1 else 0.0)}
        if isinstance(enriched, dict):
            row.update({k: v for k, v in enriched.items() if k not in row})
        rows.append(row)
    rows.sort(key=lambda r: r['heat'], reverse=True)
    return {
        'available': bool(rows),
        'source': heat_source,
        'heat_source': heat_source,
        'channel': channel,
        'ranking': rows,
        'top_scores': [r['score'] for r in rows[:8]],
    }


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    data = {'available': False, 'channel': 'mobile', 'reason': reason, 'block_reason': reason}
    data.update(extra)
    return data


def scrape_okooo_mobile_cs_zuizhi(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    html = kwargs.get('html') or kwargs.get('text')
    cache = _cache_path('cs', match_id)
    if not html and cache.exists():
        cached = _read_json_cache(cache)
        if cached is not None:
            return cached
    if not html and match_id:
        url = kwargs.get('url') or f'https://www.okooo.com/jingcai/{match_id}/cs/'
        try:
            html = _http_get(str(url), proxies=proxies, session=session)
        except RuntimeError as exc:
            return _unavailable('external_okooo_mobile_cs_fetch_failed', match_id=str(match_id or ''), error=str(exc)[:160])
    odds = _parse_score_odds_from_text(str(html or ''))
    heat = build_score_market_heat_ranking(odds)
    heat.update({'correct_score_available': bool(odds), 'match_id': str(match_id or ''), 'raw_count': len(odds)})
    if odds:
        cache.write_text(json.dumps(heat, ensure_ascii=False, indent=2), encoding='utf-8')
    return heat if odds else _unavailable('okooo_mobile_cs_no_score_odds_found', match_id=str(match_id or ''))


def scrape_okooo_mobile_six_markets(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    cache = _cache_path('six', match_id)
    if cache.exists():
        cached = _read_json_cache(cache)
        if cached is not None:
            return cached
    html = kwargs.get('html') or kwargs.get('text')
    if not html and match_id:
        try:
            html = _http_get(str(kwargs.get('url') or f'https://www.okooo.com/jingcai/{match_id}/'), proxies=proxies, session=session)
        except RuntimeError as exc:
            return _unavailable('external_okooo_six_market_fetch_failed', match_id=str(match_id or ''), error=str(exc)[:160], boards_hit={})
    text = str(html or '')
    ou_match = re.search(r'(?:大小|总进球|OU)[^0-9]{0,20}(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    ah_match = re.search(r'(?:让球|亚盘|Handicap)[^+\-0-9]{0,20}([+\-]?\d+(?:\.\d+)?)', text, re.IGNORECASE)
    pack = {
        'available': bool(ou_match or ah_match),
        'channel': 'mobile',
        'match_id': str(match_id or ''),
        'ou_line': float(ou_match.group(1)) if ou_match else None,
        'asian_handicap': float(ah_match.group(1)) if ah_match else None,
        'boards_hit': {'大小球': bool(ou_match), '亚盘让球': bool(ah_match)},
    }
    if pack['available']:
        cache.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding='utf-8')
        return pack
    return _unavailable('okooo_six_market_no_lines_found', match_id=str(match_id or ''), boards_hit={})


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
    raw_books = record.get('mobile_ah_ou_books')
    b = books if isinstance(books, dict) else raw_books if isinstance(raw_books, dict) else {}
    if b.get('ou_line') is not None:
        record['ou_line'] = b.get('ou_line')
    if b.get('asian_handicap') is not None:
        record['asian_handicap'] = b.get('asian_handicap')
    return record


def detect_six_market_contradictions(record: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    contradictions = []
    if record.get('predicted_ou') and record.get('ou_line') is None:
        contradictions.append({'type': 'missing_ou_line', 'severity': 'low'})
    return contradictions


def missing_six_market_waters(record: dict[str, Any]) -> list[str]:
    if not isinstance(record, dict):
        return ['invalid_record']
    missing = []
    for key in ('asian_handicap', 'ou_line', 'odds_1x2'):
        if not record.get(key):
            missing.append(key)
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


def _contradiction_store(match_id: Any = None) -> Path:
    if match_id in (None, ''):
        return _STORE
    return _STORE.with_name(f'six_market_contradictions_{_safe_id(match_id)}.json')


def save_six_market_contradictions(match_id_or_items: Any, items: Any = None) -> bool:
    match_id = None if items is None else match_id_or_items
    payload = match_id_or_items if items is None else items
    path = _contradiction_store(match_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload or [], ensure_ascii=False, indent=2), encoding='utf-8')
        return True
    except OSError as exc:
        print(f"   ⚠️ [澳客手机CS] 保存对账缺口失败: {type(exc).__name__}")
        return False


def load_six_market_contradictions(match_id: Any = None) -> list[Any]:
    """读取某场的对账缺口文件。

    文件不存在是正常空状态（本场还没写过缺口），不是错误：不打印告警，
    直接返回空列表。只有真的读不动（权限/JSON 损坏）才留一行提示。
    """
    path = _contradiction_store(match_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        # 探测与读取之间被删掉：同样是空状态，不吵。
        return []
    except json.JSONDecodeError as exc:
        print(f"   ℹ️ [澳客手机CS] 对账缺口文件损坏，按空处理: {path.name} ({exc.msg})")
        return []
    except OSError as exc:
        print(f"   ℹ️ [澳客手机CS] 对账缺口文件不可读，按空处理: {path.name} ({type(exc).__name__})")
        return []
    return data if isinstance(data, list) else []
