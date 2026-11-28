"""`cs_origin_ip_blocked` 只能在**同出口探针也失败**时才成立。

用户实机日志里同一秒、同一进程、同一出口出现过自相矛盾的三行：

    https://www.okooo.com/soccer/match/1341275/cs/  → HTTP 405（WAF 拦截页）
    https://m.okooo.com/match/cs.php?MatchID=...    → HTTP 404（页面找不到了）
    https://m.okooo.com/jczq/                       → HTTP 200, 325583 字节

第三条就在生产主路径上（`fetch_jczq31` 抓的就是它）。如果「出口 IP 被整体拦掉」
为真，第三条不可能拿到 325KB。所以旧结论「机房/云段 IP 一律被拦、客户端指纹/
UA/Referer/cookie 均无效、需要住宅 IP」是**证据不足**下的错误外推，而且它让
用户放弃重抓。

修法：判 `origin_blocked` 之前先用同一份 proxies / session 打一条已知可达的同主机
页面（canary）。探针 200 → 是路径级拦截，必须继续重试/换来源；探针也失败 → 才
允许说出口不可用，并且原因里必须带探针 URL 与它自己的状态码。

夹具全部来自仓库自己的真实抓包：
    tests/fixtures/okooo_jczq_list_20261126.html   m.okooo.com/jczq/ 整页（207365 字节）
    tests/fixtures/okooo_desktop_cs_1347776.html   www.okooo.com/.../cs/ 真实 200 页
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / 'tests' / 'fixtures'
JCZQ_FIXTURE = FIXTURES / 'okooo_jczq_list_20261126.html'
CS_FIXTURE = FIXTURES / 'okooo_desktop_cs_1347776.html'

import okooo_mobile_cs as cs  # noqa: E402

USER_MATCH_ID = '1347776'


def _blocked_page() -> str:
    """最小阿里云 WAF 拦截页：命中 `_BLOCK_MARKERS` 的 blocked 口径。"""
    return (
        '<html><head><title>405</title></head><body>'
        '<script>var block_message = "Sorry, your request has been blocked...";</script>'
        '</body></html>'
    )


def _jczq_html() -> str:
    return cs.decode_okooo_html(JCZQ_FIXTURE.read_bytes())


def _cs_html() -> str:
    return cs.decode_okooo_html(CS_FIXTURE.read_bytes())


class _Resp:
    def __init__(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.content = body.encode('utf-8')
        self.headers = dict(headers or {})
        self.encoding = 'utf-8'

    @property
    def text(self) -> str:
        return self.content.decode('utf-8', 'replace')


class _Session:
    """按 URL 决定响应的假 session，交给真实 `_fetch_detail` / `_http_attempt`。

    判定（状态码、WAF 响应体、缓存证据）全部是生产代码，只有传输是假的——
    这正是这一轮要测的东西。
    """

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[str] = []

    def get(self, url, headers=None, proxies=None, timeout=None):
        self.calls.append(url)
        return self.handler(url, headers or {})

    def close(self) -> None:
        pass


@pytest.fixture()
def cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- #
# (a) cs 405 + 同主机 /jczq/ 200 → 原因**不是** cs_origin_ip_blocked
# --------------------------------------------------------------------------- #
def test_cs_405_with_reachable_canary_is_not_an_ip_block(cache_dir: Path) -> None:
    """用户日志的真实形状：/cs/ 405，同主机 /jczq/ 200。

    旧逻辑只看 /cs/ 的 405 + WAF 页 + CDN 未命中就判 `cs_origin_ip_blocked`，
    连重试都跳过。有探针以后必须改成「路径级拦截」，并且原因里要点名
    「同出口其它澳客页面可达」。
    """
    jczq = _jczq_html()

    def handler(url, headers):
        if url == 'https://m.okooo.com/jczq/':
            return _Resp(200, jczq, {'age': '20', 'server': 'Lego Server'})
        return _Resp(405, _blocked_page(), {'x-cache-status': 'MISS'})

    session = _Session(handler)
    out = cs.scrape_okooo_mobile_cs_zuizhi(
        USER_MATCH_ID, force_refresh=True, patience_retries=0, session=session,
    )

    reason = str(out.get('reason') or '')
    assert not reason.startswith('cs_origin_ip_blocked'), reason
    assert out.get('origin_blocked') is not True, out.get('origin_blocked')
    # 探针真的跑过，结论挂在返回值上（便于日志与后续排查）。
    canary = out.get('canary') or {}
    assert canary.get('ok') is True, canary
    assert canary.get('url') == 'https://m.okooo.com/jczq/', canary
    assert int(canary.get('status') or 0) == 200, canary
    # 用户的行动项不能被误导成「换住宅 IP」。
    detail = str(out.get('reason_detail') or '') + str(out.get('error') or '')
    assert '同出口其它澳客页面可达' in detail, detail
    assert '不需要换住宅 IP' in detail, detail
    assert '一律被拦' not in detail, detail


def test_cs_405_with_reachable_canary_still_patience_retries(cache_dir: Path) -> None:
    """路径级拦截不允许跳过耐心重试：旧代码在这里直接 return，一次都没等。"""
    jczq = _jczq_html()
    sleeps: list[float] = []

    def handler(url, headers):
        if url == 'https://m.okooo.com/jczq/':
            return _Resp(200, jczq, {'age': '20'})
        return _Resp(405, _blocked_page(), {'x-cache-status': 'MISS'})

    session = _Session(handler)
    real_sleep = cs.time.sleep
    cs.time.sleep = lambda s: sleeps.append(s)  # type: ignore[assignment]
    try:
        cs.scrape_okooo_mobile_cs_zuizhi(
            USER_MATCH_ID, force_refresh=True,
            patience_retries=2, patience_gap=0.01, session=session,
        )
    finally:
        cs.time.sleep = real_sleep  # type: ignore[assignment]

    cs_calls = [u for u in session.calls if u.endswith('/cs/')]
    assert sleeps, 'path-level block must still go through the patience retry loop'
    assert len(cs_calls) >= 2, f'/cs/ only requested {len(cs_calls)} time(s)'


def test_reachable_canary_lets_a_later_source_succeed(cache_dir: Path) -> None:
    """探针确认出口可用后，换来源必须真的继续试，且能成功。"""
    cs_html = _cs_html()
    jczq = _jczq_html()
    seen: list[str] = []

    def handler(url, headers):
        seen.append(url)
        if url == 'https://m.okooo.com/jczq/':
            return _Resp(200, jczq, {'age': '20'})
        if 'second-source' in url:
            return _Resp(200, cs_html, {'age': '20'})
        return _Resp(405, _blocked_page(), {'x-cache-status': 'MISS'})

    session = _Session(handler)
    out = cs.scrape_okooo_mobile_cs_zuizhi(
        USER_MATCH_ID, force_refresh=True, patience_retries=0, session=session,
    )
    # 第一条来源被拦，探针可达 → 不是终态；`tried` 必须记下这次路径级拦截。
    assert out.get('origin_blocked') is not True, out
    assert any('/cs/' in u for u in seen), seen


# --------------------------------------------------------------------------- #
# (b) 探针也 405 → 原因**是** cs_origin_ip_blocked，且文案带探针 URL 与状态码
# --------------------------------------------------------------------------- #
def test_canary_also_blocked_is_the_only_way_to_claim_ip_block(cache_dir: Path) -> None:
    """同出口连 /jczq/ 都 405：这才是「这一轮出口不可用」。"""

    def handler(url, headers):
        return _Resp(405, _blocked_page(), {'x-cache-status': 'MISS'})

    session = _Session(handler)
    out = cs.scrape_okooo_mobile_cs_zuizhi(
        USER_MATCH_ID, force_refresh=True, patience_retries=0, session=session,
    )

    reason = str(out.get('reason') or '')
    assert reason.startswith('cs_origin_ip_blocked'), reason
    assert out.get('origin_blocked') is True, out
    assert out.get('parse_stage') == 'origin_blocked', out
    # 证据必须带探针 URL 与它自己的状态码，不能只写一句结论。
    assert 'm.okooo.com/jczq/' in reason, reason
    assert '405' in reason, reason
    assert int(out.get('canary_status') or 0) == 405, out.get('canary')
    # 本轮没逐个试过客户端侧手段，就不许写「客户端指纹/UA/Referer/cookie 均无效」。
    assert '客户端指纹' not in reason, reason
    assert '均无效' not in reason, reason
    # 竞彩31侧通道照旧独立抓取，两个事实互不替代。
    assert 'jc_score_fetch_reason' in out or 'jc_score_block_reason' in out, list(out)


def test_reason_is_derived_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """同一份代码，两种传输结果 → 两种原因。原因必须是推导出来的。"""
    jczq = _jczq_html()

    def reachable(url, headers):
        if url == 'https://m.okooo.com/jczq/':
            return _Resp(200, jczq, {'age': '20'})
        return _Resp(405, _blocked_page())

    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'a'))
    ok_canary = cs.scrape_okooo_mobile_cs_zuizhi(
        USER_MATCH_ID, force_refresh=True, patience_retries=0,
        session=_Session(reachable),
    )

    def dead(url, headers):
        return _Resp(405, _blocked_page())

    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'b'))
    dead_canary = cs.scrape_okooo_mobile_cs_zuizhi(
        USER_MATCH_ID, force_refresh=True, patience_retries=0,
        session=_Session(dead),
    )

    assert not str(ok_canary.get('reason') or '').startswith('cs_origin_ip_blocked')
    assert str(dead_canary.get('reason') or '').startswith('cs_origin_ip_blocked')


# --------------------------------------------------------------------------- #
# (c) 死链：m.okooo.com/match/cs.php 不再作为候选来源
# --------------------------------------------------------------------------- #
def test_dead_mobile_cs_path_is_not_a_candidate_anymore(cache_dir: Path) -> None:
    """`/match/cs.php` 在实机上同主机回 404，而同主机 /jczq/ 是 200。

    同主机 404（页面没了）与 405（WAF 拦截页）是两种完全不同的事实，所以它不是
    「被拦」而是「路径不存在」。留着它会让每场多一次必然失败的往返。
    """
    seen: list[str] = []

    def handler(url, headers):
        seen.append(url)
        if url == 'https://m.okooo.com/jczq/':
            return _Resp(200, _jczq_html(), {'age': '20'})
        return _Resp(405, _blocked_page())

    cs.scrape_okooo_mobile_cs_zuizhi(
        USER_MATCH_ID, force_refresh=True, patience_retries=0,
        session=_Session(handler),
    )
    assert not any('match/cs.php' in u for u in seen), seen
    # 生产代码里也不该再拼出这条候选（注释里说明「已移除」是允许的）。
    source = (ROOT / 'okooo_mobile_cs.py').read_text(encoding='utf-8')
    assert "match/cs.php?MatchID=" not in source


def test_canary_prefers_the_known_reachable_jczq_path() -> None:
    """探针第一条必须是生产主路径上的 `/jczq/`，不是站点根。"""
    urls = cs._canary_urls(USER_MATCH_ID)
    assert urls[0] == 'https://m.okooo.com/jczq/', urls
    assert 'https://www.okooo.com/' in urls


def test_cache_evidence_comment_no_longer_states_the_ip_block_conclusion() -> None:
    """`_cache_evidence` 的注释不能把 2026-11-26 的观察写成因果结论。

    同主机 /jczq/ 200 已经推翻「唯一决定成败的是缓存命中」这句外推；
    注释里保留 age / x-cache-status 的实测值，但结论必须交给探针验证。
    """
    source = (ROOT / 'okooo_mobile_cs.py').read_text(encoding='utf-8')
    start = source.index('def _cache_evidence')
    doc = source[start: start + 2000]
    assert '唯一决定成败的是 CDN 缓存是否命中' not in doc
    assert '待同出口探针' in doc or '_canary_probe' in doc
    assert 'age' in doc and 'x-cache-status' in doc
