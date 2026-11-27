"""澳客分析页解析器：全部断言打在真实抓包上。

夹具（`tests/fixtures/FIXTURE_SOURCES.json` 有完整出处）：
    okooo_mobile_ah.html          95392B  https://m.okooo.com/match/handicap.php?MatchID=1347776
    okooo_mobile_ou.html          13148B  https://m.okooo.com/match/ah.php?type=daxiao&matchId=1347776
    okooo_mobile_1x2.html         90907B  https://m.okooo.com/match/odds.php?MatchID=1347776
    okooo_mobile_exchanges.html   15324B  https://m.okooo.com/match/exchanges.php?MatchID=1347776

这四页都是 2026-11-25 从住宅网真实抓到的：亚指 25 家公司、大小球 20 家、
欧指 27 家（含概率/凯利面板）、盈亏五段。不要手写假 HTML 顶替它们——
选择器漂移只有在真实字节上才会暴露。

本机（云段出口）对 m.okooo.com 全站 403（2026-11-27 实测），所以抓取函数
只在"注入真实字节"这一条路径上验证；网络路径用 monkeypatch 顶掉，不假装可达。
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

tabs = importlib.import_module('okooo_analysis_tabs')
okooo = importlib.import_module('okooo_mobile_cs')


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(scope='module')
def ah_html() -> str:
    raw = _read('okooo_mobile_ah.html')
    assert len(raw) == 95392, f'fixture bytes drifted: {len(raw)}'
    return okooo.decode_okooo_html(raw)


@pytest.fixture(scope='module')
def ou_html() -> str:
    raw = _read('okooo_mobile_ou.html')
    assert len(raw) == 13148, f'fixture bytes drifted: {len(raw)}'
    return okooo.decode_okooo_html(raw)


@pytest.fixture(scope='module')
def eu_html() -> str:
    raw = _read('okooo_mobile_1x2.html')
    assert len(raw) == 90907, f'fixture bytes drifted: {len(raw)}'
    return okooo.decode_okooo_html(raw)


@pytest.fixture(scope='module')
def pnl_html() -> str:
    raw = _read('okooo_mobile_exchanges.html')
    assert len(raw) == 15324, f'fixture bytes drifted: {len(raw)}'
    return okooo.decode_okooo_html(raw)


# --------------------------------------------------------------------------- #
# URL per click step
# --------------------------------------------------------------------------- #
def test_every_click_step_has_its_own_url() -> None:
    """六个分析页各自一个 URL：`析` 不能复用 `亚指`，`让球` 与 `赔率` 同页不同面板。

    `让球` 与 `赔率` 都在 `odds.php` 是真实页面结构（两个 `<section>`），
    但它们是**两次独立的抓取/解析**，所以 click_path 必须分别记录。
    """
    urls = {tab: tabs.tab_url(tab, '1347776') for tab in tabs.TAB_CLICK_PATHS}
    assert urls['ah'] == 'https://m.okooo.com/match/handicap.php?MatchID=1347776'
    assert urls['ou'] == 'https://m.okooo.com/match/ah.php?type=daxiao&matchId=1347776'
    assert urls['eu'] == urls['rq'] == 'https://m.okooo.com/match/odds.php?MatchID=1347776'
    assert urls['pnl'] == 'https://m.okooo.com/match/exchanges.php?MatchID=1347776'
    assert urls['analysis'] == 'https://m.okooo.com/match/history.php?MatchID=1347776'
    assert tabs.TAB_CLICK_PATHS['ah'] == ('析', '亚指', '盘口', '最值')
    assert tabs.TAB_CLICK_PATHS['ou'] == ('析', '大小球', '最值')
    assert tabs.TAB_CLICK_PATHS['eu'] == ('析', '欧指', '赔率', '最值')
    assert tabs.TAB_CLICK_PATHS['rq'] == ('析', '欧指', '让球', '最值')
    assert tabs.TAB_CLICK_PATHS['pnl'] == ('析', '盈亏')
    assert tabs.tab_url('ah', '') == '', 'no match id must not fabricate a URL'


# --------------------------------------------------------------------------- #
# 亚指
# --------------------------------------------------------------------------- #
def test_ah_tab_reads_25_companies_and_the_avg_row(ah_html: str) -> None:
    parsed = tabs.parse_ah_tab(ah_html)
    assert parsed['available'] is True
    assert parsed['stage'] == 'ok'
    assert len(parsed['companies']) == 25
    assert '平均指数' in str(parsed['avg'].get('name')), parsed['avg'].get('name')
    # 平均指数行：最新 受一球 / 主水 1.90 / 客水 2.55
    assert parsed['avg']['latest'] == {
        'home_water': 1.9, 'line_text': '受一球', 'line': -1.0, 'away_water': 2.55,
    }
    first = parsed['companies'][0]
    assert first['name'] == '澳**票'
    assert first['start']['line'] == -0.75   # 受半/一
    assert first['latest']['line'] == -0.5   # 受半球


def test_ah_tab_keeps_the_company_pid_from_the_row_attributes(ah_html: str) -> None:
    """`pid` 只在 `<tr onclick="…pid=84…">` 的属性里。

    用只匹配行内 HTML 的正则会静默丢掉全部 pid（本仓库踩过一次），
    于是 25 行全都 `detail_block_reason='该行没有 change.php 链接'`。
    """
    parsed = tabs.parse_ah_tab(ah_html)
    pids = [c['pid'] for c in parsed['companies']]
    assert all(pids), f'pids dropped: {pids[:5]}'
    assert parsed['companies'][0]['pid'] == '84'


def test_ah_summary_is_labelled_as_derived_not_page_native(ah_html: str) -> None:
    """推导出来的「最值」必须自报是推导，绝不能被读成页面上的最值面板。"""
    parsed = tabs._tab_from_html('ah', ah_html)
    summary = parsed['summary']
    assert summary['provenance'] == 'derived_from_company_rows'
    assert summary['companies'] == 25
    assert summary['line_mode'] == '受半/一'
    assert summary['line_mode_hits'] == 14
    assert summary['home_water']['max'] == 2.17
    assert summary['home_water']['min'] == 1.08
    assert summary['home_water']['n'] == 25


# --------------------------------------------------------------------------- #
# 最值面板
# --------------------------------------------------------------------------- #
def test_zuizhi_panel_is_reported_as_js_rendered(ah_html: str) -> None:
    """服务端 HTML 的 `.filter-panel` 只有「全部」一项。

    这一页因此**没有**可抓的最值表。函数必须报 `found=False` +
    `zuizhi_panel_js_rendered`，而不是返回空 rows 让调用方当成成功。
    """
    block = tabs.parse_zuizhi_block(ah_html)
    assert block['found'] is False
    assert block['stage'] == 'zuizhi_panel_js_rendered'
    assert block['rows'] == []
    assert block['filter_options'] == ['全部']
    assert block['provenance'] == 'page_native'


def test_zuizhi_block_parses_a_native_panel_when_one_exists() -> None:
    """住宅 IP 上若出现服务端渲染的最值表，这段要能直接读出来。

    这一段用最小结构（不是抓包）证明解析路径存在：真实抓包尚未拿到，
    report 里已按 pending 标注，不能声称已在真实页面上验证。
    """
    html = (
        '<table><tr><td><span>平均值</span></td><td>1.90</td></tr></table>'
        '<table><tr><td>最值</td><td>受半/一</td><td>2.17</td></tr></table>'
    )
    block = tabs.parse_zuizhi_block(html)
    assert block['found'] is True
    assert block['provenance'] == 'page_native'
    labels = [row['label'] for row in block['rows']]
    # 「平均值」也含「均值」，两者都算摘要行：页面上有什么就读什么，
    # 不做取舍，取舍留给矩阵的 FIELD_PRECEDENCE。
    assert labels == ['平均值', '最值'], labels


# --------------------------------------------------------------------------- #
# 大小球
# --------------------------------------------------------------------------- #
def test_ou_tab_reads_20_lines_with_waters(ou_html: str) -> None:
    parsed = tabs.parse_ou_tab(ou_html)
    assert parsed['available'] is True
    assert parsed['stage'] == 'ok'
    assert len(parsed['companies']) == 20
    first = parsed['companies'][0]
    assert first['name'] == '香**会'
    assert first['latest'] == {
        'over_water': 1.93, 'line_text': '3.25', 'line': 3.25, 'under_water': 1.77,
    }
    summary = tabs.summarise_rows(
        [{'over_water': c['latest']['over_water'],
          'under_water': c['latest']['under_water'],
          'line_text': c['latest']['line_text']} for c in parsed['companies']],
        home_key='over_water', away_key='under_water', line_key='line_text',
    )
    assert summary['line_mode'] == '3.25'
    assert summary['line_mode_hits'] == 6, summary['line_histogram']


# --------------------------------------------------------------------------- #
# 欧指 / 让球
# --------------------------------------------------------------------------- #
def test_eu_tab_reads_27_company_rows_from_the_odds_panel(eu_html: str) -> None:
    parsed = tabs.parse_eu_tab(eu_html)
    assert parsed['available'] is True
    assert parsed['stage'] == 'ok'
    assert len(parsed['companies']) == 27
    first = parsed['companies'][0]
    assert first['name'] == '竞**方'
    assert first['start'] == {'sheng': 4.35, 'ping': 4.12, 'fu': 1.50}
    assert first['latest'] == {'sheng': 4.55, 'ping': 4.20, 'fu': 1.47}
    assert first['pid'] == '2'
    assert parsed['panels_on_page'] == ['指数', '概率', '凯利', '让球']


def test_rq_handicap_panel_is_empty_in_the_server_html(eu_html: str) -> None:
    """「让球」面板在真实抓包里是空的 `<section id="Handicap"></section>`。

    这不是解析失败：内容由 JS 点击「让球」后渲染。必须报
    `handicap_panel_empty_js_rendered`，而不是 `no_company_rows`
    ——两者的处置完全不同（前者要抓 DOM，后者要改选择器）。
    """
    parsed = tabs.parse_eu_tab(eu_html)
    assert parsed['handicap'] == []
    assert parsed['handicap_stage'] == 'handicap_panel_empty_js_rendered'
    # 同页的概率/凯利面板是服务端渲染的，必须也读到
    assert len(parsed['probability']) == 27
    assert len(parsed['kelly']) == 27


# --------------------------------------------------------------------------- #
# 盈亏
# --------------------------------------------------------------------------- #
def test_pnl_tab_reads_all_five_sections(pnl_html: str) -> None:
    parsed = tabs.parse_pnl_tab(pnl_html)
    assert parsed['available'] is True
    assert set(parsed['sections']) == {
        '竞足保存盈亏', '竞足人气盈亏', '必发交易盈亏', '交易分布对比', '五要素',
    }
    saved = parsed['sections']['竞足保存盈亏']
    assert saved['headers'] == ['', '指数', '保存量', '盈亏', '冷热指数']
    assert saved['rows'][0] == {'label': '胜', 'cells': ['4.55', '353,818', '+489,356', '-21']}
    assert len(saved['rows']) == 3


def test_pnl_betfair_is_reported_missing_not_substituted(pnl_html: str) -> None:
    """必发那一段页面自己写着「暂无数据」。

    绝不能用竞足保存盈亏顶替必发（两个不同的市场），必须
    `betfair_available=False` + 一行 note 说明。
    """
    parsed = tabs.parse_pnl_tab(pnl_html)
    assert parsed['betfair_available'] is False
    assert '暂无数据' in parsed['betfair_note']


def test_pnl_absent_sections_are_named_not_invented() -> None:
    parsed = tabs.parse_pnl_tab('<html><body>no sections here</body></html>')
    assert parsed['available'] is False
    assert parsed['stage'] == 'no_exchange_sections'
    assert parsed['sections'] == {}


# --------------------------------------------------------------------------- #
# 抓取编排：每页一次抓取、逐页记录证据
# --------------------------------------------------------------------------- #
def test_scrape_records_one_fetch_per_tab_with_url_status_bytes(
    monkeypatch: pytest.MonkeyPatch, ah_html: str, ou_html: str,
    eu_html: str, pnl_html: str,
) -> None:
    """真实抓包注入，逐页断言 `url/status/bytes/parse_stage` 都在。

    这是「每个数字可追溯到 URL + HTTP 状态 + 字节数 + 解析阶段」的落点。
    """
    payload = {
        'ah': (ah_html, 95392, 'https://m.okooo.com/match/handicap.php?MatchID=1347776'),
        'ou': (ou_html, 13148, 'https://m.okooo.com/match/ah.php?type=daxiao&matchId=1347776'),
        'eu': (eu_html, 90907, 'https://m.okooo.com/match/odds.php?MatchID=1347776'),
        'rq': (eu_html, 90907, 'https://m.okooo.com/match/odds.php?MatchID=1347776'),
        'pnl': (pnl_html, 15324, 'https://m.okooo.com/match/exchanges.php?MatchID=1347776'),
    }
    calls: list[str] = []

    def fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(url)
        for tab, (html, size, expected_url) in payload.items():
            if url == expected_url:
                return {'ok': True, 'url': url, 'status': 200, 'bytes': size,
                        'html': html, 'title': tab, 'origin_blocked': False, 'error': ''}
        return {'ok': False, 'url': url, 'status': 403, 'bytes': 0, 'html': '',
                'origin_blocked': True, 'error': 'HTTP 403'}

    monkeypatch.setattr(tabs, '_fetch_detail', fake_fetch)
    out = tabs.scrape_okooo_analysis_tabs('1347776')
    assert out['available'] is True
    assert len(calls) == len(tabs.TAB_CLICK_PATHS)
    assert out['tab_order'] == ['analysis', 'ah', 'ou', 'eu', 'rq', 'pnl']
    assert out['tabs']['ah']['bytes'] == 95392
    assert out['tabs']['ah']['parse_stage'] == 'ok'
    assert out['tabs']['ou']['bytes'] == 13148
    assert out['tabs']['pnl']['status'] == 200
    # `析` 页没有对应夹具 -> 403，必须如实报成 origin_blocked
    assert out['tabs']['analysis']['parse_stage'] == 'origin_blocked'
    assert out['tabs']['analysis']['origin_blocked'] is True
    codes = {m['tab']: m['reason_code'] for m in out['missing']}
    assert codes['analysis'] == 'okooo_origin_ip_blocked'


def test_scrape_marks_js_rendered_panels_while_still_available(
    ah_html: str, ou_html: str, eu_html: str, pnl_html: str,
) -> None:
    """页面可用 ≠ 每一块都在场。最值/让球面板缺失必须单独登记。"""
    out = tabs.scrape_okooo_analysis_tabs('1347776', html_by_tab={
        'ah': ah_html, 'ou': ou_html, 'eu': eu_html, 'rq': eu_html, 'pnl': pnl_html,
    })
    assert out['available'] is True
    codes = [(m['tab'], m['reason_code']) for m in out['missing']]
    assert ('rq', 'handicap_panel_js_rendered') in codes
    assert ('ah', 'zuizhi_panel_js_rendered') in codes
    assert ('ou', 'zuizhi_panel_js_rendered') in codes
    # 有抓包的页不该被判成不可用
    for tab in ('ah', 'ou', 'eu', 'rq', 'pnl'):
        assert out['tabs'][tab]['available'] is True, tab


def test_scrape_without_match_id_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise AssertionError('must not fetch without a match id')

    monkeypatch.setattr(tabs, '_fetch_detail', boom)
    out = tabs.scrape_okooo_analysis_tabs('')
    assert out['available'] is False
    assert out['missing'][0]['reason_code'] == 'no_match_id'


def test_tab_evidence_line_contains_all_four_trace_fields() -> None:
    line = tabs.tab_evidence({
        'url': 'https://m.okooo.com/match/handicap.php?MatchID=1',
        'status': 200, 'bytes': 95392, 'parse_stage': 'ok',
    })
    assert 'handicap.php' in line
    assert 'HTTP 200' in line
    assert '95392B' in line
    assert 'stage=ok' in line
