"""矩阵接入 app 的三条硬约束。

这一段最容易出的错不是解析，是**接线**：段没被调用、被调用但吞掉异常返回空、
或者报告里出现了但数字不是矩阵给的。所以这里断言的是调用链与失败面。
"""
from __future__ import annotations

import importlib
import inspect
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / 'tests' / 'fixtures'
mdm = importlib.import_module('match_data_matrix')
okooo = importlib.import_module('okooo_mobile_cs')


@pytest.fixture(scope='module')
def app() -> Any:
    return importlib.import_module('app_v84_hermes')


@pytest.fixture(scope='module')
def rec() -> dict[str, Any]:
    """一条形状与阶段1落盘一致的记录：order/fid/MatchID + 六页分析结果。"""
    tabs = importlib.import_module('okooo_analysis_tabs')
    parsed = tabs.scrape_okooo_analysis_tabs('1338958', html_by_tab={
        'ah': (FIXTURES / 'okooo_mobile_ah.html').read_bytes(),
        'ou': (FIXTURES / 'okooo_mobile_ou.html').read_bytes(),
        'eu': (FIXTURES / 'okooo_mobile_1x2.html').read_bytes(),
        'rq': (FIXTURES / 'okooo_mobile_1x2.html').read_bytes(),
        'pnl': (FIXTURES / 'okooo_mobile_exchanges.html').read_bytes(),
    })
    board_html = okooo.decode_okooo_html((FIXTURES / 'live500_board_20261127.html').read_bytes())
    import live500_board as board_mod
    board = board_mod.parse_board(board_html)
    board['report'] = {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129069}
    match = next(m for m in board['matches'] if m['order'] == '5002')
    detail_html = okooo.decode_okooo_html((FIXTURES / 'live500_detail_1430592_20261127.html').read_bytes())
    detail = board_mod.parse_detail(detail_html, fid='1430592')
    detail['report'] = {'url': 'https://live.500.com/detail.php?fid=1430592&r=1',
                        'status': 200, 'bytes': 17362}
    return {
        'id': '5002',
        'okooo_match_id': '1338958',
        'fixture_id_500': '1430592',
        'jc_score_orderid': '5002',
        'okooo_analysis_tabs': parsed,
        'live500_match': match,
        'live500_detail': detail,
    }


def test_wired_builder_returns_the_matrix_section(app: Any, rec: dict[str, Any]) -> None:
    lines = app.build_match_data_matrix_tg_lines(rec, {})
    text = '\n'.join(lines)
    assert '两源合并数据矩阵' in text
    assert '5002' in text
    assert '3.25' in text, '澳客大小球盘口线必须出现在矩阵里'
    assert '平手/半球' in text, '500 的让球标签必须出现在矩阵里'


def test_collector_writes_both_sources_onto_the_record(app: Any, rec: dict[str, Any]) -> None:
    """阶段1 的采集器必须把两源结构写进记录，否则发报阶段永远是「未形成」。"""
    import live500_board as board_mod
    board_html = okooo.decode_okooo_html((FIXTURES / 'live500_board_20261127.html').read_bytes())
    board = board_mod.parse_board(board_html)
    board['report'] = {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129069}
    detail = board_mod.parse_detail(
        okooo.decode_okooo_html((FIXTURES / 'live500_detail_1430592_20261127.html').read_bytes()),
        fid='1430592',
    )
    target: dict[str, Any] = {
        'id': 'x', 'jc_score_orderid': '5002', 'okooo_match_id': '1338958',
        'fixture_id_500': '1430592', 'okooo_analysis_tabs': rec['okooo_analysis_tabs'],
    }
    facts = app.collect_match_matrix_facts(
        target, board=board, details={'1430592': detail},
    )
    assert 'live500_match' in facts['written']
    assert 'live500_detail' in facts['written']
    assert target['live500_match']['fid'] == '1430592'
    assert facts['order'] == '5002'
    # 采集之后，发报段必须真的带上两侧的数
    text = '\n'.join(app.build_match_data_matrix_tg_lines(target, {}))
    assert '3.25' in text and '平手/半球' in text


def test_collector_does_not_refetch_when_the_record_already_has_tabs(app: Any) -> None:
    existing = {'available': True, 'match_id': '1338958', 'tabs': {'ah': {}}, 'missing': []}
    target = {'okooo_match_id': '1338958', 'okooo_analysis_tabs': existing}
    facts = app.collect_match_matrix_facts(target)
    assert 'okooo_analysis_tabs' not in facts['written']
    assert target['okooo_analysis_tabs'] is existing


def test_collector_survives_a_blocked_tab_fetch(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """澳客被拦是常态（本机全站 403）：记录里要留下原因，而不是抛异常。"""
    import okooo_analysis_tabs as tabs_mod

    def boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError('HTTP 403')

    monkeypatch.setattr(tabs_mod, 'scrape_okooo_analysis_tabs', boom)
    target = {'okooo_match_id': '1338958'}
    facts = app.collect_match_matrix_facts(target)
    assert 'okooo_analysis_tabs' in facts['written']
    pack = target['okooo_analysis_tabs']
    assert pack['available'] is False
    assert pack['missing'][0]['reason_code'] == 'tab_fetch_failed'


def test_emit_calls_the_matrix_section(app: Any) -> None:
    """守的是**调用点**，不是 helper 本身。

    端到端跑一遍 emit 会被 31 全表 / 六盘口 / 质量门三道硬闸拦住（那是它们
    该做的事，不能为了测试放松），所以这里做结构断言：`emit_pipeline_deepseek_tg_report`
    的函数体里必须真的调用了 `append_match_matrix_section`，且结果写回
    `_final_sections`（唯一喂给 tg_report 的列表）。
    """
    source = inspect.getsource(app.emit_pipeline_deepseek_tg_report)
    assert 'append_match_matrix_section(' in source, '矩阵段没有接进 emit'
    assert '_final_sections = append_match_matrix_section(' in source, (
        '矩阵段的返回值没有写回 _final_sections，段会被丢弃'
    )
    # tg_report 由 _final_sections 拼出：这条链路不能变
    assert 'tg_report = "\\n\\n".join(_final_sections)' in source


def test_matrix_section_lands_in_the_assembled_sections(app: Any, rec: dict[str, Any]) -> None:
    """段必须真的被追加进 `_final_sections`（emit 里唯一喂给 tg_report 的列表）。

    这里直接测这个追加动作，而不是跑整条 emit：整条链上有 31 全表、六盘口、
    质量门三道硬闸，任何一道拒发都会让断言变成「闸门的行为」。
    """
    sections = ['📌 标题行', '🧠【DeepSeek 数据分析研判报告】']
    out = app.append_match_matrix_section(sections, rec, {})
    assert len(out) == 3
    assert out[0] == '📌 标题行' and out[1].startswith('🧠')
    matrix_block = out[2]
    assert matrix_block.startswith('\x0c'), '矩阵段必须自成一条消息'
    assert '两源合并数据矩阵' in matrix_block
    assert '5002' in matrix_block
    assert '3.25' in matrix_block
    assert '平手/半球' in matrix_block


def test_matrix_append_survives_a_broken_builder(
    app: Any, rec: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **k: Any) -> list[str]:
        raise RuntimeError('builder exploded')

    monkeypatch.setattr(app, 'build_match_data_matrix_tg_lines', boom)
    out = app.append_match_matrix_section([], rec, {})
    assert len(out) == 1
    assert '未形成' in out[0] and 'builder exploded' in out[0]


def test_empty_record_still_produces_a_readable_section(app: Any) -> None:
    """没有任何数据时不能返回 []（段会整段消失），必须给一行说明。"""
    lines = app.build_match_data_matrix_tg_lines({}, {})
    assert lines
    assert '未形成' in '\n'.join(lines) or '矩阵' in '\n'.join(lines)


def test_builder_never_raises_when_the_module_is_broken(
    app: Any, rec: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """矩阵是附加段：它坏了只能降级成一行，不能拖垮整份研判报告。"""
    def boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError('matrix exploded')

    monkeypatch.setattr(mdm, 'build_match_matrix', boom)
    lines = app.build_match_data_matrix_tg_lines(rec, {})
    assert len(lines) == 1
    assert '未形成' in lines[0]
    assert 'matrix exploded' in lines[0]


def test_builder_reads_order_from_the_record_not_from_names(app: Any, rec: dict[str, Any]) -> None:
    """order 必须来自已落盘的 `jc_score_orderid`，不能靠队名或开赛时间去猜。"""
    lines = app.build_match_data_matrix_tg_lines(rec, {})
    text = '\n'.join(lines)
    assert '5002' in text
    # 故意把队名清空，join 仍必须成立（说明没用队名匹配）
    broken = dict(rec)
    broken['live500_match'] = {**rec['live500_match'], 'home': '', 'away': ''}
    still = '\n'.join(app.build_match_data_matrix_tg_lines(broken, {}))
    assert '5002' in still
    assert '3.25' in still


# --------------------------------------------------------------------------- #
# 阶段1 调用点：500 侧必须**通过真实入口**亮起来
# --------------------------------------------------------------------------- #
def test_stage1_call_site_lights_up_500_without_a_seeded_match(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不预置 `live500_match`，只给 MatchID，跑真实的阶段1采集入口。

    这是本轮最容易造假的地方：如果测试自己把 `live500_match` 塞进记录，
    它证明的只是矩阵会读一个已有字段，而不是「阶段1 真的把 500 侧接上」。
    所以这里：记录里**没有** `live500_match`、没有 `jc_score_orderid`，
    盘面通过 `_matrix_500_board()` 走真实的 `scrape_500_board`（只打桩 HTTP），
    然后断言 500 侧的比分/让球标签确实出现在矩阵段里。
    """
    import live500_board as board_mod

    raw = (FIXTURES / 'live500_board_20261127.html').read_bytes()

    def fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        assert url == 'https://live.500.com/'
        return {'ok': True, 'url': url, 'status': 200, 'bytes': len(raw),
                'html': board_mod.decode_500_html(raw), 'title': 'board',
                'origin_blocked': False, 'error': ''}

    monkeypatch.setattr(board_mod, '_fetch_detail', fake_fetch)
    app.reset_matrix_500_board_cache()

    # 只给 500 的场次号；order 由盘面自己解析出来。
    rec: dict[str, Any] = {'id': 'x', 'fixture_id_500': '1430592'}
    facts = app.collect_match_matrix_facts(rec, board=app._matrix_500_board())

    assert 'live500_match' in facts['written']
    assert rec['live500_match']['fid'] == '1430592'
    # 记录里原本没有 order，必须由盘面补齐——这就是 join 键被真的解析过。
    assert rec['jc_score_orderid'] == '5002'

    text = '\n'.join(app.build_match_data_matrix_tg_lines(rec, {}))
    assert '5002' in text
    assert '平手/半球' in text, '500 的让球标签没有进矩阵：阶段1 没接上'
    assert 'HTTP 200/129069B' in text, '盘面出处没有进矩阵'


def test_round_board_is_fetched_once_not_once_per_match(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """盘面是轮级数据：38 场不能变成 38 次同样的请求。"""
    import live500_board as board_mod

    calls: list[str] = []

    def counting_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(url)
        return {'ok': False, 'url': url, 'status': 0, 'bytes': 0, 'html': '',
                'error': 'offline', 'origin_blocked': False}

    monkeypatch.setattr(board_mod, '_fetch_detail', counting_fetch)
    app.reset_matrix_500_board_cache()
    for _ in range(5):
        app._matrix_500_board()
    assert len(calls) == 1, calls


def test_round_board_cache_survives_a_dead_host(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """500 不可达时也要缓存失败，否则每场都去撞同一个死端点。"""
    import live500_board as board_mod

    calls: list[str] = []

    def boom(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(url)
        raise RuntimeError('offline')

    monkeypatch.setattr(board_mod, '_fetch_detail', boom)
    app.reset_matrix_500_board_cache()
    first = app._matrix_500_board()
    second = app._matrix_500_board()
    assert first['available'] is False
    assert second is first
    assert len(calls) == 1


def test_stage1_collector_never_raises_when_the_board_is_garbage(
    app: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """盘面结构坏掉时，阶段1 只能少一源，不能让整场分析挂掉。"""
    rec: dict[str, Any] = {'id': 'x', 'fixture_id_500': '1430592'}
    facts = app.collect_match_matrix_facts(rec, board={'matches': 'not-a-list'})
    assert 'live500_match' not in facts['written']
    assert rec.get('match_matrix_facts', {}).get('order') == ''


def test_stage1_loop_body_calls_the_collector(app: Any) -> None:
    """守 `analyze_match` 的函数体，而不是 helper。

    `_matrix_500_board()` + `collect_match_matrix_facts(` 必须出现在阶段1
    的单场函数里，并且与 `if fixture_id_500:` **同级缩进**——否则「盘面上有
    order、但本场没有 fixture_id_500」的场次永远进不了矩阵。

    用缩进而不是「第一次出现 `if fixture_id_500:`」，因为该函数里有三个
    同名的守卫（记录初始化、500 详情、以及后面的另一处），按首次出现比较
    会把合法的写法判成错误。
    """
    source = inspect.getsource(app.analyze_match)
    assert 'collect_match_matrix_facts(' in source, '阶段1 没有调用采集器'
    assert '_matrix_500_board()' in source, '阶段1 没有取轮级盘面'

    lines = source.splitlines()
    collect_at = next(
        i for i, line in enumerate(lines) if 'collect_match_matrix_facts(' in line
    )
    # 真正把 500 详情挡在外面的那道守卫在函数体缩进 8。更深的
    # `if fixture_id_500:` 是记录初始化与 inner try 内的，不构成门槛。
    outer_guards = [
        i for i, line in enumerate(lines)
        if line.strip() == 'if fixture_id_500:' and len(line) - len(line.lstrip()) == 8
    ]
    assert outer_guards, '找不到阶段1 的 fixture_id_500 守卫，测试前提已变'
    assert collect_at < outer_guards[0], (
        f'采集器在第 {collect_at} 行，晚于第 {outer_guards[0]} 行的 fixture_id_500 守卫：'
        '有 order 但无 fid 的场次会被跳过'
    )


def test_reason_text_covers_every_reason_code() -> None:
    """缺口现在要印进正文，所以每个码都必须有一句中文。

    两者键集必须完全一致：多一个码没有文案 → 用户看到「未知原因码」；
    多一条文案没有码 → 死文案会被后人误用。
    """
    assert set(mdm.REASON_TEXT) == set(mdm.REASON_CODES), (
        set(mdm.REASON_TEXT) ^ set(mdm.REASON_CODES)
    )
    for code in mdm.REASON_CODES:
        assert mdm.REASON_TEXT[code].strip(), code
        assert mdm.reason_text(code) == mdm.REASON_TEXT[code]
    assert '未知原因码' in mdm.reason_text('no_such_code')


def test_gap_banner_prints_chinese_not_codes_only(rec: dict[str, Any]) -> None:
    """缺口横幅必须是人话：码可以有，但不能只有码。"""
    row = mdm.build_match_matrix(order='5002', okooo_match_id='1338958',
                                 five_hundred=rec['live500_match'],
                                 five_hundred_detail=rec['live500_detail'])
    matrix = {'rows': [row]}
    banner = mdm.format_gap_banner(matrix)
    assert banner, '未开赛场次必然有缺口，横幅不能为空'
    text = '\n'.join(banner)
    assert '不影响出报' in text, '横幅必须说清缺了也照发'
    assert '未开赛' in text, 'not_started 的中文解释没有出现'
    assert 'not_started' in text, '码本身也要留着，便于对日志'


def test_gap_banner_is_empty_when_nothing_is_missing() -> None:
    assert mdm.format_gap_banner({'rows': [{'gaps': []}]}) == []
    assert mdm.format_gap_banner({}) == []
