"""31 全表未齐 → 跳过系统分析 的两条根因回归测试。

用户报的错：

    理论概率缺31项(1-0/…/胜其它/平其它/负其它)
    市场概率缺9项(5-0/5-1/5-2/0-5/1-5/2-5/胜其它/平其它/负其它)
    已尝试重抓。抓全前不做系统分析。

两条根因彼此独立，各有独立用例，单独还原任一处都会让本文件的用例变红：

  根因 A  冻结 λ 没进 31 表。记录里 λ 写过 `lam_h/lam_a`、`lambda_home/lambda_away`、
          `frozen_lambda`，研判包里可能只有 `lam_home/lam_away`；31 表的调用点却只拼
          `rec.get('lam_home') or rec.get('lam_h')`，于是 theory 整列为空。
  根因 B  那 9 项只有竞彩 31 比分能给。市均 /cs/ 被 WAF 405 时 `scrape_okooo_mobile_cs_zuizhi`
          直接整包返回 unavailable，连 `fetch_jczq31` 都不调；「重抓」因此永远补不上。

所有形状都是真实的：市均 22 项（不含那 9 项）、竞彩 31 项、冻结 λ 齐备；
竞彩 31 的解析用真实 /jczq/ 列表页 fixture（23 个 jsMatchItem，var oddsData）。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault('BLADE_HEADLESS', '1')
os.environ.setdefault('OKOOO_ROUTE', 'direct')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
okooo = importlib.import_module('okooo_mobile_cs')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')

FIXTURES = ROOT / 'tests' / 'fixtures'
JCZQ_LIST = FIXTURES / 'okooo_jczq_list_20261126.html'

# 市均页面固定报不出的那 9 项：只有竞彩官方 31 比分覆盖。
NINE_JC_ONLY = (
    '5-0', '5-1', '5-2', '0-5', '1-5', '2-5', '胜其它', '平其它', '负其它',
)
ALL_31 = list(pipe_mod.JCZQ_ALL_OPTIONS)


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv('BLADE_HEADLESS', '1')
    monkeypatch.setenv('OKOOO_ROUTE', 'direct')
    monkeypatch.setenv('MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.delenv('FLANALYZER_MODELS_DIR', raising=False)
    monkeypatch.delenv('FEATURE_DB_PATH', raising=False)
    monkeypatch.delenv('FEATURE_DB_FILE', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_KEY', raising=False)
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    yield


# --------------------------------------------------------------------------- #
# 真实形状的夹具数据
# --------------------------------------------------------------------------- #
def _jc31_odds() -> dict[str, float]:
    """竞彩官方 31 项赔率：形状真实（31 键，赔率 >1 且互不相同）。"""
    return {option: round(3.5 + (i % 11) * 1.35, 2) for i, option in enumerate(ALL_31)}


def _page22_odds(jc: dict[str, float]) -> dict[str, float]:
    """市均页面（桌面 /cs/）真实形状：22 项，冒号键，那 9 项一个都没有。"""
    return {
        option.replace('-', ':'): jc[option]
        for option in ALL_31
        if option not in NINE_JC_ONLY
    }


def _jczq_list_html() -> str:
    return JCZQ_LIST.read_bytes().decode('gb18030')


# --------------------------------------------------------------------------- #
# 新夹具：真实 /jczq/ 列表页 → 至少三个 MatchID 都出 31/31 且含那 9 项
# --------------------------------------------------------------------------- #
def test_jczq_list_fixture_parses_31_for_three_real_matches() -> None:
    """真实列表页的 var oddsData：三个 MatchID 逐个 31/31，那 9 项全在。"""
    html = _jczq_list_html()
    expected = {'1328189': '5006', '1328592': '5007', '1328914': '5003'}
    for match_id, orderid in expected.items():
        parsed = okooo.parse_jczq_score_odds(html, match_id)
        assert parsed['available'] is True, (match_id, parsed['stage'])
        assert parsed['orderid'] == orderid, (match_id, parsed['orderid'])
        assert parsed['raw_count'] == 31, (match_id, parsed['raw_count'])
        assert len(parsed['odds']) == 31, (match_id, sorted(parsed['odds']))
        assert sorted(parsed['odds']) == sorted(ALL_31)
        for option in NINE_JC_ONLY:
            assert option in parsed['odds'], (match_id, option)
        # 赔率是真数（不是解析出的 0/1 占位）。
        assert all(value > 1.0 for value in parsed['odds'].values()), match_id


def test_jczq_list_fixture_rejects_a_match_that_is_not_on_the_page() -> None:
    """不在页面上的 MatchID 必须如实报缺，不能凭 oddsData 编出 31 项。"""
    parsed = okooo.parse_jczq_score_odds(_jczq_list_html(), '9999999')
    assert parsed['available'] is False
    assert parsed['odds'] == {}
    assert parsed['stage'] == 'orderid_not_found'


# --------------------------------------------------------------------------- #
# 根因 A：冻结 λ 的读侧
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ('record', 'payload', 'home_key'),
    [
        ({'lam_h': 1.45, 'lam_a': 1.10}, None, 'lam_h'),
        ({'lam_home': 1.45, 'lam_away': 1.10}, None, 'lam_home'),
        ({'lambda_home': 1.45, 'lambda_away': 1.10}, None, 'lambda_home'),
        ({'fused_lambda_h': 1.45, 'fused_lambda_a': 1.10}, None, 'fused_lambda_h'),
        ({'base_home_lambda': 1.45, 'base_away_lambda': 1.10}, None, 'base_home_lambda'),
        ({'frozen_lambda': {'home': 1.45, 'away': 1.10}}, None, 'frozen_lambda.home'),
        ({}, {'lam_home': 1.45, 'lam_away': 1.10}, 'lam_home'),
        ({}, {'lam_h': 1.45, 'lam_a': 1.10}, 'lam_h'),
        ({'lambda_calibration': {'available': True, 'final': {'home': 1.45, 'away': 1.10}}}, None,
         'lambda_calibration.final.home'),
    ],
)
def test_frozen_lambda_resolver_names_every_key_it_reads(
    record: dict[str, Any], payload: dict[str, Any] | None, home_key: str,
) -> None:
    """九个真实出现过的 λ 键位都要读得到，并点名取自哪个键。"""
    got = app._frozen_lambda_of(record, payload)
    assert got['complete'] is True, (record, payload)
    assert got['home'] == pytest.approx(1.45)
    assert got['away'] == pytest.approx(1.10)
    assert got['home_key'] == home_key, got
    assert got['source']


def test_frozen_lambda_resolver_never_invents_a_default() -> None:
    """λ 真的没有时返回 None —— 绝不补默认进球期望。"""
    got = app._frozen_lambda_of({'match': 'H vs A'}, {'match': 'H vs A'})
    assert got['home'] is None and got['away'] is None
    assert got['complete'] is False
    assert got['home_key'] == '' and got['away_key'] == ''
    assert '未形成' in app._frozen_lambda_state_text({'match': 'H vs A'})
    assert '上游拒绝补默认进球期望' in app._frozen_lambda_state_text({'match': 'H vs A'})


def test_frozen_lambda_resolver_reports_a_half_formed_pair() -> None:
    """只有主场 λ 时点名缺的是客侧，不把 None 当成 0。"""
    got = app._frozen_lambda_of({'lam_h': 1.45})
    assert got['home'] == pytest.approx(1.45)
    assert got['away'] is None
    assert got['complete'] is False
    assert '客侧' in app._frozen_lambda_state_text({'lam_h': 1.45})


def test_lambda_home_only_record_no_longer_empties_theory_column() -> None:
    """根因 A 的最小复现：记录里只有 lambda_home/lambda_away。

    修复前 31 行 theory 全 None（读侧只认 lam_home/lam_h，而记录写的是 lambda_home）。
    """
    jc = _jc31_odds()
    page22 = _page22_odds(jc)
    lam = app._frozen_lambda_of({'lambda_home': 1.45, 'lambda_away': 1.10})
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], jc_score_odds=jc, extra_market_odds=page22,
        lam_home=lam['home'], lam_away=lam['away'], lambda_provenance=lam,
    )
    assert pack['theory_filled_from_lambda'] is True
    assert pack['lambda_source_key'] == 'lambda_home'
    assert all(row['theory'] is not None for row in pack['rows'])
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(
        pack['rows'],
        sources={'market_page': len(page22), 'jc_score_odds': len(jc),
                 'frozen_lambda': f"{lam['home']}/{lam['away']}"},
    )
    assert audit['complete'] is True, audit['missing']
    assert pack['theory_sum'] == pytest.approx(100.0, abs=0.01)


# --------------------------------------------------------------------------- #
# 根因 B：竞彩 31 的真实来源
# --------------------------------------------------------------------------- #
def test_market_page_blocked_still_yields_jc31(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根因 B 的最小复现：/cs/ 405 时市均没了，竞彩 31 仍要抓回来。

    修复前这里整包返回 unavailable、jc 一个都不带，于是 31 表永远缺那 9 项。
    """
    html = _jczq_list_html()
    calls: list[str] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        if 'jczq' in str(url):
            return {
                'ok': True, 'url': url, 'status': 200, 'bytes': len(html),
                'title': '竞足', 'blocked': False, 'error': '', 'html': html,
                'encoding': 'gb18030', 'referer': '',
            }
        return {
            'ok': False, 'url': url, 'status': 405, 'bytes': 2657,
            'title': '拦截', 'blocked': True, 'error': 'HTTP 405', 'html': '',
            'encoding': '', 'referer': '',
        }

    def fake_fetch_jczq31(match_id, proxies=None, session=None):
        calls.append(str(match_id))
        parsed = okooo.parse_jczq_score_odds(html, match_id)
        return {
            'available': bool(parsed['available']),
            'odds': dict(parsed['odds']),
            'orderid': parsed['orderid'],
            'raw_count': parsed['raw_count'],
            'stage': parsed['stage'],
            'report': {'url': 'https://m.okooo.com/jczq/', 'status': 200,
                       'bytes': len(html), 'blocked': False, 'cached': False},
        }

    monkeypatch.setattr(okooo, '_fetch_detail', fake_fetch)
    monkeypatch.setattr(okooo, 'fetch_jczq31', fake_fetch_jczq31)

    pack = okooo.scrape_okooo_mobile_cs_zuizhi('1328189', patience_retries=0)
    assert pack['available'] is False
    assert pack['block_reason'] == 'external_okooo_mobile_cs_fetch_failed'
    # 市均确实没有：一个键都不伪造。
    assert not pack.get('correct_score_page_avg')
    assert not pack.get('correct_score_books_avg')
    # 竞彩 31 是另一个真实来源：31/31，且那 9 项都在。
    assert calls == ['1328189']
    assert len(pack['jc_score_odds']) == 31
    assert len(pack['correct_score_jc_official']) == 31
    for option in NINE_JC_ONLY:
        assert option in pack['jc_score_odds'], option
    # 逐项点名来源的那句话必须带真实的 HTTP/字节数。
    assert 'status=200' in pack['jc_score_fetch_reason']
    assert f"bytes={len(html)}" in pack['jc_score_fetch_reason']


# --------------------------------------------------------------------------- #
# 端到端：31 表审计分支不再打印「跳过系统分析」
# --------------------------------------------------------------------------- #
def _history_record(jc: dict[str, float], *, lam_keys: dict[str, Any]) -> dict[str, Any]:
    """一条真实形状的 history 记录：市均 22 项 + 竞彩 31 项 + 冻结 λ 齐备。"""
    page22 = _page22_odds(jc)
    rec: dict[str, Any] = {
        'match': '赫尔辛基 vs 玛丽港',
        'cn_name': '赫尔辛基 vs 玛丽港',
        'no': '周四001',
        'time': '2026-11-28 20:00',
        'status': 'pending',
        'pipeline_scrape_done': True,
        'predicted_score': '1-0',
        'okooo_match_id': '1328189',
        'correct_score_channel': 'okooo_cs',
        # 市均：22 项（冒号键，那 9 项没有）。
        'correct_score_page_avg': page22,
        'correct_score_books_avg': dict(page22),
        # 竞彩官方 31 比分：唯一能覆盖那 9 项的真实来源。
        'correct_score_jc_official': dict(jc),
        'jc_score_odds': dict(jc),
        'jc_score_source': '手机竞足→更多玩法→胜平负比分',
        'six_market_mobile': {
            'ou_line': 2.5, 'asian_handicap': -0.5, 'jc_handicap': -1,
            'odds_1x2': {'home': 2.0, 'draw': 3.2, 'away': 3.8},
            'total_goals_odds': {'0': 9.0, '1': 5.4, '2': 3.6, '3': 4.2},
            'htft_odds': {'胜胜': 4.5, '平胜': 6.0, '负胜': 9.0,
                          '胜平': 12.0, '平平': 5.5, '负平': 9.5},
        },
        'asian_handicap': -0.5,
        'ou_line': 2.5,
        'jc_handicap': -1,
        'odds_1x2': {'home': 2.0, 'draw': 3.2, 'away': 3.8},
    }
    rec.update(lam_keys)
    return rec


def _fake_model_completion(messages=None, *a, **k):
    """模型正文（不是本地占位）：让 analyze_match_judgment 走真实成功路径。"""
    return (
        '📌 模型表现自动分析报告\n'
        'DeepSeek 数据分析研判报告\n'
        '方向锁判定报告 台账01｜三者作用｜缺失判定｜命中率改进\n'
        '市短市热对照 台账01｜数据：已核｜策略：无\n'
        '①比分 2-1 ②比分 1-0\n'
    )


def _run_emit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rec: dict[str, Any],
    *,
    reports: list[str] | None = None,
    tg_enabled: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """跑完 emit 的 31 表审计分支，返回（落盘后的记录，打印过的行）。

    `reports` 收下所有经 send_judgment_report_tg 发出的文本，供质量门断言。
    """
    history_file = tmp_path / 'match_history.json'
    history_file.write_text(
        json.dumps({'1328189': rec}, ensure_ascii=False), encoding='utf-8',
    )
    sent = reports if reports is not None else []
    monkeypatch.setattr(app, 'HISTORY_FILE', str(history_file))
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', tg_enabled)
    monkeypatch.setattr(
        app, 'send_judgment_report_tg',
        lambda text, parse_mode=None: sent.append(str(text)),
    )
    monkeypatch.setattr(app, 'send_tg', lambda text, **kwargs: sent.append(str(text)))
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    monkeypatch.setattr(
        app, 'enrich_history_okooo_cs_facts', lambda rec, cn_name='', force_rescrape=False: False,
    )
    monkeypatch.setattr(app, 'is_history_match_not_started', lambda rec: True)
    # 自 (e) 起，没有 API key 时 deepseek 返回的本地占位不再算研判
    # （available=False），闸门会拒发。这些用例测的是「门禁放行 → 正文照发」，
    # 所以必须让底层补全返回一段**模型正文**，而不是本地占位串。
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(pipe_mod, 'deepseek_chat_completion', _fake_model_completion, raising=False)
    monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_LOCAL', False, raising=False)
    monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_ERROR', '', raising=False)

    lines: list[str] = []
    real_print = print

    def capture(*args, **kwargs):
        text = ' '.join(str(a) for a in args)
        lines.append(text)
        real_print(*args, **kwargs)

    monkeypatch.setattr('builtins.print', capture)
    try:
        app.emit_pipeline_deepseek_tg_report({'id': '1328189', 'cn_name': rec['match']}, i=0, total=1)
    finally:
        monkeypatch.setattr('builtins.print', real_print)
    saved = json.loads(history_file.read_text(encoding='utf-8'))
    return saved['1328189'], lines


def test_emit_no_longer_skips_system_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """端到端：真实形状记录走完 31 表审计分支，不许再出现「跳过系统分析」。"""
    jc = _jc31_odds()
    rec = _history_record(jc, lam_keys={'lam_h': 1.45, 'lam_a': 1.10,
                                        'lambda_home': 1.45, 'lambda_away': 1.10})
    saved, lines = _run_emit(monkeypatch, tmp_path, rec)
    joined = '\n'.join(lines)
    assert '跳过系统分析' not in joined, joined
    assert '理论概率缺' not in joined, joined
    assert '市场概率缺' not in joined, joined

    pack = saved['pipeline_report_bundle']['ds_payload']['theo_mkt_31_pack']
    assert pack['row_count'] == 31
    for column in ('theory', 'market'):
        assert all(row[column] is not None for row in pack['rows']), (
            column, [row['score'] for row in pack['rows'] if row[column] is None],
        )
    # 三方同基准，各自 ≈100。
    assert pack['market_sum'] == pytest.approx(100.0, abs=0.05)
    assert pack['theory_sum'] == pytest.approx(100.0, abs=0.05)
    assert pack['jc_pct_sum'] == pytest.approx(100.0, abs=0.05)
    assert saved.get('cs31_analysis_blocked') is False


def test_emit_uses_lambda_home_when_only_that_key_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """记录里只有 lambda_home/lambda_away（阶段1落盘的真实键）也要出齐 31 行 theory。"""
    jc = _jc31_odds()
    rec = _history_record(jc, lam_keys={'lambda_home': 1.45, 'lambda_away': 1.10})
    saved, lines = _run_emit(monkeypatch, tmp_path, rec)
    joined = '\n'.join(lines)
    assert '跳过系统分析' not in joined, joined
    pack = saved['pipeline_report_bundle']['ds_payload']['theo_mkt_31_pack']
    assert all(row['theory'] is not None for row in pack['rows'])
    assert all(row['market'] is not None for row in pack['rows'])


def test_emit_self_heals_a_22_item_jc31_from_the_list_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """竞彩只有 22 项时，自愈要真的去 /jczq/ 的 oddsData 补到 31（而不是原地重试）。

    这条路径就是用户说的「已尝试重抓」。以前它只重抓市均，市均永远 22 项，
    所以必然失败；现在它单独抓竞彩 31。
    """
    html = _jczq_list_html()
    jc_full = _jc31_odds()
    # 竞彩只给了那 22 项（就是市均能覆盖的那些），那 9 项缺。
    jc22 = {option: jc_full[option] for option in ALL_31 if option not in NINE_JC_ONLY}
    rec = _history_record(jc22, lam_keys={'lam_h': 1.45, 'lam_a': 1.10})

    calls: list[str] = []

    def fake_fetch_jczq31(match_id, proxies=None, session=None):
        calls.append(str(match_id))
        parsed = okooo.parse_jczq_score_odds(html, match_id)
        return {
            'available': bool(parsed['available']),
            'odds': dict(parsed['odds']),
            'orderid': parsed['orderid'],
            'raw_count': parsed['raw_count'],
            'stage': parsed['stage'],
            'report': {'url': 'https://m.okooo.com/jczq/', 'status': 200,
                       'bytes': len(html), 'blocked': False, 'cached': False},
        }

    monkeypatch.setattr(okooo, 'fetch_jczq31', fake_fetch_jczq31)
    saved, lines = _run_emit(monkeypatch, tmp_path, rec)
    joined = '\n'.join(lines)
    assert calls == ['1328189'], calls
    assert '竞彩31自愈' in joined, joined
    assert '跳过系统分析' not in joined, joined
    assert len(saved['jc_score_odds']) == 31
    pack = saved['pipeline_report_bundle']['ds_payload']['theo_mkt_31_pack']
    assert all(row['market'] is not None for row in pack['rows'])
    assert pack['market_sum'] == pytest.approx(100.0, abs=0.05)


def test_emit_reports_the_real_source_when_lambda_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """λ 真的没有：仍如实报「理论概率缺31项」，但日志必须点名上游拒绝补默认。"""
    jc = _jc31_odds()
    rec = _history_record(jc, lam_keys={})
    saved, lines = _run_emit(monkeypatch, tmp_path, rec)
    joined = '\n'.join(lines)
    assert '跳过系统分析' in joined
    assert '理论概率缺31项' in joined
    assert '冻结λ未形成' in joined
    assert '上游拒绝补默认进球期望' in joined
    # 市场侧是齐的：22 项市均 + 9 项竞彩，不许把市场也说成缺。
    assert '市场概率缺' not in joined, joined
    assert saved['cs31_analysis_blocked'] is True
    assert saved['cs31_missing']


# --------------------------------------------------------------------------- #
# 发报硬闸：缺六盘口 / 缺 DeepSeek 外壳 → 拒绝发正文
# --------------------------------------------------------------------------- #
def test_quality_gate_signature_matches_the_app_call() -> None:
    """app 用五个关键字调用，实现必须吃得下并返回 dict（曾经 TypeError 被吞）。"""
    gate = pipe_mod.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        '🧠【DeepSeek 数据分析研判报告】\n正文',
        payload={},
        jc_vs_hot_tg_lines=[],
        wdl_matrix_tg_lines=[],
        theo_mkt_31_tg_lines=[],
        mkt_over_theo_tg_lines=[],
    )
    assert isinstance(gate, dict), type(gate)
    for key in ('text', 'ok', 'reject', 'reject_reason', 'missing_boards',
                'missing_sections', 'section_status', 'passed_sections'):
        assert key in gate, key
    # 没有六盘口就没有通过的理由。
    assert gate['reject'] is True
    assert gate['ok'] is False
    assert '缺六盘口' in gate['reject_reason']


def test_quality_gate_accepts_a_complete_payload() -> None:
    """六盘口齐 + 有 DeepSeek 外壳 → 放行，且不改写正文。"""
    body = '🧠【DeepSeek 数据分析研判报告】\n正文'
    gate = pipe_mod.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        body, payload=_history_record(_jc31_odds(), lam_keys={}),
    )
    assert gate['ok'] is True
    assert gate['reject'] is False
    assert gate['reject_reason'] == ''
    assert gate['missing_boards'] == []
    assert gate['text'] == body


def test_quality_gate_treats_zero_lines_as_present() -> None:
    """0.0 是平手盘 / 大小球 0 线，是真实盘口，不能算缺。"""
    gaps = pipe_mod.DeepSeekAnalyzer.six_market_gaps({
        'asian_handicap': 0.0, 'ou_line': 0.0, 'jc_handicap': 0,
        'odds_1x2': {}, 'total_goals_odds': {}, 'htft_odds': {},
    })
    assert gaps == ['胜平负', '总进球', '半全场']


def test_emit_refuses_to_send_when_six_boards_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """缺六盘口：不发研判正文，只发一条点名原因的告警。"""
    jc = _jc31_odds()
    rec = _history_record(jc, lam_keys={'lam_h': 1.45, 'lam_a': 1.10})
    # 只剩亚盘：大小球/胜平负/竞彩让球/总进球/半全场全缺。
    rec['six_market_mobile'] = {'asian_handicap': -0.5}
    reports: list[str] = []
    saved, lines = _run_emit(monkeypatch, tmp_path, rec, reports=reports, tg_enabled=True)
    joined = '\n'.join(lines)
    assert '[TG质量门] 拒发' in joined, joined
    assert '缺六盘口' in joined
    # 正文一条都不能发出去；只允许那条「拒发」告警。
    bodies = [r for r in reports if '阶段2质量门拒发' not in r]
    assert bodies == [], bodies
    assert len(reports) == 1, reports
    assert '阶段2质量门拒发' in reports[0]
    assert '缺六盘口' in reports[0]
    # 记录里留下拒发原因，复盘能看到。
    assert '缺六盘口' in str(saved.get('tg_quality_gate_reject') or '')


def test_emit_refuses_to_send_when_the_deepseek_shell_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """六盘口齐但缺 DeepSeek 外壳：同样不发正文，并点名缺哪一段。"""
    jc = _jc31_odds()
    rec = _history_record(jc, lam_keys={'lam_h': 1.45, 'lam_a': 1.10})
    real_sections = pipe_mod.DeepSeekAnalyzer.build_deepseek_only_tg_sections

    def without_shell(*args, **kwargs):
        return [s for s in real_sections(*args, **kwargs) if 'DeepSeek 数据分析研判报告' not in s]

    monkeypatch.setattr(
        pipe_mod.DeepSeekAnalyzer, 'build_deepseek_only_tg_sections',
        staticmethod(without_shell),
    )
    reports: list[str] = []
    _saved, lines = _run_emit(monkeypatch, tmp_path, rec, reports=reports, tg_enabled=True)
    joined = '\n'.join(lines)
    assert '[TG质量门] 拒发' in joined, joined
    assert '缺DeepSeek外壳' in joined
    bodies = [r for r in reports if '阶段2质量门拒发' not in r]
    assert bodies == [], bodies
    assert len(reports) == 1, reports
    assert '缺DeepSeek外壳' in reports[0]


def test_emit_still_sends_when_the_gate_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """完整 payload：门禁放行，正文照常发出。"""
    jc = _jc31_odds()
    rec = _history_record(jc, lam_keys={'lam_h': 1.45, 'lam_a': 1.10})
    reports: list[str] = []
    saved, lines = _run_emit(monkeypatch, tmp_path, rec, reports=reports, tg_enabled=True)
    joined = '\n'.join(lines)
    assert '[TG质量门] 拒发' not in joined, joined
    assert reports, 'a passing gate must still push the report'
    assert any(pipe_mod.DeepSeekAnalyzer.JUDGMENT_SHELL_MARKER in r for r in reports), reports
    assert not saved.get('tg_quality_gate_reject')
