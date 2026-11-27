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
# 抓页 + blocked 判定（403/405 或命中 WAF 文案）。这是本仓库对「被拦」的唯一口径，
# 本模块不另写一套——两套口径必然漂移，WAF 拦截就会被误当成普通网络错误。
fetch_page = okooo._fetch_detail
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


def _thread_session(proxies: Any) -> Any:
    session = getattr(_local, 'session', None)
    if session is None:
        session = warm_session(proxies, mobile=True, referer=MOBILE_WARMUP_URL)
        _local.session = session
    return session


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
        info = fetch_page(LIST_URL, proxies=proxies, session=session,
                          retries=2, timeout=LIST_TIMEOUT)
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
    }
    if not matches:
        shape = f'{CARD_SELECTOR}={hits}, HTTP {result["status"]}, {result["source_bytes"]} bytes'
        if result['blocked']:
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
    }
    info: dict[str, Any] = {}
    last_error = ''
    for attempt in range(1, SUBPAGE_MAX_ATTEMPTS + 1):
        record['attempts'] = attempt
        info = fetch_page(sub_url, proxies=proxies,
                          session=_thread_session(proxies), timeout=SUBPAGE_TIMEOUT)
        record['http_status'] = int(info.get('status') or 0)
        record['bytes'] = int(info.get('bytes') or 0)
        record['title'] = str(info.get('title') or '')
        if info.get('ok'):
            rows = extract_rows(info.get('html') or '')
            if rows:
                record['status'] = STATUS_OK
                record['rows'] = rows
                last_error = ''
            else:
                # 200 但没有任何行容器：结构未命中，重试也是同一份字节。
                record['status'] = STATUS_ERROR
                last_error = 'no_row_container: 页面无 table tr / div 行容器'
            break
        if info.get('blocked'):
            # WAF 拦截：重试只会加重封禁，也拖慢整轮，立刻标记并跳过。
            record['status'] = STATUS_BLOCKED
            last_error = str(info.get('error') or f'HTTP {record["http_status"]} blocked')
            break
        last_error = str(info.get('error') or f'HTTP {record["http_status"]}')
        if attempt < SUBPAGE_MAX_ATTEMPTS:
            time.sleep(2 ** (attempt - 1) + random.uniform(0, RETRY_JITTER))  # nosec B311 - 退避抖动不是密码学用途
    record['error_msg'] = last_error
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


def clean_and_export(
    matches: list[dict[str, Any]],
    subpage_records: list[dict[str, Any]] | None,
    output_dir: str | Path = 'output',
) -> pd.DataFrame:
    """去重、去噪并导出 CSV（utf-8-sig）与 XLSX。

    `openpyxl` 不是核心依赖：缺失时只跳过 XLSX，CSV 照常写出，并打印一条可直接
    执行的补救命令。返回值是 DataFrame，`xlsx_written` 等结果放在 `.attrs` 里。
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_match: dict[str, list[dict[str, Any]]] = {}
    for record in _dedupe_records(subpage_records):
        by_match.setdefault(str(record.get('match_id', '')), []).append(record)

    rows: list[dict[str, Any]] = []
    for match in matches:
        base = _match_base_row(match)
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
            rows.append({**base, **_subpage_columns(record)})

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
) -> dict[str, Any]:
    """抓列表 →（可选）抓子页 → 导出。默认不碰 `/match/*.php`。"""
    listing = fetch_match_list(proxies=proxies, html=html)
    records: list[dict[str, Any]] = []
    if with_subpages and listing['available']:
        targets = listing['matches'] if limit is None else listing['matches'][:limit]
        records = fetch_subpages(targets, proxies=proxies, max_workers=workers)
    frame = clean_and_export(listing['matches'], records, output_dir)
    return {'listing': listing, 'subpage_records': records, 'frame': frame}


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
        print(f'CSV: {summary["csv_path"]}')
        print(f'XLSX: {summary["xlsx_path"] or "未生成"}')
    return 0 if summary['available'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
