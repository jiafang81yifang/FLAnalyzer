"""澳客路由：直连语义、路由选择、实际生效代理。

这一组测试补的是仓库里最大的裸奔点：`rg _http_routes tests/` 此前零命中。

三个已确认的代码事实（都能逐行指认）：

1. `_http_routes()` 曾把代理排在第一位，而 `scrape_okooo_detail` 只取 `[0]`，
   于是请求确实交给了本机代理（Clash 等）；代理之后怎么处置（是否按 GEOIP 直出）
   程序既不知道、也无法在日志里证明。
2. 「直连」路由此前**从未真的直连**：`okooo_mobile_cs._proxy_dict` 对 dict 入参做
   `{k: v for k, v in proxies.items() if isinstance(v, str) and v}`，而
   `_DIRECT_PROXIES = {'http': None, 'https': None}` 里的 `None` 不是 `str`，
   于是被过滤成 `{}`。requests 里 `proxies={}` 的语义是「我没指定，去合并环境变量」，
   **不是**直连；环境里设了 `HTTPS_PROXY` 就仍然走代理。
3. `OKOOO_ROUTE=direct` 因此是**空操作**：用户明确要求直连，请求照样走代理，
   而且日志里没有任何提示。这几乎必然导致「直连也不行 → 只能换住宅 IP」的误判。

所以这里的断言必须走 `requests.Session.merge_environment_settings` ——**真合并逻辑**，
不能自己比字典：那正是让这个缺陷骗过所有人的地方。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import okooo_mobile_cs as cs  # noqa: E402

app = importlib.import_module('app_v84_hermes')

PROXY = 'http://127.0.0.1:7890'


@pytest.fixture()
def env_proxy(monkeypatch: pytest.MonkeyPatch) -> str:
    """模拟用户机器：环境里确实设了代理（Clash 在 7890）。"""
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        monkeypatch.setenv(key, PROXY)
    for key in ('NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(key, raising=False)
    return PROXY


def _effective(proxies) -> dict:
    """用 requests 自己的合并逻辑算出最终生效的代理。"""
    sess = cs.requests.Session()
    merged = sess.merge_environment_settings(
        'https://m.okooo.com/', cs._proxy_dict(proxies), None, None, None,
    )
    return {k: v for k, v in (merged.get('proxies') or {}).items() if v}


# --------------------------------------------------------------------------- #
# 1. 显式直连必须穿过 _proxy_dict（缺陷 2）
# --------------------------------------------------------------------------- #
def test_explicit_direct_survives_proxy_dict(env_proxy: str) -> None:
    """`{'http': None, 'https': None}` 必须原样保留，最终合并结果为空。

    旧实现把它过滤成 `{}`，requests 又并回环境代理 —— 于是「直连」实际走了代理。
    """
    assert _effective({'http': None, 'https': None}) == {}, (
        '_proxy_dict 把显式直连抹掉了，requests 会把它并回环境代理'
    )


def test_app_direct_marker_survives_proxy_dict(env_proxy: str) -> None:
    """`app_v84_hermes._DIRECT_PROXIES` 走同一条链也必须真直连。"""
    assert _effective(app._DIRECT_PROXIES) == {}, (
        '_DIRECT_PROXIES 经过 _proxy_dict 后仍然走代理，直连路由是空操作'
    )


def test_unspecified_proxies_still_merges_environment(env_proxy: str) -> None:
    """`None`（未指定）与「显式直连」是两种语义，不能合并。

    未指定时保持既有行为：读环境变量挑代理。这条防的是「修直连时顺手把默认也改了」。
    """
    effective = _effective(None)
    assert effective.get('https') == PROXY, effective


def test_proxy_dict_keeps_none_values() -> None:
    """`_proxy_dict` 对 dict 入参不再过滤：None 是语义，不是噪音。"""
    out = cs._proxy_dict({'http': None, 'https': None})
    assert out == {'http': None, 'https': None}, out
    mixed = cs._proxy_dict({'http': PROXY, 'https': None})
    assert mixed['https'] is None and mixed['http'] == PROXY, mixed


# --------------------------------------------------------------------------- #
# 2. OKOOO_ROUTE=direct 端到端（缺陷 3）
# --------------------------------------------------------------------------- #
def test_route_direct_is_a_real_direct_connection(env_proxy, monkeypatch) -> None:
    """`OKOOO_ROUTE=direct` + 环境已设代理 → 实际生效代理为空。

    这条是用户最自然的自救动作，修复前它是空操作。

    打桩 `_local_proxies`（假装 Clash 在跑）：不打桩时 direct 分支虽然在读它之前就返回，
    结论一样，但「结论一样」不该靠分支顺序偶然成立——显式打桩后这条在任何机器上测的都是
    「强制直连会压过可用的代理」，而不是「这台机器刚好没有代理」。
    """
    monkeypatch.setenv('OKOOO_ROUTE', 'direct')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes('https://v3.football.api-sports.io/fixtures')
    assert routes == [app._DIRECT_PROXIES], routes
    assert _effective(routes[0]) == {}, (
        'OKOOO_ROUTE=direct 仍然走代理：这个逃生开关是空操作'
    )


def test_route_direct_beats_okooo_routes_too(monkeypatch) -> None:
    """同一份强制语义在澳客专用入口也成立（两个入口不能有一个漏掉 direct）。"""
    monkeypatch.setenv('OKOOO_ROUTE', 'direct')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    assert app._okooo_http_routes() == [app._DIRECT_PROXIES]


def test_route_proxy_still_uses_the_proxy(env_proxy, monkeypatch) -> None:
    """`OKOOO_ROUTE=proxy` 仍然强制代理优先（不能因为改 auto 把它一起改掉）。"""
    monkeypatch.setenv('OKOOO_ROUTE', 'proxy')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes()
    assert routes[0] == {'http': PROXY, 'https': PROXY}, routes
    assert routes[-1] == app._DIRECT_PROXIES, routes


def test_forced_proxy_beats_the_domestic_direct_rule(monkeypatch) -> None:
    """强制代理优先时，国内站也不许被 auto 的直连规则抢走。"""
    monkeypatch.setenv('OKOOO_ROUTE', 'proxy')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes('https://m.okooo.com/match/history.php?MatchID=1')
    assert routes[0] == {'http': PROXY, 'https': PROXY}, routes
    assert app._okooo_http_routes()[0] == {'http': PROXY, 'https': PROXY}


# --------------------------------------------------------------------------- #
# 3. auto 的两种语义：澳客直连优先，通用入口（境外 API 也走它）代理优先
#
# **这一节每条都显式打桩 `_local_proxies`。** 不打桩时本机（以及任何没跑 Clash 的机器/
# CI）`_local_proxies()` 返回 None，两条路由退化成单条 `[直连]`，于是：
#   * 「第一条是直连」的断言恒真（假绿）；
#   * 「第一条是代理」的断言恒假（假红）。
# 两种都得不到结论。详见本文件末尾「为什么必须打桩」一节的说明。
# --------------------------------------------------------------------------- #
def _stub_proxy_available(monkeypatch) -> None:
    """让 `_local_proxies()` 看起来有一个活的 Clash，这是路由顺序断言的前提。"""
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})


def test_auto_prefers_direct_for_okooo_and_keeps_proxy_as_fallback(monkeypatch) -> None:
    """**澳客专用**顺序：直连第一、代理第二。

    澳客是国内站点，把请求交给本机代理之后出口就变成了代理规则集的产物，
    程序看不到也无法在日志里证明；直连用的才是这台机器自己的出口。
    """
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    _stub_proxy_available(monkeypatch)
    routes = app._okooo_http_routes()
    assert routes[0] == app._DIRECT_PROXIES, routes
    assert {'http': PROXY, 'https': PROXY} in routes, routes


def test_auto_without_proxy_has_a_single_direct_route(monkeypatch) -> None:
    """**没有可用代理**时两条入口都只给一条直连。

    这条与上面几条是互补的：上面测「有代理时的顺序」，这条测「没代理时不会凭空造出
    一条代理路由」。两者都必须打桩——不桩的话前者恒真、后者恒真，两个方向都测不出来。
    """
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: None)
    assert app._okooo_http_routes() == [app._DIRECT_PROXIES]
    assert app._http_routes('https://v3.football.api-sports.io/fixtures') == [app._DIRECT_PROXIES]
    assert app._http_routes('https://m.okooo.com/') == [app._DIRECT_PROXIES]


def test_no_proxy_environment_makes_proxy_first_assertions_vacuous(monkeypatch) -> None:
    """不打桩时（本机/CI 没有 Clash）「代理优先」断言**测不出任何东西**。

    这条把那个陷阱本身钉住：没有代理时两条路由只剩一条 `[直连]`，于是
    「第一条是代理」恒假、「第一条是直连」恒真。所以本节所有顺序断言都必须
    `_stub_proxy_available`。它不测实现，测的是「测试环境的前提」。
    """
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    # 故意不打桩：模拟没有代理在跑的机器。
    routes = app._http_routes('https://v3.football.api-sports.io/fixtures')
    if len(routes) == 1:
        # 这正是必须打桩的原因：单条时「代理优先」这条断言无法成立也无法被证伪。
        assert routes == [app._DIRECT_PROXIES], routes


def test_proxy_order_tests_declare_their_stub() -> None:
    """守卫：涉及路由**顺序**的测试必须在本函数体内打桩 `_local_proxies`。

    否则将来有人加一条 `auto + 国内站直连优先` 的断言，在没代理的 CI 上它会恒真。
    这里只做静态检查（扫函数体），不依赖运行环境。
    """
    import ast as _ast
    import pathlib as _pathlib
    src = (_pathlib.Path(__file__).resolve()).read_text(encoding='utf-8')
    tree = _ast.parse(src)
    lines = src.splitlines()
    offenders: list[str] = []
    for node in tree.body:
        if not isinstance(node, _ast.FunctionDef) or not node.name.startswith('test_'):
            continue
        body = '\n'.join(lines[node.lineno - 1: node.end_lineno])
        # 只检查真正调用路由函数取顺序的测试；纯源码 grep / 纯 proxy_dict 的不在此列。
        if "app._http_routes(" not in body and "app._okooo_http_routes(" not in body:
            continue
        if node.name in {
            'test_no_proxy_environment_makes_proxy_first_assertions_vacuous',
            'test_auto_without_proxy_has_a_single_direct_route',
        }:
            continue  # 这两条刻意不打桩（前者测陷阱本身，后者测无代理分支）
        if "_local_proxies" not in body and "_stub_proxy_available(" not in body:
            offenders.append(node.name)
    assert offenders == [], f'这些测试依赖运行环境里恰好有代理，必须打桩: {offenders}'


def test_generic_routes_stay_proxy_first_for_foreign_apis(monkeypatch) -> None:
    """通用入口对**境外 URL** 必须代理优先。

    `_http_routes()` 不只服务澳客：`safe_api_get` 也用它。境外 API 在境内网络下通常必须
    走代理；若这里也直连优先，每次都会先超时再回退。「澳客直连优先」是澳客的业务判断，
    不能外溢到境外目标。
    """
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes('https://v3.football.api-sports.io/fixtures')
    assert routes[0] == {'http': PROXY, 'https': PROXY}, routes
    assert routes[0] is not app._DIRECT_PROXIES, routes
    assert routes[-1] == app._DIRECT_PROXIES, routes


def test_generic_routes_without_target_are_proxy_first(monkeypatch) -> None:
    """不知道目标时按代理优先（保守一侧留给不确定的情况）。"""
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes(None)
    assert routes[0] == {'http': PROXY, 'https': PROXY}, routes


@pytest.mark.parametrize('url', [
    'https://odds.500.com/fenxi/shuju-1430592.shtml',
    'https://m.okooo.com/match/history.php?MatchID=1341275',
    'https://www.okooo.com/soccer/match/1341275/history/',
])
def test_generic_routes_are_direct_first_for_domestic_hosts(url, monkeypatch) -> None:
    """国内站（500 / 澳客）经通用入口时仍是直连优先。"""
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes(url)
    assert routes[0] == app._DIRECT_PROXIES, (url, routes)


def test_domestic_host_rule_does_not_match_lookalikes() -> None:
    """主机规则要按域名边界匹配，不能被 `okooo.com.evil.io` 这类串进去。"""
    assert app._is_domestic_target('https://m.okooo.com/x') is True
    assert app._is_domestic_target('https://www.500.com/x') is True
    assert app._is_domestic_target('https://okooo.com.evil.io/x') is False
    assert app._is_domestic_target('https://notokooo.com/x') is False
    assert app._is_domestic_target('https://v3.football.api-sports.io/fixtures') is False
    assert app._is_domestic_target(None) is False


@pytest.mark.parametrize('url', [
    'https://v3.football.api-sports.io/fixtures',
    'https://v3.football.api-sports.io/predictions',
    'https://v3.football.api-sports.io/odds',
])
def test_safe_api_get_tries_the_proxy_first_for_foreign_apis(url, monkeypatch) -> None:
    """`safe_api_get` 对境外 URL 的第一条路由必须是代理。

    注：这三个 api-sports.io 地址当前在 `safe_api_get` 里是**短路返回 None**
    （欧洲库路径，从不发请求），所以这里打桩 `requests.get` 并断言「即使发了，
    第一条也是代理」——用它钉住 `_http_routes(url)` 的选路，而不是钉住那条短路。
    """
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    routes = app._http_routes(url)
    assert routes[0] == {'http': PROXY, 'https': PROXY}, (url, routes)


def test_safe_api_get_passes_its_url_to_the_router() -> None:
    """`safe_api_get` 必须把自己的 URL 传进选路，否则国内/境外规则根本不生效。"""
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    start = source.index('def safe_api_get(')
    body = source[start: start + 2000]
    assert '_http_routes(url)' in body, body[:600]


def test_okooo_call_sites_use_the_okooo_order() -> None:
    """澳客那几处调用点必须走 `_okooo_http_routes()`，而不是通用入口。"""
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    # scrape_okooo_detail 的路由选择
    marker = 'def scrape_okooo_detail('
    body = source[source.index(marker):]
    body = body[: body.index('\n    # ===== 5.')]
    assert '_okooo_http_routes()' in body, body[:400]
    # 手机盈亏（m.okooo.com）与 500 的两处循环
    assert source.count('for _px in _okooo_http_routes():') == 2, (
        '澳客/500 的两处路由循环应改为 _okooo_http_routes()'
    )


def test_route_order_is_reported_in_the_log(monkeypatch, capsys) -> None:
    """日志必须如实说清这一轮按什么顺序用路由，别让用户靠猜。"""
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    app._OKOOO_ROUTE_LOGGED.clear()
    app._announce_route_once(app._okooo_http_routes(), tag='测试路由')
    out = capsys.readouterr().out
    assert '直连' in out and PROXY in out, out
    # 一轮只打一条。
    app._announce_route_once(app._okooo_http_routes(), tag='测试路由')
    assert capsys.readouterr().out == ''


# --------------------------------------------------------------------------- #
# 4. 澳客抓取链真的按序回退（缺陷 1）
# --------------------------------------------------------------------------- #
def _stub_detail(monkeypatch, seen, second_ok=True):
    """第一条路由拿不到数据、第二条能拿到；记录每次实际用的 proxies。"""
    calls: list[dict] = []

    def fake_cs(match_id, proxies=None, **kwargs):
        calls.append(proxies if isinstance(proxies, dict) else {'raw': proxies})
        seen.append(proxies)
        if len(calls) == 1:
            return {'available': False, 'reason': 'cs_origin_ip_blocked: 第一条不行',
                    'block_reason': 'cs_origin_ip_blocked: 第一条不行'}
        if not second_ok:
            return {'available': False, 'reason': 'still_failed', 'block_reason': 'still_failed'}
        return {'available': True, 'correct_score_channel': 'okooo_cs',
                'correct_score_books_avg': {'1:0': 6.0}, 'markets': {}}

    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', fake_cs)
    # 六盘口给齐水位：否则整场会触发一次「水位重抓」，把 cs_route_used 覆盖掉。
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets',
                        lambda *a, **k: {'available': True, 'asian_handicap': -0.75,
                                         'ou_line': 2.75, 'odds_1x2': {'home': 2.0},
                                         'jc_handicap': -1.0,
                                         'total_goals_odds': {str(i): 5.0 for i in range(8)},
                                         'htft_odds': {k_: 5.0 for k_ in
                                                       ('胜胜', '胜平', '胜负', '平胜', '平平',
                                                        '平负', '负胜', '负平', '负负')}})
    for name in ('_attach_mobile_exchange_pnl', '_attach_mobile_lineup',
                 '_attach_mobile_ah_ou_books', 'seed_mobile_ah_ou_books',
                 'apply_mobile_books_lines', '_stamp_more_play_scores'):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, lambda *a, **k: {})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())
    return calls


def test_detail_scrape_walks_to_the_second_route(env_proxy, monkeypatch, tmp_path) -> None:
    """第一条路由没拿到可用数据 → 必须真的走第二条，并把用的那条记下来。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    calls = _stub_detail(monkeypatch, [])

    result = app.scrape_okooo_detail('1341275')

    assert len(calls) >= 2, f'只用了 {len(calls)} 条路由，回退没有发生'
    assert calls[0] == app._DIRECT_PROXIES, calls[0]
    assert calls[1] == {'http': PROXY, 'https': PROXY}, calls[1]
    assert result.get('correct_score_available') is True, result.get('correct_score_channel')
    assert PROXY in str(result.get('cs_route_used') or ''), result.get('cs_route_used')
    assert str(result.get('cs_route_used')) != '直连', result.get('cs_route_used')


def test_detail_scrape_stops_at_the_first_usable_route(env_proxy, monkeypatch, tmp_path) -> None:
    """真实 200 不许重抓：第一条就成功时只能调用一次。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    calls: list = []

    def fake_cs(match_id, proxies=None, **kwargs):
        calls.append(proxies)
        return {'available': True, 'correct_score_channel': 'okooo_cs',
                'correct_score_books_avg': {'1:0': 6.0}, 'markets': {}}

    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', fake_cs)
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets',
                        lambda *a, **k: {'available': True, 'asian_handicap': -0.5,
                                         'ou_line': 2.5, 'odds_1x2': {'home': 2.0}})
    for name in ('_attach_mobile_exchange_pnl', '_attach_mobile_lineup',
                 '_attach_mobile_ah_ou_books', 'seed_mobile_ah_ou_books',
                 'apply_mobile_books_lines', '_stamp_more_play_scores'):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, lambda *a, **k: {})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    app.scrape_okooo_detail('1341275')
    assert len(calls) == 1, f'成功的路由被重抓了 {len(calls)} 次'
    assert calls[0] == app._DIRECT_PROXIES, calls[0]


def test_detail_scrape_reports_the_route_it_used(env_proxy, monkeypatch, tmp_path) -> None:
    """两条都失败时，日志仍要说清最后用的是哪条。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setenv('OKOOO_ROUTE', 'auto')
    monkeypatch.setattr(app, '_CLI_OKOOO_ROUTE', '')
    monkeypatch.setattr(app, '_local_proxies', lambda: {'http': PROXY, 'https': PROXY})
    _stub_detail(monkeypatch, [], second_ok=False)

    result = app.scrape_okooo_detail('1341275')
    assert result.get('correct_score_available') is False
    assert str(result.get('cs_route_used') or ''), result


# --------------------------------------------------------------------------- #
# 5. 实际生效路由的标签
# --------------------------------------------------------------------------- #
def test_route_label_distinguishes_direct_from_proxy(env_proxy: str) -> None:
    assert cs._route_label({'http': None, 'https': None}) == 'direct', cs._route_label(
        {'http': None, 'https': None}
    )
    assert PROXY in cs._route_label({'http': PROXY, 'https': PROXY})


def test_describe_route_is_readable(monkeypatch) -> None:
    assert app._describe_route(app._DIRECT_PROXIES) == '直连'
    assert 'proxy' not in app._describe_route({'http': PROXY, 'https': PROXY})
    assert PROXY in app._describe_route({'http': PROXY, 'https': PROXY})


# --------------------------------------------------------------------------- #
# 6. app_v84_hermes 里既有的显式 None 转换不能被改坏
# --------------------------------------------------------------------------- #
def test_existing_none_coercion_sites_are_untouched() -> None:
    """其他地方用 `None if _px is _DIRECT_PROXIES else _px` 是对的，别动它们。"""
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert source.count('None if _px is _DIRECT_PROXIES else _px') >= 2, (
        '既有两处显式 None 转换被改掉了'
    )


# --------------------------------------------------------------------------- #
# 7. cookie 预热以前是死代码：405 路径上一次都没被调用过
# --------------------------------------------------------------------------- #
def _blocked_body() -> str:
    return (
        '<html><head><title>405</title></head><body>'
        '<script>var block_message = "Sorry, your request has been blocked...";</script>'
        '</body></html>'
    )


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self.content = body.encode('utf-8')
        self.headers = dict(headers or {})
        self.encoding = 'utf-8'


class _Session:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []

    def get(self, url, headers=None, proxies=None, timeout=None):
        self.calls.append(url)
        return self.handler(url, headers or {})

    def close(self):
        pass


def test_warm_session_really_runs_on_a_405_path(monkeypatch, tmp_path) -> None:
    """返回 405 的路径上，`_warm_session` 必须真的被调用一次，并在同一个 session 上重发。

    旧实现把它藏在「`transport_error` 且 `retries >= 2`」里，而全部调用点都是
    `retries=1`（`range(0)` 空循环），405 又被 `origin_blocked` 排除——文档承诺的
    cookie 预热从未发生过。
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    warm_calls: list[dict] = []
    session = _Session(lambda url, headers: _Resp(405, _blocked_body(),
                                                  {'x-cache-status': 'MISS'}))

    def fake_warm(proxies=None, mobile=False, referer=None):
        warm_calls.append({'proxies': proxies, 'mobile': mobile, 'referer': referer})
        return session

    monkeypatch.setattr(cs, '_warm_session', fake_warm)

    info = cs._fetch_detail(
        'https://www.okooo.com/soccer/match/1347776/cs/', retries=1,
    )

    assert info.get('ok') is False
    assert warm_calls, 'cookie 预热一次都没发生（这就是死代码那条）'
    assert info.get('warm_session_used') is True, info
    # 预热之后确实在**同一个 session** 上重发了一次：那个 session 的 calls 里
    # 既有预热页，也有目标页。
    assert any('/cs/' in u for u in session.calls), session.calls
    assert info.get('cookie_retry'), info


def test_warm_session_success_short_circuits(monkeypatch, tmp_path) -> None:
    """预热后的重发拿到 200 时，必须直接返回那次结果（不是再走一轮无 cookie 请求）。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    state = {'warmed': False}

    def handler(url, headers):
        if '/cs/' not in url:
            # 预热页（Referer 候选里的同场次页/首页）：拿到 cookie。
            state['warmed'] = True
            return _Resp(200, '<html><title>ok</title></html>')
        if state['warmed']:
            return _Resp(200, '<html><title>ok</title><table id="datatable"></table></html>')
        return _Resp(405, _blocked_body())

    session = _Session(handler)

    def fake_warm(proxies=None, mobile=False, referer=None):
        # 复刻真实 `_warm_session`：先在同一个 session 上访问预热页拿 cookie。
        session.get(referer or 'https://m.okooo.com/jczq/',
                    headers={'User-Agent': 'warm'}, proxies=proxies, timeout=15)
        return session

    monkeypatch.setattr(cs, '_warm_session', fake_warm)

    info = cs._fetch_detail('https://www.okooo.com/soccer/match/1347776/cs/', retries=1)
    assert info['ok'] is True, info
    assert info.get('warm_session_used') is True, info


def test_warm_session_is_not_called_when_the_first_try_succeeds(monkeypatch, tmp_path) -> None:
    """第一次就 200 时不许预热：那会白白多一次往返。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_warm_session', lambda *a, **k: pytest.fail(
        'first success must not trigger a cookie warm-up'
    ))
    session = _Session(lambda url, headers: _Resp(
        200, '<html><title>ok</title><table id="datatable"></table></html>'))
    info = cs._fetch_detail('https://www.okooo.com/soccer/match/1347776/cs/',
                            session=session, retries=1)
    assert info['ok'] is True
    assert len(session.calls) == 1, session.calls
