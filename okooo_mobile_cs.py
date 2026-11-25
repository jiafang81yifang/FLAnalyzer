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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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
            # 4xx 不是网络抖动，重试只会更快被封，直接返回让上层如实报告。
            if 400 <= resp.status_code < 500:
                raise RuntimeError(last)
        except requests.RequestException as exc:
            last = f'{type(exc).__name__}: {str(exc)[:120]}'
        time.sleep(min(4, 2**attempt))
    raise RuntimeError(last or 'empty response')


_WARMUP_URL = 'https://www.okooo.com/'


def _warm_session(proxies: Any = None) -> Any:
    """建一个带站点 cookie 的会话。

    澳客对没有 cookie 的直连请求直接回 405（阿里云 WAF 拦截页），
    先用会话访问首页拿到 acw_tc/PHPSESSID，再抓详情页就正常。
    这就是「每次必失败」的真实原因：原实现用 requests 裸 get，从不带 cookie。
    """
    try:
        sess = requests.Session()
        sess.get(
            _WARMUP_URL,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            },
            proxies=_proxy_dict(proxies),
            timeout=15,
        )
        return sess
    except requests.RequestException:
        return requests.Session()


def _fetch_detail(url: str, proxies: Any = None, session: Any = None, retries: int = 1, timeout: int = 15) -> dict[str, Any]:
    """抓一个页面并把「实际发生了什么」原样带回来，供不可用原因使用。

    返回 {ok, url, status, bytes, title, blocked, error, html}。
    状态码/异常类/页面标题都进 reason，下一次日志就能直接判断是封禁、改版还是网络。

    被 WAF 拦住（405/403）时，用带首页 cookie 的会话重试一次——
    没 cookie 的裸请求在澳客是 100% 405，这不是站点不可用。
    """
    info: dict[str, Any] = {
        'ok': False, 'url': url, 'status': 0, 'bytes': 0,
        'title': '', 'blocked': False, 'error': '', 'html': '',
    }
    last_error = ''
    attempts = max(1, retries) * 2  # 每次尝试都留一次「warm session」重试
    active_session = session
    for attempt in range(attempts):
        try:
            sess = active_session or requests
            resp = sess.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Referer': _WARMUP_URL,
            }, proxies=_proxy_dict(proxies), timeout=timeout)
            info['status'] = int(resp.status_code)
            info['bytes'] = len(resp.content or b'')
            resp.encoding = getattr(resp, 'apparent_encoding', None) or 'utf-8'
            html = resp.text or ''
            info['html'] = html
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
            info['title'] = re.sub(r'\s+', ' ', title.group(1)).strip()[:80] if title else ''
            info['blocked'] = bool(
                resp.status_code in (403, 405)
                or 'your request has been blocked' in html.lower()
                or '滑动验证' in html
                or 'aliyuncaptcha' in html.lower()
            )
            info['ok'] = bool(resp.status_code == 200 and html)
            if info['ok']:
                info['error'] = ''
                return info
            last_error = f'HTTP {resp.status_code}'
            if info['blocked'] and active_session is None:
                # 裸请求被 WAF 拦：换带首页 cookie 的会话再来一次。
                active_session = _warm_session(proxies)
                continue
            if 400 <= resp.status_code < 500:
                break
        except requests.RequestException as exc:
            last_error = f'{type(exc).__name__}: {str(exc)[:100]}'
        except Exception as exc:  # noqa: BLE001 - 详情抓取的任何失败都只回报原因
            last_error = f'{type(exc).__name__}: {str(exc)[:100]}'
            break
        if attempt + 1 < attempts:
            time.sleep(min(3, 2**attempt))
    info['error'] = last_error or 'empty response'
    return info


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


_CS_SCORE_HEADERS = ['1:0', '2:0', '2:1', '3:0', '3:1', '3:2', '4:0', '4:1', '4:2', '4:3',
                     '0:0', '1:1', '2:2', '3:3', '4:4']
# 客胜行没有表头，格子与表头前 10 列一一对应，但比分要反过来读：
# 表头第 1 列「1:0」在客胜行表示客队 1:0 取胜 → '0:1'。第 11 列起是 rowspan 的平局列，客胜行没有。
_CS_AWAY_FROM_HOME = ['0:1', '0:2', '1:2', '0:3', '1:3', '2:3', '0:4', '1:4', '2:4', '3:4']


def _parse_cs_datatable(html: Any) -> dict[str, Any]:
    """解析澳客波胆页 table#datatable。

    真实结构（2026-11 现场确认）：一行表头 `<b>1:0</b>…<b>4:4</b>` 共15列，
    后面每个公司占两行（主胜行 / 客胜行），客胜行少一列（最后一列 rowspan 合并）。
    第16列开始是「1:1 … 4:4」的 rowspan=2 单元格，因此客胜行的格子数更少。

    返回 {page_avg, books_avg, books_n, row_count, header_count, stage}。
    解析不到就是 stage 指向具体卡在哪一步，不给假数据。
    """
    out: dict[str, Any] = {
        'page_avg': {}, 'books_avg': {}, 'books_n': 0,
        'row_count': 0, 'header_count': 0, 'stage': 'ok',
    }
    table = re.search(r'<table[^>]*id="datatable"[^>]*>([\s\S]*?)</table>', str(html or ''), re.IGNORECASE)
    if not table:
        out['stage'] = 'datatable_missing'
        return out
    body = table.group(1)
    header = re.search(r'<tr[^>]*class="titlebg"[^>]*>([\s\S]*?)</tr>', body, re.IGNORECASE)
    if not header:
        out['stage'] = 'header_row_missing'
        return out
    cols = re.findall(r'<b>\s*(\d+)\s*:\s*(\d+)\s*</b>', header.group(1))
    if not cols:
        out['stage'] = 'header_scores_missing'
        return out
    out['header_count'] = len(cols)
    headers = [f'{home}:{away}' for home, away in cols]

    def _num(text: Any) -> float | None:
        token = str(text or '').replace(',', '').replace('%', '').strip()
        if not token or token in {'-', '—', '--'}:
            return None
        try:
            value = float(token)
        except ValueError:
            return None
        return value if value > 1 else None

    avg: dict[str, list[float]] = {}
    books: dict[str, list[float]] = {}
    books_n = 0
    home_scores: dict[str, float | None] = {}
    pending_home_for = ''  # 'avg' | 'books' | ''
    # 客胜行没有自己的表头：它的格子按固定顺序对应客胜比分，与主页表头无关。
    away_order = list(_CS_AWAY_FROM_HOME)
    for raw_row in re.finditer(r'<tr([^>]*)>([\s\S]*?)</tr>', body, re.IGNORECASE):
        cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', raw_row.group(2), re.IGNORECASE)
        if not cells:
            continue
        plain = [
            re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', cell)).strip()
            for cell in cells
        ]
        if any('公司名' in text for text in plain):
            continue
        joined = ' '.join(plain)
        is_avg_row = '平均值' in joined
        is_border_row = ('最大值' in joined or '最小值' in joined)
        # 找这一行里「主胜 / 客胜」标记的位置
        side = ''
        side_at = -1
        for idx, text in enumerate(plain):
            if text in ('主胜', '客胜'):
                side, side_at = text, idx
                break
        # 位置必须保留：'-' 是一个「没有赔率」的占位，删掉会让整行错位。
        if side:
            slots = [_num(t) for t in plain[side_at + 1:]]
        else:
            slots = [_num(t) for t in plain[1:]]
            if not any(value is not None for value in slots):
                continue
        if is_border_row:
            continue
        if side == '主胜':
            # 主胜行决定这一对行写进哪张表；客胜行随后沿用 pending_home_for。
            home_target = avg if is_avg_row else books
            home_scores = {}
            for header, value in zip(headers, slots):
                home_scores[header] = value
                if value is not None:
                    home_target.setdefault(header, []).append(value)
            pending_home_for = 'avg' if is_avg_row else 'books'
            if not is_avg_row:
                books_n += 1
            continue
        if side == '客胜' and pending_home_for:
            # 客胜行自己不含「平均值」字样，目标表必须沿用主胜行的判定，
            # 否则客胜那一半的平均值会被当成公司数据混进 books。
            away_target = avg if pending_home_for == 'avg' else books
            merged = dict(home_scores)
            for header, value in zip(away_order, slots):
                merged[header] = value
            for header, value in merged.items():
                if value is None:
                    continue
                away_target.setdefault(header, []).append(value)
            if pending_home_for != 'avg':
                out['row_count'] += 1
            pending_home_for = ''
            continue
    if not books and not avg:
        out['stage'] = 'no_odds_rows'
        return out
    out['books_avg'] = {
        header: round(sum(values) / len(values), 4)
        for header, values in books.items() if values
    }
    out['page_avg'] = {
        header: round(sum(values) / len(values), 4)
        for header, values in avg.items() if values
    }
    if not out['page_avg']:
        # 没有「平均值」汇总行时，用已抓公司均值代替，并标明来源。
        out['page_avg'] = dict(out['books_avg'])
        out['stage'] = 'page_avg_from_books'
    out['books_n'] = books_n
    return out


def scrape_okooo_mobile_cs_zuizhi(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    """抓澳客波胆（比分）市均。

    只用真实存在且结构可解析的页面：桌面 `/soccer/match/<id>/cs/`。
    手机 `/jingcai/<id>/cs/` 在 2026-11 起返回 404，不再作为主入口。
    不可用时 reason 必须带 URL + 状态码/异常类 + 卡在哪个解析阶段。
    """
    html = kwargs.get('html') or kwargs.get('text')
    cache = _cache_path('cs', match_id)
    if not html and cache.exists():
        cached = _read_json_cache(cache)
        if cached is not None:
            return cached
    urls = []
    if kwargs.get('url'):
        urls = [str(kwargs.get('url'))]
    elif match_id:
        urls = [
            f'https://www.okooo.com/soccer/match/{match_id}/cs/',
            f'https://m.okooo.com/match/cs.php?MatchID={match_id}',
        ]
    fetch: dict[str, Any] = {}
    tried: list[str] = []
    for url in urls:
        fetch = _fetch_detail(url, proxies=proxies, session=session, retries=1)
        tried.append(
            f'{url} → '
            + (f'HTTP {fetch.get("status")}' if fetch.get('status') else str(fetch.get('error') or 'no-response'))
            + (f', title={fetch.get("title")}' if fetch.get('title') else '')
            + (', blocked' if fetch.get('blocked') else '')
            + ', stage=fetch_failed'
        )
        if fetch.get('ok'):
            html = fetch.get('html')
            break
    if not html:
        return _unavailable(
            'external_okooo_mobile_cs_fetch_failed',
            match_id=str(match_id or ''),
            error='; '.join(tried)[:300],
            fetch_urls=list(urls),
            parse_stage='fetch_failed',
            attempted=[{'url': t.split(' → ')[0], 'result': t.split(' → ')[-1]} for t in tried],
        )
    parsed = _parse_cs_datatable(html)
    stage = str(parsed.get('stage') or 'ok')
    if not parsed.get('books_avg'):
        return _unavailable(
            'okooo_mobile_cs_parse_empty',
            match_id=str(match_id or ''),
            error=(
                f'url={fetch.get("url") or (urls[0] if urls else "")} '
                f'status={fetch.get("status")} bytes={fetch.get("bytes")} '
                f'stage={stage} headers={parsed.get("header_count")} rows={parsed.get("row_count")} '
                f'title={fetch.get("title") or "-"}'
            ),
            fetch_urls=list(urls),
            parse_stage=stage,
        )
    heat = build_score_market_heat_ranking(
        parsed['page_avg'],
        page_avg_odds=parsed['page_avg'],
        heat_source='okooo_desktop_cs_page_avg',
    )
    heat.update({
        'available': True,
        'correct_score_available': True,
        'match_id': str(match_id or ''),
        'raw_count': len(parsed['page_avg']),
        'correct_score_page_avg': parsed['page_avg'],
        'correct_score_books_avg': parsed['books_avg'],
        'correct_score_books_n': parsed['books_n'],
        'correct_score_site_avg': parsed['page_avg'],
        'correct_score_channel': 'desktop_cs',
        'cs_fetch_url': str(fetch.get('url') or (urls[0] if urls else '')),
        'cs_fetch_status': int(fetch.get('status') or 0),
        'cs_fetch_bytes': int(fetch.get('bytes') or 0),
        'cs_parse_stage': stage,
        'pool31_forbidden': True,
    })
    try:
        cache.write_text(json.dumps(heat, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError as exc:
        print(f"   ⚠️ [澳客手机CS] 缓存写入跳过 {cache.name}: {type(exc).__name__}: {exc}")
    return heat


_AH_TEXT_TO_LINE = {
    '平手': 0.0, '平/半': 0.25, '平手/半球': 0.25, '半球': 0.5, '半/一': 0.75, '半球/一球': 0.75,
    '一球': 1.0, '一/球半': 1.25, '一球/球半': 1.25, '球半': 1.5, '球半/两球': 1.75,
    '两球': 2.0, '两球/两球半': 2.25, '两球半': 2.5, '两球半/三球': 2.75, '三球': 3.0,
}


def ah_text_to_line(text: Any) -> float | None:
    """把亚盘中文盘口（含「受」前缀）换成以主队为基准的有符号盘口。

    「受半/一」= 主队受让 0.75 → -0.75；「半/一」= 主队让 0.75 → +0.75。
    认不出的写法返回 None，绝不用猜测值凑数。
    """
    raw = str(text or '').strip()
    if not raw:
        return None
    receive = raw.startswith('受')
    body = raw[1:] if receive else raw
    line = _AH_TEXT_TO_LINE.get(body)
    if line is None:
        for key, value in _AH_TEXT_TO_LINE.items():
            if key and key in body:
                line = value
                break
    if line is None:
        return None
    return -line if receive else line


def _first_avg_index_row(html: Any) -> dict[str, Any]:
    """桌面亚指页的「平均指数」行：初盘/最新各三段（主水 盘口 客水）。"""
    out: dict[str, Any] = {'start_line_text': '', 'start_line': None,
                           'latest_line_text': '', 'latest_line': None,
                           'start_home': None, 'start_away': None,
                           'latest_home': None, 'latest_away': None}
    row = re.search(r'<tr[^>]*>\s*<td[^>]*>\s*<span>\s*平均指数\s*</span>\s*</td>([\s\S]{0,1500}?)</tr>', str(html or ''), re.IGNORECASE)
    if not row:
        return out
    cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row.group(1), re.IGNORECASE)
    if len(cells) < 2:
        return out
    start_cell, latest_cell = cells[0], cells[1]

    def _read(cell: str) -> tuple[float | None, str, float | None]:
        home = re.search(r'type="(?:xinzhu|zhu)"[^>]*>\s*([\d.]+)', cell)
        away = re.search(r'type="(?:xinke|ke)"[^>]*>\s*([\d.]+)', cell)
        line_text = re.search(r'<em[^>]*>\s*([^<]+?)\s*</em>', cell)
        def _f(m):
            try:
                return float(m.group(1)) if m else None
            except (TypeError, ValueError):
                return None
        return _f(home), (line_text.group(1).strip() if line_text else ''), _f(away)

    out['start_home'], out['start_line_text'], out['start_away'] = _read(start_cell)
    out['latest_home'], out['latest_line_text'], out['latest_away'] = _read(latest_cell)
    out['start_line'] = ah_text_to_line(out['start_line_text'])
    out['latest_line'] = ah_text_to_line(out['latest_line_text'])
    return out


def _first_1x2_row(html: Any) -> dict[str, Any]:
    """桌面欧指页：竞**方那一行的最新三赔（主/平/客）。"""
    out: dict[str, Any] = {'odds_1x2': {}, 'company': '', 'stage': 'ok'}
    book = re.search(
        r'<tr[^>]*>\s*<td class="width16">\s*<a>\s*竞[^<]*</a>\s*</td>([\s\S]{0,1200}?)</tr>',
        str(html or ''), re.IGNORECASE,
    )
    if not book:
        out['stage'] = 'company_row_missing'
        return out
    latest = re.findall(r'type="xin(sheng|ping|fu)"[^>]*>\s*([\d.]+)', book.group(1))
    mapping = {'sheng': 'home', 'ping': 'draw', 'fu': 'away'}
    odds = {mapping[key]: float(value) for key, value in latest if key in mapping}
    if len(odds) < 3:
        out['stage'] = 'latest_odds_incomplete'
        return out
    out['company'] = '竞彩官方'
    out['odds_1x2'] = {
        'home': odds['home'], 'draw': odds['draw'], 'away': odds['away'],
        'Home': odds['home'], 'Draw': odds['draw'], 'Away': odds['away'],
    }
    return out


def _first_ou_line(html: Any) -> dict[str, Any]:
    """桌面大小球页：均值行的盘口与大小水位。"""
    out: dict[str, Any] = {'ou_line': None, 'line_text': '', 'stage': 'ok'}
    text = str(html or '')
    if '数据加载中' in text and text.count('数据加载中') >= 1 and '均值' not in text:
        out['stage'] = 'page_shell_only_data_loaded_by_js'
        return out
    row = re.search(r'<tr[^>]*id="avgObj"[^>]*>([\s\S]{0,2500}?)</tr>', text, re.IGNORECASE)
    if not row:
        out['stage'] = 'avg_row_missing'
        return out
    cells = [
        re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', cell)).strip()
        for cell in re.findall(r'<td[^>]*>([\s\S]*?)</td>', row.group(1), re.IGNORECASE)
    ]
    for cell in cells:
        found = re.search(r'(\d+(?:\.\d+)?)\s*(?:/|、)\s*(\d+(?:\.\d+)?)', cell)
        if found:
            out['ou_line'] = round((float(found.group(1)) + float(found.group(2))) / 2, 3)
            out['line_text'] = found.group(0)
            return out
        found = re.search(r'\b([1-5]\.\d{1,2})\b', cell)
        if found:
            out['ou_line'] = float(found.group(1))
            out['line_text'] = found.group(1)
            return out
    out['stage'] = 'avg_row_has_no_line'
    return out


def scrape_okooo_mobile_six_markets(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    """六盘口水位：亚指 / 大小球 / 胜平负，只认能解析的真实页面。

    三块各自独立抓、独立记 `source_report`（URL、状态码、字节数、页面标题、解析阶段）。
    任何一块没抓到就进 `missing` 并带上原因；绝不回填盘口线、不写待定数字。
    """
    cache = _cache_path('six', match_id)
    if not kwargs.get('html') and not kwargs.get('text') and cache.exists():
        cached = _read_json_cache(cache)
        if cached is not None:
            return cached
    mid = str(match_id or '')
    pack: dict[str, Any] = {
        'available': False,
        'channel': 'mobile',
        'match_id': mid,
        'ou_line': None,
        'asian_handicap': None,
        'odds_1x2': {},
        'boards_hit': {},
        'missing': [],
        'source_report': {},
        'fetch_urls': [],
    }
    if not mid:
        pack['block_reason'] = 'no_match_id'
        pack['missing'] = ['asian_handicap', 'ou_line', 'odds_1x2']
        return pack

    plan = (
        ('ah', f'https://www.okooo.com/soccer/match/{mid}/ah/', 'asian_handicap'),
        ('ou', f'https://www.okooo.com/soccer/match/{mid}/overunder/', 'ou_line'),
        ('1x2', f'https://www.okooo.com/soccer/match/{mid}/odds/', 'odds_1x2'),
    )
    for kind, url, field in plan:
        page = _fetch_detail(url, proxies=proxies, session=session, retries=1)
        report = {
            'url': url,
            'status': int(page.get('status') or 0),
            'bytes': int(page.get('bytes') or 0),
            'title': str(page.get('title') or ''),
            'blocked': bool(page.get('blocked')),
            'error': str(page.get('error') or ''),
            'stage': '',
        }
        pack['fetch_urls'].append(url)
        if not page.get('ok'):
            report['stage'] = 'fetch_failed'
            pack['source_report'][kind] = report
            pack['missing'].append(field)
            continue
        html = page.get('html')
        if kind == 'ah':
            parsed = _first_avg_index_row(html)
            report['stage'] = 'ok' if parsed['latest_line'] is not None else 'avg_row_no_line'
            report['line_text'] = parsed['latest_line_text']
            if parsed['latest_line'] is not None:
                pack['asian_handicap'] = parsed['latest_line']
                pack['asian_handicap_text'] = parsed['latest_line_text']
                pack['ah_start_line'] = parsed['start_line']
                pack['ah_start_text'] = parsed['start_line_text']
                pack['ah_home_water'] = parsed['latest_home']
                pack['ah_away_water'] = parsed['latest_away']
                pack['boards_hit']['亚盘让球'] = True
            else:
                pack['missing'].append(field)
        elif kind == 'ou':
            parsed = _first_ou_line(html)
            report['stage'] = parsed['stage']
            report['line_text'] = parsed['line_text']
            if parsed['ou_line'] is not None:
                pack['ou_line'] = parsed['ou_line']
                pack['ou_line_text'] = parsed['line_text']
                pack['boards_hit']['大小球'] = True
            else:
                pack['missing'].append(field)
        else:
            parsed = _first_1x2_row(html)
            report['stage'] = parsed['stage']
            if parsed['odds_1x2']:
                pack['odds_1x2'] = parsed['odds_1x2']
                pack['odds_1x2_company'] = parsed['company']
                pack['boards_hit']['胜平负'] = True
            else:
                pack['missing'].append(field)
        pack['source_report'][kind] = report

    pack['available'] = bool(pack['boards_hit'])
    _hit_n = len(pack['boards_hit'])
    if _hit_n == 3:
        try:
            cache.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as exc:
            print(f"   ⚠️ [澳客手机CS] 六盘口缓存写入跳过 {cache.name}: {type(exc).__name__}: {exc}")
        return pack
    pack['block_reason'] = (
        'okooo_six_market_incomplete'
        if _hit_n else 'external_okooo_six_market_fetch_failed'
    )
    return pack


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
