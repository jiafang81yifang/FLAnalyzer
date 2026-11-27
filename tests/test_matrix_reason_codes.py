"""缺口原因码的诚实性：不许把「抓取失败」说成「来源本来就没这个数据」。

三处缺陷各有一组守卫，全部驱动**真实调用点**（真实夹具 → `build_match_matrix`
→ `format_gap_banner`，或 `scrape_okooo_analysis_tabs` 的生产失败分支），
不测 helper 的返回字面量：

  A  未抓取的分析页必须带 `source=okooo_mobile`，`_pick` 才找得到它；
     原因码是 `tab_not_fetched`（「没发请求」），不是 `no_such_match_in_source`；
  B  `parse_stage` 的五个值各自映射到不同的原因码与中文文案——尤其是
     `origin_blocked`（HTTP 405）绝不能被印成「不是抓取失败」；
  C  `not_started` 是赛程状态，不是缺口：不进 `gaps`、不进缺口行，
     但赛前报告仍要有一行状态说明，且赛后不得凭空多出这一行。

反证（把实现改回去，哪条断言会红）由 `scripts/matrix_revert_control.py` 的
`matrix-tab-not-fetched-keeps-its-source` / `matrix-stage-reason-mapping` /
`matrix-not-started-is-not-a-gap` / `matrix-not-started-status-line` 四个用例覆盖。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / 'tests' / 'fixtures'
mdm = importlib.import_module('match_data_matrix')
board_mod = importlib.import_module('live500_board')
tabs_mod = importlib.import_module('okooo_analysis_tabs')
okooo = importlib.import_module('okooo_mobile_cs')


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope='module')
def board() -> dict[str, Any]:
    parsed = board_mod.parse_board(okooo.decode_okooo_html(_fixture('live500_board_20261127.html')))
    parsed['report'] = {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129069}
    return parsed


@pytest.fixture(scope='module')
def details() -> dict[str, Any]:
    out = {}
    for fid, size in (('1450871', 17447), ('1430592', 17362)):
        parsed = board_mod.parse_detail(
            okooo.decode_okooo_html(_fixture(f'live500_detail_{fid}_20261127.html')), fid=fid,
        )
        parsed['report'] = {'url': f'https://live.500.com/detail.php?fid={fid}&r=1',
                            'status': 200, 'bytes': size}
        out[fid] = parsed
    return out


@pytest.fixture(scope='module')
def ok_tabs() -> dict[str, Any]:
    """四页真实抓包（亚指/大小球/欧指/盈亏）全部解析成功。"""
    return tabs_mod.scrape_okooo_analysis_tabs('1338958', html_by_tab={
        'ah': _fixture('okooo_mobile_ah.html'),
        'ou': _fixture('okooo_mobile_ou.html'),
        'eu': _fixture('okooo_mobile_1x2.html'),
        'rq': _fixture('okooo_mobile_1x2.html'),
        'pnl': _fixture('okooo_mobile_exchanges.html'),
    })


def _pre_match_row(board: dict[str, Any], details: dict[str, Any],
                   tabs: dict[str, Any] | None = None) -> dict[str, Any]:
    """5002：`started=False`、无比分（真实盘面）。"""
    match = next(m for m in board['matches'] if m['order'] == '5002')
    assert match['started'] is False, '夹具前提变了：5002 不再是未开赛'
    return mdm.build_match_matrix(
        order='5002', okooo_match_id='1338958',
        five_hundred=match, five_hundred_detail=details['1430592'],
        okooo_tabs=tabs or {},
    )


def _post_match_row(board: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    """5001：`started=True`、2-0（真实盘面里唯一已结束的场次）。"""
    match = next(m for m in board['matches'] if m['order'] == '5001')
    assert match['started'] is True and match['home_score'] == 2, '夹具前提变了'
    return mdm.build_match_matrix(
        order='5001', five_hundred=match, five_hundred_detail=details['1450871'],
    )


# --------------------------------------------------------------------------- #
# 缺陷 A：未抓取的分析页必须保住来源标签
# --------------------------------------------------------------------------- #
def test_unfetched_tab_keeps_its_source_so_pick_finds_it() -> None:
    """`_okooo_cells(None, …)` 必须带 `source=okooo_mobile`。

    不带的话 `add()` 把它存成 `'_'` 键，`_pick` 在 `FIELD_PRECEDENCE` 里
    (`okooo_mobile`, `live500`) 找不到 → 返回 `no_such_match_in_source`，
    把「这一轮没抓这几个分析页」说成「澳客没有这场比赛」。
    """
    cell = mdm._okooo_cells(None, ('sections', '竞足保存盈亏', 'rows'))
    assert cell.source == mdm.SRC_OKOOO, '未抓取的澳客页丢了来源标签'
    assert cell.reason == 'tab_not_fetched'

    picked = mdm._pick('pnl_saved', {mdm.SRC_OKOOO: cell})
    assert picked.reason != 'no_such_match_in_source', (
        '未抓取被降级成「该来源里没有这场比赛」'
    )
    assert picked.reason == 'tab_not_fetched'
    assert mdm.reason_text(picked.reason) == '该分析页本轮未抓取，未发起请求'


def test_unfetched_tab_is_not_labelled_as_an_ip_block() -> None:
    """`source_blocked` 说「按出口 IP 拦截」，对「没发请求」是一个无法成立的断言。"""
    cell = mdm._okooo_cells(None, ('avg', 'latest', 'line'))
    assert cell.reason != 'source_blocked', '未抓取被说成了 WAF 拦截'
    assert '拦截' not in mdm.reason_text(cell.reason)


def test_banner_says_the_tabs_were_not_fetched_not_that_the_match_is_absent(
    board: dict[str, Any], details: dict[str, Any],
) -> None:
    """端到端：澳客在场（有 MatchID）但四个分析页本轮没抓。"""
    row = _pre_match_row(board, details, tabs={'match_id': '1338958', 'tabs': {}})
    text = '\n'.join(mdm.format_gap_banner({'rows': [row]}))
    assert 'tab_not_fetched' in text, text
    assert '本轮未抓取' in text, text
    assert 'no_such_match_in_source' not in text, text
    assert '该来源里没有这场比赛' not in text, text


def test_a_match_absent_from_okooo_entirely_is_still_reported_as_absent(
    board: dict[str, Any],
) -> None:
    """反面：澳客**根本没这场**（无 MatchID、无分析页）时不许说「没抓」。

    这是缺陷 A 的边界——修 A 不能把另一种真话顶掉。5001 只在 500 侧存在。
    """
    match = next(m for m in board['matches'] if m['order'] == '5001')
    row = mdm.build_match_matrix(order='5001', five_hundred=match)
    assert row['missing_sources'] == [mdm.SRC_OKOOO]
    assert row['fields']['asian_handicap']['reason'] == 'no_such_match_in_source', (
        row['fields']['asian_handicap']
    )


# --------------------------------------------------------------------------- #
# 缺陷 B：`parse_stage` 五个值各自映射，不再全吞成 not_in_source
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(('stage', 'code', 'text_fragment'), [
    ('origin_blocked', 'source_blocked', '拦截'),
    ('fetch_failed', 'source_fetch_failed', '请求失败'),
    ('parse_failed', 'source_parse_failed', '结构不匹配'),
    ('js_rendered', 'panel_js_rendered', 'JS'),
    ('ok', 'not_in_source', '本身没有数据'),
])
def test_every_parse_stage_maps_to_its_own_reason_code(
    stage: str, code: str, text_fragment: str,
) -> None:
    assert mdm.reason_for_stage(stage) == code
    assert text_fragment in mdm.reason_text(code), mdm.reason_text(code)


def test_stage_mapping_covers_every_code_the_scraper_emits() -> None:
    """`okooo_analysis_tabs` 实际产出的 `parse_stage` 值必须全部有映射。

    漏一个就会静默落进 `not_in_source`（「本来就没这个数」），而真实含义是
    抓取失败——那正是本缺陷。
    """
    source = (ROOT / 'okooo_analysis_tabs.py').read_text(encoding='utf-8')
    emitted = {'ok', 'origin_blocked', 'fetch_failed'}
    for value in emitted:
        assert value in mdm.STAGE_REASON_CODES, value
        assert mdm.STAGE_REASON_CODES[value] in mdm.REASON_CODES, value
    # 解析器自己给的阶段（`parse_failed` 不是本模块产出的，但映射表必须能回答）
    assert 'parse_failed' in mdm.STAGE_REASON_CODES
    assert "entry['parse_stage'] = 'origin_blocked' if page.get('origin_blocked') else 'fetch_failed'" in source


def test_panel_level_stages_go_through_the_same_mapping() -> None:
    """面板级 stage（`handicap_stage` / `zuizhi.stage`）不再单独判断 `js_rendered`。"""
    assert mdm.reason_for_stage('handicap_panel_empty_js_rendered') == 'panel_js_rendered'
    assert mdm.reason_for_stage('zuizhi_panel_js_rendered') == 'panel_js_rendered'
    # 面板级也能表达抓取失败：以前那种后缀在这里必须同样落到 source_* 上
    assert mdm.reason_for_stage('handicap_panel_origin_blocked') == 'source_blocked'

    tab = {
        'url': 'https://m.okooo.com/match/odds.php?MatchID=1',
        'status': 405, 'bytes': 0, 'parse_stage': 'ok',
        'handicap': [], 'handicap_stage': 'handicap_panel_empty_js_rendered',
    }
    cell = mdm._okooo_cells(tab, ('handicap',))
    assert cell.reason == 'panel_js_rendered'

    # 同一分支也必须能表达「页面阶段是抓取失败」：空面板 + 页面级 405
    blocked_tab = {
        'url': 'https://m.okooo.com/match/odds.php?MatchID=1',
        'status': 405, 'bytes': 0, 'parse_stage': 'origin_blocked', 'handicap': [],
    }
    assert mdm._okooo_cells(blocked_tab, ('handicap',)).reason == 'source_blocked'


def test_http_405_page_is_reported_as_blocked_not_as_no_data(
    board: dict[str, Any], details: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端：真实夹具 + 生产失败分支（盈亏页 HTTP 405 / WAF 拦截）。

    四个分析页用真实抓包；`pnl` 页故意不给 `html_by_tab`，让它走
    `scrape_okooo_analysis_tabs` 里真正的失败分支——`_fetch_detail` 只被打桩成
    「本机实测的 405 结果」，不新造任何页面内容。
    这正是阿里云出口跑真实抓包时 `pnl` 页的输出。
    """
    def blocked_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        assert 'exchanges.php' in url, url
        return {'ok': False, 'url': url, 'status': 405, 'bytes': 6116,
                'html': '', 'title': '405', 'origin_blocked': True,
                'error': 'HTTP 405'}

    monkeypatch.setattr(tabs_mod, '_fetch_detail', blocked_fetch)
    tabs = tabs_mod.scrape_okooo_analysis_tabs('1338958', tabs=('ah', 'ou', 'eu', 'rq', 'pnl'),
                                               html_by_tab={
        'ah': _fixture('okooo_mobile_ah.html'),
        'ou': _fixture('okooo_mobile_ou.html'),
        'eu': _fixture('okooo_mobile_1x2.html'),
        'rq': _fixture('okooo_mobile_1x2.html'),
    })
    # 前提：生产分支确实把它记成 origin_blocked / 405
    assert tabs['tabs']['pnl']['parse_stage'] == 'origin_blocked'
    assert tabs['tabs']['pnl']['status'] == 405

    row = mdm.build_match_matrix(
        order='5002', okooo_match_id='1338958',
        five_hundred=next(m for m in board['matches'] if m['order'] == '5002'),
        five_hundred_detail=details['1430592'], okooo_tabs=tabs,
    )
    assert row['fields']['pnl_saved']['reason'] == 'source_blocked', row['fields']['pnl_saved']

    text = '\n'.join(mdm.format_gap_banner({'rows': [row]}))
    assert 'source_blocked' in text, text
    assert '拦截' in text, text
    assert '405' in text, text
    # 反向断言：绝不能被说成「本来就没这个数」
    assert '不是抓取失败' not in text, text
    assert 'not_in_source' not in text, text


# --------------------------------------------------------------------------- #
# 缺陷 C：未开赛不是缺口
# --------------------------------------------------------------------------- #
def test_pre_match_scores_are_not_a_gap(board: dict[str, Any], details: dict[str, Any],
                                       ok_tabs: dict[str, Any]) -> None:
    row = _pre_match_row(board, details, ok_tabs)
    gap_fields = {g['field'] for g in row['gaps']}
    for field in ('home_score', 'away_score', 'half_time'):
        assert field not in gap_fields, (field, row['gaps'])
    # 但 `fields` 里必须留着 `not_started`：矩阵段仍能显示「未开赛」而不是裸 `-`
    for field in ('home_score', 'away_score', 'half_time'):
        assert row['fields'][field]['reason'] == 'not_started', (field, row['fields'][field])
    # 没有值也不是缺口，而 `_score_text` 靠这个码说「未开赛」
    assert mdm._score_text(row) == '未开赛'


def test_pre_match_status_covers_stats_and_lineups_that_have_no_rows() -> None:
    """技术统计/首发在赛前确实没有行——它们和比分同属「时间未到」。

    真实夹具里 500 的 detail 页在赛前给的是**零值行**（`home=0`）和已公布的首发，
    所以那里 `stats`/`lineups` 不算空；这里单独构造「这一场没有任何数据行」
    的 500 详情，确认它们也走 `not_started` 而不是 `not_in_source`。
    """
    match = {'fid': '1', 'home': 'A', 'away': 'B', 'started': False,
             'report': {'url': 'https://live.500.com/', 'status': 200, 'bytes': 10}}
    detail = {'fid': '1', 'report': {'url': 'https://live.500.com/detail.php?fid=1&r=1',
                                     'status': 200, 'bytes': 10}}
    row = mdm.build_match_matrix(order='5901', five_hundred=match, five_hundred_detail=detail)
    for field in ('home_score', 'away_score', 'half_time', 'stats', 'lineups'):
        assert row['fields'][field]['reason'] == 'not_started', (field, row['fields'][field])
        assert field not in {g['field'] for g in row['gaps']}, field


def test_pre_match_banner_has_a_status_line_and_no_gap_line_for_it(
    board: dict[str, Any], details: dict[str, Any], ok_tabs: dict[str, Any],
) -> None:
    row = _pre_match_row(board, details, ok_tabs)
    banner = mdm.format_gap_banner({'rows': [row]})
    text = '\n'.join(banner)
    assert banner and banner[0] == mdm.GAP_BANNER_HEADER
    # (a) 缺口行里没有 not_started
    gap_lines = [ln for ln in banner[1:] if ln.strip().startswith('·')]
    assert not any('not_started' in ln for ln in gap_lines), gap_lines
    # (b) 状态行存在，且明说「非抓取缺失」
    status_lines = [ln for ln in banner[1:] if ln.startswith(mdm.PREMARKET_STATUS_PREFIX)]
    assert len(status_lines) == 1, banner
    assert 'not_started' in status_lines[0]
    assert '非抓取缺失' in status_lines[0]
    assert '未开赛' in status_lines[0]
    # 「不影响出报」的表头仍然在：赛前报告照发
    assert '不影响出报' in text


def test_post_match_banner_has_no_status_line(board: dict[str, Any],
                                             details: dict[str, Any]) -> None:
    """防「无条件加一行」的假绿：已结束的场次不得出现赛前状态行。"""
    row = _post_match_row(board, details)
    banner = mdm.format_gap_banner({'rows': [row]})
    assert banner, '5001 缺澳客源，缺口横幅不该整块消失'
    text = '\n'.join(banner)
    assert mdm.PREMARKET_STATUS_PREFIX not in text, banner
    assert 'not_started' not in text, banner
    assert row['fields']['home_score']['reason'] == 'ok'
    assert row['fields']['home_score']['value'] == 2


def test_status_line_is_composed_apart_from_the_data_source_gap() -> None:
    """合进「数据源缺」组时赛前状态行必须另起一行，不能被读成缺数据。"""
    pipe_mod = importlib.import_module('blade_deepseek_pipeline')
    matrix = {'rows': [{'fields': {'home_score': {'reason': 'not_started'}}, 'gaps': []}]}
    gap = pipe_mod.DeepSeekAnalyzer.compose_gap_banner(matrix=matrix)
    assert gap['matrix_line'] == '', gap
    assert gap['status_line'], gap
    assert '数据源缺' not in gap['banner'], gap['banner']
    assert pipe_mod.DeepSeekAnalyzer.MATRIX_STATUS_PREFIX in gap['banner']
    assert '不影响出报' in gap['banner']
