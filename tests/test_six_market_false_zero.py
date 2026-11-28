"""六盘口「0 条」是假零：空列表被渲染成「没有矛盾」。

用户实机日志里这一行：

    📱 [澳客手机·六盘口矛盾] 0条 （含缺项 0）

同一秒上面还有一行 `⚠️ [澳客手机CS] 不可用 reason=cs_origin_ip_blocked…`——六盘口
整侧根本没抓到。`detect_six_market_contradictions`（okooo_mobile_cs.py）在整侧不可用
时返回 `[]`，于是「一条都没查到」被渲染成「0 条矛盾」，读起来像「没问题」。

这条输出**无论六块齐不齐都恒为 0**，所以它既不能证明有矛盾、也不能证明没矛盾。
「含缺项 0」还是个死计数器：它数的是 `_cons` 里 `kind == 'missing_board'` 的项，而
`detect_six_market_contradictions` 产出的键是 `type`、且只会产 `missing_ou_line`，
全仓库没有任何地方产出 `kind='missing_board'`。

修复后：
  * 不可用（`six_market_fetch_blocked` 为真）→ 明说「未抓到六盘口，未做矛盾检测」+ reason，
    绝不打印 `0条`、也不打印 `含缺项 0`；
  * 可用且真没矛盾 → 仍然打印 `0条`，但「含缺项 N」来自 `six_market_gaps`（算出来的）；
  * 部分可用（缺 2 块且检测出 1 条 missing_ou_line）→ 两条信息同时在场。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
cs = importlib.import_module('okooo_mobile_cs')


def _line(record, contradictions=None) -> str:
    status = app._six_market_contradiction_status(record, contradictions)
    return app._six_market_contradiction_line(status)


def _all_six() -> dict:
    return {
        'available': True,
        'channel': 'mobile',
        'asian_handicap': -0.75,
        'ou_line': 2.75,
        'odds_1x2': {'home': 1.91, 'draw': 3.86, 'away': 2.8},
        'jc_handicap': -1.0,
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'htft_odds': {k: 5.0 for k in
                      ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
        'missing': [],
    }


# --------------------------------------------------------------------------- #
# (a) 不可用：不能说「0 条」
# --------------------------------------------------------------------------- #
def test_unavailable_pack_never_prints_a_zero_count() -> None:
    """PAR-34 的实测形状：市均/六盘口整侧失败，真实缺口 6 块。"""
    pack = {'available': False,
            'block_reason': 'external_okooo_six_market_fetch_failed',
            'missing': ['asian_handicap', 'ou_line', 'odds_1x2']}
    rec = {'six_market_mobile': pack}

    # 前提：闸门确实知道它不可用（这条判据是既有的，不另写一套）。
    assert cs.six_market_fetch_blocked(rec) is True

    line = _line(rec, [])

    assert '0条' not in line, line
    assert '含缺项 0' not in line, line
    assert '未做矛盾检测' in line, line
    assert 'external_okooo_six_market_fetch_failed' in line, line
    # 真实缺口是 6 块，必须能算出来（来自 six_market_gaps，不是自己数）。
    status = app._six_market_contradiction_status(rec, [])
    assert status['gap_count'] == 6, status
    assert status['gaps'] == ['亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场'], status


def test_unavailable_line_goes_through_the_real_print_helper(capsys) -> None:
    """走真正的打印函数：控制台里不许出现 `0条`，且要点名缺哪几块。"""
    pack = {'available': False,
            'block_reason': 'external_okooo_six_market_fetch_failed',
            'missing': ['asian_handicap', 'ou_line', 'odds_1x2']}
    rec = {'six_market_mobile': pack}
    status = app._six_market_contradiction_status(rec, [])
    app._print_six_market_contradiction_line(status)
    out = capsys.readouterr().out

    assert '0条' not in out, out
    assert '含缺项 0' not in out, out
    assert '未做矛盾检测' in out, out
    assert 'external_okooo_six_market_fetch_failed' in out, out
    assert '六盘口真实缺口（6 块）' in out, out
    assert '亚盘让球' in out and '半全场' in out, out


def test_status_records_unavailable_for_downstream() -> None:
    """落盘/返回值必须带不可用标记，别让下游把空列表当「无矛盾」。"""
    pack = {'available': False,
            'block_reason': 'external_okooo_six_market_fetch_failed',
            'missing': ['asian_handicap', 'ou_line', 'odds_1x2']}
    rec = {'six_market_mobile': pack}
    status = app._six_market_contradiction_status(rec, [])
    assert status['detected'] is False, status
    assert status['blocked'] is True, status
    assert status['block_reason'] == 'external_okooo_six_market_fetch_failed', status
    assert status['count'] == 0 and status['contradictions'] == [], status


def test_saved_file_distinguishes_not_detected_from_no_contradiction(tmp_path, monkeypatch) -> None:
    """文件里也要能分清「没检测」与「检测过没矛盾」。

    旧写法只落一个空列表，两种情形长得一模一样。
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_STORE', tmp_path / 'six_market_contradictions.json')

    unavailable = {'six_market_mobile': {
        'available': False, 'block_reason': 'external_okooo_six_market_fetch_failed',
        'missing': ['asian_handicap', 'ou_line', 'odds_1x2']}}
    app._save_contradiction_state('mid-1', unavailable, [])
    blocked_state = cs.load_six_market_contradiction_status('mid-1')
    assert blocked_state['detected'] is False, blocked_state
    assert blocked_state['block_reason'] == 'external_okooo_six_market_fetch_failed', blocked_state
    assert blocked_state['gap_count'] == 6, blocked_state

    healthy = {'six_market_mobile': _all_six()}
    app._save_contradiction_state('mid-2', healthy, [])
    ok_state = cs.load_six_market_contradiction_status('mid-2')
    assert ok_state['detected'] is True, ok_state
    assert ok_state['count'] == 0 and ok_state['gaps'] == [], ok_state


# --------------------------------------------------------------------------- #
# (a2) 落盘格式换了，**所有**读取器都必须跟着认新格式
# --------------------------------------------------------------------------- #
def test_load_contradictions_reads_back_what_save_wrote(tmp_path, monkeypatch) -> None:
    """round-trip：`_save_contradiction_state` 写的矛盾，旧读取器必须读得回来。

    历史缺陷：写入侧改成 dict 之后，`load_six_market_contradictions` 仍然只认裸列表，
    于是「历史缺口」从第二轮起静默变成空，且没有任何报错。
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_STORE', tmp_path / 'six_market_contradictions.json')

    pack = _all_six()
    pack['ou_line'] = None
    pack['predicted_ou'] = 2.5
    items = [{'type': 'missing_ou_line', 'severity': 'low'}]
    app._save_contradiction_state('M1', {'six_market_mobile': pack}, items)

    # 新读取器看得到，旧读取器也必须看得到同一条。
    status = cs.load_six_market_contradiction_status('M1')
    assert status['count'] == 1, status
    legacy = cs.load_six_market_contradictions('M1')
    assert legacy == items, legacy
    # 契约是「返回矛盾项列表」，不能把整个状态 dict 返回出去。
    assert isinstance(legacy, list), legacy
    assert not any(isinstance(x, dict) and 'detected' in x for x in legacy), legacy


def test_load_contradictions_still_reads_the_legacy_list_format(tmp_path, monkeypatch) -> None:
    """旧格式（裸列表）仍然要读得到，不能为了兼容新格式把老文件读废。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_STORE', tmp_path / 'six_market_contradictions.json')
    import json as _json
    legacy_items = [{'type': 'missing_ou_line', 'severity': 'low'}]
    # 按本场真实的落盘路径写（`_contradiction_store(match_id)` 会带上 match_id 后缀）。
    store = cs._contradiction_store('M1')
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(_json.dumps(legacy_items), encoding='utf-8')

    assert cs.load_six_market_contradictions('M1') == legacy_items
    status = cs.load_six_market_contradiction_status('M1')
    assert status['source'] == 'legacy_list', status
    assert status['count'] == 1, status


def test_prior_contradictions_survive_a_second_round(
    monkeypatch, tmp_path,
) -> None:
    """连跑两轮：第二轮的 `prior_six_market_contradictions` 必须仍是第一轮那 1 条。

    这是这个回归最直接的形状——先读旧值、再写新值，写入格式一变就自己把自己读空。
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_STORE', tmp_path / 'six_market_contradictions.json')

    pack_first = _all_six()
    pack_first['ou_line'] = None
    pack_first['predicted_ou'] = 2.5
    first_rec = {'six_market_mobile': pack_first}
    cons_first = cs.detect_six_market_contradictions(pack_first)
    assert len(cons_first) == 1, cons_first

    # 第一轮：没有历史。
    assert cs.load_six_market_contradictions('M9') == []
    app._save_contradiction_state('M9', first_rec, cons_first)

    # 第二轮：同一场再跑一次，历史必须是第一轮那一条。
    second_rec = {'six_market_mobile': _all_six()}
    prior = cs.load_six_market_contradictions('M9')
    assert prior == cons_first, f'第二轮读到的历史缺口是 {prior!r}，应为 {cons_first!r}'
    cons_second = cs.detect_six_market_contradictions(second_rec['six_market_mobile'])
    assert cons_second == [], '第二轮六块齐，本场不该有新矛盾'
    app._save_contradiction_state('M9', second_rec, cons_second)

    # 第三轮：文件已被第二轮覆盖成「无矛盾」，历史确实清了。
    assert cs.load_six_market_contradictions('M9') == []


def test_prior_contradictions_through_the_real_scrape_path(
    monkeypatch, tmp_path,
) -> None:
    """走真实 `scrape_okooo_detail` 连跑两轮，钉住 prior_ 那段赋值。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_STORE', tmp_path / 'six_market_contradictions.json')

    def _drive(pack):
        monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {
            'available': False, 'reason': 'x', 'block_reason': 'x',
        })
        monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets',
                            lambda *a, **k: dict(pack))
        for name in ('_attach_mobile_exchange_pnl', '_attach_mobile_lineup',
                     '_attach_mobile_ah_ou_books', 'seed_mobile_ah_ou_books',
                     'apply_mobile_books_lines', '_stamp_more_play_scores'):
            if hasattr(app, name):
                monkeypatch.setattr(app, name, lambda *a, **k: {})
        monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
        monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())
        return app.scrape_okooo_detail('M77')

    first = _all_six()
    first['ou_line'] = None
    first['predicted_ou'] = 2.5
    _drive(first)

    second = _all_six()
    out = _drive(second)
    prior = out.get('prior_six_market_contradictions')
    assert isinstance(prior, list) and len(prior) == 1, (
        f'第二轮 prior_six_market_contradictions = {prior!r}，历史缺口被读空了'
    )
    assert prior[0].get('type') == 'missing_ou_line', prior


# --------------------------------------------------------------------------- #
# (b) 可用且真的没矛盾：仍然打印 0条，但 0 是算出来的
# --------------------------------------------------------------------------- #
def test_available_without_contradictions_still_prints_zero() -> None:
    rec = {'six_market_mobile': _all_six()}
    assert cs.six_market_fetch_blocked(rec) is False
    line = _line(rec, [])
    assert '0条' in line, line
    assert '未做矛盾检测' not in line, line
    status = app._six_market_contradiction_status(rec, [])
    assert status['detected'] is True, status
    assert status['gap_count'] == 0, status


def test_zero_gap_count_is_computed_not_an_identity() -> None:
    """同一个 0，六块齐时是 0，缺两块时是 2：它必须跟着数据走。"""
    full = app._six_market_contradiction_status({'six_market_mobile': _all_six()}, [])
    assert full['gap_count'] == 0

    partial_pack = _all_six()
    partial_pack['asian_handicap'] = None
    partial_pack['ou_line'] = None
    partial_pack['missing'] = ['asian_handicap', 'ou_line']
    partial = app._six_market_contradiction_status({'six_market_mobile': partial_pack}, [])
    assert partial['gap_count'] == 2, partial
    assert '亚盘让球' in partial['gaps'] and '大小球' in partial['gaps'], partial


# --------------------------------------------------------------------------- #
# (c) 部分可用：矛盾数 + 真实缺口数同时在场
# --------------------------------------------------------------------------- #
def test_partial_pack_reports_both_contradictions_and_real_gaps() -> None:
    """缺 2 块、且确实检测出 1 条 missing_ou_line → 1 条 + 缺项 2 块。"""
    pack = _all_six()
    pack['asian_handicap'] = None
    pack['ou_line'] = None
    pack['missing'] = ['asian_handicap', 'ou_line']
    # `detect_six_market_contradictions` 目前唯一的判据是「有 predicted_ou 但没 ou_line」。
    pack['predicted_ou'] = 2.5
    rec = {'six_market_mobile': pack}

    contradictions = cs.detect_six_market_contradictions(pack)
    assert contradictions, '这个包应当能检测出 missing_ou_line'

    line = _line(rec, contradictions)
    assert '1条' in line, line
    assert '含缺项 2' in line, line
    assert '0条' not in line, line


def test_partial_pack_without_detectable_contradiction_still_names_gaps() -> None:
    """缺 2 块但检测不到矛盾：0 条 + 缺项 2，不能说成「没问题」。"""
    pack = _all_six()
    pack['asian_handicap'] = None
    pack['ou_line'] = None
    pack.pop('predicted_ou', None)
    rec = {'six_market_mobile': pack}
    line = _line(rec, [])
    assert '0条' in line, line
    assert '含缺项 2' in line, line


# --------------------------------------------------------------------------- #
# (d) 死计数器：`kind == 'missing_board'` 结构上永远为 0
# --------------------------------------------------------------------------- #
def test_dead_kind_counter_is_gone_from_the_console_line() -> None:
    """源码里不许再出现用 `kind` 数缺项的那句。"""
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert "x.get('kind')=='missing_board'" not in source
    assert "x.get('kind') == 'missing_board'" not in source
    # 也不许再有写死的 `{len(_cons)}条`。
    assert '📱 [澳客手机·六盘口矛盾] {len(_cons)}条' not in source


def test_no_missing_board_kind_is_ever_produced() -> None:
    """`okooo_mobile_cs` 从没产出过 `kind='missing_board'`：这正是死计数器的证据。"""
    source = (ROOT / 'okooo_mobile_cs.py').read_text(encoding='utf-8')
    assert "kind': 'missing_board'" not in source
    assert 'kind="missing_board"' not in source
    # 它唯一会产出的类型键是 `type`，值 missing_ou_line。
    pack = _all_six()
    pack['ou_line'] = None
    pack['predicted_ou'] = 2.5
    items = cs.detect_six_market_contradictions(pack)
    assert items and all('type' in it for it in items), items
    assert not any(it.get('kind') == 'missing_board' for it in items), items


# --------------------------------------------------------------------------- #
# (e) 端到端：`scrape_okooo_detail` 真正打出来的那一行
# --------------------------------------------------------------------------- #
def _drive_detail(monkeypatch, six_pack, tmp_path):
    """驱动真实的 `scrape_okooo_detail`，六盘口包由参数给定（不再联网）。"""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {
        'available': False, 'reason': 'cs_origin_ip_blocked: 测试注入',
        'block_reason': 'cs_origin_ip_blocked: 测试注入',
        'jc_score_odds': {}, 'jc_score_fetch_reason': 'available n=0 stage=fetch_failed',
    })
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets',
                        lambda *a, **k: dict(six_pack))
    for name, repl in (
        ('_attach_mobile_exchange_pnl', lambda *a, **k: {}),
        ('_attach_mobile_lineup', lambda *a, **k: {}),
        ('_attach_mobile_ah_ou_books', lambda *a, **k: {}),
        ('seed_mobile_ah_ou_books', lambda *a, **k: {}),
        ('apply_mobile_books_lines', lambda *a, **k: {}),
        ('_stamp_more_play_scores', lambda *a, **k: {}),
    ):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, repl)
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())
    return app.scrape_okooo_detail('1341275')


def test_end_to_end_unavailable_never_prints_zero(monkeypatch, capsys, tmp_path) -> None:
    """用户现场那一刻的真实形状：六盘口整侧没抓到。

    旧代码在这里打 `0条（含缺项 0）`——无论六块齐不齐都恒为 0，所以它
    既不能证明有矛盾、也不能证明没矛盾。修复后必须明说「未做矛盾检测」+ reason。
    """
    pack = {'available': False,
            'block_reason': 'external_okooo_six_market_fetch_failed',
            'missing': ['asian_handicap', 'ou_line', 'odds_1x2']}
    _drive_detail(monkeypatch, pack, tmp_path)
    out = capsys.readouterr().out

    assert '0条' not in out, out
    assert '含缺项 0' not in out, out
    assert '未做矛盾检测' in out, out
    assert 'external_okooo_six_market_fetch_failed' in out, out
    assert '六盘口真实缺口（6 块）' in out, out


def test_end_to_end_unavailable_marks_the_saved_record(monkeypatch, tmp_path) -> None:
    """落盘记录也要带不可用标记，别让下游把空列表当「无矛盾」。"""
    pack = {'available': False,
            'block_reason': 'external_okooo_six_market_fetch_failed'}
    _drive_detail(monkeypatch, pack, tmp_path)
    saved = cs.load_six_market_contradiction_status('1341275')
    assert saved['source'] == 'status', saved
    assert saved['detected'] is False, saved
    assert saved['block_reason'] == 'external_okooo_six_market_fetch_failed', saved


def test_end_to_end_partial_pack_prints_one_contradiction_and_two_gaps(
    monkeypatch, capsys, tmp_path,
) -> None:
    """缺 2 块 + 检测出 1 条：两条信息都要在，且「含缺项」必须是 2。"""
    pack = _all_six()
    pack['asian_handicap'] = None
    pack['ou_line'] = None
    pack['predicted_ou'] = 2.5
    pack['missing'] = ['asian_handicap', 'ou_line']
    _drive_detail(monkeypatch, pack, tmp_path)
    out = capsys.readouterr().out

    assert '1条' in out, out
    assert '含缺项 2' in out, out
    assert '未做矛盾检测' not in out, out


def test_end_to_end_healthy_pack_prints_zero_counter(monkeypatch, capsys, tmp_path) -> None:
    """六块齐且真没矛盾：仍然打印 0条，含缺项 0（这个 0 是算出来的）。"""
    _drive_detail(monkeypatch, _all_six(), tmp_path)
    out = capsys.readouterr().out
    assert '0条' in out and '含缺项 0' in out, out
    assert '未做矛盾检测' not in out, out
