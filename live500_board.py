"""live.500.com 竞彩比分盘 + 每场比赛子页的结构化抓取与解析。

来源与可达性（本机 2026-11-27 实测，逐条记在 `SOURCE_REACHABILITY`）：

    https://live.500.com/              200 129069B  gb2312  竞彩比分盘（39 场）
    https://live.500.com/zqdc.php      200 700459B  gb2312  单场/北单比分盘（308 行）
    https://live.500.com/detail.php?fid=<fid>&r=1  200 ~17KB  该场直播子页
    https://live.500.com/jczq.php      301 3B      只有跳转，无正文
    https://odds.500.com/fenxi/*.shtml 连不上     DNS 解析正常、主机被挡

**盘口数字不在 500 这一侧**：500 的亚盘/欧指/让球/大小球/盈亏全在
`odds.500.com/fenxi/{yazhi,ouzhi,rangqiu,daxiao,yingkui}-<fid>.shtml`，本机整段
不可达。所以本模块只交出 500 真的有的东西：竞彩编号、fid、order、联赛、两队、
开赛时间、比分、半场、让球标签（`data-ao`/`data-pb`）、子页的技术统计与预计首发。
`ODDS_TABS_UNAVAILABLE` 把不可达的五个页逐个点名，供报告如实写出。

join key：盘面行上的 `order="5001"` 就是澳客 `oddsData` 的键（同一条 `<tr>` 上
还有 `fid`）。本模块只负责把 `order` 规范成 `str(int(x))` 并原样交出，不做匹配。
"""
from __future__ import annotations

import re
import time
from typing import Any

from okooo_mobile_cs import _fetch_detail, _num_of, decode_okooo_html

BOARD_URL = 'https://live.500.com/'
ZQDC_URL = 'https://live.500.com/zqdc.php'
DETAIL_URL = 'https://live.500.com/detail.php?fid={fid}&r=1'

SOURCE_REACHABILITY: tuple[dict[str, Any], ...] = (
    {'url': BOARD_URL, 'status': 200, 'bytes': 129069, 'content': '竞彩比分盘 39 场', 'ok': True},
    {'url': ZQDC_URL, 'status': 200, 'bytes': 700459, 'content': '单场比分盘 308 行', 'ok': True},
    {'url': DETAIL_URL, 'status': 200, 'bytes': 17917, 'content': '该场直播子页', 'ok': True},
    {'url': 'https://live.500.com/jczq.php', 'status': 301, 'bytes': 3,
     'content': '只有 301 跳转，无正文', 'ok': False},
)

# 500 的分析页全在 odds.500.com。本机连不上（连接被拒），因此这五项对 500 侧
# **不可得**；它们仍然由澳客手机页提供，两源在矩阵里各自记 provenance。
ODDS_TABS_UNAVAILABLE: tuple[dict[str, str], ...] = (
    {'tab': '亚盘', 'url': 'https://odds.500.com/fenxi/yazhi-{fid}.shtml', 'reason': 'host_blocked_here'},
    {'tab': '欧指', 'url': 'https://odds.500.com/fenxi/ouzhi-{fid}.shtml', 'reason': 'host_blocked_here'},
    {'tab': '让球', 'url': 'https://odds.500.com/fenxi/rangqiu-{fid}.shtml', 'reason': 'host_blocked_here'},
    {'tab': '大小球', 'url': 'https://odds.500.com/fenxi/daxiao-{fid}.shtml', 'reason': 'host_blocked_here'},
    {'tab': '盈亏', 'url': 'https://odds.500.com/fenxi/yingkui-{fid}.shtml', 'reason': 'host_blocked_here'},
)


def normalize_order(value: Any) -> str:
    """竞彩编号规范化。

    `5002` / `'5002'` / `' 05002 '` 必须是同一个键，否则同一场会出两行。
    非纯数字（空、'-'、'None'）返回空串——空串是「没有」，不是 0。
    """
    token = str(value if value is not None else '').strip()
    if not token:
        return ''
    try:
        return str(int(token))
    except (TypeError, ValueError):
        return ''


def _clean(fragment: Any) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(fragment or ''))).strip()


def _attr(attrs: str, name: str) -> str:
    found = re.search(rf'{re.escape(name)}="([^"]*)"', attrs or '')
    return found.group(1).strip() if found else ''


def _row_blocks(html: Any) -> list[tuple[str, str, str]]:
    """`[(fid, <tr> 属性, 行内 HTML)]`，按 `id="a<fid>"` 定位每一场。"""
    out: list[tuple[str, str, str]] = []
    for fid, attrs, inner in re.findall(
        r'<tr\s+id="a(\d+)"([^>]*)>([\s\S]*?)</tr>', str(html or ''), re.IGNORECASE,
    ):
        out.append((fid, attrs, inner))
    return out


def _cells(row: str) -> list[str]:
    return re.findall(r'<td[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE)


def parse_board(html: Any, *, source: str = 'live500_board') -> dict[str, Any]:
    """解析 `live.500.com/` 竞彩比分盘（也适用于 `zqdc.php`，列布局一致）。

    真实行（`live500_board_20261127.html`，129069B）：

        <tr id="a1450871" order="5001" status="2" gy="澳超,麦克阿瑟FC,墨尔本城"
            lid="312" fid="1450871" sid="20046" ...>
          <td …><input …/>周五001</td>                          ← 竞彩编号展示
          <td …><a …>澳超</a></td>                               ← 联赛
          <td>第6轮</td><td>11-27 16:40</td><td>&nbsp;</td>      ← 轮次/开赛/状态
          <td …>…麦克阿瑟FC…<span class="sp_sr">(+1)</span></td> ← 主队 + 让球标记
          <td><div class="pk">…class="clt1">2</…data-ao="平手"…class="clt3">0</…></div></td>
          <td …>…墨尔本城…</td>                                  ← 客队
          <td class="red"> - </td>                               ← 半场
          <td class="bf_op">…</td>                               ← 奖金（zqdc 才有数）
          <td class="red">&nbsp;</td>                            ← 结果
          …
        </tr>

    `status="0"` = 未开赛（第 5 列写「未」，比分位为空）；已开赛为其他值。
    因此 `home_score=None` 在未开赛场次上是**正确**结果，不是缺数据。
    """
    rows = _row_blocks(html)
    out: dict[str, Any] = {
        'available': False, 'source': source,
        'match_count': len(rows), 'matches': [], 'stage': 'ok',
        'fid_to_order': {}, 'order_to_fid': {},
        'order_missing': 0, 'joinable': 0,
    }
    if not rows:
        out['stage'] = 'no_match_rows'
        return out
    seen: set[str] = set()
    for fid, attrs, inner in rows:
        cells = _cells(inner)
        if len(cells) < 9:
            continue
        order = normalize_order(_attr(attrs, 'order'))
        if order and order in seen:
            # 同一 order 出现两行只可能是同一场的重复渲染：留第一次，
            # 重复的进 duplicates 供审计，不产生第二行矩阵。
            out.setdefault('duplicates', []).append({'order': order, 'fid': fid})
            continue
        if order:
            seen.add(order)
        else:
            # `zqdc.php` 的行**没有** order 属性（只有序号），这一页无法按竞彩
            # 编号 join。如实计数，不拿序号冒充 order。
            out['order_missing'] += 1
        gy = _attr(attrs, 'gy').split(',')
        yy = _attr(attrs, 'yy').split(',')
        status = _attr(attrs, 'status')
        pk = cells[6] if len(cells) > 6 else ''
        home_score = _clean(re.search(r'class="clt1"[^>]*>([\s\S]*?)</a>', pk).group(1)) \
            if re.search(r'class="clt1"[^>]*>([\s\S]*?)</a>', pk) else ''
        away_score = _clean(re.search(r'class="clt3"[^>]*>([\s\S]*?)</a>', pk).group(1)) \
            if re.search(r'class="clt3"[^>]*>([\s\S]*?)</a>', pk) else ''
        handicap_m = re.search(r'class="fgreen"[^>]*data-ao="([^"]*)"[^>]*data-pb="([^"]*)"', pk)
        if not handicap_m:
            handicap_m = re.search(r'class="fgreen"[^>]*data-pb="([^"]*)"', pk)
        handicap_label = (handicap_m.group(1) if handicap_m else '')
        half_time = _clean(cells[8]) if len(cells) > 8 else ''
        # zqdc.php 的第 10 列是「参考SP值」三个奖金；live/ 盘没有数（&nbsp;）。
        bonus = [
            _num_of(v) for v in re.findall(r'<span[^>]*>\s*([\d.]+)\s*</span>', cells[9])
        ] if len(cells) > 9 else []
        entry = {
            'order': order,
            'fid': fid,
            'local_no': _clean(re.sub(r'<input[^>]*/>', '', cells[0])),
            'league': _clean(cells[1]),
            'round': _clean(cells[2]),
            'kickoff': _clean(cells[3]),
            'status': status,
            'started': status not in ('', '0'),
            'home': _clean(re.sub(r'<span class="(?:gray|sp_rq|sp_sr)">[^<]*</span>', '', cells[5])),
            'away': _clean(re.sub(r'<span class="(?:gray|yellowcard|sp_rq|sp_sr)">[^<]*</span>', '', cells[7])),
            'home_rank': (re.search(r'<span class="gray">\[(\d+)\]</span>', cells[5]) or [None, ''])[1]
            if re.search(r'<span class="gray">\[(\d+)\]</span>', cells[5]) else '',
            'away_rank': (re.search(r'<span class="gray">\[(\d+)\]</span>', cells[7]) or [None, ''])[1]
            if re.search(r'<span class="gray">\[(\d+)\]</span>', cells[7]) else '',
            'home_score': int(home_score) if home_score.isdigit() else None,
            'away_score': int(away_score) if away_score.isdigit() else None,
            'half_time': half_time if half_time not in ('-', '') else '',
            'handicap_label': handicap_label,
            'sp_bonus': [b for b in bonus if b is not None] or [],
            'lid': _attr(attrs, 'lid'),
            'sid': _attr(attrs, 'sid'),
            'gy': gy[1:] if len(gy) > 1 else [],
            'yy': yy[1:] if len(yy) > 1 else [],
            'detail_url': DETAIL_URL.format(fid=fid),
            'source': source,
        }
        out['matches'].append(entry)
        if order:
            out['fid_to_order'][fid] = order
            out['order_to_fid'][order] = fid
    out['joinable'] = len(out['order_to_fid'])
    out['available'] = bool(out['matches'])
    if not out['available']:
        out['stage'] = 'no_parsable_rows'
    return out


PLAYERS_PER_SIDE = 11


def parse_detail(html: Any, *, fid: str = '') -> dict[str, Any]:
    """解析单场直播子页（`detail.php?fid=<fid>&r=1`）。

    真实结构（`live500_detail_1450871_20261127.html`，17447B，gb2312）：

        <title>麦克阿瑟FCVS墨尔本城足球比赛直播…</title>
        <div class="h"><a>2026-11-27 16:40 2026-2027 澳超第6轮</a></div>
        <td class="teamvs"><span class="score">2 - 0</span></td>
        <div class="title">预计首发阵容</div><div class="content">…11 行…</div>
        <div class="title">后备</div><div class="content content2">…</div>
        <div class="title">球队技术统计</div>
          <td width="40">7</td><td …bgcolor="#e4e4e4">射门</td><td width="40">7</td>

    注意：主客两侧的「预计首发阵容/后备」用**同一个** `div.title` 文案，
    各出现两次（先主后客）。所以侧别按出现顺序判定，不按标题判定。
    技术统计只有一组（主/客同处一行），不存在重复计数问题。
    """
    text = str(html or '')
    out: dict[str, Any] = {
        'available': False, 'stage': 'ok', 'fid': str(fid or ''),
        'title': '', 'head': '', 'score': '', 'home': '', 'away': '',
        'lineups': {}, 'stats': {}, 'stat_order': [],
    }
    title_m = re.search(r'<title[^>]*>([\s\S]*?)</title>', text, re.IGNORECASE)
    out['title'] = _clean(title_m.group(1)) if title_m else ''
    head_m = re.search(r'<div class="h">([\s\S]*?)</div>', text)
    out['head'] = _clean(head_m.group(1)) if head_m else ''
    score_m = re.search(r'<span class="score">([^<]*)</span>', text)
    out['score'] = _clean(score_m.group(1)) if score_m else ''
    names = re.findall(r'<h2 class="team_title"><a[^>]*>([^<]*)</a></h2>', text)
    if len(names) >= 2:
        out['home'], out['away'] = names[0].strip(), names[1].strip()

    # 首发/后备：按标题出现顺序配对，主队先。标题拿不到名字就用序号命名，
    # 绝不把客队的名单写成主队的。
    sides = ('home', 'away')
    blocks = list(re.finditer(
        r'<div class="title">\s*(预计首发阵容|后备)\s*</div>\s*<div class="content[^"]*">([\s\S]*?)</div>',
        text,
    ))
    starters: dict[str, list[str]] = {'home': [], 'away': []}
    subs: dict[str, list[str]] = {'home': [], 'away': []}
    side_index = 0
    last_kind = ''
    for match in blocks:
        kind = 'starters' if match.group(1) == '预计首发阵容' else 'subs'
        if kind == 'starters' and last_kind == 'subs':
            side_index = min(side_index + 1, 1)
        side = sides[side_index]
        players = [
            _clean(re.sub(r'<td[^>]*>\s*</td>', '', row))
            for row in re.findall(r'<tr>([\s\S]*?)</tr>', match.group(2), re.IGNORECASE)
        ]
        players = [p for p in players if p and p != '&nbsp;']
        (starters if kind == 'starters' else subs)[side] = players
        last_kind = kind
    out['lineups'] = {
        'starters': starters, 'subs': subs,
        'starters_count': {s: len(starters[s]) for s in sides},
        'expected_starters': PLAYERS_PER_SIDE,
        'starters_complete': all(len(starters[s]) == PLAYERS_PER_SIDE for s in sides),
    }

    for home_v, label, away_v in re.findall(
        r'<td width="40" align="center">([^<]*)</td>\s*'
        r'<td height="33" align="center" bgcolor="#e4e4e4">([^<]*)</td>\s*'
        r'<td width="40" align="center">([^<]*)</td>',
        text,
    ):
        key = label.strip()
        if not key or key in out['stats']:
            continue
        out['stats'][key] = {'home': _num_of(home_v), 'away': _num_of(away_v),
                             'home_text': home_v.strip(), 'away_text': away_v.strip()}
        out['stat_order'].append(key)
    out['available'] = bool(out['stats'] or out['lineups']['starters']['home'] or out['score'])
    if not out['available']:
        out['stage'] = 'no_detail_blocks'
    return out


def scrape_500_board(
    *, html: Any = None, url: str = BOARD_URL, proxies: Any = None, session: Any = None,
    source: str = 'live500_board',
) -> dict[str, Any]:
    """抓一个 500 比分盘并解析。抓不到时如实例外抛，`report` 里带 URL/状态/字节。

    `report.elapsed_ms` 是这次真实往返的耗时：盘面在阶段1 挡在每场比赛之前，
    所以这个数必须能从日志里读出来，而不是只在压测时量过。
    """
    page: dict[str, Any] = {}
    started = time.perf_counter()
    if html is None:
        page = _fetch_detail(url, proxies=proxies, session=session, retries=1, timeout=20)
        if not page.get('ok'):
            return {
                'available': False, 'source': source, 'stage': 'fetch_failed',
                'match_count': 0, 'matches': [], 'fid_to_order': {}, 'order_to_fid': {},
                'report': {'url': url, 'status': int(page.get('status') or 0),
                           'bytes': int(page.get('bytes') or 0),
                           'elapsed_ms': int((time.perf_counter() - started) * 1000),
                           'error': str(page.get('error') or '')},
            }
        html = page.get('html')
    parsed = parse_board(html, source=source)
    parsed['report'] = {
        'url': url,
        'status': int(page.get('status') or 0),
        'bytes': int(page.get('bytes') or 0) or len(str(html).encode('utf-8', 'ignore')),
        'elapsed_ms': int((time.perf_counter() - started) * 1000),
        'title': str(page.get('title') or ''),
    }
    return parsed


def scrape_500_detail(fid: Any, *, html: Any = None, proxies: Any = None,
                      session: Any = None) -> dict[str, Any]:
    """抓并解析某一场的 `detail.php`。`fid` 必须是 500 的 fid，不是澳客 MatchID。"""
    key = str(fid or '').strip()
    url = DETAIL_URL.format(fid=key)
    if not key:
        return {'available': False, 'stage': 'no_fid', 'fid': '', 'report': {'url': url}}
    page: dict[str, Any] = {}
    if html is None:
        page = _fetch_detail(url, proxies=proxies, session=session, retries=1, timeout=20)
        if not page.get('ok'):
            return {'available': False, 'stage': 'fetch_failed', 'fid': key,
                    'report': {'url': url, 'status': int(page.get('status') or 0),
                               'bytes': int(page.get('bytes') or 0),
                               'error': str(page.get('error') or '')}}
        html = page.get('html')
    parsed = parse_detail(html, fid=key)
    parsed['report'] = {
        'url': url,
        'status': int(page.get('status') or 0),
        'bytes': int(page.get('bytes') or 0) or len(str(html).encode('utf-8', 'ignore')),
        'title': str(page.get('title') or parsed.get('title') or ''),
    }
    return parsed


def decode_500_html(raw: Any) -> str:
    """500 页面是 gb2312（meta 声明）。

    直接用 `okooo_mobile_cs.decode_okooo_html`：它按 meta charset 读，把
    gb2312/gbk 映射到 gb18030（gb2312 的超集），并对 `\\uXXXX` 反转义。
    自己写 `raw.decode('gb2312')` 会在这两个真实抓包上出乱码。
    """
    return decode_okooo_html(raw)
