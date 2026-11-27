"""两源合并矩阵：澳客手机分析页 + live.500.com 竞彩比分盘。

这一层**只吃已解析好的结构**，不碰 HTML、不发请求。三个来源各自把
`{'available', ...}` 交进来，这里负责：规范化 join key、逐字段定优先级、
记 provenance、去重、把「谁没有」写清楚，最后渲染成 Telegram 段落。

join key
--------
`order`（竞彩编号，`oddsData` 的键）是唯一主键。`fid`（500 的场次号）与
`okooo_match_id`（澳客 MatchID）是**两个不同的句柄**，必须各自留列——用户要
顺着它们打开页面。三者都在同一行上出现，但只有一个能用来匹配：
名字两个站写法不同（`麦克阿瑟FC` vs `麦克阿瑟`），开赛时间也可能差时区/格式。

优先级（一条规则，写在这里，所有字段共用）
------------------------------------------
   1. **盘口数字只认澳客手机页**（`okooo_mobile`）。理由不是偏好：500 的
      亚盘/欧指/让球/大小球/盈亏全在 `odds.500.com`，那是另一台主机，本机整段
      不可达；而 `live.500.com` 盘面只有比分/半场/让球标签，没有赔率。
      所以「500 优先」在结构上就不可能成立，写成规则只是为了让日志说清缘由。
   2. **比分/半场/让球标签/技术统计只认 500**（`live500`）。这一侧是 500 的
      原生数据，澳客手机页压根没有技术统计（那是澳客桌面 `/formation/` 的事）。
   3. **两队名单**：500 胜（`gy`/`yy` 各一版，取 `gy` 的两队名，它是简体正名），
      澳客缺失时不留空，由矩阵标注 `home_source`/`away_source`。
   4. **同源内部**：更「近」的字段胜（最新 > 初盘）。这条只发生在澳客自己的
      行内，不跨源。
   5. **任何一格没有数就留 None + reason code**，绝不用另一源的同名不同义字段顶替。
      典型反例：澳客「总进球」阶梯不是大小球盘口线，500 的让球标签也不是澳客
      竞彩让球线。

结果：每个字段都带 `{value, source, url, status, bytes, stage}`，可逐格追溯。
"""
from __future__ import annotations

from typing import Any

from live500_board import normalize_order

# 每个来源在矩阵里的固定标签。报告与 provenance 都用它们，不出现同义字符串。
SRC_OKOOO = 'okooo_mobile'
SRC_500 = 'live500'

# 字段级优先级表：字段名 → 取值顺序。未列出的字段走 `DEFAULT_PRECEDENCE`。
#
# 顺序含义是「先取的赢，后者只补缺」，**不是**「只认第一个」：500 的盘口页
# （odds.500.com）在住宅 IP 上可用时，必须能补上澳客抓不到的格，否则
# 一整天「澳客被拦」就等于矩阵整段空白。
DEFAULT_PRECEDENCE: tuple[str, ...] = (SRC_OKOOO, SRC_500)

FIELD_PRECEDENCE: dict[str, tuple[str, ...]] = {
    # 盘口数字：澳客优先（用户口径以澳客手机页为准），500 只补缺。
    'asian_handicap': (SRC_OKOOO, SRC_500),
    'asian_handicap_text': (SRC_OKOOO, SRC_500),
    'ah_start_line': (SRC_OKOOO, SRC_500),
    'ah_home_water': (SRC_OKOOO, SRC_500),
    'ah_away_water': (SRC_OKOOO, SRC_500),
    'ou_line': (SRC_OKOOO, SRC_500),
    'ou_over_water': (SRC_OKOOO, SRC_500),
    'ou_under_water': (SRC_OKOOO, SRC_500),
    'odds_1x2': (SRC_OKOOO, SRC_500),
    'odds_1x2_company': (SRC_OKOOO, SRC_500),
    'handicap_1x2': (SRC_OKOOO, SRC_500),
    'pnl_saved': (SRC_OKOOO, SRC_500),
    'pnl_popularity': (SRC_OKOOO, SRC_500),
    'pnl_betfair': (SRC_OKOOO, SRC_500),
    # 500 原生：比分与比赛事实
    'home_score': (SRC_500,),
    'away_score': (SRC_500,),
    'half_time': (SRC_500,),
    'kickoff': (SRC_500, SRC_OKOOO),
    'league': (SRC_500, SRC_OKOOO),
    'handicap_label': (SRC_500,),
    'home': (SRC_500, SRC_OKOOO),
    'away': (SRC_500, SRC_OKOOO),
    'stats': (SRC_500,),
    'lineups': (SRC_500,),
}

# 缺失原因码。新增原因码必须同时加进这个集合，报告里的「为什么没有」才可枚举。
REASON_CODES: frozenset[str] = frozenset({
    'ok',
    'not_in_source',
    'source_blocked',
    'source_fetch_failed',
    'source_parse_failed',
    'panel_js_rendered',
    'tab_not_fetched',
    'not_started',
    'no_such_match_in_source',
    'no_match_id',
})

# `parse_stage`（`okooo_analysis_tabs` 的逐页解析阶段）→ 用户侧原因码。
# 这两套词汇不能混用：`parse_stage` 说「这一页发生了什么」，原因码说「用户该
# 怎么理解这一格为空」。只有映射是单一真源，报告里才不会把 405 说成「本来就没数据」。
STAGE_REASON_CODES: dict[str, str] = {
    'origin_blocked': 'source_blocked',
    'fetch_failed': 'source_fetch_failed',
    'parse_failed': 'source_parse_failed',
    'js_rendered': 'panel_js_rendered',
    'ok': 'not_in_source',
}

# 未抓取 = 本轮**没有发起请求**。与 `source_blocked`（请求发了、被源站按出口 IP
# 拦了）是两件不同的事，所以绝不复用那个码：后者的文案是一个我们无法成立的断言。
TAB_NOT_FETCHED_REASON = 'tab_not_fetched'

# 可选格：这些市场本来就可能不存在（必发未挂盘、让球面板由 JS 渲染）。
# 它们没数时不算「缺口」，但仍带原因码，读者照样知道为什么是空的。
OPTIONAL_FIELDS: frozenset[str] = frozenset({
    'pnl_betfair', 'pnl_distribution', 'pnl_five_factors',
    'handicap_1x2', 'ah_zuizhi', 'eu_zuizhi', 'ou_summary', 'ah_summary',
    'local_no',
})

# 不是缺口的原因码：这些格空了是**赛程状态**或**我们已经知道的事实**，不是
# 「没抓到」。它们留在 `fields` 里（矩阵段仍显示「未开赛」而不是裸 `-`），
# 但不进 `gaps`、不进缺口横幅。
#   not_started —— 赛前没有比分/半场/技术统计/首发；印成「缺口」会让每一份
#                  赛前报告都挂着一条假警报，而横幅的全部价值就是区分
#                  「配置/赛程问题」与「抓取问题」。
NON_GAP_REASONS: frozenset[str] = frozenset({'not_started'})

# 原因码 → 用户能读的中文解释。缺口现在会印进报告正文（不再是「缺了就不发」），
# 所以每个码都得有一句人话。新增原因码时必须同时补这里——
# `REASON_TEXT` 的键集与 `REASON_CODES` 必须完全一致（有测试守着）。
REASON_TEXT: dict[str, str] = {
    'ok': '已取到',
    'not_in_source': '该来源这一格本身没有数据（不是抓取失败）',
    'source_blocked': '来源按出口 IP 拦截（阿里云 WAF 403/405），需住宅 IP',
    'source_fetch_failed': '请求失败（超时/连接被拒）',
    'source_parse_failed': '页面取到了但结构不匹配解析器',
    'panel_js_rendered': '该面板在服务端 HTML 里为空，内容由浏览器 JS 点击后渲染',
    'tab_not_fetched': '该分析页本轮未抓取，未发起请求',
    'not_started': '比赛未开赛，比分/半场/技术统计/首发按赛程尚未产生（非抓取缺失）',
    'no_such_match_in_source': '该来源里没有这场比赛',
    'no_match_id': '缺少该来源的场次号，无法定位页面',
}


def reason_text(reason: Any) -> str:
    """原因码的中文说明；未知码原样回显并标「未知」，不假装懂它。"""
    token = str(reason or '').strip()
    if not token:
        return '未知缺口（没有原因码）'
    return REASON_TEXT.get(token) or f'未知原因码 {token}'


class _Cell:
    """一格值 + 出处。内部用，渲染前会转成普通 dict。"""

    __slots__ = ('value', 'source', 'url', 'status', 'bytes', 'stage', 'reason', 'elapsed_ms')

    def __init__(self, value: Any = None, *, source: str = '', reason: str = 'not_in_source',
                 url: str = '', status: int = 0, bytes: int = 0, stage: str = '',  # noqa: A002 - 与 evidence 字典的键同名，调用点用 **ev 展开
                 elapsed_ms: int = 0) -> None:
        self.value = value
        self.source = source
        self.reason = reason
        self.url = url
        self.status = status
        self.bytes = bytes
        self.elapsed_ms = elapsed_ms
        self.stage = stage

    @property
    def filled(self) -> bool:
        """0 是真实盘口（平手盘），空串/None/空容器才是「没有」。"""
        value = self.value
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (dict, list, tuple, set)):
            return bool(value)
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            'value': self.value,
            'source': self.source if self.filled else '',
            'reason': 'ok' if self.filled else self.reason,
            'url': self.url,
            'status': int(self.status or 0),
            'bytes': int(self.bytes or 0),
            'stage': self.stage,
            'elapsed_ms': int(self.elapsed_ms or 0),
        }


def _evidence_of(block: dict[str, Any], key: str = 'report') -> dict[str, Any]:
    rep = block.get(key) if isinstance(block.get(key), dict) else {}
    return {
        'url': str(rep.get('url') or block.get('url') or ''),
        'status': int(rep.get('status') or block.get('status') or 0),
        'bytes': int(rep.get('bytes') or block.get('bytes') or 0),
        'stage': str(block.get('stage') or rep.get('stage') or ''),
        # 顶层与 report 两处同源：先读顶层，再退回 report。缺就是 0，不编。
        'elapsed_ms': int(
            block.get('elapsed_ms') or rep.get('elapsed_ms') or 0
        ),
    }


def reason_for_stage(stage: Any) -> str:
    """`parse_stage` → 用户侧原因码。面板级 stage 与页面级 stage 共用这一条。

    以前只有 `'js_rendered' in stage` 被特判，其余一律吞成 `not_in_source`——
    于是真实 fixture 里 `pnl` 页的 `parse_stage='origin_blocked'` / HTTP 405
    被印成「这一格本身没有数据（不是抓取失败）」，把**抓取失败说成没有数据**。

    只有一条查找规则：`STAGE_REASON_CODES` 里的键按「整段相等或作为后缀出现」匹配。
    这条规则同时覆盖两种情况，所以不存在「另一条兜底分支」把错误悄悄救回来：
      * 页面级 stage 是**整段**相等（`origin_blocked`、`fetch_failed`、`ok`）；
      * 面板级 stage 把事实放在**后缀**上（`handicap_panel_empty_js_rendered`、
        `zuizhi_panel_js_rendered`）。
    先比长的键，`fetch_failed` 不会被更短的键抢先。
    """
    token = str(stage or '').strip()
    for known in sorted(STAGE_REASON_CODES, key=len, reverse=True):
        if token == known or token.endswith(known):
            return STAGE_REASON_CODES[known]
    return 'not_in_source'


def _pick(field: str, candidates: dict[str, _Cell]) -> _Cell:
    """按 `FIELD_PRECEDENCE` 取第一个「有值」的格子；都没有则返回第一个候选的缺格。

    没有候选（该源根本没参与本场）时返回 `no_such_match_in_source`，
    与「源在但字段为空」（`not_in_source`）区分开——这是两件不同的事。
    """
    order = FIELD_PRECEDENCE.get(field, DEFAULT_PRECEDENCE)
    for source in order:
        cell = candidates.get(source)
        if cell is not None and cell.filled:
            return cell
    for source in order:
        cell = candidates.get(source)
        if cell is not None:
            return cell
    return _Cell(None, reason='no_such_match_in_source')


def _okooo_cells(tab_entry: dict[str, Any] | None, field_path: tuple[str, ...]) -> _Cell:
    """从澳客某一页的解析结果里取值，出处跟着那一页（URL/状态/字节/阶段）。

    页面**根本没抓**（`tab_entry` 不是 dict）时也必须带 `source=SRC_OKOOO`：
    不带的话 `add()` 会把它存成 `'_'` 键，`_pick` 在 `FIELD_PRECEDENCE` 里
    找不到它 → 返回 `no_such_match_in_source`，把「这一轮没抓这几个分析页」
    说成「澳客没有这场比赛」。这两个结论的处置完全不同。
    """
    if not isinstance(tab_entry, dict):
        return _Cell(None, source=SRC_OKOOO, reason=TAB_NOT_FETCHED_REASON,
                     stage=TAB_NOT_FETCHED_REASON)
    ev = {
        'url': str(tab_entry.get('url') or ''),
        'status': int(tab_entry.get('status') or 0),
        'bytes': int(tab_entry.get('bytes') or 0),
        'stage': str(tab_entry.get('parse_stage') or ''),
    }
    node: Any = tab_entry
    for key in field_path:
        if isinstance(key, int):
            if not isinstance(node, (list, tuple)) or len(node) <= key:
                return _Cell(None, source=SRC_OKOOO, reason=reason_for_stage(ev['stage']), **ev)
            node = node[key]
            continue
        if not isinstance(node, dict):
            return _Cell(None, source=SRC_OKOOO, reason=reason_for_stage(ev['stage']), **ev)
        node = node.get(key)
    if node is None or (isinstance(node, (list, tuple, dict)) and not node):
        # 空容器是「这一格没有」，不是「有值」。原因码按页面阶段定：
        # `#Handicap` 与 `.filter-panel` 在服务端为空时是 JS 渲染，不是解析失败。
        # 面板级的阶段码要一路传进来，否则这一格只会落到泛化的 `not_in_source`，
        # 读者就看不出「页面确实如此」和「解析坏了」的区别。
        # 面板级 stage 与页面级 stage 走**同一条**映射（`reason_for_stage`），
        # 不在这里另写一份 `'js_rendered' in stage` 的判断。
        stage = ev['stage']
        if field_path and field_path[0] == 'handicap':
            stage = str(tab_entry.get('handicap_stage') or stage)
        elif field_path and field_path[0] == 'zuizhi':
            zuizhi = tab_entry.get('zuizhi') if isinstance(tab_entry.get('zuizhi'), dict) else {}
            stage = str(zuizhi.get('stage') or stage)
        reason = reason_for_stage(stage)
        return _Cell(None, source=SRC_OKOOO, reason=reason, **{**ev, 'stage': stage})
    return _Cell(node, source=SRC_OKOOO, reason='ok', **ev)


def build_match_matrix(
    *,
    order: Any,
    okooo_match_id: Any = '',
    five_hundred: dict[str, Any] | None = None,
    five_hundred_detail: dict[str, Any] | None = None,
    five_hundred_odds: dict[str, Any] | None = None,
    okooo_tabs: dict[str, Any] | None = None,
    okooo_boards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一场的两源数据合成一行矩阵。

    参数（都是已解析结构，全部可缺）：
      five_hundred           `live500_board.parse_board()` 的单场 dict，或其整包
      five_hundred_detail    `live500_board.parse_detail()` 的结果
      five_hundred_odds      `odds.500.com` 五个分析页的解析结果（本机不可达）。
                             这一位就是给「住宅 IP 上抓到了」的那天留的口子：
                             结构一到位，同名盘口字段就以 `live500` 出处进入候选，
                             优先级规则立刻生效，不需要改任何取源代码。
      okooo_tabs             `okooo_analysis_tabs.scrape_okooo_analysis_tabs()` 的结果
      okooo_boards           澳客竞彩页那五个市场组（胜平负/让球/比分31/总进球/半全场）
                             的已解析包；本层只做透传与出处标注，不重解析

    返回的 `fields` 是 `{字段: {value, source, reason, url, status, bytes, stage}}`，
    `provenance` 是每个**在场**来源的一行证据，`gaps` 是逐字段的缺失清单。
    """
    key = normalize_order(order)
    board = five_hundred if isinstance(five_hundred, dict) else {}
    detail = five_hundred_detail if isinstance(five_hundred_detail, dict) else {}
    odds_500 = five_hundred_odds if isinstance(five_hundred_odds, dict) else {}
    tabs = okooo_tabs if isinstance(okooo_tabs, dict) else {}
    tab_map = tabs.get('tabs') if isinstance(tabs.get('tabs'), dict) else {}
    boards = okooo_boards if isinstance(okooo_boards, dict) else {}

    board_ev = _evidence_of(board)
    detail_ev = _evidence_of(detail)
    odds_ev = _evidence_of(odds_500)
    board_ev['stage'] = board_ev['stage'] or str(board.get('stage') or '')

    src_500_present = bool(board or detail or odds_500)
    # 澳客在场 = 抓到过分析页，或至少知道它的 MatchID。只有 MatchID 也入场：
    # 「这场在澳客存在，只是那几页没抓到」与「这场澳客没有」是两件事。
    src_okooo_present = bool(tab_map or boards or okooo_match_id or tabs.get('match_id'))

    def _500(field: str, value: Any, ev: dict[str, Any]) -> _Cell:
        if not src_500_present:
            return _Cell(None, reason='not_in_source')
        if value in (None, ''):
            # 未开赛的比分位为空是正确状态，不是缺数据。
            reason = 'not_started' if board.get('started') is False else 'not_in_source'
            return _Cell(None, source=SRC_500, reason=reason, **ev)
        return _Cell(value, source=SRC_500, reason='ok', **ev)

    fields_src: dict[str, dict[str, _Cell]] = {}

    def add(field: str, cell: _Cell | None) -> None:
        if cell is None:
            # 该源完全没参与本场：不留候选，让 `_pick` 按「没有候选」如实报
            # `no_such_match_in_source`，而不是编一个我们无法成立的原因。
            fields_src.setdefault(field, {})
            return
        fields_src.setdefault(field, {})[cell.source or '_'] = cell

    # 澳客这一场**根本不存在**时（无 MatchID、无分析页、无竞彩盘口包），
    # `_okooo_cells(None, …)` 的「未抓取」是一个无法成立的断言——我们连它是哪一页
    # 都不知道。此时该说 `no_such_match_in_source`，由 `_pick` 在没有澳客候选时
    # 自己给出。所以这里只在澳客确实参与本场时才挂未抓取格。
    def _okooo(tab_entry: dict[str, Any] | None, field_path: tuple[str, ...]) -> _Cell | None:
        if not src_okooo_present and not isinstance(tab_entry, dict):
            return None
        return _okooo_cells(tab_entry, field_path)

    add('home', _500('home', board.get('home'), board_ev))
    if not fields_src['home'].get(SRC_500, _Cell()).filled and detail.get('home'):
        add('home', _500('home', detail.get('home'), detail_ev))
    add('away', _500('away', board.get('away'), board_ev))
    if not fields_src['away'].get(SRC_500, _Cell()).filled and detail.get('away'):
        add('away', _500('away', detail.get('away'), detail_ev))
    add('league', _500('league', board.get('league'), board_ev))
    add('kickoff', _500('kickoff', board.get('kickoff') or detail.get('head'), board_ev))
    add('home_score', _500('home_score', board.get('home_score'), board_ev))
    add('away_score', _500('away_score', board.get('away_score'), board_ev))
    add('half_time', _500('half_time', board.get('half_time'), board_ev))
    add('handicap_label', _500('handicap_label', board.get('handicap_label'), board_ev))
    add('stats', _500('stats', detail.get('stats'), detail_ev))
    add('lineups', _500('lineups', (detail.get('lineups') or {}).get('starters'), detail_ev))
    add('local_no', _500('local_no', board.get('local_no'), board_ev))

    # 500 侧盘口（`odds.500.com`，本机不可达）。结构一旦到位就进候选，
    # 让优先级规则自己裁决——取源逻辑不因「今天抓不到」而分叉。
    for field in (
        'asian_handicap', 'asian_handicap_text', 'ah_start_line',
        'ah_home_water', 'ah_away_water', 'ou_line', 'ou_over_water',
        'ou_under_water', 'odds_1x2', 'handicap_1x2', 'pnl_saved',
    ):
        if field in odds_500:
            add(field, _Cell(odds_500.get(field), source=SRC_500, reason='ok', **odds_ev))

    ah_tab = tab_map.get('ah')
    ou_tab = tab_map.get('ou')
    eu_tab = tab_map.get('eu')
    rq_tab = tab_map.get('rq')
    pnl_tab = tab_map.get('pnl')

    add('asian_handicap', _okooo(ah_tab, ('avg', 'latest', 'line')))
    add('asian_handicap_text', _okooo(ah_tab, ('avg', 'latest', 'line_text')))
    add('ah_start_line', _okooo(ah_tab, ('avg', 'start', 'line')))
    add('ah_home_water', _okooo(ah_tab, ('avg', 'latest', 'home_water')))
    add('ah_away_water', _okooo(ah_tab, ('avg', 'latest', 'away_water')))
    add('ah_companies', _okooo(ah_tab, ('companies',)))
    add('ah_summary', _okooo(ah_tab, ('summary',)))
    add('ah_zuizhi', _okooo(ah_tab, ('zuizhi', 'rows')))

    add('ou_line', _okooo(ou_tab, ('summary', 'line_mode')))
    add('ou_over_water', _okooo(ou_tab, ('companies', 0, 'latest', 'over_water')))
    add('ou_under_water', _okooo(ou_tab, ('companies', 0, 'latest', 'under_water')))
    add('ou_companies', _okooo(ou_tab, ('companies',)))
    add('ou_summary', _okooo(ou_tab, ('summary',)))

    add('odds_1x2', _okooo(eu_tab, ('companies',)))
    add('handicap_1x2', _okooo(rq_tab, ('handicap',)))
    add('eu_zuizhi', _okooo(eu_tab, ('zuizhi', 'rows')))

    add('pnl_saved', _okooo(pnl_tab, ('sections', '竞足保存盈亏', 'rows')))
    add('pnl_popularity', _okooo(pnl_tab, ('sections', '竞足人气盈亏', 'rows')))
    add('pnl_betfair', _okooo(pnl_tab, ('sections', '必发交易盈亏', 'rows')))
    add('pnl_distribution', _okooo(pnl_tab, ('sections', '交易分布对比', 'rows')))
    add('pnl_five_factors', _okooo(pnl_tab, ('sections', '五要素', 'rows')))

    for name in ('wdl', 'rq_wdl', 'score31', 'total_goals', 'half_full'):
        if name in boards:
            add(name, _Cell(boards.get(name), source=SRC_OKOOO, reason='ok',
                            url=str(boards.get('url') or ''), stage=str(boards.get('stage') or 'ok')))

    fields: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []
    for field, candidates in fields_src.items():
        chosen = _pick(field, candidates)
        cell = chosen.as_dict()
        if field in OPTIONAL_FIELDS and not chosen.filled:
            # 可选格没数不是缺口：它所在的市场本来就可能不存在（必发挂盘、让球面板）。
            # 但原因码仍然保留，报告里能看出「为什么这一格是空的」。
            cell['optional'] = True
            fields[field] = cell
            continue
        fields[field] = cell
        if not chosen.filled and chosen.reason not in NON_GAP_REASONS:
            gaps.append({
                'field': field,
                'reason': chosen.reason,
                'detail': _gap_detail(field, chosen),
            })

    missing_sources: list[str] = []
    if not src_500_present:
        missing_sources.append(SRC_500)
    if not src_okooo_present:
        missing_sources.append(SRC_OKOOO)

    provenance = {
        SRC_500: {
            'present': src_500_present,
            'board': dict(board_ev) if board else {},
            'detail': dict(detail_ev) if detail else {},
            'odds': dict(odds_ev) if odds_500 else {},
            'fid': str(board.get('fid') or detail.get('fid') or odds_500.get('fid') or ''),
        },
        SRC_OKOOO: {
            'present': src_okooo_present,
            'match_id': str(okooo_match_id or tabs.get('match_id') or ''),
            'tabs': {
                tab: {
                    'url': str(entry.get('url') or ''),
                    'status': int(entry.get('status') or 0),
                    'bytes': int(entry.get('bytes') or 0),
                    'stage': str(entry.get('parse_stage') or ''),
                    'available': bool(entry.get('available')),
                }
                for tab, entry in tab_map.items() if isinstance(entry, dict)
            },
        },
    }

    return {
        'order': key,
        'fid': str(board.get('fid') or detail.get('fid') or ''),
        'okooo_match_id': str(okooo_match_id or tabs.get('match_id') or ''),
        'present_sources': [s for s in (SRC_500, SRC_OKOOO) if s in
                            ([SRC_500] if src_500_present else []) +
                            ([SRC_OKOOO] if src_okooo_present else [])],
        'missing_sources': missing_sources,
        'fields': fields,
        'gaps': gaps,
        'provenance': provenance,
    }


def _gap_detail(field: str, cell: _Cell) -> str:
    base = f'field={field} reason={cell.reason}'
    if cell.url:
        base += f" url={cell.url} status={cell.status or '-'} bytes={cell.bytes or '-'}"
    if cell.stage:
        base += f' stage={cell.stage}'
    return base


def dedupe_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按 `order` 去重。

    同一 `order` 出现多次时保留**字段更全**的那一行（并列则保第一行），
    并把被丢行里的非空字段补进保留行——两行来自不同抓取轮次时，
    后一轮可能只补到了某几页，直接丢弃会丢数据。
    """
    kept: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    dropped: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = normalize_order(row.get('order'))
        if not key:
            dropped.append({'reason': 'no_order', 'row_keys': sorted(row)[:6]})
            continue
        if key not in kept:
            row = dict(row)
            row['order'] = key
            kept[key] = row
            order.append(key)
            continue
        existing = kept[key]
        existing_filled = {
            f for f, c in (existing.get('fields') or {}).items() if c.get('reason') == 'ok'
        }
        new_filled = {
            f for f, c in (row.get('fields') or {}).items() if c.get('reason') == 'ok'
        }
        existing_n, new_n = len(existing_filled), len(new_filled)
        dropped.append({'order': key, 'kept_filled': max(existing_n, new_n),
                        'dropped_filled': min(existing_n, new_n)})
        # 两行来自不同抓取轮次：后一轮可能只补到了某几页，直接丢弃会丢数据。
        # 所以逐格取「有值的那一格」，而不是整行二选一。
        keep, other = (existing, row) if existing_n >= new_n else (row, existing)
        merged_fields = dict(other.get('fields') or {})
        for field, cell in (keep.get('fields') or {}).items():
            current = merged_fields.get(field)
            if isinstance(cell, dict) and cell.get('reason') == 'ok':
                merged_fields[field] = cell
            elif current is None:
                merged_fields[field] = cell
        keep = dict(keep)
        keep['fields'] = merged_fields
        kept[key] = keep
    return {'rows': [kept[k] for k in order], 'duplicates': dropped, 'count': len(order)}


def build_matrix(
    *,
    five_hundred_board: dict[str, Any] | None = None,
    five_hundred_details: dict[str, dict[str, Any]] | None = None,
    five_hundred_odds: dict[str, dict[str, Any]] | None = None,
    okooo_by_order: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把整轮两源数据合成矩阵。

    `okooo_by_order` 是 `{order: {'match_id':…, 'tabs':…, 'boards':…}}`；
    它的键就是澳客 `oddsData` 的 order，所以两侧不需要任何名字匹配。
    `five_hundred_odds` 是 `{fid_or_order: {...}}` 的 500 盘口解析结果
    （`odds.500.com`，本机不可达，只在住宅 IP 上才会有内容）。
    """
    board = five_hundred_board if isinstance(five_hundred_board, dict) else {}
    details = five_hundred_details if isinstance(five_hundred_details, dict) else {}
    odds_500 = five_hundred_odds if isinstance(five_hundred_odds, dict) else {}
    okooo = okooo_by_order if isinstance(okooo_by_order, dict) else {}
    from live500_board import ODDS_TABS_UNAVAILABLE as _ODDS_UNAVAILABLE  # noqa: F401 - 文档化常量
    rows: list[dict[str, Any]] = []
    for match in board.get('matches') or []:
        if not isinstance(match, dict):
            continue
        key = normalize_order(match.get('order'))
        if not key:
            continue
        fid = str(match.get('fid') or '')
        okooo_item = okooo.get(key) if isinstance(okooo.get(key), dict) else {}
        # 盘面级的 URL/状态/字节是这一行所有 500 字段的共同出处，必须挂到行上，
        # 否则每格都会显示 `HTTP -/-B`（单场 dict 里没有 report）。
        match_with_report = dict(match)
        match_with_report.setdefault('report', board.get('report') or {})
        rows.append(build_match_matrix(
            order=key,
            okooo_match_id=okooo_item.get('match_id') or okooo_item.get('okooo_match_id') or '',
            five_hundred=match_with_report,
            five_hundred_detail=details.get(fid) or details.get(key) or {},
            five_hundred_odds=odds_500.get(fid) or odds_500.get(key) or {},
            okooo_tabs=okooo_item.get('tabs') or {},
            okooo_boards=okooo_item.get('boards') or {},
        ))
    # 只在澳客出现的场次也要成行：一侧缺席是常态，不是错误。
    for key, item in okooo.items():
        if normalize_order(key) in {r['order'] for r in rows}:
            continue
        item = item if isinstance(item, dict) else {}
        rows.append(build_match_matrix(
            order=key,
            okooo_match_id=item.get('match_id') or item.get('okooo_match_id') or '',
            five_hundred={},
            five_hundred_detail={},
            okooo_tabs=item.get('tabs') or {},
            okooo_boards=item.get('boards') or {},
        ))
    deduped = dedupe_rows(rows)
    return {
        'available': bool(deduped['rows']),
        'rows': deduped['rows'],
        'count': deduped['count'],
        'duplicates': deduped['duplicates'],
        'board_report': board.get('report') or {},
        'precedence': {k: list(v) for k, v in FIELD_PRECEDENCE.items()},
        'default_precedence': list(DEFAULT_PRECEDENCE),
        'reason_codes': sorted(REASON_CODES),
    }


# --------------------------------------------------------------------------- #
# Telegram 渲染
# --------------------------------------------------------------------------- #
def _num_text(value: Any, digits: int = 2) -> str:
    if isinstance(value, bool) or value is None:
        return '-'
    if isinstance(value, (int, float)):
        text = f'{value:.{digits}f}'
        return text.rstrip('0').rstrip('.') if '.' in text else text
    return str(value)


def _odds_text(value: Any) -> str:
    """胜平负三项：`主4.55/平4.20/客1.47`，缺哪项就写哪项缺。"""
    if not isinstance(value, (list, tuple)) or not value:
        return '-'
    first = value[0] if isinstance(value[0], dict) else {}
    start = first.get('start') or {}
    home, draw, away = start.get('sheng'), start.get('ping'), start.get('fu')
    if None in (home, draw, away):
        return '-'
    return f'{_num_text(home)}/{_num_text(draw)}/{_num_text(away)}'


def _score_text(row: dict[str, Any]) -> str:
    fields = row.get('fields') or {}
    home = (fields.get('home_score') or {}).get('value')
    away = (fields.get('away_score') or {}).get('value')
    if home is None or away is None:
        reason = (fields.get('home_score') or {}).get('reason') or ''
        return '未开赛' if reason == 'not_started' else '-'
    return f'{home}-{away}'


def format_matrix_tg_lines(matrix: dict[str, Any], max_rows: int = 0) -> list[str]:
    """渲染成 Telegram 段落（纯文本行）。

    每场两行：主表（编号/两队/比分/联赛/让球/亚盘/大小球/欧指/盈亏）
    + 出处行（两源各自的 URL·状态·字节·阶段，以及缺什么）。

    缺项一律写原因码，不写「-」了事：读者必须能区分「未开赛」与「抓不到」。
    """
    matrix = matrix if isinstance(matrix, dict) else {}
    rows = [r for r in (matrix.get('rows') or []) if isinstance(r, dict)]
    lines = ['🧩【两源合并数据矩阵】', '  join 键 = 竞彩编号(order)；盘口只认澳客手机页，比分只认 500']
    banner = format_gap_banner(matrix)
    if banner:
        lines.extend(['  ' + b for b in banner])
    if not rows:
        lines.append('  未形成（没有任何一源给出可用场次）')
        return lines
    head = (
        '  编号   对阵                       比分    联赛  让球  亚盘     大小球'
        '  欧指(主/平/客)   盈亏(胜/平/负)'
    )
    lines.append(head)
    shown = rows if not max_rows else rows[:max_rows]
    for row in shown:
        fields = row.get('fields') or {}
        get = lambda name, default='-': (fields.get(name) or {}).get('value', default)  # noqa: E731
        home = get('home') or '?'
        away = get('away') or '?'
        versus = f'{home}vs{away}'
        if len(versus) > 26:
            versus = versus[:25] + '…'
        ah = _num_text(get('asian_handicap'))
        ou = _num_text(get('ou_line'))
        pnl_rows = get('pnl_saved') or []
        pnl_text = '-'
        if isinstance(pnl_rows, list) and pnl_rows:
            cells = [
                f"{r.get('label')}{r['cells'][2].strip()}" if len(r.get('cells') or []) > 2 else ''
                for r in pnl_rows if isinstance(r, dict)
            ]
            pnl_text = '/'.join(c for c in cells if c) or '-'
        lines.append(
            f"  {str(row.get('order') or '-'):<5} {versus:<26} {_score_text(row):<6} "
            f"{(get('league') or '-'):<4} {(get('handicap_label') or '-'):<4} "
            f"{ah:<8} {ou:<6} {_odds_text(get('odds_1x2')):<16} {pnl_text}"
        )
        lines.append('    ' + _source_line(row))
    if max_rows and len(rows) > max_rows:
        lines.append(f'  …另有 {len(rows) - max_rows} 场未列出（同表同口径）')
    duplicates = matrix.get('duplicates') or []
    if duplicates:
        lines.append(f'  ⚠️ 去重合并 {len(duplicates)} 行（同 order 多次出现，已按字段完整度合并）')
    return lines


def _source_line(row: dict[str, Any]) -> str:
    prov = row.get('provenance') or {}
    bits: list[str] = []

    def _ev_line(label: str, ev: dict[str, Any], extra: str = '') -> str:
        ev = ev if isinstance(ev, dict) else {}
        # 报告只写「抓了哪个 URL + HTTP 状态 + 字节」，解析阶段留给 JSON 证据，
        # 否则每场两行会被元数据挤掉真正要读的数字。
        text = f"{label} HTTP {ev.get('status') or '-'}/{ev.get('bytes') or '-'}B"
        if extra:
            text += f' {extra}'
        return text

    five = prov.get(SRC_500) or {}
    if five.get('present'):
        board_ev = five.get('board') or {}
        extra = f"{board_ev.get('elapsed_ms')}ms" if board_ev.get('elapsed_ms') else ''
        bits.append(_ev_line(f"500 fid={five.get('fid') or '-'}", board_ev, extra))
    else:
        bits.append('500 缺')
    okooo = prov.get(SRC_OKOOO) or {}
    if okooo.get('present'):
        tabs = okooo.get('tabs') or {}
        got = [name for name, info in tabs.items() if info.get('available')]
        blocked = [name for name, info in tabs.items() if not info.get('available')]
        text = f"澳客 mid={okooo.get('match_id') or '-'} 页={len(got)}可用"
        if blocked:
            text += f"/{len(blocked)}缺({','.join(sorted(blocked))})"
        bits.append(text)
    else:
        bits.append('澳客 缺')
    gaps = row.get('gaps') or []
    if gaps:
        # 「这一场在另一源里根本不存在」与「源在但那一格空着」是两件不同的事，
        # 后者才需要在报告里点名；前者用一个词说清就够。
        real = [g for g in gaps if g.get('reason') != 'no_such_match_in_source']
        absent = [g for g in gaps if g.get('reason') == 'no_such_match_in_source']
        if real:
            codes = sorted({g.get('reason') or '?' for g in real})
            bits.append(f'缺{len(real)}格[{"/".join(codes)}]')
        if absent:
            bits.append(f'对方源无此场({len(absent)}格)')
    return '｜'.join(bits)


# 缺口横幅的唯一表头常量。报告顶部最终会把矩阵缺口、六盘口缺口、31 表缺口
# 合成**一个**块，共用这一行表头，所以它必须是常量而不是散落的字面量。
GAP_BANNER_HEADER = '⚠️【本场缺口（先说清楚，不影响出报）】'

# 赛前状态行的前缀。它**不是**缺口项：赛前没有比分是赛程状态，把它混进缺口
# 会让每一份赛前报告都挂着一条假警报。所以前缀与 `  · ` 不同，读者一眼能分开。
PREMARKET_STATUS_PREFIX = '  ℹ️ 赛前状态：'


def _premarket_status_line(fields: list[str], code: str, max_items: int) -> str:
    """赛前状态行的唯一写法：说明「时间未到」，并明说「非抓取缺失」。"""
    shown = '、'.join(sorted(set(fields))[:max_items])
    more = f" 等{len(set(fields))}格" if len(set(fields)) > max_items else ''
    return f'{PREMARKET_STATUS_PREFIX}{reason_text(code)}（{shown}{more}｜{code}）'


def format_gap_banner(matrix: dict[str, Any], max_items: int = 6) -> list[str]:
    """缺口横幅 + 赛前状态行，供报告顶部「先说缺口再说结论」使用。

    缺了就不发报的旧策略已经作废：缺口必须**印出来**。这个函数是那段文字的
    唯一来源，报告与矩阵段都用它，避免两处措辞漂移。

    两类内容严格分开：
      * **缺口**（`  · ` 开头）—— 我们没拿到、且本该拿到的东西；
      * **赛前状态**（`PREMARKET_STATUS_PREFIX` 开头）—— 时间未到，本来就没有。
        `not_started` 只出现在这一类里，绝不进缺口。赛前报告不能因为「没有缺口」
        就沉默：披露必须留着，只是换个说法。

    两者都为空时返回空列表（调用方据此不加横幅）。
    """
    matrix = matrix if isinstance(matrix, dict) else {}
    rows = [r for r in (matrix.get('rows') or []) if isinstance(r, dict)]
    buckets: dict[str, list[str]] = {}
    status_fields: dict[str, list[str]] = {}
    for row in rows:
        for field, cell in (row.get('fields') or {}).items():
            if not isinstance(cell, dict):
                continue
            code = str(cell.get('reason') or '')
            if code in NON_GAP_REASONS:
                status_fields.setdefault(code, []).append(str(field))
        for gap in row.get('gaps') or []:
            if not isinstance(gap, dict):
                continue
            code = str(gap.get('reason') or '')
            if code in ('', 'ok'):
                continue
            if code in NON_GAP_REASONS:
                # 兜底：调用方若自己拼了一份含 `not_started` 的 gaps（旧结构），
                # 仍然按状态行印，不按缺口印。语义只有一处定义。
                status_fields.setdefault(code, []).append(str(gap.get('field') or ''))
                continue
            buckets.setdefault(code, []).append(str(gap.get('field') or ''))
    status_lines = [
        _premarket_status_line(status_fields[code], code, max_items)
        for code in sorted(status_fields)
    ]
    if not buckets and not status_lines:
        return []
    lines = [GAP_BANNER_HEADER]
    for code in sorted(buckets, key=lambda c: (-len(buckets[c]), c)):
        fields = sorted({f for f in buckets[code] if f})
        shown = '、'.join(fields[:max_items])
        more = f" 等{len(fields)}格" if len(fields) > max_items else ''
        lines.append(f'  · {reason_text(code)}（{shown}{more}｜{code}）')
    lines.extend(status_lines)
    return lines
