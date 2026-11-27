"""澳客手机「析」六个分析页的结构化抓取与解析。

用户的点击路径（逐字照抄，每一跳都是独立页面，必须逐页记录）：

    竞足 → 每场比赛的「更多玩法」→ 胜平负/让球胜平负/比分31/总进球/半全场
    → 退出 → 析 → 亚指 → 盘口 → 最值
                  → 大小球 → 最值
                  → 欧指 → 赔率 → 最值 → 让球 → 最值
                  → 盈亏

前五个玩法组（胜平负/让球胜平负/比分31/总进球/半全场）在 `m.okooo.com/jczq/`
的单个 `oddsData` blob 里，已由 `okooo_mobile_cs.parse_jczq_score_odds` 与
`parse_jczq_list_boards` 覆盖——**本模块不重解析那五个市场**，只负责 `析` 之后
的六个分析页，因为它们是各自独立的 URL。

页面与解析目标的对应（2026-11-25 真实抓包核对）：

    析      /match/history.php?MatchID=<mid>       交锋/近况/积分等文字情报
    亚指    /match/handicap.php?MatchID=<mid>      公司行 + 「平均指数」行（主水/盘口/客水）
    大小球  /match/ah.php?type=daxiao&matchId=<mid> 每行 `filter-xin` 是盘口线，两侧是大/小水
    欧指    /match/odds.php?MatchID=<mid>           `#Odds` 面板=赔率，`#Handicap` 面板=让球
    盈亏    /match/exchanges.php?MatchID=<mid>      竞足保存盈亏/人气/必发/交易分布/五要素

`最值` 不在服务端 HTML 里：真实页面只有一个 `.filter-panel`，里面唯一一项是
`全部`，其余选项由 JS 在点击后填充。因此本模块对 `最值` 做两件事，且**分开标注**：

  1. `parse_zuizhi_block()` —— 页面自带「最值/最大/最小」区块时照抓，标 `page_native`；
  2. `summarise_rows()` —— 没有该区块时，从该公司行**推导**最大值/最小值/均值/盘口众数，
     标 `derived_from_company_rows`。

两者绝不可混淆：报告里读到的每个数都要能回答「它来自页面还是来自我们的计算」。
推导值不是「页面上的最值」——澳客的最值面板按筛选档位（如「初盘/最新」「让球盘」）
重新取样，档位选不中时数值本来就会不同。所以推导值只作为**可核对的下界参照**，
并且在缺失清单里同时点名 `zuizhi_panel_js_rendered`。

不可达时的口径与全仓库一致：`missing` 里给出 reason code + 详情，绝不补造数字。
"""
from __future__ import annotations

import re
from typing import Any

from okooo_mobile_cs import (
    _fetch_detail,
    _num_of,
    _tag_text,
    ah_text_to_line,
    decode_okooo_html,
)

MOBILE_BASE = 'https://m.okooo.com'

# 用户点击路径 → 真实 URL。顺序即报告里的展示顺序。
TAB_CLICK_PATHS: dict[str, tuple[str, ...]] = {
    'analysis': ('析',),
    'ah': ('析', '亚指', '盘口', '最值'),
    'ou': ('析', '大小球', '最值'),
    'eu': ('析', '欧指', '赔率', '最值'),
    'rq': ('析', '欧指', '让球', '最值'),
    'pnl': ('析', '盈亏'),
}

TAB_LABELS: dict[str, str] = {
    'analysis': '析',
    'ah': '亚指',
    'ou': '大小球',
    'eu': '欧指·赔率',
    'rq': '欧指·让球',
    'pnl': '盈亏',
}


def tab_url(tab: str, match_id: Any) -> str:
    """每一跳的落地 URL。拼不出来就返回空串，由调用方如实报 `no_match_id`。"""
    mid = str(match_id or '').strip()
    if not mid:
        return ''
    return {
        'analysis': f'{MOBILE_BASE}/match/history.php?MatchID={mid}',
        'ah': f'{MOBILE_BASE}/match/handicap.php?MatchID={mid}',
        'ou': f'{MOBILE_BASE}/match/ah.php?type=daxiao&matchId={mid}',
        'eu': f'{MOBILE_BASE}/match/odds.php?MatchID={mid}',
        'rq': f'{MOBILE_BASE}/match/odds.php?MatchID={mid}',
        'pnl': f'{MOBILE_BASE}/match/exchanges.php?MatchID={mid}',
    }.get(tab, '')


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def _clean(fragment: Any) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(fragment or ''))).strip()


def _section(html: Any, section_id: str) -> str | None:
    """取 `<section id="...">…</section>` 的内容。

    面板之间是平级的 `<section>`，用非贪婪匹配到最近的 `</section>` 即可；
    嵌套 section 在真实页面里不存在，所以这里不需要配平栈。
    """
    match = re.search(
        rf'<section[^>]*id="{re.escape(section_id)}"[^>]*>([\s\S]*?)</section>',
        str(html or ''), re.IGNORECASE,
    )
    return match.group(1) if match else None


def _rows(fragment: Any) -> list[str]:
    """行内部 HTML（不含 `<tr>` 自身的属性）。

    需要 `<tr onclick="…pid=84…">` 里的属性时要走 `_row_blocks`。
    """
    return re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', str(fragment or ''), re.IGNORECASE)


def _row_blocks(fragment: Any) -> list[tuple[str, str]]:
    """`[(<tr> 属性, 行内 HTML)]`。

    `change.php?mid=&pid=` 只出现在 `<tr onclick=…>` 的属性里，不在单元格文本里，
    所以任何要用 pid 的地方都必须走这里。
    """
    return re.findall(r'<tr([^>]*)>([\s\S]*?)</tr>', str(fragment or ''), re.IGNORECASE)


def _cells(row: str) -> list[str]:
    return re.findall(r'<td[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE)


def _panel_names(html: Any) -> list[str]:
    """页面上的 tab 名称（`div.tabObj`），用来证明「这一页确实是那一跳」。"""
    return [
        _clean(m.group(1))
        for m in re.finditer(r'<div[^>]*class="[^"]*tabObj[^"]*"[^>]*>([^<]*)</div>', str(html or ''))
    ]


def _pid_of(row: str) -> str:
    found = re.search(r'change\.php\?mid=(\d+)&(?:amp;)?pid=(\d+)', row)
    return found.group(2) if found else ''


# --------------------------------------------------------------------------- #
# 最值：页面自带区块 vs 由公司行推导
# --------------------------------------------------------------------------- #
_ZUIZHI_LABELS = ('最值', '最大值', '最小值', '均值', '平均')


def parse_zuizhi_block(html: Any) -> dict[str, Any]:
    """抓页面自带的「最值」区块。

    真实页面（2026-11-25 抓包）**没有**这个区块：`.filter-panel` 只有 `全部` 一项，
    其余档位由 JS 在点击后填充。所以这里的正常结果是
    `{'found': False, 'stage': 'zuizhi_panel_js_rendered'}`，而不是空 dict 冒充成功。

    一旦用户的住宅 IP 抓包里有服务端渲染的最值表，这一段会直接把行读出来，
    `provenance='page_native'`，与推导值明确区分。
    """
    text = str(html or '')
    options: set[str] = set()
    for panel in re.findall(r'<div class="filter-panel">([\s\S]{0,400})', text, re.IGNORECASE):
        for item in re.findall(r'<div[^>]*class="item[^"]*"[^>]*>([^<]*)</div>', panel, re.IGNORECASE):
            label = _clean(item)
            if label:
                options.add(label)
    rows: list[dict[str, Any]] = []
    for row in _rows(text):
        cells = [_clean(c) for c in _cells(row)]
        if not cells:
            continue
        if any(label in cells[0] for label in _ZUIZHI_LABELS) and '平均指数' not in cells[0]:
            rows.append({'label': cells[0], 'cells': cells[1:]})
    if rows:
        return {'found': True, 'provenance': 'page_native', 'stage': 'ok', 'rows': rows,
                'filter_options': sorted(options)}
    return {
        'found': False,
        'provenance': 'page_native',
        'stage': 'zuizhi_panel_js_rendered',
        'rows': [],
        'filter_options': sorted(options),
        'detail': (
            '服务端 HTML 的 .filter-panel 只含「全部」；最值档位由 JS 点击后填充，'
            '静态抓包拿不到，需要住宅 IP 抓取点击「最值」后的 DOM'
        ),
    }


def _stat(values: list[float]) -> dict[str, Any]:
    if not values:
        return {'n': 0, 'max': None, 'min': None, 'mean': None}
    return {
        'n': len(values),
        'max': max(values),
        'min': min(values),
        'mean': round(sum(values) / len(values), 4),
    }


def summarise_rows(
    rows: list[dict[str, Any]], *, home_key: str, away_key: str, line_key: str,
) -> dict[str, Any]:
    """从公司行推导最大值/最小值/均值/盘口众数。

    `provenance='derived_from_company_rows'` 是本函数的固定标签：这是我们的计算，
    不是澳客最值面板上的数。
    """
    homes = [r[home_key] for r in rows if isinstance(r.get(home_key), (int, float))]
    aways = [r[away_key] for r in rows if isinstance(r.get(away_key), (int, float))]
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get(line_key) or '').strip()
        if label:
            counts[label] = counts.get(label, 0) + 1
    best = max(counts.items(), key=lambda kv: kv[1]) if counts else ('', 0)
    return {
        'provenance': 'derived_from_company_rows',
        'companies': len(rows),
        'home_water': _stat([float(v) for v in homes]),
        'away_water': _stat([float(v) for v in aways]),
        'line_mode': best[0],
        'line_mode_hits': best[1],
        'line_histogram': dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }


# --------------------------------------------------------------------------- #
# 亚指（handicap.php）：公司行 + 平均指数行
# --------------------------------------------------------------------------- #
def parse_ah_tab(html: Any) -> dict[str, Any]:
    """解析亚指页 `#pankou` 面板。

    真实结构（`okooo_mobile_ah.html`，95392 字节）：
        <section id="pankou" ...> <table class="matchtable">
          <tr><td><span>平均指数</span></td>
              <td class="datetxt01"><span type="zhu">1.86</span><em type="chu">受半/一</em><span type="ke">1.98</span></td>
              <td class="datetxt01"><span type="xinzhu">1.90</span><em type="xin">受一球</em><span type="xinke">2.55</span></td></tr>
          <tr onclick="...pid=84&Type=Handicap">
              <td class="width16"><a>澳**票</a></td> … 同上两列 …
    返回 `companies`（不含平均行）与 `avg`（平均指数行，可能缺失）。
    """
    panel = _section(html, 'pankou')
    out: dict[str, Any] = {
        'available': False, 'stage': 'ok', 'panel': 'pankou',
        'companies': [], 'avg': {}, 'panels_on_page': _panel_names(html),
    }
    if panel is None:
        out['stage'] = 'pankou_panel_missing'
        return out
    companies: list[dict[str, Any]] = []
    avg: dict[str, Any] = {}
    for tr_attrs, inner in _row_blocks(panel):
        cells = _cells(inner)
        if len(cells) < 3:
            continue
        name = _clean(cells[0])
        start = _tag_text(cells[1], None, ['zhu', 'chu', 'ke'])
        latest = _tag_text(cells[2], None, ['xinzhu', 'xin', 'xinke'])
        if not latest and not start:
            continue
        entry = {
            'name': name,
            'pid': _pid_of(tr_attrs + inner),
            'start': {
                'home_water': _num_of(start.get('zhu')),
                'line_text': start.get('chu') or '',
                'line': ah_text_to_line(start.get('chu')),
                'away_water': _num_of(start.get('ke')),
            },
            'latest': {
                'home_water': _num_of(latest.get('xinzhu')),
                'line_text': latest.get('xin') or '',
                'line': ah_text_to_line(latest.get('xin')),
                'away_water': _num_of(latest.get('xinke')),
            },
        }
        if '平均' in name:
            avg = entry
        elif name:
            companies.append(entry)
    out['companies'] = companies
    out['avg'] = avg
    out['available'] = bool(companies or avg)
    if not out['available']:
        out['stage'] = 'no_company_rows'
    return out


# --------------------------------------------------------------------------- #
# 大小球（ah.php?type=daxiao）：每行一条盘口线 + 两侧水位
# --------------------------------------------------------------------------- #
def parse_ou_tab(html: Any) -> dict[str, Any]:
    """解析大小球页的 `tr.jsContentItem` 行。

    真实结构（`okooo_mobile_ou.html`，UTF-8）：
        <tr class="jsContentItem" index="0">
          <td class="width16"><a>香**会</a></td>
          <td class="datetxt02">
            <span class="sort-chu-daqiu">1.93</span><span class="filter-chu">3.25</span><span class="sort-chu-xiaoqiu">1.77</span>
          </td>
          <td class="datetxt02">
            <span class="sort-xin-daqiu fontblue">1.93</span><span class="filter-xin">3.25</span><span class="sort-xin-xiaoqiu fontred2">1.77</span>
          </td>
    """
    out: dict[str, Any] = {'available': False, 'stage': 'ok', 'companies': []}
    rows = re.findall(
        r'<tr[^>]*class="[^"]*jsContentItem[^"]*"[^>]*>([\s\S]*?)</tr>',
        str(html or ''), re.IGNORECASE,
    )
    if not rows:
        out['stage'] = 'no_jscontentitem_rows'
        return out
    companies: list[dict[str, Any]] = []
    for row in rows:
        name_m = re.search(r'<td class="width16"><a>([^<]*)</a>', row)
        start = _tag_text(row, None, ['sort-chu-daqiu', 'filter-chu', 'sort-chu-xiaoqiu'])
        latest = _tag_text(row, None, ['sort-xin-daqiu', 'filter-xin', 'sort-xin-xiaoqiu'])
        if not latest:
            continue
        companies.append({
            'name': (name_m.group(1).strip() if name_m else ''),
            'start': {
                'over_water': _num_of(start.get('sort-chu-daqiu')),
                'line_text': start.get('filter-chu') or '',
                'line': _num_of(start.get('filter-chu')),
                'under_water': _num_of(start.get('sort-chu-xiaoqiu')),
            },
            'latest': {
                'over_water': _num_of(latest.get('sort-xin-daqiu')),
                'line_text': latest.get('filter-xin') or '',
                'line': _num_of(latest.get('filter-xin')),
                'under_water': _num_of(latest.get('sort-xin-xiaoqiu')),
            },
        })
    out['companies'] = companies
    out['available'] = bool(companies)
    if not companies:
        out['stage'] = 'no_line_rows'
    return out


# --------------------------------------------------------------------------- #
# 欧指（odds.php）：`#Odds` = 赔率，`#Handicap` = 让球
# --------------------------------------------------------------------------- #
def _parse_odds_panel(panel: str, types: tuple[str, str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tr_attrs, inner in _row_blocks(panel):
        name_m = re.search(r'<td class="width16"><a>([^<]*)</a>', inner)
        if not name_m:
            continue
        cells = _cells(inner)
        start = _tag_text(cells[1], None, [types[0], types[1], types[2]]) if len(cells) > 1 else {}
        latest = _tag_text(cells[2], None, ['xin' + t for t in types]) if len(cells) > 2 else {}
        entry: dict[str, Any] = {
            'name': name_m.group(1).strip(),
            'pid': _pid_of(tr_attrs + inner),
            'start': {t: _num_of(start.get(t)) for t in types},
            'latest': {t: _num_of(latest.get('xin' + t)) for t in types},
        }
        out.append(entry)
    return out


def parse_eu_tab(html: Any) -> dict[str, Any]:
    """解析欧指页：`#Odds`（赔率）与 `#Handicap`（让球）两个面板。

    真实抓包里 `#Handicap` 是 `<section id="Handicap" ...></section>` —— 空的，
    内容由 JS 在点击「让球」后渲染。所以让球面板的正常结果是
    `stage='handicap_panel_empty_js_rendered'`，不是解析失败。
    """
    odds_panel = _section(html, 'Odds')
    handicap_panel = _section(html, 'Handicap')
    out: dict[str, Any] = {
        'available': False, 'stage': 'ok',
        'panels_on_page': _panel_names(html),
        'companies': [], 'handicap': [], 'handicap_stage': 'ok',
        'probability': [], 'kelly': [],
    }
    if odds_panel is None:
        out['stage'] = 'odds_panel_missing'
    else:
        out['companies'] = _parse_odds_panel(odds_panel, ('sheng', 'ping', 'fu'))
        if not out['companies']:
            out['stage'] = 'no_company_rows'
    prob_panel = _section(html, 'Probability')
    if prob_panel:
        out['probability'] = _parse_odds_panel(prob_panel, ('sheng', 'ping', 'fu'))
    kelly_panel = _section(html, 'Kelly')
    if kelly_panel:
        out['kelly'] = _parse_odds_panel(kelly_panel, ('sheng', 'ping', 'fu'))
    if handicap_panel is None:
        out['handicap_stage'] = 'handicap_panel_missing'
    else:
        out['handicap'] = _parse_odds_panel(handicap_panel, ('sheng', 'ping', 'fu'))
        if not out['handicap']:
            out['handicap_stage'] = 'handicap_panel_empty_js_rendered'
    out['available'] = bool(out['companies'])
    return out


# --------------------------------------------------------------------------- #
# 盈亏（exchanges.php）
# --------------------------------------------------------------------------- #
_PNL_LABELS = ('竞足保存盈亏', '竞足人气盈亏', '必发交易盈亏', '交易分布对比', '五要素')


def parse_pnl_tab(html: Any) -> dict[str, Any]:
    """解析盈亏页的五个 section。

    真实结构（`okooo_mobile_exchanges.html`）：
        <section class="exchange-box">
          <div class="exchange-title"> 竞足保存盈亏…</div>
          <table class="exchange-table">
            <tr><th></th><th>指数</th><th>保存量</th><th>盈亏</th><th>冷热指数</th></tr>
            <tr><td>胜</td><td>4.55</td><td>353,818</td><td> +489,356 </td><td>-21</td></tr>
    必发那一段常是「暂无数据」：如实标 `betfair_available=False`，绝不拿竞足保存盈亏顶替。
    """
    text = str(html or '')
    out: dict[str, Any] = {
        'available': False, 'stage': 'ok',
        'sections': {}, 'betfair_available': False, 'betfair_note': '',
    }
    chunks = re.split(r'<section class="exchange-box">', text)
    if len(chunks) <= 1:
        out['stage'] = 'no_exchange_sections'
        return out
    for chunk in chunks[1:]:
        title_m = re.search(r'<div class="exchange-title">\s*([^<]+)', chunk)
        title = _clean(title_m.group(1)) if title_m else ''
        key = next((label for label in _PNL_LABELS if title.startswith(label)), '')
        if not key:
            continue
        headers: list[str] = []
        table_rows: list[dict[str, Any]] = []
        for row in _rows(chunk):
            raw_cells = _cells(row)
            if not raw_cells:
                th = re.findall(r'<th[^>]*>([\s\S]*?)</th>', row, re.IGNORECASE)
                if th:
                    headers = [_clean(c) for c in th]
                continue
            cells = [_clean(c) for c in raw_cells]
            if not cells or cells[0] not in ('胜', '平', '负') and len(cells) < 2:
                continue
            table_rows.append({'label': cells[0], 'cells': cells[1:]})
        out['sections'][key] = {'title': title, 'headers': headers, 'rows': table_rows}
        if key == '必发交易盈亏':
            has_value = any(
                any(cell not in ('-', '', None) for cell in row['cells'])
                for row in table_rows
            )
            out['betfair_available'] = bool(has_value)
            if not has_value:
                out['betfair_note'] = '必发交易盈亏: 页面显示暂无数据（未对该场挂盘）'
    out['available'] = bool(out['sections'])
    if not out['available']:
        out['stage'] = 'no_pnl_sections_matched'
    return out


# --------------------------------------------------------------------------- #
# 析（history.php）
# --------------------------------------------------------------------------- #
def parse_analysis_tab(html: Any) -> dict[str, Any]:
    """解析情报页：页面把交锋/近况/积分等写成小节标题 + 表格。

    本页在 2026-11-25 的抓包里没有被捕获（仓库只有亚指/大小球/欧指/盈亏四页），
    所以这里只做**结构级**解析：抓小节标题与其行数，作为矩阵里的情报计数，
    不臆造任何数值。标题拿不到就明确报 `no_sections`。
    """
    text = str(html or '')
    titles = [
        _clean(m.group(1))
        for m in re.finditer(r'<div class="(?:section-title|match-title|title)"[^>]*>\s*([^<]{1,30})', text)
    ]
    titles = [t for t in dict.fromkeys(titles) if t]
    return {
        'available': bool(titles),
        'stage': 'ok' if titles else 'no_sections',
        'section_titles': titles,
        'table_count': len(re.findall(r'<table', text, re.IGNORECASE)),
    }


PARSERS = {
    'analysis': parse_analysis_tab,
    'ah': parse_ah_tab,
    'ou': parse_ou_tab,
    'eu': parse_eu_tab,
    'rq': parse_eu_tab,
    'pnl': parse_pnl_tab,
}


def _tab_from_html(tab: str, html: Any) -> dict[str, Any]:
    parsed = PARSERS[tab](html)
    if tab == 'ah' and parsed.get('companies'):
        parsed['zuizhi'] = parse_zuizhi_block(html)
        parsed['summary'] = summarise_rows(
            [{'home_water': (c['latest'] or {}).get('home_water'),
              'away_water': (c['latest'] or {}).get('away_water'),
              'line_text': (c['latest'] or {}).get('line_text')} for c in parsed['companies']],
            home_key='home_water', away_key='away_water', line_key='line_text',
        )
    if tab == 'ou' and parsed.get('companies'):
        parsed['zuizhi'] = parse_zuizhi_block(html)
        parsed['summary'] = summarise_rows(
            [{'over_water': (c['latest'] or {}).get('over_water'),
              'under_water': (c['latest'] or {}).get('under_water'),
              'line_text': (c['latest'] or {}).get('line_text')} for c in parsed['companies']],
            home_key='over_water', away_key='under_water', line_key='line_text',
        )
    return parsed


def scrape_okooo_analysis_tabs(
    match_id: Any, *args: Any, **kwargs: Any,
) -> dict[str, Any]:
    """按用户点击顺序逐页抓取六个分析页，每页独立记录一次抓取。

    返回::

        {
          'available': bool,            # 至少有一页拿到可用结构
          'match_id': str,
          'tabs': {tab: {...}},          # 每页: url/status/bytes/parse_stage/click_path/…
          'missing': [{'tab','reason_code','detail'}],
          'source': 'okooo_mobile',
        }

    每页都带 `url` / `status` / `bytes` / `parse_stage`，这就是「每个数字可追溯到
    URL + HTTP 状态 + 字节数 + 解析阶段」的落点。
    `html_by_tab` 允许测试直接注入真实抓包，不经过网络。
    """
    mid = str(match_id or '').strip()
    supplied = kwargs.get('html_by_tab') if isinstance(kwargs.get('html_by_tab'), dict) else {}
    only = kwargs.get('tabs')
    tabs = tuple(only) if only else tuple(TAB_CLICK_PATHS)
    out: dict[str, Any] = {
        'available': False, 'match_id': mid, 'tabs': {}, 'missing': [],
        'source': 'okooo_mobile', 'tab_order': list(tabs),
    }
    if not mid:
        out['missing'].append({'tab': '*', 'reason_code': 'no_match_id', 'detail': '缺少澳客 MatchID'})
        return out
    for tab in tabs:
        if tab not in TAB_CLICK_PATHS:
            out['missing'].append(
                {'tab': tab, 'reason_code': 'unknown_tab', 'detail': f'未知分析页：{tab}'})
            continue
        url = tab_url(tab, mid)
        entry: dict[str, Any] = {
            'tab': tab, 'label': TAB_LABELS[tab], 'url': url,
            'click_path': list(TAB_CLICK_PATHS[tab]),
            'status': 0, 'bytes': 0, 'parse_stage': '', 'available': False,
            'origin_blocked': False, 'title': '',
        }
        html = supplied.get(tab)
        if isinstance(html, (bytes, bytearray)):
            entry['bytes'] = len(html)
            html = decode_okooo_html(html)
        elif isinstance(html, str):
            entry['bytes'] = len(html.encode('utf-8', 'ignore'))
        if not isinstance(html, str) or not html:
            page = _fetch_detail(
                url, proxies=kwargs.get('proxies'), session=kwargs.get('session'), retries=1,
            )
            entry.update({
                'status': int(page.get('status') or 0),
                'bytes': int(page.get('bytes') or 0),
                'origin_blocked': bool(page.get('origin_blocked')),
                'title': str(page.get('title') or ''),
                'error': str(page.get('error') or ''),
            })
            if not page.get('ok'):
                entry['parse_stage'] = 'origin_blocked' if page.get('origin_blocked') else 'fetch_failed'
                out['tabs'][tab] = entry
                out['missing'].append({
                    'tab': tab,
                    'reason_code': 'okooo_origin_ip_blocked' if page.get('origin_blocked')
                    else 'tab_fetch_failed',
                    'detail': f"{url} → HTTP {entry['status'] or entry['error']}",
                })
                continue
            html = str(page.get('html') or '')
        parsed = _tab_from_html(tab, html)
        entry.update(parsed)
        entry['parse_stage'] = str(parsed.get('stage') or 'ok')
        entry['available'] = bool(parsed.get('available'))
        out['tabs'][tab] = entry
        if not entry['available']:
            out['missing'].append({
                'tab': tab,
                'reason_code': f'{tab}_{entry["parse_stage"]}',
                'detail': f"{url} → HTTP {entry['status'] or '-'} {entry['bytes']}B stage={entry['parse_stage']}",
            })
        # 让球面板空、最值面板由 JS 渲染：这两条是「页面确实如此」的事实，
        # 必须单独登记，不能被「亚指/欧指页可用」掩盖。
        if tab == 'rq' and entry.get('handicap_stage') == 'handicap_panel_empty_js_rendered':
            out['missing'].append({
                'tab': 'rq',
                'reason_code': 'handicap_panel_js_rendered',
                'detail': f'{url} 的 #Handicap 面板在服务端为空，内容由 JS 点击「让球」后渲染',
            })
        if tab in ('ah', 'ou', 'eu', 'rq'):
            zuizhi = entry.get('zuizhi') or {}
            if zuizhi and not zuizhi.get('found'):
                out['missing'].append({
                    'tab': tab,
                    'reason_code': 'zuizhi_panel_js_rendered',
                    'detail': f'{url} 的 .filter-panel 只含「全部」，最值档位由 JS 填充',
                })
    out['available'] = any(t.get('available') for t in out['tabs'].values())
    return out


def tab_evidence(entry: dict[str, Any]) -> str:
    """一行可贴进报告/日志的出处串：URL + 状态 + 字节 + 阶段。"""
    entry = entry if isinstance(entry, dict) else {}
    bits = [str(entry.get('url') or '-')]
    if entry.get('status'):
        bits.append(f"HTTP {entry['status']}")
    if entry.get('bytes'):
        bits.append(f"{entry['bytes']}B")
    if entry.get('parse_stage'):
        bits.append(f"stage={entry['parse_stage']}")
    return ' '.join(bits)
