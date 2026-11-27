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
def _match_stub() -> dict[str, Any]:
    return {
        'match_id': '1348444', 'match_no': '四001', 'league': '亚冠乙',
        'home': '淡滨尼', 'away': '上海申',
        'sub_urls': ['https://m.okooo.com/match/history.php?MatchID=1348444'],
    }


def test_waf_blocked_is_terminal_and_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        calls.append(url)
        assert 'blocked' in _blocked_page().lower() or '405'
        return {
            'ok': False, 'url': url, 'status': 405, 'bytes': 2657,
            'title': '405', 'blocked': True, 'error': 'HTTP 405',
            'html': _blocked_page(), 'encoding': 'gbk', 'referer': '',
        }

    monkeypatch.setattr(scraper, '_thread_session', lambda proxies: object())
    monkeypatch.setattr(scraper, 'fetch_page', fake_fetch)
    monkeypatch.setattr(scraper, 'POST_TASK_SLEEP', (0.0, 0.0))

    records = scraper.fetch_subpages([_match_stub()], max_workers=1)

    assert len(records) == 1
    record = records[0]
    assert record['status'] == 'blocked', record
    assert record['status'] != 'error'
    assert record['attempts'] == 1, 'WAF 拦截重试没用，必须一次就收手'
    assert 'HTTP 405' in record['error_msg']
    assert record['http_status'] == 405
    assert record['rows'] == []
    assert calls == ['https://m.okooo.com/match/history.php?MatchID=1348444']


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
    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        return {
            'ok': False, 'url': url, 'status': 405, 'bytes': 2657,
            'title': '405', 'blocked': True, 'error': 'HTTP 405',
            'html': _blocked_page(), 'encoding': 'gbk', 'referer': '',
        }

    monkeypatch.setattr(scraper, 'warm_session', lambda *a, **k: object())
    monkeypatch.setattr(scraper, 'fetch_page', fake_fetch)
    result = scraper.fetch_match_list()
    assert result['available'] is False
    assert result['blocked'] is True
    assert 'WAF' in result['reason']
    assert 'HTTP 405' in result['reason']


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


def test_subpages_are_opt_in(listing_html: str, tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        seen.append(url)
        return {
            'ok': True, 'url': url, 'status': 200, 'bytes': 64, 'title': '历史',
            'blocked': False, 'error': '',
            'html': '<table><tr><td>主队</td><td>6胜</td></tr></table>',
            'encoding': 'gbk', 'referer': '',
        }

    with (
        mock.patch.dict(sys.modules, {'openpyxl': None}),
        mock.patch.object(scraper, 'fetch_page', fake_fetch),
        mock.patch.object(scraper, '_thread_session', lambda proxies: object()),
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
    # 列表页卡片上只有 note/history/odds 三种比赛子页，没有亚指链接。
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
    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        return {
            'ok': True, 'url': url, 'status': 200, 'bytes': 40, 'title': 't',
            'blocked': False, 'error': '', 'html': '<html><body>空</body></html>',
            'encoding': 'gbk', 'referer': '',
        }

    monkeypatch.setattr(scraper, '_thread_session', lambda proxies: object())
    monkeypatch.setattr(scraper, 'fetch_page', fake_fetch)
    monkeypatch.setattr(scraper, 'POST_TASK_SLEEP', (0.0, 0.0))

    records = scraper.fetch_subpages([_match_stub()], max_workers=1)
    assert records[0]['status'] == 'error'
    assert records[0]['rows'] == []
    assert 'no_row_container' in records[0]['error_msg']


def test_transient_error_retries_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        attempts.append(1)
        return {
            'ok': False, 'url': url, 'status': 500, 'bytes': 0, 'title': '',
            'blocked': False, 'error': 'HTTP 500', 'html': '', 'encoding': '', 'referer': '',
        }

    monkeypatch.setattr(scraper, '_thread_session', lambda proxies: object())
    monkeypatch.setattr(scraper, 'fetch_page', fake_fetch)
    monkeypatch.setattr(scraper, 'POST_TASK_SLEEP', (0.0, 0.0))
    monkeypatch.setattr(scraper.time, 'sleep', lambda *_: None)

    records = scraper.fetch_subpages([_match_stub()], max_workers=1)
    assert records[0]['status'] == 'error'
    assert records[0]['attempts'] == scraper.SUBPAGE_MAX_ATTEMPTS
    assert len(attempts) == scraper.SUBPAGE_MAX_ATTEMPTS


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
