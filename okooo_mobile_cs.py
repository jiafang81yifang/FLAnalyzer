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
_MOBILE_WARMUP_URL = 'https://m.okooo.com/jczq/'

_DESKTOP_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)
# 手机页只认手机 UA：同一个 URL 用桌面 UA 会被 WAF 挡成 403/405。
_MOBILE_UA = (
    'Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36'
)


def decode_okooo_html(raw: Any, headers: Any = None) -> str:
    """把澳客页面字节解成文本。中文选择器全靠这一步，解错就什么都匹配不到。

    真实情况（2026-11-25 现场核对）：
      * 桌面页与多数手机页：`<meta charset="GBK">`，body 是 GBK。
      * 手机大小球页 `ah.php?type=daxiao`：**UTF-8**，而且整页是
        `\\uXXXX` 转义，没有任何 charset 声明。
    所以顺序是：meta charset → 严格 UTF-8 → gb18030 → 宽松 UTF-8，
    并且把 `\\uXXXX` 反转义。绝不用「先 gb18030 不行再换」这种猜测顺序，
    因为 GBK 字节流常常也能被 UTF-8 之外的编码解出半对半错的乱码。
    """
    if isinstance(raw, str):
        text = raw
    else:
        data = bytes(raw or b'')
        if not data:
            return ''
        head = data[:4096].lower()
        meta = re.search(rb'charset\s*=\s*["\']?\s*([a-z0-9_\-]+)', head)
        candidates: list[str] = []
        if meta:
            declared = meta.group(1).decode('ascii', 'ignore').strip().lower()
            if declared in {'gbk', 'gb2312', 'gb18030'}:
                candidates = ['gb18030']
            elif declared:
                candidates = [declared]
        candidates += ['utf-8', 'gb18030']
        text = ''
        for encoding in candidates:
            try:
                text = data.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if not text:
            text = data.decode('utf-8', 'replace')
    if '\\u' in text:
        # 有的页面把整段 HTML 写成 JS 的 \uXXXX 转义。只替换转义序列本身，
        # 不能用 unicode_escape 整体往返——那会把真实的非 ASCII 文本弄坏，
        # 而且对 "\/" 之类的 HTML 转义会告警。
        def _unescape(match: re.Match[str]) -> str:
            try:
                return chr(int(match.group(1), 16))
            except ValueError:
                return match.group(0)

        text = re.sub(r'\\u([0-9a-fA-F]{4})', _unescape, text)
    return text


def _tag_text(html: Any, attr: Any, names: Any) -> dict[str, str]:
    """从一段 HTML 里取「标记落在 names 里」的标签文本。

    澳客两种写法都有，必须都认：
      * 手机亚指/欧指页：`<span type="xinzhu">2.17</span>`（属性）
      * 手机大小球页：`<span class="sort-xin-daqiu fontblue">1.93</span>`（class 片段）
    标签和属性还会跨行（`<em\\n class="..." type="xin">`），所以先压平空白再匹配。
    """
    wanted = {str(n) for n in (names or []) if n}
    out: dict[str, str] = {}
    for match in re.finditer(r'<(\w+)([^>]*)>([^<]*)', str(html or '')):
        attrs = re.sub(r'\s+', ' ', match.group(2))
        marks: set[str] = set()
        found = re.search(r'type\s*=\s*["\']([^"\']+)["\']', attrs)
        if found:
            marks.add(found.group(1))
        classes = re.search(r'class\s*=\s*["\']([^"\']+)["\']', attrs)
        if classes:
            marks.update(classes.group(1).split())
        hit = wanted & marks
        if not hit:
            continue
        value = re.sub(r'\s+', ' ', match.group(3)).strip()
        # 同一标签可能同时命中多个别名，按优先级全部写入，先到先得。
        for name in hit:
            out.setdefault(name, value)
    return out


def _num_of(raw: Any) -> float | None:
    token = str(raw or '').replace(',', '').replace('%', '').strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _referers_for(url: str) -> list[str]:
    """给一个页面挑 Referer 候选。

    现场核对（2026-11-25）：桌面波胆页
      * `Referer: https://www.okooo.com/`            → 405
      * `Referer: .../soccer/match/<id>/` 或 `/odds/` → 200
    所以对 /soccer/match/<id>/xxx/ 这类页面，先用「同场次页」当来源，
    首页只作最后兜底。手机页用手机赛程页。
    """
    if 'm.okooo.com' in url:
        return [_MOBILE_WARMUP_URL]
    found = re.search(r'https?://[^/]+(/soccer/match/\d+/)', url)
    if found:
        base = url.split('/soccer/match/')[0]
        return [f'{base}{found.group(1)}', _WARMUP_URL]
    return [_WARMUP_URL]


def _warm_session(proxies: Any = None, mobile: bool = False, referer: str | None = None) -> Any:
    """建一个带站点 cookie 的会话。

    澳客对没有 cookie 的直连请求直接回 405/403（阿里云 WAF 拦截页），
    先用会话访问一个正常页拿到 cookie，再抓详情页就正常。
    """
    try:
        sess = requests.Session()
        sess.get(
            referer or (_MOBILE_WARMUP_URL if mobile else _WARMUP_URL),
            headers={
                'User-Agent': _MOBILE_UA if mobile else _DESKTOP_UA,
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

    返回 {ok, url, status, bytes, title, blocked, error, html, encoding, referer}。
    手机页自动用手机 UA 并带手机赛程页来源；桌面页按 _referers_for 逐个试——
    波胆页对 Referer 敏感，来源页不对就是 405，这不是站点不可用。
    """
    info: dict[str, Any] = {
        'ok': False, 'url': url, 'status': 0, 'bytes': 0,
        'title': '', 'blocked': False, 'error': '', 'html': '', 'encoding': '', 'referer': '',
    }
    is_mobile = 'm.okooo.com' in url
    referers = _referers_for(url)
    last_error = ''
    attempts = max(1, retries) * (len(referers) + 1)
    active_session = session
    referer = referers[0] if referers else _WARMUP_URL
    for attempt in range(attempts):
        headers = {
            'User-Agent': _MOBILE_UA if is_mobile else _DESKTOP_UA,
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': referer,
        }
        try:
            sess = active_session or requests
            resp = sess.get(url, headers=headers, proxies=_proxy_dict(proxies), timeout=timeout)
            info['status'] = int(resp.status_code)
            info['bytes'] = len(resp.content or b'')
            info['referer'] = referer
            info['encoding'] = str(resp.encoding or '').lower()
            html = decode_okooo_html(resp.content or b'', resp.headers)
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
            if info['blocked']:
                # 被拦：换一个 Referer 再试；还有候选就继续，别在同一组合上死等。
                nxt = _next_referer(referers, referer)
                if nxt:
                    referer = nxt
                    active_session = _warm_session(proxies, mobile=is_mobile, referer=referer)
                    continue
                if active_session is None:
                    active_session = _warm_session(proxies, mobile=is_mobile, referer=referer)
                    continue
                break
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


def _next_referer(referers: list[str], current: str) -> str | None:
    """取当前 Referer 之后的下一个候选；没有就返回 None。"""
    try:
        index = referers.index(current)
    except ValueError:
        return referers[0] if referers else None
    return referers[index + 1] if index + 1 < len(referers) else None


# 缓存版本：打包格式一变就 +1，旧包一律拒绝并重抓。
# 没有版本的缓存会让「任何跑过旧版本的机器」永久看不到之后的修复——
# 用户的 channel=desktop_cs / books=25 / jc=0 正是旧包被当成新数据。
CACHE_SCHEMA = 2
_ACCEPTED_CS_CHANNELS = {'mobile', 'okooo_cs'}


def _cache_stamp(pack: dict[str, Any]) -> dict[str, Any]:
    """给要落盘的包盖上版本和时间戳。"""
    if isinstance(pack, dict):
        pack['cache_schema'] = CACHE_SCHEMA
        pack['built_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return pack


def _cache_reject_reason(cache: Path, data: Any, *, require_31: bool = False) -> str:
    """判断一个缓存包还能不能用。返回拒绝原因，'' 表示可用。

    三条硬规则：
      * cache_schema 必须是当前版本（旧包 = 旧解析逻辑的产物）；
      * correct_score_channel 必须是现在真正会写出的通道
        （desktop_cs 是旧标签，出现即说明是旧包）；
      * 若要求 31 全表，包里必须有合并后的 31 项（cs31_complete / 31 个键）。
    """
    if not isinstance(data, dict) or not data:
        return 'cache_empty'
    if int(data.get('cache_schema') or 0) != CACHE_SCHEMA:
        return 'cache_schema_old'
    if 'correct_score_channel' in data:
        ch = str(data.get('correct_score_channel') or '')
        if ch and ch not in _ACCEPTED_CS_CHANNELS:
            return 'cache_channel_stale'
    if require_31:
        page = data.get('correct_score_page_avg')
        n = len(page) if isinstance(page, dict) else 0
        if not data.get('cs31_complete') or n < 31:
            return 'cache_missing_jc31'
    return ''


def _read_json_cache(
    path: Path, *, require_31: bool = False, label: str = '澳客手机CS',
) -> dict[str, Any] | None:
    """读缓存；旧版本/旧通道/缺31 一律拒绝，并打印一行原因后重抓。"""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"   ⚠️ [{label}] 缓存读取跳过 {path.name}: {type(exc).__name__}")
        return None
    if not isinstance(data, dict):
        return None
    reason = _cache_reject_reason(path, data, require_31=require_31)
    if reason:
        # 明确说出来，不要静默重抓——否则用户无从知道为什么之前的修复「没生效」。
        print(
            f"   ♻️ [{label}] 缓存已作废 {path.name}: reason={reason} "
            f"(schema={data.get('cache_schema') or 0}→{CACHE_SCHEMA}, "
            f"channel={data.get('correct_score_channel') or '-'})，重新抓取"
        )
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


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


def _looks_like_score_map(value: Any) -> bool:
    """一个 dict 是不是「比分→赔率」裸映射（而不是带键的 pack）。

    判据：没有任何已知的容器键，且过半的键看起来像比分（1-0 / 1:0 / 胜其它）。
    """
    if not isinstance(value, dict) or not value:
        return False
    known = {
        'correct_score_jc_official', 'jc_score_odds', 'correct_score_page_avg',
        'correct_score_books_avg', 'correct_score_site_avg', 'available', 'channel',
        'match_id', 'boards_hit', 'missing', 'source_report',
    }
    if known & set(value):
        return False
    score_like = 0
    for key, odd in value.items():
        text = str(key or '').strip()
        if re.fullmatch(r'\d{1,2}\s*[-:：]\s*\d{1,2}', text) or '其它' in text:
            try:
                if float(odd) > 1:
                    score_like += 1
            except (TypeError, ValueError):
                continue
    return score_like > 0 and score_like * 2 >= len(value)


def _okooo_apply_jc_score_fill(
    record: dict[str, Any],
    pack: dict[str, Any] | None = None,
    source_note: str = '',
    overwrite: bool = False,
) -> dict[str, Any]:
    """把竞彩比分赔率写进 record['correct_score_jc_official']。

    `pack` 既可以是带键的容器（jc_score_odds / correct_score_jc_official），
    也可以直接是裸的 {score: odd} 映射。历史上只认前者，于是调用方把裸映射
    传进来时什么都写不进去，日志里 `jc=0` 永远消不掉。
    """
    if not isinstance(record, dict):
        return {}
    src = pack if isinstance(pack, dict) else record
    jc = src.get('correct_score_jc_official') or src.get('jc_score_odds') or {}
    if not (isinstance(jc, dict) and jc) and _looks_like_score_map(src):
        jc = src
    if not (isinstance(jc, dict) and jc) and _looks_like_score_map(record):
        jc = record
    if isinstance(jc, dict) and jc:
        if overwrite or not record.get('correct_score_jc_official'):
            record['correct_score_jc_official'] = dict(jc)
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
    **kwargs: Any,
) -> dict[str, Any]:
    """按比分赔率排热度。

    `channel` 是正式参数名，但历史调用方（以及已发布的单文件内嵌副本）
    发的是 `heat_channel`。这里接受它作为别名，避免一个关键字名把整块
    score_market_heat 打挂——那会连带方向锁重建失败。
    """
    alias_channel = kwargs.get('heat_channel')
    if alias_channel and (not channel or channel == 'mobile'):
        # 显式传了别名就以它为准（默认值 'mobile' 不算显式）。
        channel = str(alias_channel)
    if not channel:
        channel = 'unknown'
    # 法律通道标签归一：桌面波胆页抓到的市均是合法的 27 家公司均值，
    # 不是被禁的「桌面分析页/pool31」。历史标签 desktop_cs 归一成 okooo_cs，
    # 让 _is_legal_mobile_cs_heat 的 okooo_cs_books_avg 分支能认它。
    # 标签归一前先强制成字符串：heat_source/channel 可能被传 None 或数字，
    # `'desktop' in None` 会直接 TypeError，把整块 heat 打挂。
    channel = str(channel or '')
    heat_source = str(heat_source or '')
    if channel in {'desktop_cs', 'okooo_desktop_cs', 'okooo_cs_desktop'}:
        channel = 'okooo_cs'
    if 'desktop' in heat_source:
        heat_source = 'okooo_cs_books_avg'
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
        # avg_odds 是赔率的同义词：合法市均闸门按 avg_odds 数「可用的行」，
        # 只发 odds 会让真市均被判非法。
        row['avg_odds'] = odd_f
        if isinstance(enriched, dict):
            row.update({k: v for k, v in enriched.items() if k not in row})
            if row.get('avg_odds') in (None, 0, 0.0) and odd_f:
                row['avg_odds'] = odd_f
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
    """统一的不可用载板。

    即使不可用也带空的 ranking / top_scores / source / channel，
    调方不必对「有没有这个键」做分支（自检与下游都按同一形状读取）。
    """
    data = {
        'available': False,
        'channel': 'mobile',
        'reason': reason,
        'block_reason': reason,
        'source': '',
        'heat_source': '',
        'ranking': [],
        'top_scores': [],
    }
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


def fetch_jczq31(match_id: Any, proxies: Any = None, session: Any = None) -> dict[str, Any]:
    """抓竞彩官方 31 比分赔率（唯一来源：竞足赛程页 var oddsData）。

    这是「补齐 9 个市均覆盖不到的选项」的唯一入口，供 CS 抓取、补抓、以及
    enrich 的 force_rescrape 复用（后者以前只调 scrape_okooo_mobile_cs_zuizhi，
    那条路根本不碰 jczq，所以「重新抓全」永远补不上那 9 项）。

    返回 {'available', 'odds', 'orderid', 'raw_count', 'stage', 'report'}。
    """
    mid = str(match_id or '').strip()
    out: dict[str, Any] = {'available': False, 'odds': {}, 'orderid': '', 'raw_count': 0, 'stage': ''}
    if not mid:
        out['stage'] = 'no_match_id'
        return out
    url = 'https://m.okooo.com/jczq/'
    page, cached, fetched_at = _jczq_page_cached(url, proxies=proxies, session=session)
    report = {
        'url': url, 'channel': 'mobile',
        'status': int(page.get('status') or 0),
        'bytes': int(page.get('bytes') or 0),
        'title': str(page.get('title') or ''),
        'blocked': bool(page.get('blocked')),
        'cached': bool(cached),
        'fetched_at': fetched_at,
        'stage': '',
        'orderid': '',
    }
    if not page.get('ok'):
        report['stage'] = 'fetch_failed'
        return {**out, 'stage': 'fetch_failed', 'report': report}
    parsed = parse_jczq_score_odds(page.get('html'), mid)
    report['stage'] = parsed.get('stage') or ''
    report['orderid'] = parsed.get('orderid') or ''
    if not parsed.get('available') and cached:
        # 缓存页里没有本场（抓缓存时还没上架）：真抓一次再解析。
        page = _fetch_detail(url, proxies=proxies, session=session, retries=1)
        if page.get('ok'):
            _remember_jczq_page(url, page)
            parsed = parse_jczq_score_odds(page.get('html'), mid)
            report.update({
                'status': int(page.get('status') or 0),
                'bytes': int(page.get('bytes') or 0),
                'cached': False,
                'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'stage': parsed.get('stage') or '',
                'orderid': parsed.get('orderid') or '',
            })
    return {
        'available': bool(parsed.get('available')),
        'odds': dict(parsed.get('odds') or {}),
        'orderid': parsed.get('orderid') or '',
        'raw_count': int(parsed.get('raw_count') or 0),
        'stage': parsed.get('stage') or '',
        'report': report,
    }


def scrape_okooo_mobile_cs_zuizhi(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    """抓澳客波胆（比分）市均。

    只用真实存在且结构可解析的页面：桌面 `/soccer/match/<id>/cs/`。
    手机 `/jingcai/<id>/cs/` 在 2026-11 起返回 404，不再作为主入口。
    不可用时 reason 必须带 URL + 状态码/异常类 + 卡在哪个解析阶段。
    """
    html = kwargs.get('html') or kwargs.get('text')
    cache = _cache_path('cs', match_id)
    force_refresh = bool(kwargs.get('force_refresh'))
    if force_refresh and not html and cache.exists():
        # 补抓路径必须能推翻旧包，否则第二次尝试只是再读同一个陈旧文件。
        print(f"   ♻️ [澳客手机CS] force_refresh 丢弃缓存 {cache.name}，重新抓取")
        try:
            cache.unlink()
        except OSError:
            pass
    if not html and not force_refresh and cache.exists():
        cached = _read_json_cache(cache, require_31=True, label='澳客手机CS')
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
    # 波胆页的 405 是间歇的：现场核对过「同 Referer 等 35s 再要就 200」。
    # 所以对第一候选做有界耐心重试（默认 2 轮、每轮间隔 35s），
    # 仍然拿不到才如实报失败。绝不在这里编数据。
    patience = int(kwargs.get('patience_retries', os.environ.get('OKOOO_CS_PATIENCE') or 2) or 0)
    gap = float(kwargs.get('patience_gap', os.environ.get('OKOOO_CS_PATIENCE_GAP') or 35) or 35)
    for url in urls:
        fetch = _fetch_detail(url, proxies=proxies, session=session, retries=1)
        for _attempt in range(patience if url == urls[0] else 0):
            if fetch.get('ok'):
                break
            if not fetch.get('blocked'):
                # 不是被 WAF 拦（例如 404）就没必要等，直接换下一条来源。
                break
            time.sleep(gap)
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
    # 桌面 /cs/ 波胆页是 27 家公司的合法市均，不是被禁的「桌面分析页/pool31」。
    # 通道标签用 okooo_cs（法律闸门已有 okooo_cs_books_avg 分支），
    # 不再自标 desktop_cs——那个名字会被 'desktop' 规则整体判非法。
    # 31 池唯一合并入口：市均先占，竞彩官方31补缺，逐键记出处。
    # 之前这里只交出 25 个市均键（冒号形式、还带 4-3/3-4/4-4），
    # 下游同时报「市均 25 项」和「31全表缺 9 项」——两个都对，却是两张表。
    _jc = kwargs.get('jc_score_odds')
    _jc_report: dict[str, Any] = {}
    if not isinstance(_jc, dict) or len(_jc) < 31:
        # 拿不到 31 就直接去竞彩官方抓：补抓/重抓都靠这一步，
        # 否则「重新抓全」永远补不上那 9 个市均没有的选项。
        _jc_res = fetch_jczq31(match_id, proxies=proxies, session=session)
        _jc_report = _jc_res.get('report') or {}
        if _jc_res.get('available'):
            _jc = _jc_res['odds']
    merged31 = merge_cs31(parsed['page_avg'], parsed['books_avg'], _jc)
    heat = build_score_market_heat_ranking(
        merged31['scores'],
        page_avg_odds=merged31['scores'],
        heat_source='okooo_cs_books_avg',
        channel='okooo_cs',
    )
    heat.update({
        'available': True,
        'correct_score_available': True,
        'match_id': str(match_id or ''),
        'raw_count': len(merged31['scores']),
        'correct_score_page_avg': merged31['scores'],
        'correct_score_books_avg': parsed['books_avg'],
        'correct_score_books_n': parsed['books_n'],
        'correct_score_site_avg': merged31['scores'],
        'correct_score_channel': 'okooo_cs',
        'cs31_provenance': merged31['provenance'],
        'cs31_counts': merged31['counts'],
        'cs31_extras': merged31['extras'],
        'cs31_complete': merged31['complete'],
        'cs_fetch_url': str(fetch.get('url') or (urls[0] if urls else '')),
        'cs_fetch_status': int(fetch.get('status') or 0),
        'cs_fetch_bytes': int(fetch.get('bytes') or 0),
        'cs_parse_stage': stage,
        'pool31_forbidden': True,
    })
    try:
        cache.write_text(
            json.dumps(_cache_stamp(heat), ensure_ascii=False, indent=2), encoding='utf-8',
        )
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
    """桌面大小球页：均值行的盘口与大小水位。

    注意：真实页面的 `#avgObj` 平均值行是按权限遮的（全 `-`），
    所以这里几乎永远回 `avg_row_has_no_line`。大小球以手机页 `_mobile_ou_line` 为准。
    """
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


# ---------------------------------------------------------------------------
# 手机页解析（m.okooo.com/match/*.php）
#
# 用户实盘日志里的三块水位只有手机页真的给了数：
#   亚指   handicap.php?MatchID=<id>      25 家公司，span[type=zhu|xinzhu] + em[type=chu|xin]
#   大小球 ah.php?type=daxiao&matchId=<id> 21 家，span.sort-{chu,xin}-{daqiu,xiaoqiu} + span.filter-*
#   胜平负 odds.php?MatchID=<id>          竞**方 行 span[type=xinsheng|xinping|xinfu]
# 桌面页 /ah/ 间歇 405、/overunder/ 均价行被权限遮、/odds/ 公司行由 JS 渲染，
# 三者都取不到数，因此这些函数是主路径，桌面只留波胆。
# ---------------------------------------------------------------------------

def _mobile_ah_line(html: Any) -> dict[str, Any]:
    """手机亚指页：取「平均指数」行的最新盘口；没有就退回众数。"""
    out: dict[str, Any] = {
        'asian_handicap': None, 'line_text': '', 'stage': 'ok',
        'start_line': None, 'start_text': '', 'home_water': None, 'away_water': None,
        'company_rows': 0, 'line_source': '',
    }
    text = str(html or '')
    rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', text, re.IGNORECASE)
    if not rows:
        out['stage'] = 'no_rows'
        return out
    per_row: list[dict[str, Any]] = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE)
        if len(cells) < 3:
            continue
        name = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', cells[0])).strip()
        start = _tag_text(cells[1], None, ['zhu', 'chu', 'ke'])
        latest = _tag_text(cells[2], None, ['xinzhu', 'xin', 'xinke'])
        if not latest:
            continue
        per_row.append({'name': name, 'start': start, 'latest': latest})
    if not per_row:
        out['stage'] = 'no_odds_rows'
        return out
    out['company_rows'] = len(per_row)
    pick = next((r for r in per_row if '平均' in r['name']), per_row[0])
    if '平均' in pick['name']:
        out['line_source'] = 'avg_row'
    else:
        # 没有平均行：用最新盘口的众数，并标明来源，绝不写死一条盘口。
        counted: dict[str, int] = {}
        for row in per_row:
            label = str(row['latest'].get('xin') or '')
            if label:
                counted[label] = counted.get(label, 0) + 1
        if counted:
            best = max(counted.items(), key=lambda kv: kv[1])[0]
            pick = next(r for r in per_row if str(r['latest'].get('xin')) == best)
            out['line_source'] = f'mode({counted[best]}/{len(per_row)})'
        else:
            out['stage'] = 'no_line_text'
            return out
    out['line_text'] = str(pick['latest'].get('xin') or '')
    out['start_text'] = str(pick['start'].get('chu') or '')
    out['asian_handicap'] = ah_text_to_line(out['line_text'])
    out['start_line'] = ah_text_to_line(out['start_text'])
    out['home_water'] = _num_of(pick['latest'].get('xinzhu'))
    out['away_water'] = _num_of(pick['latest'].get('xinke'))
    if out['asian_handicap'] is None:
        out['stage'] = f'unmapped_line_text:{out["line_text"][:12]}'
    return out


def _mobile_ou_line(html: Any) -> dict[str, Any]:
    """手机大小球页：盘口线取最新 `filter-xin` 的众数，水位取同一条线的公司。"""
    out: dict[str, Any] = {
        'ou_line': None, 'line_text': '', 'stage': 'ok',
        'start_line': None, 'over_water': None, 'under_water': None,
        'company_rows': 0, 'line_source': '',
    }
    text = str(html or '')
    rows = re.findall(r'<tr[^>]*class="[^"]*jsContentItem[^"]*"[^>]*>([\s\S]*?)</tr>', text, re.IGNORECASE)
    if not rows:
        rows = re.findall(r'<tbody class="[^"]*jsContent[^"]*"[^>]*>([\s\S]*?)</tbody>', text, re.IGNORECASE)
    if not rows:
        out['stage'] = 'no_rows'
        return out
    lines: list[float] = []
    first: dict[str, Any] = {}
    for row in rows:
        latest = _tag_text(row, None, ['sort-xin-daqiu', 'filter-xin', 'sort-xin-xiaoqiu'])
        if not latest:
            continue
        line_value = _num_of(latest.get('filter-xin'))
        if line_value is None:
            continue
        out['company_rows'] += 1
        lines.append(line_value)
        if not first:
            start = _tag_text(row, None, ['sort-chu-daqiu', 'filter-chu', 'sort-chu-xiaoqiu'])
            first = {
                'line': line_value,
                'over': _num_of(latest.get('sort-xin-daqiu')),
                'under': _num_of(latest.get('sort-xin-xiaoqiu')),
                'start_line': _num_of(start.get('filter-chu')),
            }
    if not lines:
        out['stage'] = 'no_line'
        return out
    counted: dict[float, int] = {}
    for value in lines:
        counted[value] = counted.get(value, 0) + 1
    mode_line, mode_hits = max(counted.items(), key=lambda kv: kv[1])
    # 取与盘口众数一致的第一个公司作为水位样本，保证水位与盘口同源。
    for row in rows:
        latest = _tag_text(row, None, ['sort-xin-daqiu', 'filter-xin', 'sort-xin-xiaoqiu'])
        if _num_of(latest.get('filter-xin')) == mode_line:
            out['over_water'] = _num_of(latest.get('sort-xin-daqiu'))
            out['under_water'] = _num_of(latest.get('sort-xin-xiaoqiu'))
            start = _tag_text(row, None, ['sort-chu-daqiu', 'filter-chu', 'sort-chu-xiaoqiu'])
            out['start_line'] = _num_of(start.get('filter-chu'))
            break
    out['ou_line'] = mode_line
    out['line_text'] = f'{mode_line:g}'
    out['line_source'] = f'mode({mode_hits}/{len(lines)})'
    return out


def _mobile_1x2(html: Any) -> dict[str, Any]:
    """手机欧指页：竞**方 行的最新胜/平/负（手机页有服务端渲染的公司行）。"""
    out: dict[str, Any] = {'odds_1x2': {}, 'company': '', 'stage': 'ok', 'company_rows': 0}
    text = str(html or '')
    rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', text, re.IGNORECASE)
    out['company_rows'] = sum(1 for row in rows if 'xin' in row)
    for row in rows:
        cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE)
        if len(cells) < 3:
            continue
        name = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', cells[0])).strip()
        if name == '竞**方':
            latest = _tag_text(cells[2], None, ['xinsheng', 'xinping', 'xinfu'])
            home = _num_of(latest.get('xinsheng'))
            draw = _num_of(latest.get('xinping'))
            away = _num_of(latest.get('xinfu'))
            if None in (home, draw, away):
                # 有些场次竞彩官方水位缺失，退回该行的初盘，并说明来源。
                start = _tag_text(cells[1], None, ['sheng', 'ping', 'fu'])
                home = home if home is not None else _num_of(start.get('sheng'))
                draw = draw if draw is not None else _num_of(start.get('ping'))
                away = away if away is not None else _num_of(start.get('fu'))
                out['stage'] = 'latest_missing_used_start' if None not in (home, draw, away) else 'odds_incomplete'
            if None in (home, draw, away):
                return out
            out['company'] = '竞彩官方'
            out['odds_1x2'] = {
                'home': home, 'draw': draw, 'away': away,
                'Home': home, 'Draw': draw, 'Away': away,
            }
            return out
    out['stage'] = 'company_row_missing'
    return out


def _mobile_exchange_pnl(html: Any) -> dict[str, Any]:
    """手机交易盈亏页：竞足保存盈亏 / 竞足人气盈亏 / 必发交易盈亏 三段。"""
    out: dict[str, Any] = {
        'available': False, 'stage': 'ok', 'betfair_available': False,
        'jc_saved': {}, 'jc_popularity': {}, 'betfair_trade': {},
        'betfair_note': '', 'saved_total': None,
    }
    text = str(html or '')
    sections = re.split(r'<section class="exchange-box"', text)
    if len(sections) <= 1:
        out['stage'] = 'no_exchange_sections'
        return out

    def _table_rows(chunk: str) -> dict[str, dict[str, float | None]]:
        rows: dict[str, dict[str, float | None]] = {}
        for row in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', chunk, re.IGNORECASE):
            cells = [
                re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', cell)).strip()
                for cell in re.findall(r'<td[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE)
            ]
            if not cells or cells[0] not in ('胜', '平', '负'):
                continue
            numbers = [_num_of(value) for value in cells[1:]]
            rows[cells[0]] = {
                'odds': numbers[0] if len(numbers) > 0 else None,
                'volume': numbers[1] if len(numbers) > 1 else None,
                'pnl': numbers[2] if len(numbers) > 2 else None,
            }
        return rows

    for chunk in sections[1:]:
        title = re.search(r'<div class="exchange-title">\s*([^<]+)', chunk)
        name = re.sub(r'\s+', ' ', title.group(1)).strip() if title else ''
        rows = _table_rows(chunk)
        total = re.search(r'<div class="num">([\d,]+)</div>', chunk)
        if name.startswith('竞足保存盈亏'):
            out['jc_saved'] = rows
            out['saved_total'] = _num_of(total.group(1)) if total else None
        elif name.startswith('竞足人气盈亏'):
            out['jc_popularity'] = rows
        elif name.startswith('必发交易盈亏'):
            out['betfair_trade'] = rows
            has_value = any(
                (item.get('volume') or 0) or (item.get('pnl') or 0)
                for item in rows.values()
            ) if rows else False
            out['betfair_available'] = bool(has_value)
            if not has_value:
                # 页面自己写着「暂无数据」：如实说必发这段没有数，而不是伪造。
                out['betfair_note'] = '必发交易盈亏: 页面显示暂无数据（该公司/交易所未对该场挂盘）'
    out['available'] = bool(out['jc_saved'] or out['jc_popularity'])
    if not out['available'] and not out['betfair_note']:
        out['stage'] = 'no_pnl_rows'
    return out


_JCZQ_PAGE_CACHE: dict[str, dict[str, Any]] = {}


def _jczq_cache_ttl() -> float:
    try:
        return float(os.environ.get('OKOOO_JCZQ_CACHE_TTL') or 90)
    except (TypeError, ValueError):
        return 90.0


def _remember_jczq_page(url: str, page: dict[str, Any]) -> None:
    _JCZQ_PAGE_CACHE[url] = {
        'page': dict(page),
        'at': time.time(),
        'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def _jczq_page_cached(url: str, proxies: Any = None, session: Any = None) -> tuple[dict[str, Any], bool, str]:
    """取竞足赛程页的原始 HTML，带短 TTL 进程内缓存。

    一轮 20 场共用同一份 183KB 文档；不缓存就要下载 20 次、等 20 次 WAF。
    只缓存**原始页面**，每场仍现场解析（parse_jczq_score_odds 按 matchid 取行）。
    返回 (page, cached, fetched_at)：命中缓存时 cached=True 且带原始抓取时间，
    调用方把它写进 source_report，日志仍然说清数据是哪来的。
    """
    ttl = _jczq_cache_ttl()
    entry = _JCZQ_PAGE_CACHE.get(url)
    if entry and (time.time() - float(entry.get('at') or 0)) <= ttl:
        page = dict(entry.get('page') or {})
        if page.get('ok'):
            return page, True, str(entry.get('fetched_at') or '')
    page = _fetch_detail(url, proxies=proxies, session=session, retries=1)
    # 与波胆/阵容页同受间歇 WAF 影响：被拦时有界重试，别让 31 项比分静默变空。
    for _jc_try in range(int(os.environ.get('OKOOO_JCZQ_PATIENCE') or 2)):
        if page.get('ok') or not page.get('blocked'):
            break
        time.sleep(float(os.environ.get('OKOOO_JCZQ_PATIENCE_GAP') or 30))
        page = _fetch_detail(url, proxies=proxies, session=session, retries=1)
    if page.get('ok'):
        _remember_jczq_page(url, page)
    return page, False, time.strftime('%Y-%m-%d %H:%M:%S')


def scrape_okooo_mobile_six_markets(match_id: Any, proxies: Any = None, session: Any = None, **kwargs: Any) -> dict[str, Any]:
    """六盘口水位：亚指 / 大小球 / 胜平负，只认能解析的真实页面。

    三块各自独立抓、独立记 `source_report`（URL、状态码、字节数、页面标题、解析阶段）。
    任何一块没抓到就进 `missing` 并带上原因；绝不回填盘口线、不写待定数字。
    """
    cache = _cache_path('six', match_id)
    _force6 = bool(kwargs.get('force_refresh'))
    if _force6 and not kwargs.get('html') and not kwargs.get('text') and cache.exists():
        try:
            cache.unlink()
        except OSError:
            pass
    if not kwargs.get('html') and not kwargs.get('text') and not _force6 and cache.exists():
        cached = _read_json_cache(cache, label='澳客六盘口')
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

    # 每块盘口按「手机页优先 → 桌面页兜底」依次尝试，直到拿到数为止。
    # 理由（2026-11-25 现场核对）：桌面 /ah/ 间歇 405、/overunder/ 均价行被权限
    # 遮成 `-`、/odds/ 公司行由 JS 渲染；手机三页都是服务端渲染，真给数。
    plan: tuple[tuple[str, str, tuple[tuple[str, str, Any], ...]], ...] = (
        (
            'ah', 'asian_handicap', (
                (f'https://m.okooo.com/match/handicap.php?MatchID={mid}', 'mobile', _mobile_ah_line),
                (f'https://www.okooo.com/soccer/match/{mid}/ah/', 'desktop', _first_avg_index_row),
            ),
        ),
        (
            'ou', 'ou_line', (
                (f'https://m.okooo.com/match/ah.php?type=daxiao&matchId={mid}', 'mobile', _mobile_ou_line),
                (f'https://www.okooo.com/soccer/match/{mid}/overunder/', 'desktop', _first_ou_line),
            ),
        ),
        (
            '1x2', 'odds_1x2', (
                (f'https://m.okooo.com/match/odds.php?MatchID={mid}', 'mobile', _mobile_1x2),
                (f'https://www.okooo.com/soccer/match/{mid}/odds/', 'desktop', _first_1x2_row),
            ),
        ),
    )
    for kind, field, sources in plan:
        board = {'ah': '亚盘让球', 'ou': '大小球', '1x2': '胜平负'}[kind]
        _attempts: list[dict[str, Any]] = []
        for url, channel, parser in sources:
            page = _fetch_detail(url, proxies=proxies, session=session, retries=1)
            pack['fetch_urls'].append(url)
            report = {
                'url': url,
                'channel': channel,
                'status': int(page.get('status') or 0),
                'bytes': int(page.get('bytes') or 0),
                'title': str(page.get('title') or ''),
                'blocked': bool(page.get('blocked')),
                'error': str(page.get('error') or ''),
                'stage': '',
            }
            if not page.get('ok'):
                report['stage'] = 'fetch_failed'
                _attempts.append(report)
                # 被 WAF 拦或网络失败：换下一条来源，别在同一页死等。
                continue
            parsed = parser(page.get('html'))
            if kind == 'ah':
                value = parsed.get('asian_handicap')
                report['stage'] = parsed.get('stage') or ('ok' if value is not None else 'no_line')
                report['line_text'] = parsed.get('line_text') or ''
                if value is not None:
                    pack['asian_handicap'] = value
                    pack['asian_handicap_text'] = parsed.get('line_text') or ''
                    pack['ah_start_line'] = parsed.get('start_line')
                    pack['ah_start_text'] = parsed.get('start_text') or ''
                    pack['ah_home_water'] = parsed.get('home_water')
                    pack['ah_away_water'] = parsed.get('away_water')
                    pack['ah_line_source'] = parsed.get('line_source') or ''
                    pack['boards_hit'][board] = True
            elif kind == 'ou':
                value = parsed.get('ou_line')
                report['stage'] = parsed.get('stage') or ('ok' if value is not None else 'no_line')
                report['line_text'] = parsed.get('line_text') or ''
                if value is not None:
                    pack['ou_line'] = value
                    pack['ou_line_text'] = parsed.get('line_text') or ''
                    pack['ou_start_line'] = parsed.get('start_line')
                    pack['ou_over_water'] = parsed.get('over_water')
                    pack['ou_under_water'] = parsed.get('under_water')
                    pack['ou_line_source'] = parsed.get('line_source') or ''
                    pack['boards_hit'][board] = True
            else:
                odds = parsed.get('odds_1x2') or {}
                report['stage'] = parsed.get('stage') or ('ok' if odds else 'no_odds')
                if odds:
                    pack['odds_1x2'] = odds
                    pack['odds_1x2_company'] = parsed.get('company') or ''
                    pack['boards_hit'][board] = True
            _attempts.append(report)
            if pack['boards_hit'].get(board):
                break
        if len(_attempts) == 1:
            pack['source_report'][kind] = _attempts[0]
        else:
            last = _attempts[-1] if _attempts else {}
            pack['source_report'][kind] = {
                'url': _attempts[0]['url'] if _attempts else '',
                'channel': _attempts[0]['channel'] if _attempts else '',
                'status': _attempts[0]['status'] if _attempts else 0,
                'bytes': _attempts[0]['bytes'] if _attempts else 0,
                'title': _attempts[0]['title'] if _attempts else '',
                'blocked': any(a.get('blocked') for a in _attempts),
                'error': last.get('error', ''),
                'stage': last.get('stage', ''),
                'attempts': _attempts,
            }
        if not pack['boards_hit'].get(board):
            pack['missing'].append(field)

    pack['available'] = bool(pack['boards_hit'])
    # 竞彩比分 31 项：唯一真实来源是竞足赛程页的 oddsData.SportteryScore。
    # 该页 183KB 且一轮 20 场都是同一份文档，所以走短 TTL 页缓存（只缓存原始页面，
    # 每场仍现场解析）。命中缓存必须如实标注，不能伪装成刚抓的。
    try:
        _jc_url = 'https://m.okooo.com/jczq/'
        _jc_page, _jc_cached, _jc_fetched_at = _jczq_page_cached(
            _jc_url, proxies=proxies, session=session,
        )
        _jc_fetch = _jc_page
        _jc_report = {
            'url': _jc_url,
            'channel': 'mobile',
            'status': int(_jc_fetch.get('status') or 0),
            'bytes': int(_jc_fetch.get('bytes') or 0),
            'title': str(_jc_fetch.get('title') or ''),
            'blocked': bool(_jc_fetch.get('blocked')),
            'error': str(_jc_fetch.get('error') or ''),
            'cached': bool(_jc_cached),
            'fetched_at': _jc_fetched_at,
            'stage': '',
        }
        if _jc_fetch.get('ok'):
            _jc = parse_jczq_score_odds(_jc_fetch.get('html'), mid)
            _jc_report['stage'] = _jc.get('stage') or ''
            _jc_report['orderid'] = _jc.get('orderid') or ''
            if not _jc.get('available') and _jc_cached:
                # 缓存页里没有本场（抓缓存时这场还没上架）：允许真抓一次再解析。
                _jc_page = _fetch_detail(_jc_url, proxies=proxies, session=session, retries=1)
                _jc_fetch = _jc_page
                _jc_report.update({
                    'status': int(_jc_fetch.get('status') or 0),
                    'bytes': int(_jc_fetch.get('bytes') or 0),
                    'title': str(_jc_fetch.get('title') or ''),
                    'blocked': bool(_jc_fetch.get('blocked')),
                    'error': str(_jc_fetch.get('error') or ''),
                    'cached': False,
                    'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                })
                if _jc_fetch.get('ok'):
                    _remember_jczq_page(_jc_url, _jc_fetch)
                    _jc = parse_jczq_score_odds(_jc_fetch.get('html'), mid)
                    _jc_report['stage'] = _jc.get('stage') or ''
                    _jc_report['orderid'] = _jc.get('orderid') or ''
            if _jc.get('available'):
                pack['jc_score_odds'] = dict(_jc['odds'])
                pack['jc_score_source'] = '手机竞足→更多玩法→胜平负比分'
                pack['jc_score_orderid'] = _jc.get('orderid') or ''
                pack['jc_score_page_cached'] = bool(_jc_cached)
                pack['boards_hit']['胜平负比分'] = True
            else:
                pack['jc_score_block_reason'] = (
                    f"oddsData 未取到本场31项 stage={_jc.get('stage')} "
                    f"orderid={_jc.get('orderid') or '-'}"
                )
        else:
            _jc_report['stage'] = 'fetch_failed'
            pack['jc_score_block_reason'] = (
                f"HTTP {_jc_report['status'] or _jc_fetch.get('error')}"
            )
        pack['source_report']['jczq_scores'] = _jc_report
    except Exception as _jc_err:  # noqa: BLE001 - 比分赔率抓取失败不影响三块水位
        pack['jc_score_block_reason'] = f'{type(_jc_err).__name__}: {_jc_err}'
    _hit_n = len(pack['boards_hit'])
    if _hit_n == 3:
        try:
            cache.write_text(
                json.dumps(_cache_stamp(pack), ensure_ascii=False, indent=2), encoding='utf-8',
            )
        except OSError as exc:
            print(f"   ⚠️ [澳客手机CS] 六盘口缓存写入跳过 {cache.name}: {type(exc).__name__}: {exc}")
        return pack
    pack['block_reason'] = (
        'okooo_six_market_incomplete'
        if _hit_n else 'external_okooo_six_market_fetch_failed'
    )
    return pack


_AH_CHANGE_LINK_RE = r'change\.php\?mid=(\d+)&(?:amp;)?pid=(\d+)&(?:amp;)?Type=Handicap'


def _parse_ah_change_list(html: Any) -> list[dict[str, Any]]:
    """解析单个公司的盘口变化表 `table.changeTable`。

    真实结构（m.okooo.com/match/change.php?mid=<id>&pid=<pid>&Type=Handicap&c=1）：
      <td><span class="fontblue">2.17</span><span>受半球</span><span class="fontred2">1.61</span></td>
      <td class="timetd jsChangeContent" time="11-25 21:01">赛前28小时43分</td>
    颜色 class 表示涨跌，不参与语义；取三个 span 的文本即可。
    """
    text = str(html or '')
    table = re.search(r'<table[^>]*class="changeTable"[^>]*>([\s\S]*?)</table>', text, re.IGNORECASE)
    if not table:
        return []
    changes: list[dict[str, Any]] = []
    for row in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', table.group(1), re.IGNORECASE):
        spans = [
            re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s)).strip()
            for s in re.findall(r'<span[^>]*>([\s\S]*?)</span>', row)
        ]
        stamp = re.search(r'class="timetd[^"]*"[^>]*time="([^"]*)"[^>]*>\s*([^<]*?)\s*<', row)
        if len(spans) >= 3:
            home_water, line_text, away_water = spans[0], spans[1], spans[2]
        else:
            continue
        changes.append({
            'home_water': _num_of(home_water),
            'line_text': line_text,
            'line': ah_text_to_line(line_text),
            'away_water': _num_of(away_water),
            'time': stamp.group(1).strip() if stamp else '',
            'note': (stamp.group(2).strip() if stamp else ''),
        })
    return changes


def scrape_okooo_mobile_ah_ou_books(match_id: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """每家公司亚指/大小球明细。

    亚指：手机 `handicap.php` 每行都带 `change.php?mid=&pid=&Type=Handicap`，
    跟着抓该公司的时间线（首次/最新盘口随时间变化）。
    大小球：`ah.php?type=daxiao` **没有** change.php 链接，所以只给最新/初盘两列，
    `changes` 留空并写明 detail_block_reason——绝不编造时间线。
    """
    mid = str(match_id or '').strip()
    if not mid:
        return {'available': False, 'match_id': '', 'reason': 'no_match_id',
                'block_reason': 'no_match_id', 'ah_books': [], 'ou_books': []}
    cap = int(kwargs.get('book_cap') or os.environ.get('OKOOO_AH_BOOK_CAP') or 8)
    detail_cap = int(kwargs.get('detail_cap') or os.environ.get('OKOOO_AH_DETAIL_CAP') or 5)

    ah_url = f'https://m.okooo.com/match/handicap.php?MatchID={mid}'
    ah_page = _fetch_detail(ah_url, proxies=kwargs.get('proxies'), session=kwargs.get('session'), retries=1)
    ah_books: list[dict[str, Any]] = []
    detail_fetched = 0
    _ah_html = ah_page.get('html')
    if ah_page.get('ok') and isinstance(_ah_html, str):
        text = _ah_html
        # 注意：change.php 链接在 <tr onclick="...window.location='...'"> 的属性里，
        # 不在行内文本中。必须连 <tr ...> 标签一起抓，否则拿不到任何 pid。
        for row in re.findall(r'<tr([^>]*)>([\s\S]*?)</tr>', text, re.IGNORECASE):
            tr_attrs, inner = row
            cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', inner, re.IGNORECASE)
            if len(cells) < 3:
                continue
            pids = re.findall(_AH_CHANGE_LINK_RE, tr_attrs) or re.findall(_AH_CHANGE_LINK_RE, inner)
            name = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', cells[0])).strip()
            start = _tag_text(cells[1], None, ['zhu', 'chu', 'ke'])
            latest = _tag_text(cells[2], None, ['xinzhu', 'xin', 'xinke'])
            if not latest and not pids:
                continue
            book = {
                'name': name,
                'pid': pids[0][1] if pids else '',
                'start': {'home_water': _num_of(start.get('zhu')),
                          'line_text': start.get('chu') or '',
                          'line': ah_text_to_line(start.get('chu')),
                          'away_water': _num_of(start.get('ke'))},
                'latest': {'home_water': _num_of(latest.get('xinzhu')),
                           'line_text': latest.get('xin') or '',
                           'line': ah_text_to_line(latest.get('xin')),
                           'away_water': _num_of(latest.get('xinke'))},
                'changes': [],
                'detail_url': (f'https://m.okooo.com/match/change.php?mid={pids[0][0]}&pid={pids[0][1]}&Type=Handicap&c=1'
                               if pids else ''),
                'detail_block_reason': '',
            }
            if not book['pid']:
                book['detail_block_reason'] = '该行没有 change.php 链接，无法取时间线'
            elif name and '平均' not in name and detail_fetched < detail_cap:
                detail = _fetch_detail(book['detail_url'], proxies=kwargs.get('proxies'),
                                       session=kwargs.get('session'), retries=1)
                if detail.get('ok'):
                    book['changes'] = _parse_ah_change_list(detail.get('html'))
                    detail_fetched += 1
                    if not book['changes']:
                        book['detail_block_reason'] = '变化页无可解析行(changeTable 为空)'
                else:
                    book['detail_block_reason'] = (
                        f"变化页 HTTP {detail.get('status') or detail.get('error')}"
                    )
            elif '平均' not in name:
                book['detail_block_reason'] = f'达到明细抓取上限({detail_cap}家)，只保留最新/初盘'
            ah_books.append(book)
            if len([b for b in ah_books if '平均' not in b['name']]) >= cap:
                break

    ou_url = f'https://m.okooo.com/match/ah.php?type=daxiao&matchId={mid}'
    ou_page = _fetch_detail(ou_url, proxies=kwargs.get('proxies'), session=kwargs.get('session'), retries=1)
    ou_books: list[dict[str, Any]] = []
    ou_reason = ''
    _ou_html = ou_page.get('html')
    if ou_page.get('ok') and isinstance(_ou_html, str):
        text = _ou_html
        rows = re.findall(r'<tr[^>]*class="[^"]*jsContentItem[^"]*"[^>]*>([\s\S]*?)</tr>', text, re.IGNORECASE)
        for row in rows:
            name_m = re.search(r'<td class="width16"><a>([^<]*)</a>', row)
            start = _tag_text(row, None, ['sort-chu-daqiu', 'filter-chu', 'sort-chu-xiaoqiu'])
            latest = _tag_text(row, None, ['sort-xin-daqiu', 'filter-xin', 'sort-xin-xiaoqiu'])
            if not latest:
                continue
            ou_books.append({
                'name': (name_m.group(1).strip() if name_m else ''),
                'start': {'over_water': _num_of(start.get('sort-chu-daqiu')),
                          'line': _num_of(start.get('filter-chu')),
                          'under_water': _num_of(start.get('sort-chu-xiaoqiu'))},
                'latest': {'over_water': _num_of(latest.get('sort-xin-daqiu')),
                           'line': _num_of(latest.get('filter-xin')),
                           'under_water': _num_of(latest.get('sort-xin-xiaoqiu'))},
                'changes': [],
            })
            if len(ou_books) >= cap:
                break
        ou_reason = '大小球页没有 change.php 链接，只给最新/初盘两列，无法生成时间线'
    else:
        ou_reason = f"大小球页 HTTP {ou_page.get('status') or ou_page.get('error')}"

    ah_report = {
        'url': ah_url, 'status': int(ah_page.get('status') or 0),
        'bytes': int(ah_page.get('bytes') or 0), 'title': str(ah_page.get('title') or ''),
    }
    ou_report = {
        'url': ou_url, 'status': int(ou_page.get('status') or 0),
        'bytes': int(ou_page.get('bytes') or 0), 'title': str(ou_page.get('title') or ''),
    }
    out = {
        'available': bool(ah_books or ou_books),
        'channel': 'mobile',
        'match_id': mid,
        'source_path': '手机析→亚指→盘口→公司；析→大小球→最值→公司',
        'asian_handicap': {'rows': [b for b in ah_books if '平均' in b['name']],
                           'companies': [b for b in ah_books if '平均' not in b['name']]},
        'over_under': {'rows': [], 'companies': ou_books,
                       'detail_block_reason': ou_reason},
        'ah_detail_fetched': detail_fetched,
        'source_report': {'ah': ah_report, 'ou': ou_report},
        'writes_first_score': False,
        'changes_direction': False,
    }
    if not out['available']:
        out['reason'] = 'okooo_ah_ou_books_fetch_failed'
        out['block_reason'] = 'okooo_ah_ou_books_fetch_failed'
        out['error'] = (
            f"ah HTTP {ah_report['status']} {ah_report['bytes']}B; "
            f"ou HTTP {ou_report['status']} {ou_report['bytes']}B"
        )
    return out


def scrape_okooo_mobile_exchange_pnl(match_id: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """手机交易盈亏页：竞足保存盈亏 / 竞足人气盈亏 / 必发交易盈亏。

    真实来源是 `m.okooo.com/match/exchanges.php`（桌面必发页已 405/失效）。
    必发那一段在很多场次是页面自己写着「暂无数据」，这种情况如实回报，
    不把竞足保存盈亏冒充成必发。
    """
    mid = str(match_id or '').strip()
    if not mid:
        return {'available': False, 'match_id': '', 'reason': 'no_match_id', 'writes_first_score': False}
    url = f'https://m.okooo.com/match/exchanges.php?MatchID={mid}'
    page = _fetch_detail(url, proxies=kwargs.get('proxies'), session=kwargs.get('session'), retries=1)
    report = {
        'url': url,
        'status': int(page.get('status') or 0),
        'bytes': int(page.get('bytes') or 0),
        'title': str(page.get('title') or ''),
        'blocked': bool(page.get('blocked')),
    }
    if not page.get('ok'):
        return {
            'available': False,
            'channel': 'mobile',
            'source_path': '手机竞足→析→盈亏',
            'match_id': mid,
            'reason': 'external_okooo_exchange_fetch_failed',
            'block_reason': 'external_okooo_exchange_fetch_failed',
            'error': f"{url} → HTTP {report['status'] or page.get('error')}",
            'source_report': report,
            'writes_first_score': False,
        }
    parsed = _mobile_exchange_pnl(page.get('html'))

    def _as_rows(block: Any) -> list[dict[str, Any]]:
        rows = []
        for side in ('胜', '平', '负'):
            item = (block or {}).get(side) if isinstance(block, dict) else None
            if isinstance(item, dict):
                rows.append({'wdl': side, **item})
        return rows

    out = {
        'available': bool(parsed.get('available')),
        'channel': 'mobile',
        'source_path': '手机竞足→析→盈亏',
        'match_id': mid,
        'jc_saved': {'rows': _as_rows(parsed.get('jc_saved'))},
        'jc_popularity': {'rows': _as_rows(parsed.get('jc_popularity'))},
        'betfair_trade': {'rows': _as_rows(parsed.get('betfair_trade'))},
        'betfair_available': bool(parsed.get('betfair_available')),
        'betfair_note': parsed.get('betfair_note') or '',
        'saved_total': parsed.get('saved_total'),
        'parse_stage': parsed.get('stage'),
        'source_report': report,
        'writes_first_score': False,
    }
    if not out['available']:
        out['reason'] = 'okooo_exchange_no_pnl_rows'
        out['block_reason'] = 'okooo_exchange_no_pnl_rows'
        out['error'] = (
            f"{url} 200 {report['bytes']}B title={report['title'] or '-'} "
            f"stage={parsed.get('stage')}"
        )
    return out


_ABSENCE_STATE = {'伤': '伤', '停': '停', '疑': '疑'}


def _parse_lineup_page(html: Any) -> dict[str, Any]:
    """解析桌面 /soccer/match/<id>/formation/ 阵容页。

    真实结构（2026-11-25 现场核对，1347776 191,730B / 1347778 199,508B，gb18030）：
      * `div.match-battle-field`：左半 =.field-flex-col（主队），
        右半 = `div.field-right`（客队），共 22 个 `div.field-player-box`，
        每个含 `.qius`(球衣号) / `.player-name` / `.player-price`。
      * `div.match-battle-substitution`：`主队替补` / `客队替补` 两块，
        每块若干 `a.name.playersObj` + `.price`。
      * `div.match-battle-shangting.item-home` / `.item-away`：伤停，
        每个 `div.shangting-list-item` 含 `.item-state`(伤/停) /
        `.item-text`(位置) / `.item-detail a.name` + `.price` + `.text`。
    页面上「右半」用 class field-right 标出，主客以位置区分，不靠队名猜。
    """
    text = str(html or '')
    out: dict[str, Any] = {
        'available': False, 'stage': 'ok',
        'home': {}, 'away': {},
        'starters': [], 'substitutes': [], 'absences': [],
    }
    if 'match-battle-field' not in text:
        out['stage'] = 'no_battle_field'
        return out

    def _txt(raw: str) -> str:
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', raw)).strip()

    def _players(chunk: str, side: str) -> list[dict[str, Any]]:
        """切出所有 field-player-box。

        不能靠 `</div></a>` 收尾：页面最后一个球员的闭合标签写法不同，
        会漏掉一人（客队只剩 10）。这里按 box 起点切片。
        """
        # class 有两种写法：'field-player-box' 与 'field-player-box left-price'
        # （客队/左价球员用后者）。只匹配前者会静默漏掉整半支球队。
        starts = [m.start() for m in re.finditer(r'<div class="field-player-box\b', chunk)]
        players = []
        for idx, start in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else len(chunk)
            box = chunk[start:end]
            number = re.search(r'<div class="qius"[^>]*>\s*([^<]*?)\s*</div>', box)
            name = re.search(r'<div class="player-name">\s*([^<]*?)\s*</div>', box)
            price = re.search(r'<div class="player-price">\s*([^<]*?)\s*</div>', box)
            if not name:
                continue
            players.append({
                'side': side,
                'number': (number.group(1).strip() if number else ''),
                'name': name.group(1).strip(),
                'price': price.group(1).strip() if price else '',
            })
        return players

    field = re.search(r'<div class="match-battle-field">([\s\S]*?)\Z', text)
    field_body = field.group(1) if field else text
    right_at = field_body.find('field-right')
    left_part = field_body[:right_at] if right_at > 0 else field_body
    right_part = field_body[right_at:] if right_at > 0 else ''
    home_starters = _players(left_part, 'home')
    away_starters = _players(right_part, 'away')
    if not away_starters and right_at > 0:
        # 右半没有 field-player-box 时，按 11 人切分（页面顺序：主队 11 后客队 11）。
        all_players = _players(field_body, 'home')
        home_starters, away_starters = all_players[:11], [
            {**p, 'side': 'away'} for p in all_players[11:]
        ]

    subs = {'home': [], 'away': []}
    sub_at = text.find('match-battle-substitution')
    if sub_at > 0:
        sub_block = text[sub_at:]
        blocks = re.split(r'<div class="item-title">\s*(主队替补|客队替补)\s*</div>', sub_block)
        for idx in range(1, len(blocks) - 1, 2):
            label = blocks[idx]
            side = 'home' if '主队' in label else 'away'
            # 价格 div 里还嵌着 <em> 标记，所以按 item-item 起点切片而不是用收尾正则；
            # 价格取 <div class="price"> 到下一个 <em 之前的纯文本。
            body = blocks[idx + 1]
            starts = [m.start() for m in re.finditer(r'<div class="item-item clearfix">', body)]
            for j, start in enumerate(starts):
                end = starts[j + 1] if j + 1 < len(starts) else len(body)
                item = body[start:end]
                name = re.search(r'class="name playersObj"[^>]*>([^<]*?)</a>', item)
                if not name:
                    continue
                price = re.search(r'<div class="price">([\s\S]*?)(?:<em|</div>)', item)
                subs[side].append({
                    'side': side,
                    'name': name.group(1).strip(),
                    'price': re.sub(r'\s+', ' ', price.group(1)).strip() if price else '',
                })

    absences = []
    for side, cls in (('home', 'item-home'), ('away', 'item-away')):
        block = re.search(
            rf'<div class="match-battle-shangting {cls}">([\s\S]*?)(?=<div class="match-battle-shangting|'
            rf'<div class="match-battle-item|</section>)', text)
        if not block:
            continue
        for item in re.findall(r'<div class="shangting-list-item clearfix">([\s\S]*?)</div>\s*</div>\s*</div>', block.group(1)):
            state = re.search(r'<div class="item-state[^"]*">\s*([^<]*?)\s*</div>', item)
            position = re.search(r'<div class="item-text">\s*([^<]*?)\s*</div>', item)
            name = re.search(r'class="name playersObj"[^>]*>([^<]*?)</a>', item)
            price = re.search(r'<div class="price">\s*([^<]*?)\s*</div>', item)
            note = re.search(r'<div class="text">\s*([^<]*?)\s*</div>', item)
            if not name:
                continue
            absences.append({
                'side': side,
                'state': (state.group(1).strip() if state else ''),
                'position': (position.group(1).strip() if position else ''),
                'name': name.group(1).strip(),
                'price': (price.group(1).strip() if price else ''),
                'note': (note.group(1).strip() if note else ''),
            })

    out['home'] = {'starters': home_starters, 'substitutes': subs['home'],
                   'absences': [a for a in absences if a['side'] == 'home']}
    out['away'] = {'starters': away_starters, 'substitutes': subs['away'],
                   'absences': [a for a in absences if a['side'] == 'away']}
    out['starters'] = home_starters + away_starters
    out['substitutes'] = subs['home'] + subs['away']
    out['absences'] = absences
    out['home_count'] = len(home_starters)
    out['away_count'] = len(away_starters)
    out['available'] = bool(out['starters'] or out['substitutes'] or out['absences'])
    if not out['available']:
        out['stage'] = 'no_players_parsed'
    return out


def scrape_okooo_mobile_lineup(match_id: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """每场阵容：桌面 `/soccer/match/<id>/formation/`（手机端没有阵容页）。

    已探明的死路（不要再试）：桌面 lineup/ players/ shuju/ → 405/404；
    手机 lineup.php formation.php analysis.php team.php → 404；
    手机 players.php 是「每场球员数据」，不是首发名单。
    """
    mid = str(match_id or '').strip()
    if not mid:
        return {
            'available': False, 'match_id': '', 'channel': 'desktop_formation',
            'home': {}, 'away': {}, 'starters': [], 'substitutes': [], 'absences': [],
            'reason': 'no_match_id', 'block_reason': 'no_match_id',
        }
    url = f'https://www.okooo.com/soccer/match/{mid}/formation/'
    page = _fetch_detail(url, proxies=kwargs.get('proxies'), session=kwargs.get('session'), retries=1)
    # 阵容页与波胆页同受间歇 WAF 405 影响（现场见过同一 URL 隔几十秒转 200）。
    # 有界耐心重试，仍然失败就如实回报，不编名单。
    patience = int(kwargs.get('patience_retries', os.environ.get('OKOOO_LINEUP_PATIENCE') or 2) or 0)
    gap = float(kwargs.get('patience_gap', os.environ.get('OKOOO_LINEUP_PATIENCE_GAP') or 35) or 35)
    for _attempt in range(patience):
        if page.get('ok') or not page.get('blocked'):
            break
        time.sleep(gap)
        page = _fetch_detail(url, proxies=kwargs.get('proxies'), session=kwargs.get('session'), retries=1)
    report = {
        'url': url, 'status': int(page.get('status') or 0),
        'bytes': int(page.get('bytes') or 0), 'title': str(page.get('title') or ''),
        'blocked': bool(page.get('blocked')),
    }
    base = {
        'match_id': mid, 'channel': 'desktop_formation', 'source_path': '桌面 析→阵容',
        'source_report': report,
    }
    if not page.get('ok'):
        return {
            **base, 'available': False, 'home': {}, 'away': {}, 'starters': [],
            'substitutes': [], 'absences': [],
            'reason': 'external_okooo_formation_fetch_failed',
            'block_reason': 'external_okooo_formation_fetch_failed',
            'error': f"{url} → HTTP {report['status'] or page.get('error')}",
        }
    parsed = _parse_lineup_page(page.get('html'))
    out = {**base, **parsed}
    if not out.get('available'):
        out['reason'] = 'okooo_formation_parse_empty'
        out['block_reason'] = 'okooo_formation_parse_empty'
        out['error'] = (
            f"{url} 200 {report['bytes']}B title={report['title'] or '-'} "
            f"stage={parsed.get('stage')}"
        )
    return out


# 竞彩比分 31 个选项的官方代码顺序：30..42 主胜、43..47 平、48..60 客胜。
# 与 oddsData.OddsList.SportteryScore 的键一一对应。
_JCZQ_SCORE_CODES: tuple[tuple[str, str], ...] = (
    *((str(30 + i), s) for i, s in enumerate(
        ['1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2', '5-0', '5-1', '5-2', '胜其它']
    )),
    *((str(43 + i), s) for i, s in enumerate(['0-0', '1-1', '2-2', '3-3', '平其它'])),
    *((str(48 + i), s) for i, s in enumerate(
        ['0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4', '0-5', '1-5', '2-5', '负其它']
    )),
)
_JCZQ_CODE_TO_SCORE = dict(_JCZQ_SCORE_CODES)
JCZQ_SCORE_OPTIONS = tuple(score for _code, score in _JCZQ_SCORE_CODES)


def _decode_js_object(text: str, marker: str = 'var oddsData') -> dict[str, Any] | None:
    """从页面里切出 `var oddsData = {...}` 并 json.loads。

    不能直接对切片 json.loads：切片边界要按花括号配对算（还要跳过字符串里的括号）。
    """
    start = text.find(marker)
    if start < 0:
        return None
    brace = text.find('{', start)
    if brace < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    end = -1
    for idx in range(brace, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end < 0:
        return None
    try:
        data = json.loads(text[brace:end])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _jc_orderid_for_match(html: str, match_id: Any) -> str:
    """在竞足赛程页里把 matchid 映射到 orderid（oddsData 是按 orderid 键的）。

    同一行里有 `matchid="<id>"` 和 `id="match_<orderid>" orderid="<orderid>"`，
    但两者先后不定，所以按 jsMatchItem 分块后在同一块内取。
    """
    wanted = str(match_id or '').strip()
    if not wanted:
        return ''
    for block in re.split(r'<div[^>]*class="[^"]*jsMatchItem[^"]*"', str(html or '')):
        if f'matchid="{wanted}"' not in block:
            continue
        found = re.search(r'orderid="(\d+)"', block)
        if found:
            return found.group(1)
    return ''


def parse_jczq_score_odds(html: Any, match_id: Any) -> dict[str, Any]:
    """从 m.okooo.com/jczq/ 取本场 31 个竞彩比分赔率。

    返回 {'available', 'odds': {score: odd}, 'orderid', 'raw_count', 'stage'}。
    这是 `jc_score_odds` 的唯一真实来源（此前只有读者没有写者，31池比分永远空）。
    """
    text = str(html or '')
    out: dict[str, Any] = {'available': False, 'odds': {}, 'orderid': '', 'raw_count': 0, 'stage': 'ok'}
    data = _decode_js_object(text)
    if not data:
        out['stage'] = 'odds_data_missing'
        return out
    orderid = _jc_orderid_for_match(text, match_id)
    if not orderid:
        out['stage'] = 'orderid_not_found'
        return out
    out['orderid'] = orderid
    _slot_raw = data.get(orderid)
    slot: dict[str, Any] = _slot_raw if isinstance(_slot_raw, dict) else {}
    score_block = ((slot.get('OddsList') or {}).get('SportteryScore') or {})
    if not isinstance(score_block, dict) or not score_block:
        out['stage'] = 'sporttery_score_missing'
        return out
    odds: dict[str, float] = {}
    for code, score in _JCZQ_SCORE_CODES:
        value = _num_of(score_block.get(code))
        if value is not None and value > 1:
            odds[score] = value
    out['raw_count'] = len(score_block)
    out['odds'] = odds
    out['available'] = len(odds) >= 31
    if not out['available']:
        out['stage'] = f'incomplete_{len(odds)}_of_31'
    return out


# apply_mobile_books_lines 要搬的字段：三块水位 + 它们的来源与水位值。
# odds_1x2 漏搬过一次，导致「抓到了但记录还是空的」，进而触发重复抓取。
_MOBILE_BOOKS_FIELDS = (
    'ou_line', 'ou_line_text', 'ou_start_line', 'ou_over_water', 'ou_under_water', 'ou_line_source',
    'asian_handicap', 'asian_handicap_text', 'ah_start_line', 'ah_start_text',
    'ah_home_water', 'ah_away_water', 'ah_line_source',
    'odds_1x2', 'odds_1x2_company',
)
_MOBILE_BOOKS_PROVENANCE = ('boards_hit', 'missing', 'source_report', 'fetch_urls', 'channel', 'match_id')


def _is_filled(value: Any) -> bool:
    """一个值算不算「有内容」。空串/空容器/None 都算没有。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def normalize_cs_key(score: Any) -> str:
    """比分键归一：`1:0` / `1：0` / 空格 → `1-0`。

    桌面 /cs/ 页给的是冒号形式，竞彩31项给的是横杠形式；不归一时两边永远对不上，
    日志里就会同时出现「市均 25 项」和「31全表缺 9 项」两个都对的说法。
    """
    return str(score or '').replace('：', '-').replace(':', '-').replace(' ', '').strip()


def merge_cs31(
    page_avg: Any = None,
    books_avg: Any = None,
    jc_odds: Any = None,
) -> dict[str, Any]:
    """把桌面波胆市均与竞彩官方31比分合并成竞彩31项全表，带逐键出处。

    这是 31 池唯一的合并入口：抓取、补抓、闸门、三视图都调它，避免各算各的。

    规则：
      * 市均（page_avg 优先，books_avg 兜底）先归一键，占它覆盖得到的项；
      * 竞彩官方 31 补市均缺的项（5-0/5-1/5-2/0-5/1-5/2-5 与三个「其它」）；
      * 市均里不属于竞彩31项的键（4-3/3-4/4-4）单独留在 extras，绝不删除，
        但也不计入 31；
      * 每一项记 provenance：desktop_cs / jc_official。

    返回 {'scores': {option: odd}, 'provenance': {option: src},
          'extras': {k: odd}, 'counts': {...}, 'complete': bool}。
    """
    from_page: dict[str, float] = {}
    for source in (page_avg, books_avg):
        if not isinstance(source, dict):
            continue
        for key, odd in source.items():
            norm = normalize_cs_key(key)
            raw = odd.get('odds') if isinstance(odd, dict) else odd
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if norm and value > 1 and norm not in from_page:
                from_page[norm] = value
    from_jc: dict[str, float] = {}
    if isinstance(jc_odds, dict):
        for key, odd in jc_odds.items():
            norm = normalize_cs_key(key)
            raw = odd.get('odds') if isinstance(odd, dict) else odd
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if norm and value > 1:
                from_jc[norm] = value

    jc_set = set(JCZQ_SCORE_OPTIONS)
    scores: dict[str, float] = {}
    provenance: dict[str, str] = {}
    extras: dict[str, float] = {}
    for option in JCZQ_SCORE_OPTIONS:
        if option in from_page:
            scores[option] = from_page[option]
            provenance[option] = 'desktop_cs'
        elif option in from_jc:
            scores[option] = from_jc[option]
            provenance[option] = 'jc_official'
    for key, value in from_page.items():
        if key not in jc_set:
            extras[key] = value
    counts = {'desktop_cs': 0, 'jc_official': 0}
    for src in provenance.values():
        counts[src] = counts.get(src, 0) + 1
    return {
        'scores': scores,
        'provenance': provenance,
        'extras': extras,
        'counts': counts,
        'missing': [o for o in JCZQ_SCORE_OPTIONS if o not in scores],
        'complete': len(scores) == 31,
        'row_count': len(scores),
    }
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def seed_mobile_ah_ou_books(record: dict[str, Any], pack: dict[str, Any] | None = None) -> dict[str, Any]:
    """把盘口包放进 record['mobile_ah_ou_books']，供 apply_mobile_books_lines 读取。

    两个包在同一个键上形状不同，必须都留住：
      * books 包（scrape_okooo_mobile_ah_ou_books）：asian_handicap 是
        {'rows': [...], 'companies': [{name, pid, start, latest, changes, ...}]}
      * six 包（scrape_okooo_mobile_six_markets）：asian_handicap 是标量 -0.5
    以前直接 dict.update，标量把公司明细整块覆盖掉，刚抓到的 change.php 时间线就没了。
    现在撞到「一边 dict、一边标量」时：明细留在原键，标量另存为
    asian_handicap_line（apply_mobile_books_lines 需要这个数）。
    历史用 setdefault，先挂上的空壳会把真数据挡在外面，这里也一并按字段合并。
    """
    if not isinstance(record, dict):
        return {'available': False}
    incoming = pack if isinstance(pack, dict) else {}
    existing = record.get('mobile_ah_ou_books')
    if not isinstance(existing, dict):
        merged = dict(incoming) if incoming else {'available': False}
        _lift_scalar_ah(merged)
        record['mobile_ah_ou_books'] = merged
        return merged
    merged = dict(existing)
    for key, value in incoming.items():
        if key == 'asian_handicap':
            _merge_ah_slot(merged, value)
            continue
        if _is_filled(value) or key not in merged:
            merged[key] = value
    _lift_scalar_ah(merged)
    record['mobile_ah_ou_books'] = merged
    return merged


def _merge_ah_slot(target: dict[str, Any], value: Any) -> None:
    """asian_handicap 这个键可能是明细 dict（books 包）或标量（six 包）。

    明细 dict 留在 asian_handicap；标量写 asian_handicap_line。
    两者同时到达时都保留，谁也不覆盖谁。
    """
    if isinstance(value, dict):
        current = target.get('asian_handicap')
        if isinstance(current, dict):
            merged = dict(current)
            for k, v in value.items():
                if _is_filled(v) or k not in merged:
                    merged[k] = v
            target['asian_handicap'] = merged
        else:
            # 已存的标量先搬去 asian_handicap_line，再把明细放回主键。
            if current is not None:
                target.setdefault('asian_handicap_line', current)
            target['asian_handicap'] = dict(value)
        return
    if value is None:
        return
    if isinstance(target.get('asian_handicap'), dict):
        target['asian_handicap_line'] = value
    else:
        target['asian_handicap'] = value


def _lift_scalar_ah(target: dict[str, Any]) -> None:
    """把 asian_handicap_line 里的标量同步到记录可直接读的位置。

    只补 asian_handicap_line，不动 asian_handicap（那是明细 dict）。
    """
    if isinstance(target.get('asian_handicap'), dict) and target.get('asian_handicap_line') is None:
        row = (target['asian_handicap'].get('rows') or [{}])
        first = row[0] if row and isinstance(row[0], dict) else {}
        _latest_raw = first.get('latest')
        latest: dict[str, Any] = _latest_raw if isinstance(_latest_raw, dict) else {}
        if latest.get('line') is not None:
            target['asian_handicap_line'] = latest.get('line')


def apply_mobile_books_lines(record: dict[str, Any], books: dict[str, Any] | None = None) -> dict[str, Any]:
    """把盘口包里的水位写回记录。

    三块水位都必须搬（odds_1x2 曾漏搬），并且带上来源字段，下游才能看到出处。
    规则：包里有值才写，绝不拿空值覆盖记录里已有的非空值。
    取值顺序：显式传入的包 → 已挂上的 mobile_ah_ou_books → six_market_mobile，
    逐字段取第一个有值的，这样空壳挡不住真数据。
    """
    if not isinstance(record, dict):
        return {}
    sources = [
        books,
        record.get('mobile_ah_ou_books'),
        record.get('six_market_mobile'),
    ]
    picked: dict[str, Any] = {}
    for key in _MOBILE_BOOKS_FIELDS:
        for source in sources:
            if not isinstance(source, dict):
                continue
            value = source.get(key)
            if key == 'asian_handicap' and isinstance(value, dict):
                # 同一个键在 books 包里是公司明细 dict。水位本身要写标量，
                # 明细另存 ah_detail —— 覆盖成一个 dict 会让下游算术直接崩。
                row = (value.get('rows') or [{}])
                first = row[0] if row and isinstance(row[0], dict) else {}
                _latest_raw2 = first.get('latest')
                latest2: dict[str, Any] = _latest_raw2 if isinstance(_latest_raw2, dict) else {}
                scalar = value.get('asian_handicap')
                if scalar is None:
                    scalar = latest2.get('line')
                picked.setdefault('ah_detail', value)
                if scalar is not None and 'asian_handicap' not in picked:
                    picked['asian_handicap'] = scalar
                continue
            if _is_filled(value):
                picked[key] = value
                break
    for key, value in picked.items():
        record[key] = value
    provenance: dict[str, Any] = {}
    for key in _MOBILE_BOOKS_PROVENANCE:
        for source in sources:
            if not isinstance(source, dict):
                continue
            value = source.get(key)
            if _is_filled(value):
                provenance[key] = value
                break
    if provenance:
        existing = record.get('mobile_water_provenance')
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(provenance)
        record['mobile_water_provenance'] = merged
    return record


def detect_six_market_contradictions(record: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    contradictions = []
    if record.get('predicted_ou') and record.get('ou_line') is None:
        contradictions.append({'type': 'missing_ou_line', 'severity': 'low'})
    return contradictions


def water_is_present(value: Any) -> bool:
    """水位字段算不算「有」。

    0.0 是真实盘口（平手盘 / 大小球 0 线），必须算有；None、空串、空容器、
    空 bool 之外的缺失都算没有。`not value` 会把 0.0 误判成缺失，
    于是全齐的记录被报缺、被整场重抓、被研判闸门挡住。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    return True


_WATER_FIELDS = ('asian_handicap', 'ou_line', 'odds_1x2')


def missing_six_market_waters(record: dict[str, Any]) -> list[str]:
    if not isinstance(record, dict):
        return ['invalid_record']
    return [key for key in _WATER_FIELDS if not water_is_present(record.get(key))]


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
