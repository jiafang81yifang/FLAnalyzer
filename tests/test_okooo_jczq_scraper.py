"""okooo_jczq_scraper 的离线测试，全部基于真实抓取夹具。

夹具 `tests/fixtures/okooo_jczq_list_20261126.html`：
  https://m.okooo.com/jczq/ 2026-11-26 真实响应，207365 字节，GBK，
  23 张卡片 / 23 个不同 MatchID / 每场 31 项竞彩比分赔率。
不要手写假 HTML 顶替它：选择器漂移只有在真实字节上才会暴露。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / 'tests' / 'fixtures'
LIST_FIXTURE = FIXTURES / 'okooo_jczq_list_20261126.html'

scraper = importlib.import_module('okooo_jczq_scraper')
okooo = importlib.import_module('okooo_mobile_cs')


@pytest.fixture(scope='module')
def list_html() -> str:
    raw = LIST_FIXTURE.read_bytes()
    assert len(raw) == 207365, f'fixture bytes drifted: {len(raw)}'
    return scraper.decode_html(raw)


@pytest.fixture(scope='module')
def listing_html(list_html: str) -> str:
    return list_html


@pytest.fixture(scope='module')
def listing(list_html: str) -> dict[str, Any]:
    return scraper.fetch_match_list(html=list_html)


def _json_tail(output: str) -> dict[str, Any]:
    """从 CLI 输出里取最后一段 JSON（--json 模式是带缩进的多行对象）。"""
    start = output.index('{')
    return json.loads(output[start:])


def _blocked_page() -> str:
    """最小阿里云 WAF 拦截页：命中 okooo_mobile_cs 的 blocked 口径。"""
    return (
        '<html><head><title>405</title></head><body>'
        '<script>var block_message = "Sorry, your request has been blocked...";</script>'
        '</body></html>'
    )


# --------------------------------------------------------------------------- #
# 1. 列表解析
# --------------------------------------------------------------------------- #
def test_list_parses_every_card_with_real_selectors(listing: dict[str, Any]) -> None:
    assert listing['available'] is True
    assert listing['reason'] == ''
    assert listing['status'] == 200
    assert listing['blocked'] is False
    assert listing['match_count'] == 23
    assert listing['card_hits'] == 23
    assert listing['skipped_cards'] == 0
    matches = listing['matches']
    assert len(matches) == 23
    assert len({m['match_id'] for m in matches}) == 23, 'MatchID must be unique per card'


def test_first_match_fields_match_the_live_dom(listing: dict[str, Any]) -> None:
    first = listing['matches'][0]
    assert first['match_id'] == '1348444'
    assert first['league_id'] == '668', 'data-mid is the league id, not the match id'
    assert first['match_no'] == '四001'
    assert first['league'] == '亚冠乙'
    assert first['home'] == '淡滨尼'
    assert first['away'] == '上海申'
    assert first['score'] == '1-7'
    assert first['handicap'] == '0'
    # betway=0 是胜平负，betway=1 是让球胜平负。
    assert first['wdl_odds'] == {'胜': 5.80, '平': 4.90, '负': 1.31}
    assert first['handicap_odds'] == {'胜': 2.72, '平': 3.60, '负': 2.03}


def test_unplayed_match_has_empty_score(listing: dict[str, Any]) -> None:
    """未完赛的卡片没有 span.zVS，必须记 '' 而不是 '0-0' 之类的假比分。"""
    scores = [m['score'] for m in listing['matches']]
    assert any(score == '' for score in scores), scores
    for match in listing['matches']:
        if match['score']:
            assert '-' in match['score'], match['score']


def test_card_links_are_deduped_and_match_pages_separated(
    listing: dict[str, Any]
) -> None:
    """每张卡片 4 条去重链接，其中 3 条是可抓的比赛子页。

    实测 DOM：`a.liansai` 的联赛页 + `div[href]` 的 note.php + history.php + odds.php。
    规则（写清并断言）：
      * `sub_urls` 保留卡片上的全部 4 条去重链接（含联赛页/商品页），因为
        「4 条」是真实 DOM 的事实；非比赛链接按 URL 规则归 `other`。
      * `match_sub_urls` 只保留 `/match/{history,odds,note}.php` 且 MatchID 为纯数字
        的 3 条——联赛页/商品页不带 MatchID，无法归属到某场比赛，永不抓取。
    """
    for match in listing['matches']:
        urls = match['sub_urls']
        assert len(urls) == len(set(urls)), urls
        assert len(urls) == 4, urls
        assert len(match['match_sub_urls']) == 3, match['match_sub_urls']
        assert set(match['match_sub_urls']) <= set(urls)
        kinds = {scraper.classify_subpage(url) for url in match['match_sub_urls']}
        assert kinds == {'odds', 'analysis', 'detail'}, kinds
        assert scraper.classify_subpage(
            next(u for u in urls if u not in match['match_sub_urls'])
        ) == 'other'
        for url in match['match_sub_urls']:
            assert url.startswith('https://m.okooo.com/match/'), url
            assert f'MatchID={match["match_id"]}' in url, url
    first = listing['matches'][0]
    # 保序：DOM 里 note 在 history 之前，不能按类型重排。
    assert first['match_sub_urls'][0].startswith(
        'https://m.okooo.com/match/note.php?MatchID=1348444'
    )
    assert all('/saishi/' not in u and 'f.okooo.com' not in u
               for u in first['match_sub_urls'])
    # 联赛页/商品页分别出现在两类卡片里，都必须归 other 而不是当比赛子页。
    other_links = {u for m in listing['matches'] for u in m['sub_urls']
                   if u not in m['match_sub_urls']}
    assert any('/saishi/' in u for u in other_links)
    assert any('f.okooo.com/goods/league/' in u for u in other_links)


def test_list_carries_full_jc31_score_odds(listing: dict[str, Any]) -> None:
    """列表页 `var oddsData` 自带每场 31 项竞彩比分，等于一次请求免费拿到。"""
    assert listing['jc31_match_count'] == 23
    first = listing['matches'][0]
    assert first['jc31_available'] is True
    assert len(first['jc_score_odds']) == 31
    # 这 9 项市均页面从不报价，但竞彩 SportteryScore 一定有。
    for score in scraper.JC31_ALWAYS_QUOTED_ELSEWHERE:
        assert score in first['jc_score_odds'], score
        assert first['jc_score_odds'][score] > 1
    assert first['jc_score_odds']['1-0'] == 22.0


def test_team_alias_normalization() -> None:
    assert scraper.normalize_team_name('曼联') == '曼彻斯特联'
    assert scraper.normalize_team_name('曼城') == '曼彻斯特城'
    assert scraper.normalize_team_name('国米') == '国际米兰'
    assert scraper.normalize_team_name('大巴黎') == '巴黎圣日耳曼'
    # 不认识的队名原样保留（只压空白），不能凭空改写。
    assert scraper.normalize_team_name(' 淡 滨 尼 ') == '淡滨尼'


def test_subpage_type_rules() -> None:
    assert scraper.classify_subpage('https://m.okooo.com/match/odds.php?MatchID=1') == 'odds'
    assert scraper.classify_subpage('https://m.okooo.com/match/ah.php?type=daxiao') == 'asian'
    assert scraper.classify_subpage('https://m.okooo.com/match/history.php?MatchID=1') == 'analysis'
    assert scraper.classify_subpage('https://m.okooo.com/match/note.php?MatchID=1') == 'detail'
    assert scraper.classify_subpage('https://m.okooo.com/match/unknown.php') == 'other'


# --------------------------------------------------------------------------- #
# 2. 编码
# --------------------------------------------------------------------------- #
def test_fixture_decodes_as_gbk(list_html: str) -> None:
    assert '<title>竞足最新赛程-澳客</title>' in list_html
    assert '亚冠乙' in list_html
    raw_declared = LIST_FIXTURE.read_bytes()[:4096].lower()
    assert b'charset=gbk' in raw_declared.replace(b'"', b'').replace(b"'", b'')


# --------------------------------------------------------------------------- #
# 3. WAF 拦截：独立状态、不重试
# --------------------------------------------------------------------------- #
class _ProbeResp:
    """最小响应对象：够 _http_attempt 读状态码/头/正文即可。"""

    def __init__(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.content = body.encode('utf-8')
        self.headers = headers or {}
        self.encoding = 'utf-8'


def _patch_requests_get(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[str]:
    calls: list[str] = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(okooo.requests, 'get', fake_get)
    # 必须打 scraper 自己的别名：它是 import 时绑定的引用，
    # patch okooo._warm_session 改不到它，测试会真的去打线上站点。
    monkeypatch.setattr(scraper, 'warm_session', lambda *a, **k: okooo.requests)
    monkeypatch.setattr(scraper, '_thread_session', lambda *a, **k: okooo.requests)
    return calls


def _match_stub() -> dict[str, Any]:
    return {
        'match_id': '1348444', 'match_no': '四001', 'league': '亚冠乙',
        'home': '淡滨尼', 'away': '上海申',
        'sub_urls': ['https://m.okooo.com/match/history.php?MatchID=1348444'],
    }


def test_waf_blocked_is_terminal_and_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """子页被源站按出口 IP 拦：status=origin_blocked，且一次往返就收手。"""
    calls = _patch_requests_get(
        monkeypatch,
        [_ProbeResp(405, _blocked_page(), {'x-cache-status': 'EXPIRED', 'server': 'nginx'})],
    )

    records = scraper.fetch_subpages([_match_stub()], max_workers=1)

    assert len(records) == 1
    record = records[0]
    assert record['status'] == 'origin_blocked', record
    assert record['status'] != 'error'
    assert record['attempts'] == 1, '源站 IP 拦截重试没用，必须一次就收手'
    assert record['origin_blocked'] is True
    assert record['x_cache_status'] == 'EXPIRED'
    assert '源站按出口 IP 拦截' in record['error_msg']
    assert '不是页面结构变化' in record['error_msg']
    assert record['http_status'] == 405
    assert record['rows'] == []
    assert len(calls) == 1, calls


def test_okooo_blocked_detection_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    """本模块不另写 blocked 判定：405 页面走 okooo_mobile_cs 的同一条口径。"""
    class _Resp:
        status_code = 405
        content = _blocked_page().encode('utf-8')
        encoding = 'utf-8'

        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def get(self, *a, **k):
            return self

    monkeypatch.setattr(okooo.requests, 'get', lambda *a, **k: _Resp())
    info = okooo._fetch_detail('https://m.okooo.com/match/note.php?MatchID=1', retries=1)
    assert info['blocked'] is True
    assert info['ok'] is False
    assert info['status'] == 405


# --------------------------------------------------------------------------- #
# 4. 选择器未命中
# --------------------------------------------------------------------------- #
def test_missing_selector_reports_reason() -> None:
    plain = '<html><head><title>正常页面</title></head><body><div class="other">没有卡片</div></body></html>'
    result = scraper.fetch_match_list(html=plain)
    assert result['available'] is False
    assert result['match_count'] == 0
    assert result['matches'] == []
    assert 'div.jsMatchItem' in result['reason']
    assert '选择器未命中' in result['reason']
    assert '0' in result['reason']


def test_blocked_list_page_reason_names_waf() -> None:
    result = scraper.fetch_match_list(html=_blocked_page())
    assert result['available'] is False
    assert result['reason']
    # 离线喂 html 时没有状态码，此时不能谎称 WAF；但必须点名选择器与命中数。
    assert 'div.jsMatchItem' in result['reason']


def test_blocked_flag_from_network_names_waf(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_requests_get(
        monkeypatch,
        [_ProbeResp(405, _blocked_page(), {'x-cache-status': 'EXPIRED'})],
    )
    result = scraper.fetch_match_list()
    assert result['available'] is False
    assert result['blocked'] is True
    assert result['origin_blocked'] is True
    assert '源站按出口 IP 拦截' in result['reason']
    assert 'HTTP 405' in result['reason']
    assert 'x-cache-status=EXPIRED' in result['reason']


# --------------------------------------------------------------------------- #
# 5. 导出：openpyxl 缺失只降级 CSV
# --------------------------------------------------------------------------- #
def test_export_without_openpyxl_writes_csv_only(
    listing: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with mock.patch.dict(sys.modules, {'openpyxl': None}):
        frame = scraper.clean_and_export(listing['matches'], [], tmp_path)

    assert frame.attrs['xlsx_written'] is False
    assert frame.attrs['xlsx_path'] == ''
    csv_path = Path(frame.attrs['csv_path'])
    assert csv_path.exists()
    assert csv_path.stat().st_size > 0
    assert not list(tmp_path.glob('*.xlsx'))
    assert list(frame.columns) == scraper.FINAL_COLUMNS
    assert len(frame) == 23
    err = capsys.readouterr().err
    assert 'pip install openpyxl' in err


def test_export_with_openpyxl_writes_both(listing: dict[str, Any], tmp_path: Path) -> None:
    pytest.importorskip('openpyxl')
    frame = scraper.clean_and_export(listing['matches'], [], tmp_path)
    assert frame.attrs['xlsx_written'] is True
    assert Path(frame.attrs['xlsx_path']).exists()
    assert Path(frame.attrs['csv_path']).exists()


def test_export_deduplicates_same_match_and_url(
    listing: dict[str, Any], tmp_path: Path
) -> None:
    url = 'https://m.okooo.com/match/history.php?MatchID=1348444'
    base = {
        'match_id': '1348444', 'match_no': '四001', 'league': '亚冠乙',
        'home': '淡滨尼', 'away': '上海申', 'sub_url': url,
        'subpage_type': 'analysis', 'status': 'ok', 'attempts': 1,
        'http_status': 200, 'bytes': 100, 'title': 't',
    }
    first = {**base, 'rows': ['  主队   近10场  6胜  ']}
    second = {**base, 'rows': ['重复行']}
    frame = scraper.clean_and_export([listing['matches'][0]], [first, second], tmp_path)
    kept = frame[frame['match_id'] == '1348444']
    assert len(kept) == 1, kept.to_dict('records')
    assert '重复行' not in kept.iloc[0]['rows_text']


def test_rows_are_denoised(listing: dict[str, Any], tmp_path: Path) -> None:
    record = {
        'match_id': '1348444',
        'sub_url': 'https://m.okooo.com/match/note.php?MatchID=1348444',
        'subpage_type': 'detail', 'status': 'ok', 'attempts': 1,
        'http_status': 200, 'bytes': 10, 'title': 't',
        'rows': ['  主队\t近期   战绩 ', '立即下载APP查看', '打开客户端', '客队 3胜'],
    }
    frame = scraper.clean_and_export([listing['matches'][0]], [record], tmp_path)
    text = frame[frame['sub_url'] != ''].iloc[0]
    assert text['row_count'] == 2
    assert '下载APP' not in text['rows_text'] and '客户端' not in text['rows_text']
    assert text['rows_text'] == '主队 近期 战绩 | 客队 3胜'


# --------------------------------------------------------------------------- #
# 6. 默认模式一条子页请求都不发
# --------------------------------------------------------------------------- #
def test_default_mode_issues_no_subpage_requests(
    listing_html: str, tmp_path: Path
) -> None:
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append(str(args[0] if args else kwargs.get('url')))
        raise AssertionError(f'unexpected network call: {calls}')

    with (
        mock.patch.dict(sys.modules, {'openpyxl': None}),
        mock.patch.object(scraper, 'fetch_page', forbidden),
        mock.patch.object(scraper, 'warm_session', forbidden),
        mock.patch.object(okooo.requests, 'get', forbidden),
        mock.patch.object(okooo.requests, 'Session', forbidden),
    ):
        result = scraper.scrape(
            output_dir=tmp_path, html=listing_html, with_subpages=False
        )

    assert calls == [], calls
    assert result['listing']['available'] is True
    assert result['subpage_records'] == []
    assert len(result['frame']) == 23


def test_subpages_are_opt_in(listing_html: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = '<html><body><table><tr><td>主队</td><td>6胜</td></tr></table></body></html>'
    seen = _patch_requests_get(
        monkeypatch,
        [_ProbeResp(200, body, {'age': '12', 'server': 'Lego Server'})],
    )
    with (
        mock.patch.dict(sys.modules, {'openpyxl': None}),
        mock.patch.object(scraper, 'POST_TASK_SLEEP', (0.0, 0.0)),
    ):
        result = scraper.scrape(
            output_dir=tmp_path, html=listing_html,
            with_subpages=True, limit=1,
        )

    # 只抓 3 条比赛子页；联赛页没有 MatchID，不参与抓取。
    assert len(seen) == 3, seen
    assert all('/match/' in url for url in seen)
    assert {r['status'] for r in result['subpage_records']} == {'ok'}
    # 卡片上只有 note/history/odds 三种比赛子页，没有亚指链接。
    assert {r['subpage_type'] for r in result['subpage_records']} == {
        'odds', 'analysis', 'detail',
    }


# --------------------------------------------------------------------------- #
# 7. 子页行提取
# --------------------------------------------------------------------------- #
def test_rows_extracted_from_table() -> None:
    html = '<table><tr><td>主队</td><td>6胜2平2负</td></tr><tr><td>客队</td><td>3胜</td></tr></table>'
    assert scraper.extract_rows(html) == ['主队 6胜2平2负', '客队 3胜']


def test_no_row_container_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 但没有行容器时必须报 error，不能用空行列表冒充成功。"""
    _patch_requests_get(
        monkeypatch,
        [_ProbeResp(200, '<html><body>空</body></html>', {'age': '5'})],
    )
    monkeypatch.setattr(scraper, 'POST_TASK_SLEEP', (0.0, 0.0))

    records = scraper.fetch_subpages([_match_stub()], max_workers=1)
    assert records[0]['status'] == 'error'
    assert records[0]['rows'] == []
    assert 'no_row_container' in records[0]['error_msg']


def test_transient_error_retries_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 是普通抖动：走退避重试直到上限，最终如实记 error。"""
    calls = _patch_requests_get(monkeypatch, [_ProbeResp(500, 'server error')])
    monkeypatch.setattr(scraper, 'POST_TASK_SLEEP', (0.0, 0.0))
    monkeypatch.setattr(scraper.time, 'sleep', lambda *_: None)

    records = scraper.fetch_subpages([_match_stub()], max_workers=1)
    assert records[0]['status'] == 'error'
    assert records[0]['attempts'] == scraper.SUBPAGE_MAX_ATTEMPTS
    assert len(calls) == scraper.SUBPAGE_MAX_ATTEMPTS


# --------------------------------------------------------------------------- #
# 8. 缓存命中探测：三种分支
# --------------------------------------------------------------------------- #
def test_probe_cache_hit_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中 CDN 缓存（age: 30）→ ok，且 cache_hit=True，只请求一次。"""
    body = '<html><head><title>ok</title></head><body><p>hi</p></body></html>'
    calls = _patch_requests_get(
        monkeypatch, [_ProbeResp(200, body, {'age': '30', 'server': 'Lego Server'})]
    )
    out = scraper.fetch_with_cache_probe('https://m.okooo.com/jczq/', retries=3)
    assert out['ok'] is True
    assert out['cache_hit'] is True
    assert out['origin_blocked'] is False
    assert out['age'] == '30'
    assert out['probe_status'] == 'ok'
    assert out['attempts'] == 1
    assert len(calls) == 1


def test_probe_cache_miss_405_blocked_page_is_origin_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未命中缓存 + 405 + WAF 拦截页 → origin_blocked，且不重试（调用次数 == 1）。"""
    calls = _patch_requests_get(
        monkeypatch,
        [_ProbeResp(405, _blocked_page(), {'x-cache-status': 'EXPIRED', 'server': 'nginx'})],
    )
    out = scraper.fetch_with_cache_probe('https://www.okooo.com/soccer/match/1/cs/', retries=4)
    assert out['ok'] is False
    assert out['origin_blocked'] is True
    assert out['cache_hit'] is False
    assert out['probe_status'] == 'origin_blocked'
    assert out['x_cache_status'] == 'EXPIRED'
    # 源站按出口 IP 拦截：重试没用，必须一次就收手。
    assert out['attempts'] == 1
    assert len(calls) == 1, calls


def test_probe_timeout_retries_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """普通超时不是 IP 拦截：必须走退避重试，直到次数用尽。"""
    import requests as _requests

    calls = _patch_requests_get(
        monkeypatch, [_requests.Timeout('read timed out')]
    )
    monkeypatch.setattr(scraper.time, 'sleep', lambda *_: None)
    out = scraper.fetch_with_cache_probe('https://m.okooo.com/jczq/', retries=3)
    assert out['ok'] is False
    assert out['origin_blocked'] is False
    assert out['probe_status'] == 'timeout'
    assert out['attempts'] == 3
    assert len(calls) == 3, calls


def test_probe_retries_transient_500_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_requests_get(monkeypatch, [_ProbeResp(500, 'server error')])
    monkeypatch.setattr(scraper.time, 'sleep', lambda *_: None)
    out = scraper.fetch_with_cache_probe('https://m.okooo.com/jczq/', retries=2)
    assert out['probe_status'] == 'http_error'
    assert out['attempts'] == 2
    assert len(calls) == 2


def test_probe_404_is_not_origin_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 是「页面没了」，不是「这台机器被源站拦」，不能记成 origin_blocked。"""
    calls = _patch_requests_get(
        monkeypatch, [_ProbeResp(404, '<html><body>not found</body></html>')]
    )
    out = scraper.fetch_with_cache_probe('https://m.okooo.com/jczq/', retries=3)
    assert out['origin_blocked'] is False
    assert out['probe_status'] == 'http_error'
    assert len(calls) == 1


def test_probe_never_rewrites_or_busters_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """防回归：探测层不得给 URL 加随机 query / 改写 URL。

    实测过反直觉的事实：同一路径、URL 一字不差且缓存命中时是 200，加个随机 query
    就回源被 WAF 拦成 405。所以「加 cache-buster 重试」会把本来成功的请求变成必然
    失败。这条测试对同一 URL 连续调用，断言 URL 逐字节不变。
    """
    body = '<html><head><title>ok</title></head><body><p>hi</p></body></html>'
    url = 'https://m.okooo.com/jczq/'
    calls = _patch_requests_get(monkeypatch, [_ProbeResp(200, body, {'age': '30'})])

    first = scraper.fetch_with_cache_probe(url, retries=3)
    second = scraper.fetch_with_cache_probe(url, retries=3)

    assert first['ok'] is True and second['ok'] is True
    assert calls == [url, url], calls
    assert all(call == url for call in calls), 'probe must never mutate the URL'
    for call in calls:
        assert not urlparse(call).query, f'url grew a query string: {call}'


def test_probe_rejects_cache_buster_url() -> None:
    """带 cache-buster 的 URL 直接拒绝：这是把 200 变成 405 的做法。"""
    for bad in ('https://m.okooo.com/jczq/?_=1712', 'https://m.okooo.com/jczq/?t='):
        with pytest.raises(ValueError, match='cache-buster'):
            scraper.fetch_with_cache_probe(bad)


def test_probe_keeps_meaningful_query_params() -> None:
    """带真实业务参数（MatchID / from）的 URL 必须照常放行，别误伤。"""
    scraper._assert_stable_url(
        'https://m.okooo.com/match/note.php?MatchID=1348444&from=%2Fjczq%2F'
    )


# --------------------------------------------------------------------------- #
# 9. 市均：IP 拦截不重试 / 离线注入可解析
# --------------------------------------------------------------------------- #
CS_FIXTURE = FIXTURES / 'okooo_desktop_cs_1347776.html'


def test_market_average_origin_blocked_does_not_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """市均页被源站按出口 IP 拦时，只发一次请求，并写明原因不是结构变化。"""
    calls = _patch_requests_get(
        monkeypatch,
        [_ProbeResp(405, _blocked_page(), {'x-cache-status': 'EXPIRED', 'server': 'nginx'})],
    )
    record = scraper.fetch_market_average(
        {'match_id': '1347776', 'match_no': 'x', 'home': 'A', 'away': 'B'}
    )
    assert record['status'] == 'origin_blocked'
    assert record['origin_blocked'] is True
    assert record['attempts'] == 1
    assert len(calls) == 1, calls
    assert record['http_status'] == 405
    assert record['x_cache_status'] == 'EXPIRED'
    assert '源站按出口 IP 拦截' in record['reason']
    assert '不是页面结构变化' in record['reason']
    assert record['page_avg'] == {} and record['books_avg'] == {}


def test_market_average_offline_fixture_parses_25() -> None:
    """注入真实波胆页抓包：25 项市均 / 27 家公司。"""
    record = scraper.fetch_market_average(
        {'match_id': '1347776', 'match_no': '四001', 'home': 'A', 'away': 'B'},
        cs_html=CS_FIXTURE.read_bytes(),
    )
    assert record['status'] == 'ok'
    assert record['origin_blocked'] is False
    assert record['books_n'] == 27
    assert len(record['page_avg']) == 25
    assert len(record['books_avg']) == 25
    # 市均从不报价的那 9 项确实不在市均表里——这正是竞彩 31 项的价值。
    assert '胜其它' not in record['page_avg']


def test_market_average_offline_accepts_path() -> None:
    """--cs-html 传进来的可能是路径，边界处必须自己读字节。"""
    record = scraper.fetch_market_average(
        {'match_id': '1347776', 'home': 'A', 'away': 'B'}, cs_html=CS_FIXTURE
    )
    assert record['status'] == 'ok'
    assert len(record['page_avg']) == 25


def test_export_carries_cs_reason_when_blocked(tmp_path: Path) -> None:
    """导出的 cs_reason 必须带得动「IP 拦截」结论，而不是空字符串。"""
    record = {
        'match_id': '1348444', 'status': 'origin_blocked', 'books_n': 0,
        'origin_blocked': True, 'page_avg': {}, 'books_avg': {},
        'age': '', 'x_cache_status': 'EXPIRED',
        'reason': '源站按出口 IP 拦截（阿里云 WAF 405，响应体 block_message）',
    }
    with mock.patch.dict(sys.modules, {'openpyxl': None}):
        frame = scraper.clean_and_export(
            [{'match_id': '1348444', 'match_no': '四001'}], [], tmp_path, [record],
        )
    row = frame.iloc[0]
    assert row['cs_status'] == 'origin_blocked'
    # pandas 会把 bool 列读成 numpy.bool_，用 bool() 归一后再断言。
    assert bool(row['cs_origin_blocked']) is True
    assert row['cs_x_cache_status'] == 'EXPIRED'
    assert '源站按出口 IP 拦截' in row['cs_reason']


# --------------------------------------------------------------------------- #
# 8. CLI
# --------------------------------------------------------------------------- #
def test_cli_offline_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch.dict(sys.modules, {'openpyxl': None}):
        code = scraper.main(['--html', str(LIST_FIXTURE), '--out', str(tmp_path), '--json'])
    assert code == 0
    summary = _json_tail(capsys.readouterr().out)
    assert summary['available'] is True
    assert summary['match_count'] == 23
    assert summary['jc31_match_count'] == 23
    assert summary['subpage_requests'] == 0
    assert Path(summary['csv_path']).exists()


def test_cli_returns_nonzero_when_list_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / 'empty.html'
    empty.write_text('<html><body>nothing</body></html>', encoding='utf-8')
    with mock.patch.dict(sys.modules, {'openpyxl': None}):
        code = scraper.main(['--html', str(empty), '--out', str(tmp_path / 'o'), '--json'])
    assert code == 1
    summary = _json_tail(capsys.readouterr().out)
    assert summary['available'] is False
    assert 'div.jsMatchItem' in summary['reason']


def test_module_has_no_playwright_dependency() -> None:
    source = (ROOT / 'okooo_jczq_scraper.py').read_text(encoding='utf-8')
    import ast

    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split('.')[0])
    assert 'playwright' not in imported
    allowed = {
        'argparse', 'json', 'random', 're', 'sys', 'threading', 'time',
        'concurrent', 'pathlib', 'typing', 'urllib', 'pandas', 'bs4',
        'okooo_mobile_cs', '__future__', 'openpyxl',
    }
    assert set(imported) <= allowed, sorted(set(imported) - allowed)
