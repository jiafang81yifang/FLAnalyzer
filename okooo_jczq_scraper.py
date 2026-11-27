"""Okooo 竞足赛程抓取器：requests + bs4，不含 Playwright。

为什么不是 Playwright
--------------------
`https://m.okooo.com/jczq/` 是服务端直出的静态 HTML（实测 200 / 207365 字节 /
GBK / 23 张卡片），列表 DOM 和每场 31 项竞彩比分赔率都在首屏字节里，不需要跑 JS。
Playwright 要多下一套 ~150MB 浏览器，安装器也得跟着改，换不来任何数据。

实测补充（2026-11-26）：列表页里 `var oddsData` 一处就带着每场的
`OddsList.SportteryScore`，也就是竞彩 31 个比分选项的完整赔率——包括市均页面
从不报价的那 9 项（5-0/5-1/5-2/0-5/1-5/2-5/胜其它/平其它/负其它）。
所以一次列表请求 = 赛程 + 全部 31 项比分赔率，抓取器最有价值的产出在这里，
`/match/*.php` 子页只是可选增强（本机实测全被阿里云 WAF 405 拦掉）。

复用而不是重写
--------------
请求层完全复用 `okooo_mobile_cs`：解码顺序、`blocked` 判定（403/405 或命中 WAF
文案）、无 cookie 先 warm 拿 session、手机 UA 与代理口径。本模块**不另写一套
blocked 判定**——两套口径迟早漂移，WAF 拦截就会被当成普通网络错误。

子页链接规则
------------
只认 `/match/{history,odds,note}.php` 且 query 里 `MatchID` 是纯数字的链接；
`/saishi/<league_id>/zuixin/`、`https://f.okooo.com/goods/league/...` 这类
联赛页/商品页一律丢弃——它们不带 MatchID，抓回来也无法归属到某一场比赛。

注意：`note.php` 在真实 DOM 里挂在 `div[href]` 上而不是 `a[href]`，只选 `<a>`
会漏掉「态度」子页。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
from bs4 import BeautifulSoup

import okooo_mobile_cs as okooo

# ---- 复用 okooo_mobile_cs 的请求层（口径唯一） ----
# 解码：按 meta charset → utf-8 → gb18030，顺序错就会解出半对半错的乱码。
decode_html = okooo.decode_okooo_html
# 抓页 + blocked / origin_blocked / cache_hit 判定。这是本仓库对「被拦」的唯一口径，
# 本模块不另写一套——两套口径必然漂移，IP 拦截就会被误当成普通网络错误。
fetch_page = okooo._fetch_detail
# 单次 HTTP 往返（含 blocked / origin_blocked / 缓存证据判定）。探测层的重试
# 建立在它之上，保证「一次尝试 == 一次往返」，不与其他重试循环叠乘。
attempt_http = okooo._http_attempt
# 澳客对无 cookie 的直连请求直接回 405，必须先 warm 一次拿 session。
warm_session = okooo._warm_session
# 同一个数字口径（去逗号/百分号），赔率解析不另写一套。
to_number = okooo._num_of
MOBILE_WARMUP_URL = okooo._MOBILE_WARMUP_URL

LIST_URL = 'https://m.okooo.com/jczq/'
"""用户脚本里的 `/jingcai/` 实测 405；真实列表页是 `/jczq/`。"""

CARD_SELECTOR = 'div.jsMatchItem'
"""列表页唯一可靠的卡片选择器；命中数为 0 就必须报不可用，不能返回空列表。"""

ROW_SELECTORS = ('table tr', 'div.list-row', 'div.row', 'ul.row > li')
"""子页行容器。真实竞彩子页主要靠 `table tr`；div 类只作补充。"""

MAX_ROWS = 400
SUBPAGE_MAX_ATTEMPTS = 4
"""首次 + 最多重试 3 次。"""

SUBPAGE_MAX_WORKERS = 3
RETRY_JITTER = 0.6
POST_TASK_SLEEP = (1.2, 2.8)
LIST_TIMEOUT = 20
SUBPAGE_TIMEOUT = 20

TEAM_ALIAS_MAP = {
    '曼联': '曼彻斯特联',
    '曼城': '曼彻斯特城',
    '国米': '国际米兰',
    '大巴黎': '巴黎圣日耳曼',
}

SUBPAGE_TYPE_RULES = (
    ('odds', r'/match/odds\.php|欧指|欧赔|指数'),
    ('asian', r'/match/(?:handicap|ah)\.php|亚指|亚盘|让球'),
    ('analysis', r'/match/history\.php|交锋|历史|战绩'),
    ('detail', r'/match/note\.php|态度|推荐|情报'),
)
"""按「URL 在前、标题关键词在后」归类；顺序即优先级，未命中记 other。"""

JC31_ALWAYS_QUOTED_ELSEWHERE = (
    '5-0', '5-1', '5-2', '0-5', '1-5', '2-5', '胜其它', '平其它', '负其它',
)
"""9 个市均页面从不报价、但竞彩 SportteryScore 一定给的比分选项。"""

STATUS_OK = 'ok'
STATUS_BLOCKED = 'blocked'
STATUS_ERROR = 'error'

FINAL_COLUMNS = [
    'match_id', 'match_no', 'league', 'league_id', 'home', 'away',
    'score', 'handicap', 'wdl_home', 'wdl_draw', 'wdl_away',
    'hc_home', 'hc_draw', 'hc_away',
    'jc31_available', 'jc31_count', 'jc31_odds_json',
    'cs_status', 'cs_books_n', 'cs_origin_blocked',
    'cs_page_avg_json', 'cs_books_avg_json',
    'cs_age', 'cs_x_cache_status', 'cs_reason',
    'subpage_type', 'sub_url', 'status', 'attempts', 'http_status',
    'bytes', 'title', 'row_count', 'rows_text', 'error_msg',
]
"""导出列顺序。比分赔率整体序列化进 `jc31_odds_json`，避免 31 列摊平。"""

_ROW_NOISE = ('下载app', '客户端')
_local = threading.local()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def normalize_team_name(name: Any) -> str:
    """球队名标准化：先按别名表替换，再压空白。

    别名表是用户脚本里的原样口径（曼联→曼彻斯特联 等），保留以免下游匹配漂移。
    """
    text = re.sub(r'\s+', '', str(name or ''))
    return TEAM_ALIAS_MAP.get(text, text)


def classify_subpage(url: Any, title: Any = '') -> str:
    """按 URL/标题给子页归类：odds / asian / analysis / detail / other。"""
    haystack = f'{url or ""} {title or ""}'.lower()
    for kind, pattern in SUBPAGE_TYPE_RULES:
        if re.search(pattern, haystack):
            return kind
    return 'other'


def _text(node: Any) -> str:
    if node is None:
        return ''
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip()


def _odds_float(raw: Any) -> float | None:
    value = to_number(raw)
    return value if value is not None and value > 1 else None


def _soup(html: Any) -> BeautifulSoup:
    return BeautifulSoup(html or '', 'html.parser')


def _thread_session(proxies: Any, mobile: bool = True) -> Any:
    cache = getattr(_local, 'sessions', None)
    if cache is None:
        cache = _local.sessions = {}
    key = bool(mobile)
    session = cache.get(key)
    if session is None:
        session = warm_session(
            proxies, mobile=key,
            referer=MOBILE_WARMUP_URL if key else 'https://www.okooo.com/',
        )
        cache[key] = session
    return session


# --------------------------------------------------------------------------- #
# 统一请求入口：缓存命中探测
# --------------------------------------------------------------------------- #
STATUS_ORIGIN_BLOCKED = 'origin_blocked'
STATUS_TIMEOUT = 'timeout'
STATUS_HTTP_ERROR = 'http_error'
STATUS_OK_PROBE = 'ok'


_CACHE_BUSTER_PARAMS = (
    'cb', '_', '__', 'cachebuster', 'cache_buster', 'nocache', 'no_cache',
    'ts', 'timestamp', 't', 'r', 'rnd', 'rand', 'random', '_t', '_r',
)


def _assert_stable_url(url: str) -> None:
    """拒绝带 cache-buster 的 URL。

    实测：`/jczq/` 原样请求命中 CDN 缓存返回 200；同一路径加一个随机 query 就
    回源，被 WAF 拦成 405。所以给 URL 加随机参数不是「更保险」，而是把一个必然
    成功的请求变成必然失败。这里直接拒绝，避免有人日后「顺手加个时间戳」。
    """
    parsed = urlparse(url)
    # keep_blank_values：`?t=` 这种空值 cache-buster 默认会被 parse_qs 丢掉，
    # 那样就漏判了，所以必须保留空值参数。
    params = parse_qs(parsed.query, keep_blank_values=True)
    # 名字命中即拒绝，与取值无关：`?_=1712` 与 `?t=` 都是 cache-buster 的常见写法。
    offenders = sorted(
        name for name in params if name.strip().lower() in _CACHE_BUSTER_PARAMS
    )
    if offenders:
        raise ValueError(
            f'{url} 带 cache-buster 参数 {offenders}：给澳客 URL 加随机 query 会让'
            'CDN 缓存失效并回源，被 WAF 拦成 405。请原样使用列表页/子页 URL。'
        )


def fetch_with_cache_probe(
    url: str,
    *,
    proxies: Any = None,
    session: Any = None,
    retries: int = 2,
    timeout: int = LIST_TIMEOUT,
    mobile: bool | None = None,
) -> dict[str, Any]:
    """所有抓取请求的统一入口：先判缓存命中/源站 IP 拦截，再谈重试。

    返回在 `_fetch_detail` 的字段之上补齐两个布尔量和两个状态键：
      `cache_hit`       命中 CDN 缓存（`age` 有值且非零，或 `x-cache-status: HIT`）
      `origin_blocked`  未命中缓存 + 403/405 + WAF 拦截页 → 源站按出口 IP 拦截
      `probe_status`    归一化结果：ok / origin_blocked / timeout / http_error
      `attempts`        实际发出的 HTTP 往返次数

    重试规则（这是与旧行为的唯一区别）：
      * 命中缓存或 200        → ok
      * origin_blocked        → 立刻返回，**不计入重试次数**、不换 UA/Referer/session
      * 其它错误（超时、5xx） → 指数退避重试

    每次尝试只发**一次** HTTP 往返（直接走 `okooo_mobile_cs._http_attempt`），
    重试决策全部收在这一层，避免与 `_fetch_detail` 内部的重试互相叠乘。
    blocked 文案与解码口径仍只有 `_http_attempt` 那一份，本模块不另判一套。

    为什么要这样：2026-11-26 逐条实测证明源站按出口 IP 信誉拦截机房/云段，客户端
    指纹、UA、Referer、cookie 全部无效（证据清单见模块 docstring）。对 IP 拦截做
    重试只会加深封禁并拖慢整轮，所以它必须与「普通网络抖动」分开处置。

    **URL 必须逐字节保持不变。** 实测的反直觉事实：同一个路径、URL 一字不差且命中
    缓存时是 200，多加一个随机 query 就变成回源 405。所以「加 cache-buster」会把
    本来能成功的请求变成必然失败。这条防护写在代码里（`_assert_stable_url`），
    不只写在文档里——将来谁加 cache-buster 会立刻被这条断言拦下。
    """
    _assert_stable_url(url)
    if mobile is None:
        mobile = 'm.okooo.com' in url
    referers = okooo._referers_for(url) or [MOBILE_WARMUP_URL]
    active_session = session
    retries = max(1, int(retries or 1))
    last: dict[str, Any] = {}
    referers_tried: list[str] = []
    blocked_any = False
    attempts = 0
    for referer in referers:
        referers_tried.append(referer)
        for retry in range(retries):
            if active_session is None and attempts:
                active_session = _thread_session(proxies, mobile=mobile)
            last = attempt_http(
                url, proxies=proxies, session=active_session,
                referer=referer, timeout=timeout,
            )
            attempts += 1
            # 注意：`status` 始终是 HTTP 状态码（_http_attempt 的既有语义），
            # 归一化后的结果类别另放 `probe_status`，两个含义不挤一个键。
            last['cache_hit'] = bool(last.get('cache_hit'))
            last['origin_blocked'] = bool(last.get('origin_blocked'))
            last['attempts'] = attempts
            last['referers_tried'] = list(referers_tried)
            # 防御性检查：探测层自己绝不改写 URL（见 _assert_stable_url 的说明）。
            # 用显式抛错而不是 assert——assert 在 -O 下会被剥掉，这种防护不能失效。
            if str(last.get('url') or url) != url:
                raise RuntimeError(
                    f'probe rewrote the URL: {last.get("url")!r} != {url!r}'
                )
            if last.get('ok'):
                last['probe_status'] = STATUS_OK_PROBE
                return last
            if last['origin_blocked']:
                # 两种 405 的响应体完全一样（Referer 不对 vs 出口 IP 被拦），
                # 所以先换下一个 Referer 候选；候选全试完才算真的 IP 拦截。
                blocked_any = True
                break
            if not last.get('transport_error') and 400 <= int(last.get('status') or 0) < 500:
                # 非 WAF 的 4xx（404 等）是「页面没了」，换 Referer 也不会变好。
                last['probe_status'] = STATUS_HTTP_ERROR
                last['attempts'] = attempts
                return last
            last['probe_status'] = (
                STATUS_TIMEOUT if last.get('transport_error') else STATUS_HTTP_ERROR
            )
            if retry + 1 < retries:
                time.sleep(min(4, 2 ** retry) + random.uniform(0, RETRY_JITTER))  # nosec B311 - 退避抖动，非密码学用途
    if blocked_any:
        last['probe_status'] = STATUS_ORIGIN_BLOCKED
        last['origin_blocked'] = True
        last['attempts'] = attempts
        return last
    last.setdefault('probe_status', STATUS_HTTP_ERROR)
    last['attempts'] = attempts
    return last


# --------------------------------------------------------------------------- #
# 列表页解析
# --------------------------------------------------------------------------- #
def _match_id_from_href(href: str, base: str = LIST_URL) -> tuple[str, str]:
    """从一条链接里取 (子页类型, MatchID)；不是比赛子页就返回 ('', '')。"""
    resolved = urljoin(base, href.replace('&amp;', '&'))
    parsed = urlparse(resolved)
    name = parsed.path.rsplit('/', 1)[-1]
    kind = {'history.php': 'history', 'odds.php': 'odds', 'note.php': 'note'}.get(name)
    if not kind:
        return '', ''
    ids = parse_qs(parsed.query).get('MatchID') or []
    match_id = ids[0].strip() if ids else ''
    if not match_id.isdigit():
        return '', ''
    return kind, match_id


def _card_hrefs(card: Any) -> list[str]:
    """卡片内全部链接的绝对 URL，保序去重（真实 DOM 每张卡片正好 4 条）。

    用 `[href]` 而不是 `a[href]`：真实 DOM 里 `note.php` 挂在 `div` 上，
    只选 `<a>` 会丢掉「态度」子页。

    规则：非比赛链接（`/saishi/<league_id>/zuixin/` 联赛页、
    `f.okooo.com/goods/...` 商品页）**保留在 `sub_urls` 里**，因为它们确实是
    卡片上的链接、丢掉会让「4 条」这个实测事实对不上；但它们不带 MatchID、
    无法归属到某场比赛，所以归为 `other`，并且**永远不参与子页抓取**——
    抓取只用 `match_sub_urls`（带纯数字 MatchID 的那三条）。
    """
    urls: list[str] = []
    for node in card.select('[href]'):
        href = node.get('href') or ''
        if not href:
            continue
        resolved = urljoin(LIST_URL, href.replace('&amp;', '&'))
        if resolved not in urls:
            urls.append(resolved)
    return urls


def _match_sub_urls(sub_urls: list[str]) -> list[str]:
    """从卡片链接里挑出可抓的比赛子页（history/odds/note，且 MatchID 是数字）。"""
    return [url for url in sub_urls if _match_id_from_href(url)[0]]


def _betway_odds(card: Any, betway: str) -> dict[str, float]:
    """取某个 betway 组的三项赔率。实测 betway=0 胜平负、betway=1 让球胜平负。"""
    odds: dict[str, float] = {}
    for group in card.select('div.ctrl_betway_wrap'):
        if str(group.get('betway', '')).strip() != betway:
            continue
        for option in group.select('div.ctrl_betopt'):
            label = _text(option.select_one('p.ctrl_txt'))
            value = _odds_float(_text(option.select_one('p.ctrl_odds')))
            if label and value is not None:
                odds[label] = value
    return odds


def _parse_card(card: Any, html: str) -> dict[str, Any] | None:
    sub_urls = _card_hrefs(card)
    match_urls = _match_sub_urls(sub_urls)
    match_id = ''
    for url in match_urls:
        _kind, found = _match_id_from_href(url)
        if found:
            match_id = found
            break
    if not match_id:
        return None

    jc = okooo.parse_jczq_score_odds(html, match_id)
    # 未完赛卡片里 zVS 的位置写的是 "VS"，不是比分；按 '' 记，别让下游当成 0-0。
    score = _text(card.select_one('span.zVS'))
    if not re.search(r'\d+\s*-\s*\d+', score):
        score = ''
    return {
        'match_id': match_id,
        'league_id': str(card.get('data-mid', '') or '').strip(),
        'match_no': _text(card.select_one('p.xuhao')),
        'league': _text(card.select_one('a.liansai')),
        'home': normalize_team_name(_text(card.select_one('em.ctrl_homename'))),
        'away': normalize_team_name(_text(card.select_one('em.ctrl_awayname'))),
        'score': score,
        'handicap': _text(card.select_one('em.rangqiu')),
        'wdl_odds': _betway_odds(card, '0'),
        'handicap_odds': _betway_odds(card, '1'),
        'sub_urls': sub_urls,
        'match_sub_urls': match_urls,
        'jc_score_odds': jc['odds'],
        'jc31_available': bool(jc['available']),
        'jc_orderid': jc['orderid'],
        'jc_stage': jc['stage'],
    }


def fetch_match_list(
    session: Any = None,
    proxies: Any = None,
    html: Any = None,
) -> dict[str, Any]:
    """抓并解析 `m.okooo.com/jczq/` 列表页。

    `html` 传入时完全离线（喂夹具/离线模式），为 None 才真发一次请求。
    抓不到任何卡片时 `available=False` 且 `reason` 点名是 WAF 拦截还是选择器未命中。
    """
    info: dict[str, Any] = {
        'ok': False, 'status': 0, 'bytes': 0, 'blocked': False,
        'error': '', 'html': '', 'url': LIST_URL,
    }
    if html is None:
        session = session or warm_session(proxies, mobile=True, referer=MOBILE_WARMUP_URL)
        # 列表页是主路径，且实测稳定命中 CDN 缓存（age 有值）→ 200。
        info = fetch_with_cache_probe(
            LIST_URL, proxies=proxies, session=session,
            retries=3, timeout=LIST_TIMEOUT, mobile=True,
        )
        page = info.get('html') or ''
    else:
        page = html if isinstance(html, str) else decode_html(html)
        info['status'] = 200 if page else 0
        info['bytes'] = len(page.encode('utf-8'))
        info['ok'] = bool(page)

    soup = _soup(page)
    matches: list[dict[str, Any]] = []
    skipped = 0
    for card in soup.select(CARD_SELECTOR):
        parsed = _parse_card(card, page)
        if parsed is None:
            skipped += 1
            continue
        matches.append(parsed)

    hits = len(soup.select(CARD_SELECTOR))
    result: dict[str, Any] = {
        'available': bool(matches),
        'reason': '',
        'url': LIST_URL,
        'status': int(info.get('status') or 0),
        'blocked': bool(info.get('blocked')),
        'match_count': len(matches),
        'matches': matches,
        'source_bytes': int(info.get('bytes') or 0),
        'card_hits': hits,
        'skipped_cards': skipped,
        'jc31_match_count': sum(1 for m in matches if m['jc31_available']),
        'error': str(info.get('error') or ''),
        'cache_hit': bool(info.get('cache_hit')),
        'origin_blocked': bool(info.get('origin_blocked')),
        'age': str(info.get('age') or ''),
        'x_cache_status': str(info.get('x_cache_status') or ''),
        'server': str(info.get('server') or ''),
        'probe_status': str(info.get('probe_status') or ''),
    }
    if not matches:
        shape = f'{CARD_SELECTOR}={hits}, HTTP {result["status"]}, {result["source_bytes"]} bytes'
        if result['origin_blocked']:
            result['reason'] = (
                f'源站按出口 IP 拦截（WAF 405，{info.get("error") or "blocked"}；'
                f'x-cache-status={result["x_cache_status"] or "-"}, age={result["age"] or "-"}）: {shape}'
            )
        elif result['blocked']:
            result['reason'] = f'WAF 拦截（{info.get("error") or "blocked"}）: {shape}'
        elif not page:
            result['reason'] = f'列表页为空/请求失败（{info.get("error") or "empty response"}）: {shape}'
        else:
            result['reason'] = f'选择器未命中 {CARD_SELECTOR}: {shape}'
    return result


# --------------------------------------------------------------------------- #
# 子页（可选增强）
# --------------------------------------------------------------------------- #
def _drop_nested(nodes: list[Any]) -> list[Any]:
    """丢掉「包含另一个命中节点」的祖先，否则表容器会变成一整行。"""
    ids = {id(node) for node in nodes}
    kept: list[Any] = []
    for node in nodes:
        if any(id(child) in ids for child in node.find_all(True)):
            continue
        kept.append(node)
    return kept


def extract_rows(html: Any, max_rows: int = MAX_ROWS) -> list[str]:
    """抓子页的行文本。抓不到任何行容器就返回空列表——不伪造空行充数。"""
    if not html:
        return []
    soup = _soup(html)
    nodes: list[Any] = []
    seen: set[int] = set()
    for selector in ROW_SELECTORS:
        for node in soup.select(selector):
            if id(node) in seen:
                continue
            seen.add(id(node))
            nodes.append(node)
    rows: list[str] = []
    for node in _drop_nested(nodes):
        text = _text(node)
        if text:
            rows.append(text)
        if len(rows) >= max_rows:
            break
    return rows


def _subpage_record(
    match: dict[str, Any], sub_url: str, proxies: Any
) -> dict[str, Any]:
    """抓一个子页，并如实记录发生了什么。blocked 立刻收手，不重试到上限。"""
    kind = classify_subpage(sub_url)
    record: dict[str, Any] = {
        'match_id': str(match.get('match_id', '')),
        'match_no': match.get('match_no', ''),
        'league': match.get('league', ''),
        'home': match.get('home', ''),
        'away': match.get('away', ''),
        'sub_url': sub_url,
        'subpage_type': kind,
        'status': STATUS_ERROR,
        'attempts': 0,
        'http_status': 0,
        'bytes': 0,
        'title': '',
        'rows': [],
        'error_msg': '',
        'cache_hit': False,
        'origin_blocked': False,
        'age': '',
        'x_cache_status': '',
        'traceid': '',
        'referers_tried': [],
    }
    # 走统一入口：缓存命中 / 源站 IP 拦截 / 普通抖动的判定与重试策略全在探测层，
    # 子页这里不再自己判 blocked、也不再自己写退避。
    probe = fetch_with_cache_probe(
        sub_url, proxies=proxies, session=_thread_session(proxies),
        retries=SUBPAGE_MAX_ATTEMPTS, timeout=SUBPAGE_TIMEOUT,
    )
    record.update({
        'attempts': int(probe.get('attempts') or 0),
        'http_status': int(probe.get('status') or 0),
        'bytes': int(probe.get('bytes') or 0),
        'title': str(probe.get('title') or ''),
        'cache_hit': bool(probe.get('cache_hit')),
        'origin_blocked': bool(probe.get('origin_blocked')),
        'age': str(probe.get('age') or ''),
        'x_cache_status': str(probe.get('x_cache_status') or ''),
        'traceid': str(probe.get('traceid') or ''),
        'referers_tried': list(probe.get('referers_tried') or []),
    })
    if probe.get('ok'):
        rows = extract_rows(probe.get('html') or '')
        if rows:
            record['status'] = STATUS_OK
            record['rows'] = rows
        else:
            # 200 但没有任何行容器：结构未命中，重试也是同一份字节。
            record['status'] = STATUS_ERROR
            record['error_msg'] = 'no_row_container: 页面无 table tr / div 行容器'
    elif probe.get('origin_blocked'):
        record['status'] = STATUS_ORIGIN_BLOCKED
        record['error_msg'] = (
            f'源站按出口 IP 拦截（HTTP {record["http_status"]}，响应体 block_message；'
            f'x-cache-status={record["x_cache_status"] or "-"}, age={record["age"] or "-"}；'
            f'已试 {len(record["referers_tried"])} 个 Referer 候选）'
            '；这不是页面结构变化'
        )
    else:
        record['status'] = STATUS_ERROR
        record['error_msg'] = str(
            probe.get('error') or f'HTTP {record["http_status"]}'
        )
    time.sleep(random.uniform(*POST_TASK_SLEEP))  # nosec B311 - 限速抖动不是密码学用途
    return record


def fetch_subpages(
    matches: list[dict[str, Any]],
    proxies: Any = None,
    max_workers: int = SUBPAGE_MAX_WORKERS,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """并发抓所有比赛子页。requests 是阻塞的，所以用线程池而不是 page pool。"""
    tasks: list[tuple[dict[str, Any], str]] = []
    for match in matches:
        for sub_url in match.get('match_sub_urls') or _match_sub_urls(
            match.get('sub_urls') or []
        ):
            tasks.append((match, sub_url))
    if limit is not None:
        tasks = tasks[:max(0, limit)]
    if not tasks:
        return []
    workers = max(1, min(int(max_workers or 1), len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_subpage_record, match, url, proxies) for match, url in tasks]
        return [future.result() for future in futures]


# --------------------------------------------------------------------------- #
# 市均（桌面波胆页）：默认不可用，如实上报 IP 拦截
# --------------------------------------------------------------------------- #
CS_URL_TEMPLATE = 'https://www.okooo.com/soccer/match/{match_id}/cs/'
CS_ALWAYS_SUMMED = ()


def fetch_market_average(
    match: dict[str, Any],
    *,
    proxies: Any = None,
    cs_html: Any = None,
) -> dict[str, Any]:
    """取某场的市均比分（25 项）。

    **默认在本机不可用**：桌面波胆页 `/soccer/match/<id>/cs/` 需要住宅 IP，
    机房/云段一律被阿里云 WAF 拦成 405（响应体 `block_message`）。这里绝不重试、
    绝不换 UA/Referer、也绝不拿竞彩价冒充市均——三者都会让下游拿到假数据。

    传入 `cs_html`（`--cs-html <dir>` 或调用方注入）时走离线解析路径：
    仓库 `tests/fixtures/okooo_desktop_cs_1347776.html` 是真实抓包，
    实测解出 25 项市均 / 27 家公司。
    """
    match_id = str(match.get('match_id', '') or '')
    url = CS_URL_TEMPLATE.format(match_id=match_id)
    record: dict[str, Any] = {
        'match_id': match_id,
        'match_no': match.get('match_no', ''),
        'home': match.get('home', ''),
        'away': match.get('away', ''),
        'cs_url': url,
        'status': STATUS_BLOCKED,
        'reason': '',
        'origin_blocked': False,
        'cache_hit': False,
        'age': '',
        'x_cache_status': '',
        'referers_tried': [],
        'attempts': 0,
        'http_status': 0,
        'bytes': 0,
        'page_avg': {},
        'books_avg': {},
        'books_n': 0,
        'parse_stage': '',
        'error_msg': '',
    }

    if cs_html is not None:
        if isinstance(cs_html, (str, Path)) and Path(str(cs_html)).exists():
            cs_html = Path(str(cs_html)).read_bytes()
        html = cs_html if isinstance(cs_html, str) else decode_html(cs_html)
        parsed = okooo._parse_cs_datatable(html)
        record.update({
            'http_status': 200,
            'bytes': len(html.encode('utf-8')),
            'parse_stage': parsed.get('stage', ''),
            'page_avg': parsed.get('page_avg') or {},
            'books_avg': parsed.get('books_avg') or {},
            'books_n': int(parsed.get('books_n') or 0),
        })
        if parsed.get('books_avg'):
            record['status'] = STATUS_OK
        else:
            record['status'] = STATUS_ERROR
            record['reason'] = (
                f'离线市均 HTML 解析失败（stage={parsed.get("stage")}，'
                f'headers={parsed.get("header_count")}，rows={parsed.get("row_count")}）'
            )
            record['error_msg'] = record['reason']
        return record

    probe = fetch_with_cache_probe(
        url, proxies=proxies, session=_thread_session(proxies, mobile=False),
        retries=1, timeout=SUBPAGE_TIMEOUT, mobile=False,
    )
    record.update({
        'attempts': int(probe.get('attempts') or 0),
        'http_status': int(probe.get('status') or 0),
        'bytes': int(probe.get('bytes') or 0),
        'origin_blocked': bool(probe.get('origin_blocked')),
        'cache_hit': bool(probe.get('cache_hit')),
        'age': str(probe.get('age') or ''),
        'x_cache_status': str(probe.get('x_cache_status') or ''),
        'referers_tried': list(probe.get('referers_tried') or []),
        'cache_origin': 'okooo.com',
    })
    if probe.get('ok'):
        parsed = okooo._parse_cs_datatable(probe.get('html') or '')
        record.update({
            'status': STATUS_OK,
            'parse_stage': parsed.get('stage', ''),
            'page_avg': parsed.get('page_avg') or {},
            'books_avg': parsed.get('books_avg') or {},
            'books_n': int(parsed.get('books_n') or 0),
        })
        return record
    if probe.get('origin_blocked'):
        record['status'] = STATUS_ORIGIN_BLOCKED
        record['reason'] = (
            '源站按出口 IP 拦截：CDN 未命中（'
            f'x-cache-status={record["x_cache_status"] or "-"}, age={record["age"] or "-"}），'
            f'回源被阿里云 WAF 拦成 HTTP {record["http_status"]}（响应体 block_message）；'
            f'已按顺序试过 {len(record["referers_tried"])} 个 Referer 候选全部被拦。'
            '客户端指纹/UA/cookie 均无效（Referer 也已穷尽），需要住宅 IP 或缓存命中；'
            '这不是页面结构变化。'
        )
    else:
        record['status'] = STATUS_ERROR
        record['reason'] = (
            f'市均页抓取失败（HTTP {record["http_status"]}, {probe.get("error") or "unknown"}）'
        )
    record['error_msg'] = record['reason']
    return record


# --------------------------------------------------------------------------- #
# 清洗与导出


# --------------------------------------------------------------------------- #
# 清洗与导出
# --------------------------------------------------------------------------- #
def _denoise_rows(rows: list[str]) -> list[str]:
    cleaned: list[str] = []
    for row in rows or []:
        text = re.sub(r'\s+', ' ', str(row or '')).strip()
        if not text:
            continue
        lowered = text.lower()
        if any(noise in lowered for noise in _ROW_NOISE):
            continue
        cleaned.append(text)
    return cleaned


def _match_base_row(match: dict[str, Any]) -> dict[str, Any]:
    wdl = match.get('wdl_odds') or {}
    hc = match.get('handicap_odds') or {}
    jc = match.get('jc_score_odds') or {}
    row: dict[str, Any] = {column: '' for column in FINAL_COLUMNS}
    row.update({
        'match_id': str(match.get('match_id', '')),
        'match_no': match.get('match_no', ''),
        'league': match.get('league', ''),
        'league_id': match.get('league_id', ''),
        'home': match.get('home', ''),
        'away': match.get('away', ''),
        'score': match.get('score', ''),
        'handicap': match.get('handicap', ''),
        'wdl_home': wdl.get('胜', ''),
        'wdl_draw': wdl.get('平', ''),
        'wdl_away': wdl.get('负', ''),
        'hc_home': hc.get('胜', ''),
        'hc_draw': hc.get('平', ''),
        'hc_away': hc.get('负', ''),
        'jc31_available': bool(match.get('jc31_available')),
        'jc31_count': len(jc),
        'jc31_odds_json': json.dumps(jc, ensure_ascii=False) if jc else '',
    })
    return row


def _subpage_columns(record: dict[str, Any]) -> dict[str, Any]:
    rows = _denoise_rows(record.get('rows') or [])
    return {
        'subpage_type': record.get('subpage_type', ''),
        'sub_url': record.get('sub_url', ''),
        'status': record.get('status', ''),
        'attempts': record.get('attempts', ''),
        'http_status': record.get('http_status', ''),
        'bytes': record.get('bytes', ''),
        'title': record.get('title', ''),
        'row_count': len(rows),
        'rows_text': ' | '.join(rows),
        'error_msg': record.get('error_msg', ''),
    }


def _dedupe_records(subpage_records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    for record in subpage_records or []:
        key = (str(record.get('match_id', '')), str(record.get('sub_url', '')))
        if key in seen:
            continue
        seen.add(key)
        kept.append(record)
    return kept


def _cs_columns(record: dict[str, Any] | None) -> dict[str, Any]:
    """市均记录的导出列。缺口原样写进 `cs_reason`，日志里能一眼看出是 IP 拦截。"""
    if not record:
        return {
            'cs_status': '', 'cs_books_n': '', 'cs_origin_blocked': '',
            'cs_page_avg_json': '', 'cs_books_avg_json': '', 'cs_age': '',
            'cs_x_cache_status': '', 'cs_reason': '',
        }
    return {
        'cs_status': record.get('status', ''),
        'cs_books_n': record.get('books_n', ''),
        'cs_origin_blocked': bool(record.get('origin_blocked')),
        'cs_page_avg_json': json.dumps(record.get('page_avg') or {}, ensure_ascii=False)
        if record.get('page_avg') else '',
        'cs_books_avg_json': json.dumps(record.get('books_avg') or {}, ensure_ascii=False)
        if record.get('books_avg') else '',
        'cs_age': record.get('age', ''),
        'cs_x_cache_status': record.get('x_cache_status', ''),
        'cs_reason': record.get('reason', ''),
    }


def clean_and_export(
    matches: list[dict[str, Any]],
    subpage_records: list[dict[str, Any]] | None,
    output_dir: str | Path = 'output',
    cs_records: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """去重、去噪并导出 CSV（utf-8-sig）与 XLSX。

    `openpyxl` 不是核心依赖：缺失时只跳过 XLSX，CSV 照常写出，并打印一条可直接
    执行的补救命令。返回值是 DataFrame，`xlsx_written` 等结果放在 `.attrs` 里。
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cs_by_match: dict[str, dict[str, Any]] = {}
    for record in cs_records or []:
        cs_by_match.setdefault(str(record.get('match_id', '')), record)

    by_match: dict[str, list[dict[str, Any]]] = {}
    for record in _dedupe_records(subpage_records):
        by_match.setdefault(str(record.get('match_id', '')), []).append(record)

    rows: list[dict[str, Any]] = []
    for match in matches:
        base = {**_match_base_row(match),
                **_cs_columns(cs_by_match.get(str(match.get('match_id', ''))))}
        records = by_match.pop(str(match.get('match_id', '')), [])
        if not records:
            rows.append(base)
            continue
        for record in records:
            rows.append({**base, **_subpage_columns(record)})
    # 子页记录指向列表里没有的场次时也要留住，不能静默丢数据。
    for records in by_match.values():
        for record in records:
            base: dict[str, Any] = {column: '' for column in FINAL_COLUMNS}
            base.update({
                'match_id': str(record.get('match_id', '')),
                'match_no': record.get('match_no', ''),
                'league': record.get('league', ''),
                'home': record.get('home', ''),
                'away': record.get('away', ''),
            })
            rows.append({**base, **_cs_columns(None), **_subpage_columns(record)})

    frame = pd.DataFrame(rows, columns=FINAL_COLUMNS)

    stamp = time.strftime('%Y%m%d_%H%M%S')
    csv_path = out_dir / f'okooo_jczq_{stamp}.csv'
    frame.to_csv(csv_path, index=False, encoding='utf-8-sig')

    xlsx_path = out_dir / f'okooo_jczq_{stamp}.xlsx'
    xlsx_written = False
    try:
        import openpyxl  # noqa: F401 - 只为探测可选依赖是否可用
    except ImportError:
        print(
            'XLSX 未生成：本机缺少 openpyxl。CSV 已写出，可执行 '
            '`pip install openpyxl` 后重跑以一并导出 XLSX。',
            file=sys.stderr,
        )
    else:
        try:
            frame.to_excel(xlsx_path, index=False, engine='openpyxl')
            xlsx_written = True
        except Exception as exc:  # noqa: BLE001 - 导出失败只降级，不中断
            print(
                f'XLSX 未生成：{type(exc).__name__}: {exc}；CSV 已写出，可执行 '
                '`pip install openpyxl` 后重跑。',
                file=sys.stderr,
            )

    frame.attrs.update({
        'csv_path': str(csv_path),
        'xlsx_path': str(xlsx_path) if xlsx_written else '',
        'xlsx_written': xlsx_written,
        'row_count': len(frame),
        'match_count': len(matches),
    })
    return frame


# --------------------------------------------------------------------------- #
# 编排 + CLI
# --------------------------------------------------------------------------- #
def scrape(
    output_dir: str | Path = 'output',
    html: Any = None,
    with_subpages: bool = False,
    proxies: Any = None,
    limit: int | None = None,
    workers: int = SUBPAGE_MAX_WORKERS,
    cs_html: Any = None,
    with_cs: bool = False,
) -> dict[str, Any]:
    """抓列表 →（可选）市均/子页 → 导出。

    默认只打 `/jczq/` 列表页（主产出：赛程 + 每场 31 项竞彩比分）。
    `cs_html`（目录或 {match_id: html}）用于离线解析市均；`with_cs` 才尝试在线抓市均。
    """
    listing = fetch_match_list(proxies=proxies, html=html)
    records: list[dict[str, Any]] = []
    if with_subpages and listing['available']:
        targets = listing['matches'] if limit is None else listing['matches'][:limit]
        records = fetch_subpages(targets, proxies=proxies, max_workers=workers)
    cs_records: list[dict[str, Any]] = []
    if listing['available'] and (with_cs or cs_html is not None):
        targets = listing['matches'] if limit is None else listing['matches'][:limit]
        for match in targets:
            injected = _cs_html_for(cs_html, str(match.get('match_id', '')))
            if injected is None and not with_cs:
                continue
            cs_records.append(
                fetch_market_average(match, proxies=proxies, cs_html=injected)
            )
    frame = clean_and_export(listing['matches'], records, output_dir, cs_records)
    return {
        'listing': listing, 'subpage_records': records,
        'cs_records': cs_records, 'frame': frame,
    }


def _cs_html_for(cs_html: Any, match_id: str) -> Any:
    """从 `--cs-html` 的目录或映射里取某场的市均 HTML；没有就返回 None。"""
    if cs_html is None:
        return None
    if isinstance(cs_html, dict):
        return cs_html.get(match_id)
    base = Path(str(cs_html))
    if not base.exists():
        return None
    if base.is_file():
        return base
    for pattern in (f'{match_id}.html', f'cs_{match_id}.html', f'*{match_id}*.html'):
        found = sorted(base.glob(pattern))
        if found:
            return found[0]
    return None


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, '') or '')
        counts[value] = counts.get(value, 0) + 1
    return counts


def _summarize(result: dict[str, Any]) -> dict[str, Any]:
    listing = result['listing']
    records = result['subpage_records']
    statuses: dict[str, int] = {}
    for record in records:
        statuses[record['status']] = statuses.get(record['status'], 0) + 1
    frame = result['frame']
    return {
        'available': listing['available'],
        'reason': listing['reason'],
        'status': listing['status'],
        'blocked': listing['blocked'],
        'match_count': listing['match_count'],
        'jc31_match_count': listing['jc31_match_count'],
        'card_hits': listing['card_hits'],
        'skipped_cards': listing['skipped_cards'],
        'subpage_requests': len(records),
        'subpage_statuses': statuses,
        'cache_hit': listing.get('cache_hit', False),
        'age': listing.get('age', ''),
        'x_cache_status': listing.get('x_cache_status', ''),
        'cs_requests': len(result.get('cs_records') or []),
        'cs_statuses': _count_by(result.get('cs_records') or [], 'status'),
        'cs_origin_blocked': any(
            r.get('origin_blocked') for r in (result.get('cs_records') or [])
        ),
        'rows_written': int(frame.attrs.get('row_count', 0)),
        'csv_path': frame.attrs.get('csv_path', ''),
        'xlsx_path': frame.attrs.get('xlsx_path', ''),
        'xlsx_written': bool(frame.attrs.get('xlsx_written', False)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='抓取 m.okooo.com/jczq/ 赛程与竞彩 31 项比分赔率（requests + bs4）。',
    )
    parser.add_argument('--out', default='output', help='导出目录（默认 output/）')
    parser.add_argument(
        '--html', default=None,
        help='离线模式：直接解析本地 HTML 夹具，完全不发请求',
    )
    parser.add_argument(
        '--with-subpages', action='store_true',
        help='额外抓 /match/*.php 子页（默认关闭；本机实测被 WAF 405 拦）',
    )
    parser.add_argument(
        '--no-subpages', action='store_true',
        help='显式关闭子页（当前默认即为关闭，保留该开关以兼容旧命令行）',
    )
    parser.add_argument(
        '--cs-html', default=None,
        help='离线市均：本地 HTML 文件，或按 <match_id>.html 命名的目录（不联网即可解析市均）',
    )
    parser.add_argument(
        '--with-cs', action='store_true',
        help='尝试在线抓市均 /soccer/match/<id>/cs/（本机实测被源站按出口 IP 拦成 405）',
    )
    parser.add_argument('--proxy', default=None, help='HTTP(S) 代理，如 http://127.0.0.1:7890')
    parser.add_argument('--limit', type=int, default=None, help='只抓前 N 场比赛的子页')
    parser.add_argument('--workers', type=int, default=SUBPAGE_MAX_WORKERS,
                        help=f'子页并发上限（默认 {SUBPAGE_MAX_WORKERS}）')
    parser.add_argument('--json', action='store_true', help='以 JSON 打印运行摘要')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    html: Any = None
    if args.html:
        html = Path(args.html).read_bytes()
    with_subpages = bool(args.with_subpages) and not args.no_subpages
    result = scrape(
        output_dir=args.out,
        html=html,
        with_subpages=with_subpages,
        proxies=args.proxy,
        limit=args.limit,
        workers=args.workers,
        cs_html=args.cs_html,
        with_cs=bool(args.with_cs),
    )
    summary = _summarize(result)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f'列表页: {"可用" if summary["available"] else "不可用"} '
              f'(HTTP {summary["status"]}, 卡片 {summary["card_hits"]})')
        if summary['reason']:
            print(f'原因: {summary["reason"]}')
        print(f'场次: {summary["match_count"]}，其中 31 项竞彩比分齐全: '
              f'{summary["jc31_match_count"]}')
        if summary['subpage_requests']:
            print(f'子页请求: {summary["subpage_requests"]} 条，状态 {summary["subpage_statuses"]}')
        elif not with_subpages:
            print('子页: 默认不抓（关键数据已在列表页；需要时加 --with-subpages）')
        if summary['cs_requests']:
            print(f'市均: {summary["cs_requests"]} 条，状态 {summary["cs_statuses"]}')
            if summary['cs_origin_blocked']:
                print('  ⚠️ 市均缺失原因：源站按出口 IP 拦截（不是页面结构变化）')
        print(f'CSV: {summary["csv_path"]}')
        print(f'XLSX: {summary["xlsx_path"] or "未生成"}')
    return 0 if summary['available'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
