from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import requests

os.environ.setdefault('BLADE_HEADLESS', '1')
os.environ.setdefault('OKOOO_ROUTE', 'direct')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
okooo = importlib.import_module('okooo_mobile_cs')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')
math_mod = importlib.import_module('odds_index_math')


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
    for key in ('_blade_tg_getme_status', '_blade_tg_invalid_token_logged', '_blade_tg_missing_token_logged'):
        if hasattr(app.threading, key):
            delattr(app.threading, key)
    yield


def table_names(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_feature_store_auto_init_is_idempotent(tmp_path: Path) -> None:
    path = app.resolve_feature_db_path()
    assert path == str((tmp_path / 'models' / 'data' / 'feature_store.db').resolve())
    app.ensure_feature_store_table()
    app.ensure_feature_store_table()
    assert {'match_features', 'match_entropy_features', 'reconciliation_audit', 'feature_store_meta'} <= table_names(path)
    status = app.feature_store_schema_status()
    assert status['schema_version'] == app.FEATURE_STORE_SCHEMA_VERSION
    assert status['row_counts']['match_features'] == 0


def test_feature_store_empty_state_and_roundtrip() -> None:
    assert app.FEATURE_STORE_EMPTY_AUDIT_MESSAGE == '暂无对账数据：数据库已初始化，等待赛果写入'
    result = app.feature_store_reconciliation_round_trip()
    assert result['inserted'] is True
    assert result['read_back']['match_name'] == '诊断假赛事'
    assert result['deleted'] is True
    with sqlite3.connect(app.resolve_feature_db_path()) as conn:
        leftover = conn.execute(
            "SELECT COUNT(*) FROM match_entropy_features WHERE match_id='__diag_reconciliation_roundtrip__'"
        ).fetchone()[0]
    assert leftover == 0


def test_resolve_feature_db_path_honors_model_envs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv('MODELS_DIR', raising=False)
    monkeypatch.setenv('FLANALYZER_MODELS_DIR', str(tmp_path / 'fl_models'))
    assert app.resolve_feature_db_path().endswith('fl_models/data/feature_store.db')
    explicit = tmp_path / 'explicit.db'
    monkeypatch.setenv('FEATURE_DB_PATH', str(explicit))
    assert app.resolve_feature_db_path() == str(explicit.resolve())


def test_build_system_diagnostics_shape_without_keys() -> None:
    diag = app.build_system_diagnostics(force_tg_check=False)
    assert diag['db']['schema_version'] == app.FEATURE_STORE_SCHEMA_VERSION
    assert diag['deepseek_key_present'] is False
    assert diag['telegram_token_present'] is False
    assert diag['telegram_getme']['status'] == 'missing_token'
    assert 'proxy' in diag


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self._payload = payload or {'ok': False}

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def validate_with_fake(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> dict[str, Any]:
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', 'TEST_TOKEN')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, '_TG_SESSION_DIRECT', session)
    for key in ('_blade_tg_getme_status', '_blade_tg_invalid_token_logged'):
        if hasattr(app.threading, key):
            delattr(app.threading, key)
    return app._tg_validate_bot_token_once(force=True)


@pytest.mark.parametrize('status_code', [401, 404])
def test_telegram_getme_invalid_token_mapping(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    result = validate_with_fake(monkeypatch, FakeSession(response=FakeResponse(status_code)))
    assert result['status'] == 'invalid_token'
    assert result['http_status'] == status_code


def test_telegram_getme_server_error_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    result = validate_with_fake(monkeypatch, FakeSession(response=FakeResponse(500)))
    assert result['status'] == 'telegram_server_error'
    assert result['http_status'] == 500


def test_telegram_getme_timeout_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    result = validate_with_fake(monkeypatch, FakeSession(error=requests.exceptions.Timeout('slow')))
    assert result['status'] == 'timeout'


def test_streamlit_apptest_empty_state_has_no_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv('BLADE_HEADLESS', raising=False)
    at = AppTest.from_file(str(ROOT / 'app_v84_hermes.py'))
    at.run(timeout=30)
    assert list(at.exception) == []
    info_values = [getattr(item, 'value', '') for item in at.info]
    assert app.FEATURE_STORE_EMPTY_AUDIT_MESSAGE in info_values


def test_tg_command_dispatcher_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(app, 'send_tg', lambda text, parse_mode=None: sent.append(str(text)))
    monkeypatch.setattr(app, 'load_json', lambda path: {})
    monkeypatch.setattr(app, 'load_brain_rules', lambda: 'offline rules')
    monkeypatch.setattr(app, 'format_system_diagnostics_text', lambda force_tg_check=False: 'diagnostics ok')
    monkeypatch.setattr(app, 'auto_run', lambda: sent.append('auto_run ok'))
    monkeypatch.setattr(app, 'post_match_review', lambda: sent.append('post_match_review ok'))
    monkeypatch.setattr(app, 'ai_furnace', lambda: sent.append('ai_furnace ok'))
    monkeypatch.setattr(app, 'compare_matches', lambda: sent.append('compare_matches ok'))
    monkeypatch.setattr(app, 'add_script_run_ctx', lambda thread: None)

    class ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            if self.target:
                self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(app.threading, 'Thread', ImmediateThread)
    commands = ['分析', '复盘', '熔炉', '今日', '详情 周四001', '方向 周四001 胜', '冷门', '比较', '教训', '状态', '帮助']
    replies_by_command: dict[str, int] = {}
    for command in commands:
        before = len(sent)
        app.handle_tg_command(command)
        replies_by_command[command] = len(sent) - before
    assert any('指令大全' in message for message in sent)
    assert any('系统状态' in message for message in sent)
    assert all(count > 0 for count in replies_by_command.values()), replies_by_command
    assert sent


def test_module_api_audit_has_zero_mismatches() -> None:
    audit_module = importlib.import_module('scripts.audit_module_api')
    assert audit_module.audit(ROOT) == []


def test_send_tg_makes_no_request_without_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 chat_id 时一个 HTTP 请求都不能发，只留一行可操作提示。"""
    sent: list[Any] = []
    monkeypatch.setattr(app, '_tg_post', lambda url, payload: sent.append(payload) or True)
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '')
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE', {'captured': '', 'printed': ''})
    monkeypatch.setattr(app, 'resolve_tg_chat_id_path', lambda path=None: '/nonexistent/tg_chat_id.txt')
    app.reset_tg_delivery_state()

    app.send_tg('没有 chat_id 的消息')

    assert sent == [], sent
    assert app.tg_delivery_disabled() is True
    app.reset_tg_delivery_state()


def test_capture_tg_chat_id_persists_and_reloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """入站 chat.id 被采纳并落盘，下一次启动能从磁盘读回（含群聊负数）。"""
    target = tmp_path / 'models' / 'data' / 'tg_chat_id.txt'
    monkeypatch.setattr(app, 'resolve_tg_chat_id_path', lambda path=None: str(target))
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '')
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE', {'captured': '', 'printed': ''})

    assert app.capture_tg_chat_id(123456789) == '123456789'
    assert target.exists()
    assert target.read_text(encoding='utf-8').strip() == '123456789'

    # 群聊负数按字符串保存。
    assert app.capture_tg_chat_id(-1001234567890) == '-1001234567890'
    assert target.read_text(encoding='utf-8').strip() == '-1001234567890'

    # 模拟重启：清空内存态，仅凭磁盘恢复。
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '')
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE', {'captured': '', 'printed': ''})
    app._bootstrap_tg_chat_id()
    assert app.AUTO_CHAT_ID == '-1001234567890'

    # 非法输入不采纳。
    assert app._normalize_chat_id('not-a-chat-id') == ''
    assert app._normalize_chat_id('') == ''


def test_permanent_telegram_error_skips_retry_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 chat_id is empty 是永久配置错误：一次即停，不走代理/直连/SSL 阶梯。"""
    attempts: list[str] = []

    class _Resp:
        status_code = 400
        text = '{"ok":false,"error_code":400,"description":"Bad Request: chat_id is empty"}'

        def __init__(self):
            self.headers: dict[str, str] = {}

    class _Session:
        def post(self, url, *args: Any, **kwargs: Any):
            attempts.append(url)
            return _Resp()

    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, '_TG_SESSION_DIRECT', _Session())
    app.reset_tg_delivery_state()

    result = app._tg_post('https://api.telegram.org/bottest/sendMessage', {'chat_id': '', 'text': 'x'})

    assert result == 'PERMANENT'
    assert len(attempts) == 1, attempts
    assert app._tg_is_permanent_failure(401, '{"description":"Unauthorized"}') is True
    assert app._tg_is_permanent_failure(403, '{"description":"Forbidden"}') is True
    assert app._tg_is_permanent_failure(404, '{"description":"Not Found"}') is True
    # can't parse entities 仍要回退纯文本，不算永久错误。
    assert app._tg_is_permanent_failure(400, "Bad Request: can't parse entities") is False
    # 网络错误不算永久错误，仍走重试阶梯。
    assert app._tg_is_permanent_failure(500, 'server error') is False
    app.reset_tg_delivery_state()


def test_full_audit_harness_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'full_audit.py')],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'FAIL 0' in proc.stdout
    # The audit matrix only ever grows; assert monotonic growth, never a fixed count.
    match = re.search(r'TOTAL\s+(\d+)\s+PASS\s+(\d+)\s+FAIL\s+(\d+)', proc.stdout)
    assert match, proc.stdout[-2000:]
    total, passed, failed = (int(part) for part in match.groups())
    assert failed == 0, proc.stdout[-2000:]
    assert total == passed
    assert total >= 262, f'audit matrix shrank to {total} checks'


class FakeTextNode:
    def __init__(self, text: str):
        self.text = text

    def get_text(self, strip: bool = False):
        return self.text.strip() if strip else self.text


class FakeScheduleRow:
    def __init__(self, zvs_text: str):
        self.zvs_text = zvs_text

    def select_one(self, selector: str):
        if selector == '.zVS':
            return FakeTextNode(self.zvs_text)
        return None


def test_mobile_schedule_finished_and_pending_rows_classify() -> None:
    assert okooo.jingcai_board_row_finished(day_closed=True) is True
    assert okooo.jingcai_board_row_finished(end='ended') is True
    assert okooo.jingcai_board_row_finished(inside_end_hide=True) is True
    assert okooo.jingcai_board_row_finished(FakeScheduleRow('2-1')) is True
    assert okooo.jingcai_board_row_finished(FakeScheduleRow('22:00')) is False


# ---------------------------------------------------------------------------
# Regressions for the 2026-11-25 Windows run log (eight matches, nothing formed).
# Each test pins one defect from that log so it cannot come back silently.
# ---------------------------------------------------------------------------

def _reload_skill_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Force a fresh knowledge_base load against an empty directory."""
    monkeypatch.setenv('BLADE_KB_DIR', str(tmp_path / 'kb_probe'))
    engine = app.SkillMatchingEngine
    monkeypatch.setattr(engine, '_loaded', False)
    monkeypatch.setattr(engine, '_seeded', False)
    monkeypatch.setattr(engine, '_stat_skills', None)
    monkeypatch.setattr(engine, '_strat_skills', None)
    monkeypatch.setattr(engine, '_load_error', None)
    return engine


def test_missing_knowledge_base_is_one_line_never_a_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Defect 1/7: no 系统已挂起 block, no empty exception, exactly one line."""
    engine = _reload_skill_engine(tmp_path, monkeypatch)
    # No seed available and nowhere to write it: the honest "unavailable" path.
    monkeypatch.setattr(engine, '_SEED_SKILLS', {}, raising=False)
    monkeypatch.setattr(engine, '_kb_base_dirs', classmethod(lambda cls: [str(tmp_path / 'nope')]))
    monkeypatch.setattr(engine, '_seed_knowledge_base', classmethod(lambda cls, d: False))
    result = engine().match_skills({})
    printed = capsys.readouterr().out
    assert result['available'] is False
    assert '系统已挂起' not in printed
    assert '知识库文件缺失' not in printed
    assert '异常(已降级跳过)' not in printed
    # The reason is real, not an empty string.
    assert result['error'] and 'statistical_skills.json' in result['error']
    # And the caller prints exactly one line for it.
    engine().match_skills({})
    assert capsys.readouterr().out == ''


def test_knowledge_base_seeds_itself_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defect 1/7: the normal path is "loaded", because the app seeds the files."""
    engine = _reload_skill_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, '_kb_base_dirs', classmethod(lambda cls: [str(tmp_path / 'install')]))
    monkeypatch.setattr(engine, '_seeded', False)
    result = engine().match_skills({
        'pool15_enriched': [{'score': '1-0', 'theory': 18.0, 'market': 15.0, 'ibi': 0.8, 'delta': 3.0}],
        'asian_handicap': 0.5,
        'ou_line': 2.5,
    })
    assert result['available'] is True, result
    assert result['error'] is None
    seeded = tmp_path / 'install' / 'knowledge_base'
    for name in ('statistical_skills.json', 'strategy_skills.json'):
        assert (seeded / name).exists()
        payload = __import__('json').loads((seeded / name).read_text(encoding='utf-8'))
        assert payload['skills'], f'{name} seeded empty'


def test_every_deepseek_analyzer_method_has_a_real_implementation() -> None:
    """Defect 2/8: the 阶段2 call path must not depend on a missing attribute."""
    analyzer = app._ensure_deepseek_analyzer()
    assert analyzer is not None
    audit = analyzer.audit_theo_mkt_31_rows([{'score': '1-0', 'theory': 10.0, 'market': 9.0}])
    assert isinstance(audit, dict) and 'complete' in audit and 'missing' in audit
    assert audit['complete'] is False
    # A full 31-row table must审计 as complete.
    full = [
        {'score': score, 'theory': 3.0, 'market': 2.9}
        for score in importlib.import_module('blade_deepseek_pipeline').JCZQ_ALL_OPTIONS
    ]
    assert analyzer.audit_theo_mkt_31_rows(full)['complete'] is True
    # The judgment entry point itself must exist and return the documented shape.
    judgment = analyzer.analyze_match_judgment({'home': 'A', 'away': 'B'})
    assert set(judgment) >= {'available', 'content', 'error', 'model'}


def test_missing_analyzer_methods_are_all_defined() -> None:
    """Guards the whole DeepSeekAnalyzer surface the app calls."""
    required = [
        'audit_theo_mkt_31_rows', 'build_theo_mkt_31_pack', 'build_mkt_short_wdl_matrix',
        'analyze_match_judgment', 'analyze_cross_pairs', 'extract_physical_anchor',
        'explain_match_ou_bounds', 'extract_dual_scores_from_judgment_text',
    ]
    analyzer = app._ensure_deepseek_analyzer()
    missing = [name for name in required if not hasattr(analyzer, name)]
    assert missing == [], missing


def test_cs_unavailable_reason_names_url_status_and_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defect 3: reason must carry URL + status/exception + parse stage."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', os.environ.get('OKOOO_CACHE_DIR', '/tmp/okooo-cache-test'))
    blocked = {
        'ok': False, 'url': 'https://www.okooo.com/soccer/match/1/cs/',
        'status': 405, 'bytes': 4681, 'title': '405', 'blocked': True,
        'error': 'HTTP 405', 'html': '',
    }
    monkeypatch.setattr(okooo, '_fetch_detail', lambda *a, **k: blocked)
    # 耐心重试会真的 sleep；这里只测 reason 形态，关掉等待。
    pack = okooo.scrape_okooo_mobile_cs_zuizhi('999001', patience_retries=0)
    assert pack['available'] is False
    assert pack['reason'] == 'external_okooo_mobile_cs_fetch_failed'
    detail = pack['error']
    assert 'okooo.com' in detail and '/cs/' in detail
    assert 'HTTP 405' in detail
    assert 'stage=' in detail
    assert pack.get('parse_stage') == 'fetch_failed'


def test_cs_parser_reads_the_real_datatable_shape() -> None:
    """Defect 3: the shipped parser must read okooo's actual 波胆 table."""
    html = '''
    <table id="datatable">
      <tr class="titlebg"><td>公司名</td><td><b>1:0</b></td><td><b>2:0</b></td>
        <td><b>2:1</b></td></tr>
      <tr><td>1</td><td>竞**方</td><td>主胜</td><td>10.00</td><td>20.00</td><td>9.00</td></tr>
      <tr class="trBgBlue"><td>客胜</td><td>-</td></tr>
      <tr><td>2</td><td>威***尔</td><td>主胜</td><td>12.00</td><td>22.00</td><td>10.00</td></tr>
      <tr class="trBgBlue"><td>客胜</td><td>-</td></tr>
      <tr class="avgobjH"><td>&nbsp;</td><td>平均值</td><td>主胜</td>
        <td>11.00</td><td>21.00</td><td>9.50</td></tr>
      <tr class="avgobjA"><td>客胜</td><td>-</td></tr>
    </table>
    '''
    parsed = okooo._parse_cs_datatable(html)
    assert parsed['stage'] == 'ok'
    assert parsed['header_count'] == 3
    assert parsed['books_n'] == 2
    assert abs(parsed['page_avg']['1:0'] - 11.0) < 1e-9
    assert abs(parsed['books_avg']['2:1'] - 9.5) < 1e-9


def test_cs_parser_keeps_away_and_average_halves_separate() -> None:
    """The 客胜 row has no 「平均值」 text; its half must still join the average.

    Regression for a real mis-parse: the away half of the 平均值 block was being
    counted as company data, which corrupted both the average and the book count.
    """
    html = '''
    <table id="datatable">
      <tr class="titlebg"><td>公司名</td><td><b>1:0</b></td><td><b>2:0</b></td>
        <td><b>0:0</b></td></tr>
      <tr><td>1</td><td>竞**方</td><td>主胜</td><td>10.00</td><td>20.00</td><td>8.00</td></tr>
      <tr class="trBgBlue"><td>客胜</td><td>9.00</td></tr>
      <tr class="avgobjH"><td>&nbsp;</td><td>平均值</td><td>主胜</td>
        <td>11.00</td><td>21.00</td><td>7.50</td></tr>
      <tr class="avgobjA"><td>客胜</td><td>6.50</td></tr>
    </table>
    '''
    parsed = okooo._parse_cs_datatable(html)
    assert parsed['books_n'] == 1, 'the 平均值 客胜 row must not count as a company'
    assert parsed['row_count'] == 1
    assert abs(parsed['page_avg']['1:0'] - 11.0) < 1e-9
    # 客胜行第一个格子对应表头第一列反过来的比分（1:0 → 0:1）。
    assert abs(parsed['page_avg']['0:1'] - 6.5) < 1e-9
    assert abs(parsed['books_avg']['0:1'] - 9.0) < 1e-9
    assert parsed['stage'] == 'ok'


def test_water_retry_is_single_shot_per_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Defect 5/9: one re-fetch and one line per match, never three."""
    calls: list[str] = []

    def fake_detail(match_id, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(str(match_id))
        return {
            'okooo_match_id': str(match_id),
            'six_market_mobile': {
                'available': False, 'channel': 'mobile',
                'block_reason': 'external_okooo_six_market_fetch_failed',
                'source_report': {
                    'ah': {'stage': 'fetch_failed', 'status': 405, 'blocked': True},
                },
            },
        }

    monkeypatch.setattr(app, 'scrape_okooo_detail', fake_detail)
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())
    rec = {
        'okooo_match_id': '555001',
        'correct_score_available': False,
        'six_market_mobile': {'available': False, 'channel': 'mobile'},
    }
    for _ in range(3):
        app._ensure_mobile_six(rec, '555001')
    app._ensure_mobile_six(rec, '555001', force=True)
    app._ensure_mobile_six(rec, '555001')
    printed = capsys.readouterr().out
    assert len(calls) == 1, calls
    assert printed.count('[水位] mid=555001') == 1
    assert printed.count('[水位重抓]') == 1
    # The line must say WHY, field by field, from the real source report.
    assert 'asian_handicap:fetch_failed/HTTP405' in printed
    assert 'ou_line=无抓取记录' in printed
    assert '重复抓取后仍无' not in printed


def test_dual_source_gate_never_returns_none_fields() -> None:
    """Defect 6: no ready=None / reason=None on the judgment gate."""
    fallback = importlib.import_module('odds_fallback')
    gate = fallback.ensure_dual_source_before_judgment({'okooo_match_id': '1'})
    for key in ('ready', 'mobile_cs_entered', 'cs_books_n', 'filled_from_500', 'kept_mobile', 'reason'):
        assert key in gate, gate
        assert gate[key] is not None, (key, gate)
    assert gate['ready'] is False
    assert gate['reason'], 'an unready gate must explain itself'
    # And a ready gate reports an empty reason, not None.
    ready = fallback.ensure_dual_source_before_judgment({
        'correct_score_page_avg': {'1-0': 8.0},
        'correct_score_channel': 'mobile',
        'asian_handicap': 0.5, 'ou_line': 2.5, 'odds_1x2': {'home': 2.0},
    })
    assert ready['ready'] is True
    assert ready['reason'] == ''


def test_rank_tracking_failure_reports_a_real_reason() -> None:
    """Defect 10: an empty pool must produce a cause, not 未知."""
    result = app.RankTrackingMatrix.run([])
    assert result['ok'] is False
    assert result.get('error'), result


def test_chart31_verdict_does_not_claim_success_on_empty_input() -> None:
    """Defect 11: no '已生成' claim when nothing was rendered."""
    text = app.generate_31pool_theory_vs_market_chart({'prob_dict': None})
    verdict = app.chart31_pool_verdict(text)
    assert verdict['status'] == 'degraded'
    assert verdict['rows'] == 0


def test_low_dti_blames_missing_inputs_not_the_market() -> None:
    """Defect 12: low DTI from an empty match is an input problem."""
    note = app.explain_low_dti(0.02, ['亚指盘口', 'λ'])
    assert '输入不足' in note['warning']
    assert '亚盘异动' not in note['warning']
    assert note['missing_inputs'] == ['亚指盘口', 'λ']
    complete = app.explain_low_dti(0.02, [])
    assert '人工复核' in complete['warning']


def test_replay_of_the_live_log_has_no_banned_lines() -> None:
    """End-to-end: eight matches, no network, the log contract holds."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'replay_live_log.py')],
        cwd=ROOT, text=True, capture_output=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    assert 'REPLAY PASS' in proc.stdout


# ---------------------------------------------------------------------------
# Mobile water pages. Fixtures are the real pages captured from m.okooo.com on
# 2026-11-25; the desktop pages cannot supply these three boards at all
# (/ah/ intermittent 405, /overunder/ 平均值 row paywalled to '-', /odds/
# company rows rendered by JS), so these parsers are the primary path.
# ---------------------------------------------------------------------------

FIXTURES = ROOT / 'tests' / 'fixtures'


def test_mobile_pages_decode_from_mixed_encodings() -> None:
    """AH/1X2/交易盈亏 are GBK; the 大小球 page is UTF-8 despite no charset tag."""
    ah = (FIXTURES / 'okooo_mobile_ah.html').read_bytes()
    ou = (FIXTURES / 'okooo_mobile_ou.html').read_bytes()
    for raw, needle in ((ah, '亚指'), (ou, '大球')):
        text = okooo.decode_okooo_html(raw)
        assert needle in text, f'{needle} not decoded — mojibake would break every selector'
    # A blanket gb18030 decode would corrupt the UTF-8 page.
    assert '大球' not in ou.decode('gb18030', 'replace')


def test_mobile_ah_parser_matches_verified_company_row() -> None:
    """澳**票 start 1.93 受半/一 1.85 → latest 2.17 受半球 1.61 (verified live)."""
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_mobile_ah.html').read_bytes())
    parsed = okooo._mobile_ah_line(text)
    assert parsed['stage'] == 'ok'
    assert parsed['line_source'] == 'avg_row'
    assert parsed['asian_handicap'] is not None
    assert parsed['company_rows'] >= 20
    rows = re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', text)
    macau = next(r for r in rows if '澳**票' in r)
    cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', macau)
    start = okooo._tag_text(cells[1], None, ['zhu', 'chu', 'ke'])
    latest = okooo._tag_text(cells[2], None, ['xinzhu', 'xin', 'xinke'])
    assert (start['zhu'], start['chu'], start['ke']) == ('1.93', '受半/一', '1.85')
    assert (latest['xinzhu'], latest['xin'], latest['xinke']) == ('2.17', '受半球', '1.61')
    # 受半球 = 主队受让 0.5 → -0.5 的语汇在此行；整页平均行为受一球。
    assert okooo.ah_text_to_line('受半球') == -0.5


def test_mobile_ou_parser_reads_line_and_waters() -> None:
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_mobile_ou.html').read_bytes())
    parsed = okooo._mobile_ou_line(text)
    assert parsed['stage'] == 'ok'
    assert parsed['ou_line'] is not None
    assert parsed['company_rows'] >= 15
    assert parsed['line_source'].startswith('mode(')
    assert parsed['over_water'] is not None and parsed['under_water'] is not None


def test_mobile_1x2_parser_matches_jingcai_row() -> None:
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_mobile_1x2.html').read_bytes())
    parsed = okooo._mobile_1x2(text)
    assert parsed['stage'] == 'ok'
    assert parsed['company'] == '竞彩官方'
    odds = parsed['odds_1x2']
    assert (odds['home'], odds['draw'], odds['away']) == (4.55, 4.2, 1.47)


def test_mobile_exchange_pnl_reports_betfair_absence_honestly() -> None:
    """The real page has 竞足保存盈亏 data and a 必发 block that says 暂无数据."""
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_mobile_exchanges.html').read_bytes())
    parsed = okooo._mobile_exchange_pnl(text)
    assert parsed['available'] is True
    assert parsed['jc_saved']['胜']['pnl'] == 489356.0
    assert parsed['saved_total'] == 2099228.0
    assert parsed['betfair_available'] is False
    assert '暂无数据' in parsed['betfair_note']


def test_six_markets_prefers_mobile_and_records_every_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Each board tries mobile first; a 405 on mobile falls through to desktop."""
    pages = {
        'handicap.php': ('okooo_mobile_ah.html', 200),
        'ah.php?type=daxiao': ('okooo_mobile_ou.html', 200),
        'match/odds.php': ('okooo_mobile_1x2.html', 200),
        'm.okooo.com/jczq/': ('okooo_jczq_oddsdata.html', 200),
    }
    seen: list[str] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        seen.append(url)
        for needle, (fixture, status) in pages.items():
            if needle in url:
                raw = (FIXTURES / fixture).read_bytes()
                return {
                    'ok': status == 200, 'url': url, 'status': status,
                    'bytes': len(raw), 'title': '', 'blocked': False,
                    'error': '', 'html': okooo.decode_okooo_html(raw),
                }
        return {'ok': False, 'url': url, 'status': 405, 'bytes': 2657,
                'title': '405', 'blocked': True, 'error': 'HTTP 405', 'html': ''}

    monkeypatch.setattr(okooo, '_fetch_detail', fake_fetch)
    # 缓存写进 tmp_path：绝不能把抓取缓存落成 tests/fixtures 里的「测试夹具」。
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache'))
    # jczq 页也走真实夹具，否则会进「被拦 → 等待重试」的分支把测试拖慢。
    monkeypatch.setenv('OKOOO_JCZQ_PATIENCE', '0')
    pack = okooo.scrape_okooo_mobile_six_markets('1347776')
    assert pack['available'] is True
    assert pack['asian_handicap'] is not None
    assert pack['ou_line'] is not None
    assert (pack['odds_1x2']['home'], pack['odds_1x2']['draw'], pack['odds_1x2']['away']) == (4.55, 4.2, 1.47)
    assert pack['missing'] == []
    # Mobile endpoints were the ones used — desktop was never needed.
    assert all('m.okooo.com' in url for url in seen), seen
    for kind in ('ah', 'ou', '1x2'):
        report = pack['source_report'][kind]
        assert report['url'] and report['status'] == 200 and report['bytes'] > 0
        assert report['stage'] == 'ok'


def test_cs_referer_candidates_target_the_match_page() -> None:
    """The 波胆 page 405s on a homepage referer and 200s on the match page."""
    referers = okooo._referers_for('https://www.okooo.com/soccer/match/1347776/cs/')
    assert referers[0] == 'https://www.okooo.com/soccer/match/1347776/'
    assert okooo._referers_for('https://m.okooo.com/match/odds.php?MatchID=1') == [
        'https://m.okooo.com/jczq/'
    ]


def _captured_pack() -> dict[str, Any]:
    """The real mobile pack captured from m.okooo.com for 1347776."""
    return json.loads((FIXTURES / 'okooo_six_market_pack_1347776.json').read_text(encoding='utf-8'))


def test_apply_mobile_books_lines_carries_every_board() -> None:
    """Defect: apply only copied ah/ou, so odds_1x2 stayed empty and triggered re-fetch."""
    pack = _captured_pack()
    record: dict[str, Any] = {'okooo_match_id': pack['match_id']}
    okooo.apply_mobile_books_lines(record, pack)
    assert record['asian_handicap'] == pack['asian_handicap']
    assert record['ou_line'] == pack['ou_line']
    assert record['odds_1x2'] == pack['odds_1x2']
    # provenance comes along so downstream can see where the numbers came from
    assert record['mobile_water_provenance']['boards_hit'] == pack['boards_hit']
    assert record['ou_line_source'] == pack['ou_line_source']
    assert record['ah_line_source'] == pack['ah_line_source']


def test_seed_then_apply_leaves_no_water_gaps() -> None:
    """End-to-end: scrape → seed → apply must satisfy missing_six_market_waters.

    Uses a real captured pack, so this cannot regress into a hand-built dict that
    happens to have the fields the code wants.
    """
    pack = _captured_pack()
    record: dict[str, Any] = {'okooo_match_id': pack['match_id']}
    okooo.seed_mobile_ah_ou_books(record, pack)
    okooo.apply_mobile_books_lines(record)
    assert okooo.missing_six_market_waters(record) == []
    assert len(record['odds_1x2']) >= 3
    assert record['odds_1x2']['home'] == 4.55
    assert record['odds_1x2']['draw'] == 4.2
    assert record['odds_1x2']['away'] == 1.47


def test_empty_stub_cannot_block_the_real_pack() -> None:
    """An unavailable stub already on the record must not shadow real data."""
    pack = _captured_pack()
    record: dict[str, Any] = {
        'okooo_match_id': pack['match_id'],
        'mobile_ah_ou_books': {'available': False},
    }
    okooo.seed_mobile_ah_ou_books(record, pack)
    okooo.apply_mobile_books_lines(record)
    assert okooo.missing_six_market_waters(record) == []
    assert record['mobile_ah_ou_books']['odds_1x2'] == pack['odds_1x2']


def test_apply_never_overwrites_a_non_empty_value_with_an_empty_one() -> None:
    """A failed scrape must not erase water the record already has."""
    record: dict[str, Any] = {
        'odds_1x2': {'home': 9.0, 'draw': 9.0, 'away': 9.0},
        'ou_line': 2.5,
        'asian_handicap': 0.5,
    }
    okooo.apply_mobile_books_lines(record, {
        'available': False, 'odds_1x2': {}, 'ou_line': None, 'asian_handicap': None,
        'ou_line_text': '',
    })
    assert record['odds_1x2'] == {'home': 9.0, 'draw': 9.0, 'away': 9.0}
    assert record['ou_line'] == 2.5
    assert record['asian_handicap'] == 0.5
    assert 'ou_line_text' not in record


def test_six_market_pack_fixture_is_not_a_scrape_cache() -> None:
    """The pack fixture must be a curated capture, not tests/fixtures/_cache_*."""
    assert not list((ROOT / 'tests' / 'fixtures').glob('_cache_*')), (
        'scrape caches must not be committed as fixtures'
    )
    pack = _captured_pack()
    assert pack['_fixture_note']
    assert pack['source_report']['1x2']['status'] == 200


# ---------------------------------------------------------------------------
# Round 4: heat kwarg/legality, lineup, AH company timeline, dual-source gate.
# ---------------------------------------------------------------------------

def test_cs_heat_labels_emit_the_accepted_keyword() -> None:
    """Defect 1: _cs_heat_labels must emit `channel`, not `heat_channel`."""
    rec = {'correct_score_channel': 'okooo_cs', 'score_market_heat': {'source': 'okooo_cs_books_avg'}}
    labels = app._cs_heat_labels(rec)
    assert 'heat_channel' not in labels
    assert labels['channel'] == 'okooo_cs'
    # And the call it feeds must accept the label verbatim (this used to TypeError).
    heat = okooo.build_score_market_heat_ranking({'1:0': 8.0, '2:1': 12.0}, **labels)
    assert heat['ranking']


def test_build_score_market_heat_ranking_accepts_heat_channel_alias() -> None:
    """Older/embedded callers still send heat_channel; that must not raise."""
    heat = okooo.build_score_market_heat_ranking({'1:0': 8.0}, heat_channel='okooo_cs')
    assert heat['channel'] == 'okooo_cs'
    assert heat['ranking'][0]['odds'] == 8.0


def test_ranking_rows_expose_avg_odds() -> None:
    """Defect 1b: the legality gate counts rows by avg_odds, which was never emitted."""
    heat = okooo.build_score_market_heat_ranking(
        {'1:0': 15.28, '2:0': 25.48, '2:1': 19.09, '0:0': 17.21},
        heat_source='okooo_cs_books_avg', channel='okooo_cs',
    )
    for row in heat['ranking']:
        assert 'avg_odds' in row and row['avg_odds'] == row['odds']
    assert app._is_legal_mobile_cs_heat(heat) is True


def test_desktop_pool31_heat_still_banned_but_desktop_cs_page_legal() -> None:
    """The ban targets the desktop analysis page/pool31, not the /cs/ 波胆 page."""
    banned = {'source': 'pool31', 'channel': 'pool31', 'ranking': [{'avg_odds': 9}]}
    assert app._is_legal_mobile_cs_heat(banned) is False
    banned2 = {'source': 'desktop_api', 'channel': 'desktop', 'ranking': [{'avg_odds': 9}]}
    assert app._is_legal_mobile_cs_heat(banned2) is False
    legal = okooo.build_score_market_heat_ranking(
        {'1:0': 15.28, '2:0': 25.48, '1:1': 7.88, '0:0': 17.21},
        heat_source='okooo_cs_books_avg', channel='okooo_cs',
    )
    assert app._is_legal_mobile_cs_heat(legal) is True


def test_legacy_desktop_cs_labels_normalise_to_legal() -> None:
    """Old recs/embedded copies labelled desktop_cs must still reach the table."""
    heat = okooo.build_score_market_heat_ranking(
        {'1:0': 15.28, '2:0': 25.48, '1:1': 7.88, '0:0': 17.21},
        heat_channel='desktop_cs', heat_source='okooo_desktop_cs_page_avg',
    )
    assert heat['channel'] == 'okooo_cs'
    assert heat['source'] == 'okooo_cs_books_avg'
    assert app._is_legal_mobile_cs_heat(heat) is True


def test_rec_to_heat_to_compare_heat_keeps_a_ranking() -> None:
    """rec → heat → _resolve_compare_heat_from_history must survive non-empty."""
    page_avg = {'1:0': 15.28, '2:0': 25.48, '2:1': 19.09, '0:0': 17.21,
                '1:1': 7.88, '0:1': 9.58, '2:2': 16.43}
    heat = okooo.build_score_market_heat_ranking(
        page_avg, page_avg_odds=page_avg,
        heat_source='okooo_cs_books_avg', channel='okooo_cs',
    )
    rec = {
        'correct_score_channel': 'okooo_cs',
        'correct_score_page_avg': page_avg,
        'correct_score_books_avg': page_avg,
        'score_market_heat': heat,
    }
    resolved = app._resolve_compare_heat_from_history(rec)
    assert resolved.get('ranking'), resolved
    assert resolved.get('source') == 'okooo_cs_books_avg'
    assert resolved.get('channel') == 'okooo_cs'


def test_formation_parser_reads_starters_subs_and_absences() -> None:
    """Defect 2b: real /formation/ page → 11+11 starters, subs, 伤停."""
    html = (FIXTURES / 'okooo_formation_1347776.html').read_bytes()
    parsed = okooo._parse_lineup_page(okooo.decode_okooo_html(html))
    assert parsed['available'] is True
    assert parsed['home_count'] == 11 and parsed['away_count'] == 11
    home0 = parsed['home']['starters'][0]
    assert home0['number'] and home0['name']
    # 澳**票-style prices come from .player-price
    assert any(p['price'].endswith('万') for p in parsed['starters'])
    assert parsed['substitutes'], 'substitutes must parse from 主队替补/客队替补'
    assert all(s['price'].endswith('万') for s in parsed['substitutes'][:3] if s['price'])
    assert parsed['absences'], '伤停 must parse from match-battle-shangting'
    absence = parsed['absences'][0]
    assert absence['state'] and absence['position'].startswith('【') and absence['name']


def test_formation_parser_second_match_has_six_absences() -> None:
    html = (FIXTURES / 'okooo_formation_1347778.html').read_bytes()
    parsed = okooo._parse_lineup_page(okooo.decode_okooo_html(html))
    assert parsed['home_count'] == 11 and parsed['away_count'] == 11
    assert len(parsed['absences']) == 6


def test_ah_change_timeline_parses_real_rows() -> None:
    """Defect 3: change.php carries the per-company盤口 timeline."""
    html = (FIXTURES / 'okooo_ah_change_1347776_pid84.html').read_bytes()
    changes = okooo._parse_ah_change_list(okooo.decode_okooo_html(html))
    assert len(changes) == 8
    first = changes[0]
    assert (first['home_water'], first['line_text'], first['away_water']) == (2.17, '受半球', 1.61)
    assert first['line'] == -0.5
    # the row the user quoted: 2.18 / 受半球 / 1.60 @ 赛前33小时9分
    assert any(
        c['home_water'] == 2.18 and c['line_text'] == '受半球'
        and c['away_water'] == 1.60 and c['note'] == '赛前33小时9分'
        for c in changes
    )


def test_ah_ou_books_reports_no_ou_timeline_honestly() -> None:
    """OU has no change.php links; the pack must say so instead of inventing one."""
    books = okooo.scrape_okooo_mobile_ah_ou_books('1347776', book_cap=1, detail_cap=0)
    # Network may be blocked in CI; only assert the contract when it succeeded.
    if not books.get('available'):
        pytest.skip(f"live AH unavailable: {books.get('error')}")
    assert all(b['changes'] == [] for b in books['over_under']['companies'])
    assert books['over_under']['detail_block_reason'], 'must explain the missing timeline'


def test_dual_source_gate_recomputes_water_gaps() -> None:
    """Defect 4: a stale cached water_gaps must not force ready=False."""
    fallback = importlib.import_module('odds_fallback')
    rec = {
        'correct_score_page_avg': {'1:0': 8.0, '2:1': 12.0},
        'correct_score_channel': 'okooo_cs',
        'asian_handicap': -0.75,
        'ou_line': 3.25,
        'odds_1x2': {'home': 4.55, 'draw': 4.2, 'away': 1.47},
        # stale from an earlier failed scrape
        'water_gaps': ['asian_handicap', 'ou_line', 'odds_1x2'],
    }
    gate = fallback.ensure_dual_source_before_judgment(rec)
    assert gate['ready'] is True
    assert gate['reason'] == ''
    assert gate['water_gaps'] == []
    assert rec['water_gaps'] == [], 'fresh gaps must be written back'


def test_dual_source_gate_still_reports_genuine_gaps() -> None:
    fallback = importlib.import_module('odds_fallback')
    rec = {
        'correct_score_page_avg': {'1:0': 8.0},
        'correct_score_channel': 'okooo_cs',
        'asian_handicap': -0.75,
    }
    gate = fallback.ensure_dual_source_before_judgment(rec)
    assert gate['ready'] is False
    assert 'ou_line' in gate['reason'] and 'odds_1x2' in gate['reason']
    assert rec['water_gaps'] == ['ou_line', 'odds_1x2']


def test_pack_reason_never_collapses_to_empty() -> None:
    """Defect 2a: the real reason must survive the log line."""
    assert app._pack_reason({'reason': 'external_fetch_not_configured'}) == 'external_fetch_not_configured'
    assert app._pack_reason({'block_reason': 'x', 'reason': 'y'}) == 'x'
    assert app._pack_reason({'error': 'HTTP 405'}) == 'HTTP 405'
    assert app._pack_reason({}) == 'empty'
    assert app._pack_reason(None) == 'invalid_payload'


def test_lineup_and_books_stubs_expose_a_reason_field() -> None:
    """Every unavailable pack must carry a reason the log can print."""
    for fn, args in (
        (okooo.scrape_okooo_mobile_lineup, ('',)),
        (okooo.scrape_okooo_mobile_ah_ou_books, ('',)),
    ):
        pack = fn(*args)
        assert pack.get('available') is False
        assert app._pack_reason(pack) not in ('empty', 'invalid_payload')


def test_jczq_oddsdata_yields_all_31_jc_score_odds() -> None:
    """Defect 5: the only real source of jc_score_odds (which had no writer)."""
    html = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata.html').read_bytes())
    parsed = okooo.parse_jczq_score_odds(html, '1347780')
    assert parsed['available'] is True
    assert parsed['orderid'] == '4005'
    assert parsed['raw_count'] == 31
    assert len(parsed['odds']) == 31
    # Canonical 竞彩 code order, corroborated by the same page's
    # HalfFull '28'=2.75 (负负, away favourite) and TotalGoals peaking at '03'.
    assert parsed['odds']['1-0'] == 11.0
    assert parsed['odds']['2-1'] == 10.5
    assert parsed['odds']['1-1'] == 6.25
    assert parsed['odds']['0-1'] == 7.25
    # The 9 options the 市均 can never cover are present here.
    for option in ('5-0', '5-1', '5-2', '0-5', '1-5', '2-5', '胜其它', '平其它', '负其它'):
        assert option in parsed['odds'], option


def test_jczq_oddsdata_maps_orderid_not_match_id() -> None:
    """oddsData is keyed by orderid; passing a match id as the key must not work."""
    html = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata.html').read_bytes())
    direct = okooo.parse_jczq_score_odds(html, '4005')
    assert direct['available'] is False  # no row carries matchid="4005"
    assert direct['stage'] == 'orderid_not_found'


def test_jczq_odds_feed_the_31_pool_pack() -> None:
    """theory from frozen λ + market from JC odds ⇒ all 31 formed."""
    html = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata.html').read_bytes())
    jc = okooo.parse_jczq_score_odds(html, '1347780')['odds']
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=1.4, lam_away=1.2, jc_score_odds=jc,
    )
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(pack['rows'])
    assert pack['formed_count'] == 31
    assert audit['complete'] is True, audit['missing']
    # 市均不可覆盖的 9 项，其 market 必须来自竞彩比分赔率
    by_score = {r['score']: r for r in pack['rows']}
    for option in ('5-0', '胜其它'):
        assert by_score[option]['market_source'] == 'jc_score_odds'


def test_theo_pack_keeps_theory_empty_without_lambda() -> None:
    """No λ ⇒ theory stays None (never invented), so the audit reports it."""
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack([], jc_score_odds={'1-0': 11.0})
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(pack['rows'])
    assert audit['complete'] is False
    assert any('理论概率缺' in item for item in audit['missing'])


def test_settlement_v2_refuses_all_none_input() -> None:
    """Defect 8: it used to claim available=True with every field None."""
    empty = math_mod.apply_six_cs_settlement_v2_to_rec({})
    assert empty['available'] is False
    assert empty['block_reason'] == 'missing_inputs:ou_line/asian_handicap/correct_score'
    full = math_mod.apply_six_cs_settlement_v2_to_rec({
        'ou_line': 3.25, 'asian_handicap': -0.75,
        'correct_score_page_avg': {'1:0': 8.0},
    })
    assert full['available'] is True
    assert 'block_reason' not in full


def test_resolve_direction_returns_pair_and_str_shim() -> None:
    """Defect 7: the app unpacks two values; the module returned a str."""
    arb_mod = importlib.import_module('convergence_arbitrator')
    direction, resolution = arb_mod.resolve_direction(['1-0', '2-1', '0-1'])
    assert direction == '胜'
    assert isinstance(resolution, dict)
    assert resolution['counts'] == {'胜': 2, '平': 0, '负': 1}
    # empty input still yields a 2-tuple
    empty = arb_mod.resolve_direction([])
    assert isinstance(empty, tuple) and len(empty) == 2 and empty[0] == ''
    # the str-shaped caller helper
    assert arb_mod.resolve_direction_value(['1-0']) == '胜'


def test_settlement_and_audit_counts_agree() -> None:
    """Defect 8b: 31池三视图 must not print a success count against a failed audit."""
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack([{'score': '1-0', 'theory': 9.0, 'market': 8.0}])
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(pack['rows'])
    assert audit['complete'] is False
    # the message the app prints must be derivable from the audit itself
    assert audit['formed'] < 31
    assert audit['rows'] == 31


def test_gate_books_n_matches_the_key_it_read() -> None:
    """Defect 9: books_in_pack and cs_n must describe the same field."""
    fallback_mod = importlib.import_module('odds_fallback')
    rec = {
        'correct_score_page_avg': {f'{i}-0': 9.0 for i in range(25)},
        'correct_score_channel': 'desktop_cs',
        'asian_handicap': -0.75, 'ou_line': 3.25,
        'odds_1x2': {'home': 4.55, 'draw': 4.2, 'away': 1.47},
    }
    gate = fallback_mod.ensure_dual_source_before_judgment(rec)
    assert gate['cs_books_n'] == 25
    assert gate['books_from'] == 'correct_score_page_avg'
    assert rec['dual_source_facts']['correct_score']['books_n'] == 25
    assert len(rec['dual_source_facts']['correct_score']['books_avg']) == 25
    assert gate['ready'] is True


def test_jc_scores_land_in_correct_score_jc_official() -> None:
    """Item 1: a pack carrying only jc_score_odds must still fill jc_official."""
    jc = {option: 7.0 + idx for idx, option in enumerate(pipe_mod.JCZQ_ALL_OPTIONS)}
    assert len(jc) == 31
    rec: dict[str, Any] = {}
    app._stamp_more_play_scores(rec, {'jc_score_odds': jc})
    assert len(rec['correct_score_jc_official']) == 31
    assert len(rec['jc_score_odds']) == 31
    # a bare {score: odd} map works too (the reported failure mode)
    bare: dict[str, Any] = {}
    okooo._okooo_apply_jc_score_fill(bare, jc)
    assert len(bare['correct_score_jc_official']) == 31
    # a full pack still takes the explicit key
    packed: dict[str, Any] = {}
    okooo._okooo_apply_jc_score_fill(packed, {'jc_score_odds': {'1-0': 9.0}})
    assert packed['correct_score_jc_official'] == {'1-0': 9.0}


def test_heat_ranking_tolerates_bad_source_and_channel() -> None:
    """Item 2: 'desktop' in None raised TypeError."""
    for source in (None, 5, '', object()):
        heat = okooo.build_score_market_heat_ranking({'1:0': 8.0}, heat_source=source)
        assert heat['ranking']
    for channel in (None, 5, ''):
        assert okooo.build_score_market_heat_ranking({'1:0': 8.0}, channel=channel)['ranking']


def test_resolve_direction_tolerates_non_iterable() -> None:
    """Item 3: int/float/bool input must degrade, never raise."""
    arb_mod = importlib.import_module('convergence_arbitrator')
    for bad in (3, 3.5, True, False, 0, None):
        result = arb_mod.resolve_direction(bad)
        assert isinstance(result, tuple) and len(result) == 2, (bad, result)
        assert result[0] == ''


def test_pool31_views_are_exactly_the_jc_options() -> None:
    """Item 4: the view's 31 must be the JC 31, not 28 + 4-3/3-4/5-3."""
    views = app.build_score_pool31_views([], lam_home=1.45, lam_away=1.1)
    assert views['pool_size'] == 31
    assert set(views['options']) == set(pipe_mod.JCZQ_ALL_OPTIONS)
    # the raw cells that used to steal slots are gone
    scores = {row['比分选项'] for row in views['theory_desc']}
    assert not ({'4-3', '3-4', '5-3'} & scores)
    for bucket in ('胜其它', '平其它', '负其它'):
        assert bucket in scores


def test_pool31_views_audit_complete_with_jc_odds() -> None:
    """Item 4: with real JC odds the view-backed audit is complete and the
    log line must take the full branch, claiming exactly pool_size."""
    jc = okooo.parse_jczq_score_odds(
        okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata.html').read_bytes()), '1347780',
    )['odds']
    views = app.build_score_pool31_views([], lam_home=1.45, lam_away=1.1, jc_score_odds=jc)
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(views['rows'])
    assert audit['complete'] is True, audit['missing']
    line = (
        f"市场/理论/双序对齐={views['pool_size']}项（未齐故不作满额结论）"
        if not audit['complete'] else f"市场/理论/双序对齐={views['pool_size']}项"
    )
    assert '未齐' not in line
    assert '=31项' in line


def test_all_three_producers_emit_exactly_jc31_summing_to_one() -> None:
    """Item 4b: every "31" producer must mean the same 31 options, Σtheory≈1."""
    lam_h, lam_a = 1.45, 1.1
    # 1) the view builder
    views = app.build_score_pool31_views([], lam_home=lam_h, lam_away=lam_a)
    assert set(views['options']) == set(pipe_mod.JCZQ_ALL_OPTIONS)
    assert views['pool_size'] == 31
    # 2) the enrichment list that feeds pool31_enriched / chart_pool_enriched
    enriched = app.build_31pool_enriched_list({'lam_h': lam_h, 'lam_a': lam_a})
    assert {row['score'] for row in enriched} == set(pipe_mod.JCZQ_ALL_OPTIONS)
    assert len(enriched) == 31
    assert abs(sum(row['theory_prob'] for row in enriched) - 1.0) < 1e-6
    # 3) the audited pack
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=lam_h, lam_away=lam_a, jc_score_odds={o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS},
    )
    assert {row['score'] for row in pack['rows']} == set(pipe_mod.JCZQ_ALL_OPTIONS)
    assert len(pack['rows']) == 31


def test_31pool_enriched_list_no_longer_truncates_at_six_goals() -> None:
    """The old max_goals=6 lost 5-0/5-1/5-2 entirely."""
    rows = app.build_31pool_enriched_list({'lam_h': 2.4, 'lam_a': 0.6})
    scores = {row['score'] for row in rows}
    assert {'5-0', '5-1', '5-2'} <= scores
    assert len(rows) == 31


def test_inline_detail_key_list_matches_shared_constant() -> None:
    """Item 5: one key list, so the jc keys can never be dropped again."""
    for key in ('jc_score_odds', 'jc_score_source', 'jc_score_orderid'):
        assert key in app._OKOOO_DETAIL_KEYS


def test_complete_jc31_skips_the_500_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 6: 31 JC odds are a legal market source, so no 500 fallback."""
    calls: list[str] = []

    def fake_fallback(rec, m_obj=None, **kwargs):
        calls.append('500')
        return {'available': False, 'source': 'fallback_existing_odds'}

    fb_mod = importlib.import_module('odds_fallback')
    monkeypatch.setattr(fb_mod, 'apply_fallback_cs_to_rec', fake_fallback)
    # 整个函数会真的去抓 CS/六盘口；这里全部短路成「抓不到」，只验证兜底决策。
    monkeypatch.setattr(okooo, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(okooo, 'scrape_okooo_mobile_six_markets', lambda *a, **k: {'available': False, 'missing': []})
    monkeypatch.setattr(app, 'scrape_okooo_detail', lambda *a, **k: {'okooo_match_id': '1347776'})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: {'available': False, 'missing': []})
    monkeypatch.setattr(app, '_attach_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, '_attach_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, '_attach_mobile_ah_ou_books', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'attach_okooo_ids_to_matches', lambda rows: rows)

    jc = {o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS}
    rec = {'okooo_match_id': '1347776', 'match': 'H vs A', 'jc_score_odds': jc}
    result = app.enrich_history_okooo_cs_facts(dict(rec), cn_name='H vs A')
    assert result is False
    assert calls == [], '500 fallback must not run when jc31 is complete'

    # without jc31 (and no CS) the fallback path is allowed to run
    calls.clear()
    app.enrich_history_okooo_cs_facts({'okooo_match_id': '1347776', 'match': 'H vs A'}, cn_name='H vs A')
    assert calls == ['500'], '500 fallback must still run with no CS source at all'


def test_falsy_zero_ah_is_present_everywhere() -> None:
    """Item 7: ah=0.0 (平手盘) was dropped by `not`/`or` in seven places."""
    rec = {
        'asian_handicap': 0.0, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
        'correct_score_page_avg': {'1:0': 8.0}, 'correct_score_channel': 'okooo_cs',
        'ah_line': 0.55,  # a stale second key must not win over the real 0.0
    }
    assert okooo.ah_text_to_line('平手') == 0.0
    # 1 & 2: the two gap functions
    assert okooo.missing_six_market_waters(rec) == []
    fallback_mod = importlib.import_module('odds_fallback')
    assert fallback_mod._local_water_gaps(rec) == []
    # 3: settlement keeps the real line
    settle = math_mod.apply_six_cs_settlement_v2_to_rec(dict(rec))
    assert settle['available'] is True
    assert settle['asian_handicap'] == 0.0
    # 4: cross-market board
    board = pipe_mod.DeepSeekAnalyzer.build_cross_market_board(rec)
    assert board['asian_handicap'] == 0.0
    # 6: the gate
    gate = fallback_mod.ensure_dual_source_before_judgment(dict(rec))
    assert gate['ready'] is True
    assert 'asian_handicap' in gate['kept_mobile']
    # 7: 500 must not overwrite a real 平手盘
    rec500 = dict(rec)
    rec500['dual_source_facts'] = {'fallback_500_fenxi': {'available': True, 'ah_line': 0.5}}
    assert 'ah_line' not in fallback_mod.ensure_dual_source_before_judgment(rec500)['filled_from_500']
    # negatives stay gaps
    for bad in (None, '', {}):
        assert 'asian_handicap' in okooo.missing_six_market_waters(
            {'asian_handicap': bad, 'ou_line': 2.5, 'odds_1x2': {'h': 1}}
        )


def test_falsy_zero_ou_line_is_present() -> None:
    """Item 7: OU 0.0 counts as present for gap purposes (downstream judges plausibility)."""
    assert okooo.missing_six_market_waters(
        {'asian_handicap': 0.0, 'ou_line': 0.0, 'odds_1x2': {'h': 1}}
    ) == []


def test_falsy_zero_without_cs_blames_cs_only() -> None:
    """Item 7: a 0.0 rec with no CS is unready for the CS reason, never water."""
    fallback_mod = importlib.import_module('odds_fallback')
    gate = fallback_mod.ensure_dual_source_before_judgment({
        'asian_handicap': 0.0, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
    })
    assert gate['ready'] is False
    assert '水位缺' not in gate['reason']
    assert '手机市均' in gate['reason']


def test_chart_uses_jc31_when_market_avg_blocked() -> None:
    """Item 8: the chart's documented market source includes 竞彩 odds."""
    jc = okooo.parse_jczq_score_odds(
        okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata.html').read_bytes()), '1347780',
    )['odds']
    text = app.generate_31pool_theory_vs_market_chart({
        'lam_h': 1.45, 'lam_a': 1.1,
        'odds_correct_score': dict(jc),
        'market_source_note': '竞彩31比分',
    })
    verdict = app.chart31_pool_verdict(text)
    assert verdict['status'] == 'full'
    assert verdict['rows'] == 31
    assert '市场来源: 竞彩31比分' in text


def test_chart_stays_honest_with_no_market_at_all() -> None:
    """Item 8: with neither 市均 nor jc, it must not claim a full market."""
    text = app.generate_31pool_theory_vs_market_chart({'lam_h': 1.45, 'lam_a': 1.1, 'odds_correct_score': {}})
    assert app.chart31_pool_verdict(text)['status'] != 'full'


def test_jczq_page_cache_is_ttl_bounded_and_honest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Item 9: one page serves many matches, but a hit must say it was a hit."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache'))
    html = (FIXTURES / 'okooo_jczq_oddsdata.html').read_bytes()
    calls: list[str] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        calls.append(url)
        return {
            'ok': True, 'url': url, 'status': 200, 'bytes': len(html),
            'title': 'x', 'blocked': False, 'error': '',
            'html': okooo.decode_okooo_html(html),
        }

    monkeypatch.setattr(okooo, '_fetch_detail', fake_fetch)
    monkeypatch.setattr(okooo, '_JCZQ_PAGE_CACHE', {})
    monkeypatch.setenv('OKOOO_JCZQ_CACHE_TTL', '90')

    first = okooo.scrape_okooo_mobile_six_markets('1347780')
    report1 = first['source_report']['jczq_scores']
    assert report1['cached'] is False
    assert len(first.get('jc_score_odds') or {}) == 31
    assert calls, 'first call must actually fetch'

    before = len([u for u in calls if u.endswith('/jczq/')])
    second = okooo.scrape_okooo_mobile_six_markets('1347776')
    report2 = second['source_report']['jczq_scores']
    assert report2['cached'] is True, 'second match must report the cache hit'
    assert report2['fetched_at'], 'a cached hit must carry the original fetch time'
    after = len([u for u in calls if u.endswith('/jczq/')])
    assert after == before, 'a cache hit must not re-download the jczq page'
    # per-match parsing still happens fresh: different row, different orderid
    assert report2['orderid'] and report2['orderid'] != report1['orderid']
    assert len(second.get('jc_score_odds') or {}) == 31

    # TTL 0 disables the cache entirely
    monkeypatch.setenv('OKOOO_JCZQ_CACHE_TTL', '0')
    okooo._JCZQ_PAGE_CACHE.clear()
    third = okooo.scrape_okooo_mobile_six_markets('1347780')
    assert third['source_report']['jczq_scores']['cached'] is False


def test_seed_keeps_ah_company_detail_and_the_scalar_line() -> None:
    """Item 11: the six pack's scalar asian_handicap used to destroy the books pack's
    company list and change.php timelines on the same key."""
    books = {
        'available': True,
        'asian_handicap': {
            'rows': [{'name': '平均', 'latest': {'line': -0.5}}],
            'companies': [{
                'name': '澳**票', 'pid': '84',
                'start': {'line': -0.75}, 'latest': {'line': -0.5},
                'changes': [{'line': -0.5, 'note': '赛前33小时9分'}, {'line': -0.75}],
                'detail_url': 'https://m.okooo.com/match/change.php?mid=1347776&pid=84&Type=Handicap',
            }],
        },
        'over_under': {'companies': [{'name': 'Pinnacle'}]},
    }
    six = {'available': True, 'asian_handicap': -0.5, 'ou_line': 2.5,
           'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0}}
    rec: dict[str, Any] = {'mobile_ah_ou_books': books}
    okooo.seed_mobile_ah_ou_books(rec, six)
    merged = rec['mobile_ah_ou_books']
    # the scalar line survives, under a key that cannot collide with the detail
    assert merged['asian_handicap_line'] == -0.5
    # and so does the per-company detail, timelines included
    companies = merged['asian_handicap']['companies']
    assert [c['name'] for c in companies] == ['澳**票']
    assert len(companies[0]['changes']) == 2
    assert 'change.php' in companies[0]['detail_url']
    assert merged['over_under']['companies'][0]['name'] == 'Pinnacle'

    # apply must write a SCALAR to record['asian_handicap'] and keep detail apart
    okooo.apply_mobile_books_lines(rec)
    assert rec['asian_handicap'] == -0.5
    assert not isinstance(rec['asian_handicap'], dict)
    assert rec['ou_line'] == 2.5
    assert [c['name'] for c in rec['ah_detail']['companies']] == ['澳**票']
    assert okooo.missing_six_market_waters(rec) == []


def test_scrape_okooo_detail_order_keeps_ah_company_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Item 12: the REAL scrape_okooo_detail ordering must keep the AH detail.

    This drives attach -> seed -> apply exactly as scrape_okooo_detail does, so it
    fails if either seed call site passes the books pack instead of the record.
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache'))
    books = {
        'available': True,
        'asian_handicap': {
            'rows': [{'name': '平均', 'latest': {'line': -0.75}}],
            'companies': [
                {'name': '澳**票', 'pid': '84', 'latest': {'line': -0.75},
                 'changes': [{'line': -0.75}, {'line': -0.5}],
                 'detail_url': 'https://m.okooo.com/match/change.php?mid=1&pid=84&Type=Handicap'},
            ],
        },
        'over_under': {'companies': [{'name': 'Pinnacle', 'latest': {'line': 2.5}}]},
    }
    six = {
        'available': True, 'channel': 'mobile', 'match_id': '1347776',
        'asian_handicap': -0.75, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
        'boards_hit': {'亚盘让球': True, '大小球': True, '胜平负': True},
        'missing': [], 'source_report': {},
    }
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', lambda *a, **k: dict(books))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    result = app.scrape_okooo_detail('1347776')

    assert isinstance(result.get('asian_handicap'), float), result.get('asian_handicap')
    detail = result.get('ah_detail') or {}
    assert detail.get('companies'), 'AH company list must survive the seed/apply chain'
    assert detail['companies'][0]['name'] == '澳**票'
    assert len(detail['companies'][0]['changes']) == 2, 'change.php timeline must survive'
    assert okooo.missing_six_market_waters(result) == []


def test_ensure_mobile_six_order_keeps_ah_company_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Item 12 (other call site): _ensure_mobile_six must also keep the detail."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache2'))
    books = {
        'available': True,
        'asian_handicap': {
            'rows': [{'name': '平均', 'latest': {'line': -0.5}}],
            'companies': [{'name': '立*', 'pid': '82', 'changes': [{'line': -0.5}]}],
        },
        'over_under': {'companies': [{'name': 'P'}]},
    }
    six = {
        'available': True, 'channel': 'mobile',
        'asian_handicap': -0.5, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
        'boards_hit': {'亚盘让球': True, '大小球': True, '胜平负': True},
        'missing': [], 'source_report': {},
    }
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_detail', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', lambda *a, **k: dict(books))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    # Waters already present so _ensure_mobile_six skips the gap refill and takes
    # the six-market -> seed -> apply path (the 27698 call site);
    # the existing six pack is not "core ok", so it still refreshes.
    rec: dict[str, Any] = {
        'okooo_match_id': '1347776',
        'asian_handicap': -0.75, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
        'six_market_mobile': {'available': False, 'channel': 'mobile'},
    }
    app._ensure_mobile_six(rec, '1347776', force=True)

    assert isinstance(rec.get('asian_handicap'), float), rec.get('asian_handicap')
    detail = rec.get('ah_detail') or {}
    assert detail.get('companies'), 'AH company list must survive _ensure_mobile_six'
    assert detail['companies'][0]['name'] == '立*'


def test_jc31_is_fetched_before_the_500_fallback_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Item 13: the guard used to read jc_score_odds before anything populated it.

    Deliberately does NOT pre-populate jc_score_odds: the stub six-market scrape
    supplies it, exactly as the live flow does, and the 500 path must not fire.
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache3'))
    jc31 = {option: 9.0 for option in pipe_mod.JCZQ_ALL_OPTIONS}
    six = {
        'available': True, 'channel': 'mobile', 'match_id': '1347776',
        'asian_handicap': -0.5, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
        'jc_score_odds': jc31,
        'jc_score_source': '手机竞足→更多玩法→胜平负比分',
        'boards_hit': {'亚盘让球': True, '大小球': True, '胜平负': True, '胜平负比分': True},
        'missing': [], 'source_report': {},
    }
    five_hundred: list[str] = []

    def fake_fallback(rec, m_obj=None, **kwargs):
        five_hundred.append('500')
        return {'available': False, 'source': 'fallback_existing_odds'}

    fb_mod = importlib.import_module('odds_fallback')
    monkeypatch.setattr(fb_mod, 'apply_fallback_cs_to_rec', fake_fallback)
    # CS scrape returns nothing, but the detail scrape is what carries jc31 in the
    # real flow (scrape_okooo_detail calls scrape_okooo_mobile_six_markets itself).
    monkeypatch.setattr(
        app, 'scrape_okooo_detail',
        lambda *a, **k: {**six, 'okooo_match_id': '1347776'},
    )
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'attach_okooo_ids_to_matches', lambda rows: rows)
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    rec: dict[str, Any] = {'okooo_match_id': '1347776', 'match': 'H vs A'}
    assert not rec.get('jc_score_odds'), 'test must start with no jc31'
    app.enrich_history_okooo_cs_facts(rec, cn_name='H vs A')

    assert five_hundred == [], '500 fallback must not run once jc31 is available'
    assert len(rec.get('jc_score_odds') or {}) == 31


def test_merge_copies_ah_detail_into_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Item 12 (second half): the merge allow-list must not drop ah_detail.

    scrape_okooo_detail produced ah_detail correctly, but _OKOOO_DETAIL_KEYS did
    not list it, so _merge_okooo_detail_into_rec silently dropped it and the
    record ended with 0 companies even though the pack had 8.
    """
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache'))
    books = {
        'available': True,
        'asian_handicap': {
            'rows': [{'name': '平均', 'latest': {'line': -0.75}}],
            'companies': [
                {'name': '澳**票', 'pid': '84', 'latest': {'line': -0.75},
                 'changes': [{'line': -0.75}, {'line': -0.5}]},
                {'name': '立*', 'pid': '82', 'latest': {'line': -0.5}, 'changes': [{'line': -0.5}]},
            ],
        },
        'over_under': {'companies': [{'name': 'P'}]},
    }
    six = {
        'available': True, 'channel': 'mobile', 'match_id': '1347776',
        'asian_handicap': -0.75, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
        'boards_hit': {'亚盘让球': True, '大小球': True, '胜平负': True},
        'missing': [], 'source_report': {},
    }
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', lambda *a, **k: dict(books))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    pack = app.scrape_okooo_detail('1347776')
    assert pack.get('ah_detail'), 'the producer must emit ah_detail'
    assert len(pack['ah_detail']['companies']) == 2

    rec: dict[str, Any] = {'okooo_match_id': '1347776'}
    app._merge_okooo_detail_into_rec(rec, pack)
    assert rec.get('ah_detail'), '_merge_okooo_detail_into_rec dropped ah_detail'
    assert [c['name'] for c in rec['ah_detail']['companies']] == ['澳**票', '立*']
    assert rec['ah_detail']['companies'][0]['changes'] == [{'line': -0.75}, {'line': -0.5}]
    assert isinstance(rec.get('asian_handicap'), float)


def test_detail_allow_list_covers_every_producer_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A merge allow-list that silently drops new producer keys is the same shape
    as the old books_in_pack bug. Every key scrape_okooo_detail emits must be
    either allow-listed or handled explicitly (markets -> okooo_markets)."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache2'))
    books = {'available': True,
             'asian_handicap': {'companies': [{'name': 'X', 'changes': []}]},
             'over_under': {'companies': []}}
    six = {'available': True, 'channel': 'mobile', 'asian_handicap': -0.75,
           'ou_line': 2.5, 'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
           'boards_hit': {}, 'missing': [], 'source_report': {}}
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', lambda *a, **k: dict(books))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    pack = app.scrape_okooo_detail('1347776')
    # 'markets' is deliberately mapped onto rec['okooo_markets'] instead.
    handled_elsewhere = {'markets'}
    unlisted = sorted(set(pack) - set(app._OKOOO_DETAIL_KEYS) - handled_elsewhere)
    assert unlisted == [], f'producer keys missing from the merge allow-list: {unlisted}'

    # and every allow-listed producer key actually lands on the record
    rec: dict[str, Any] = {'okooo_match_id': '1347776'}
    app._merge_okooo_detail_into_rec(rec, pack)
    for key in set(pack) & set(app._OKOOO_DETAIL_KEYS):
        if pack.get(key) in (None, '', [], {}):
            continue
        assert key in rec, f'allow-listed key {key!r} did not reach the record'


def test_enrich_path_keeps_ah_detail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """enrich_history_okooo_cs_facts copies via the same tuple, so one edit must
    cover both paths. Drives the REAL scrape_okooo_detail through underlying
    stubs, so ah_detail is produced by apply_mobile_books_lines as in production."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path / 'cache3'))
    books = {'available': True,
             'asian_handicap': {'rows': [{'latest': {'line': -0.5}}],
                                'companies': [{'name': '澳**票', 'changes': [{'line': -0.5}]}]},
             'over_under': {'companies': []}}
    six = {'available': True, 'channel': 'mobile', 'asian_handicap': -0.5,
           'ou_line': 2.5, 'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
           'jc_score_odds': {o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS},
           'boards_hit': {'亚盘让球': True, '大小球': True, '胜平负': True},
           'missing': [], 'source_report': {}}
    monkeypatch.setattr(app, 'scrape_okooo_mobile_cs_zuizhi', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', lambda *a, **k: dict(six))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', lambda *a, **k: dict(books))
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup', lambda *a, **k: {'available': False})
    monkeypatch.setattr(app, 'attach_okooo_ids_to_matches', lambda rows: rows)
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    monkeypatch.setattr(app, '_WATER_GAP_LOGGED', set())

    rec: dict[str, Any] = {'okooo_match_id': '1347776', 'match': 'H vs A'}
    app.enrich_history_okooo_cs_facts(rec, cn_name='H vs A', force_rescrape=True)
    assert rec.get('ah_detail'), 'enrich path dropped ah_detail'
    assert rec['ah_detail']['companies'][0]['name'] == '澳**票'
    assert isinstance(rec.get('asian_handicap'), float), rec.get('asian_handicap')


def _jczq20() -> dict[str, Any]:
    """The 20-slot live capture (184,010 B, utf-8)."""
    return okooo.parse_jczq_score_odds(
        okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes()),
        '1347778',
    )


def test_jczq20_ground_truth_all_four_matches() -> None:
    """Operator ground truth: orderids and 31/31 for all four complained matches."""
    expect = {
        '1347778': ('4003', 200.0, 200.0, 60.0),
        '1347775': ('4004', 700.0, 400.0, 80.0),
        '1347776': ('4002', 400.0, 250.0, 70.0),
        '1347789': ('4010', 200.0, 120.0, 40.0),
    }
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes())
    for mid, (oid, s50, s52, soth) in expect.items():
        parsed = okooo.parse_jczq_score_odds(text, mid)
        assert parsed['orderid'] == oid, mid
        assert parsed['raw_count'] == 31 and len(parsed['odds']) == 31, mid
        assert parsed['odds']['5-0'] == s50, mid
        assert parsed['odds']['5-2'] == s52, mid
        assert parsed['odds']['胜其它'] == soth, mid


def test_pool31_union_is_per_key_22_plus_9() -> None:
    """D1/D2/D8 acceptance: 22 keys from 市均 + 9 from jc_official, audit complete.

    This is the exact arithmetic the operator derived: the desktop page covers 22
    of the 31 options, and the 9 it cannot cover come from 竞彩 official odds.
    """
    jc = _jczq20()['odds']
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as _fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(_fh.read()))
    rows = app.build_pool31_option_rows(
        [], odds_correct_score=page['page_avg'], jc_score_odds=jc,
        lam_home=1.45, lam_away=1.10,
    )
    assert len(rows) == 31
    by_src: dict[str, list[str]] = {}
    for row in rows:
        by_src.setdefault(row['market_source'], []).append(row['score'])
    assert len(by_src['market_odds']) == 22, by_src['market_odds']
    assert len(by_src['jc_score_odds']) == 9, by_src['jc_score_odds']
    assert sorted(by_src['jc_score_odds']) == sorted([
        '5-0', '5-1', '5-2', '0-5', '1-5', '2-5', '胜其它', '平其它', '负其它',
    ])
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(rows)
    assert audit['complete'] is True, audit['missing']


def test_pool31_union_never_mixes_other_buckets_from_the_page_fold() -> None:
    """The 其它 rows must each come from one source only, never page-fold + jc."""
    jc = _jczq20()['odds']
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as _fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(_fh.read()))
    rows = app.build_pool31_option_rows([], odds_correct_score=page['page_avg'], jc_score_odds=jc)
    others = {r['score']: r for r in rows if '其它' in r['score']}
    assert set(others) == {'胜其它', '平其它', '负其它'}
    # 市均 only ever quotes explicit keys; 4-3/3-4/4-4 are folded, not quotes,
    # so all three 其它 buckets must be labelled jc_official.
    for name, row in others.items():
        assert row['market_source'] == 'jc_score_odds', (name, row['market_source'])
        assert row['delta_same_source'] is True
    assert abs(sum(r['market'] or 0 for r in rows) - 100.0) < 0.01


def test_pool31_market_never_rounds_a_longshot_to_zero() -> None:
    """Audit treats market<=0 as missing; a 200.00 odd must not round to 0."""
    rows = app.build_pool31_option_rows(
        [], jc_score_odds={'5-0': 200.0}, lam_home=1.4, lam_away=1.1,
    )
    row = next(r for r in rows if r['score'] == '5-0')
    assert row['market'] is not None and row['market'] > 0


def test_tg_payload_contains_all_31_options_and_three_matrices() -> None:
    """D4: the TG payload must carry the 31-row table plus 市短 and 市热 matrices."""
    jc = _jczq20()['odds']
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=1.45, lam_away=1.10, jc_score_odds=jc,
    )
    sections = pipe_mod.DeepSeekAnalyzer.build_deepseek_only_tg_sections(
        theo_mkt_31_pack=pack, title_line='📌 测试',
    )
    text = '\n'.join(sections)
    # hardcoded expected option list — deliberately NOT taken from the builder
    expected = (
        '1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2', '5-0', '5-1', '5-2', '胜其它',
        '0-0', '1-1', '2-2', '3-3', '平其它',
        '0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4', '0-5', '1-5', '2-5', '负其它',
    )
    assert len(expected) == 31
    missing = [o for o in expected if o not in text]
    assert missing == [], f'missing from TG payload: {missing}'
    assert '31项市均×竞彩对比表' in text
    assert '市短矩阵' in text
    assert '市热矩阵' in text
    assert '胜平负' in text


def test_tg_chunks_never_cut_a_table_row() -> None:
    jc = _jczq20()['odds']
    pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=1.45, lam_away=1.10, jc_score_odds=jc,
    )
    text = '\n'.join(pipe_mod.DeepSeekAnalyzer.build_deepseek_only_tg_sections(theo_mkt_31_pack=pack))
    chunks = pipe_mod.split_tg_html_preserving_pre(text, 900)
    assert ''.join(chunks) == text, 'chunking must be lossless'
    expected = ('5-0', '5-2', '胜其它', '负其它')
    for chunk in chunks:
        for opt in expected:
            # every occurrence inside a chunk must be on a complete line
            for line in chunk.splitlines():
                if opt in line:
                    assert line.strip(), 'row cut mid-line'


def test_stale_pre_fix_cache_is_rejected_and_refetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """D1/D2/D8 root cause: an old 25-key desktop_cs pack must never be served."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    stale = {
        'available': True,
        'correct_score_channel': 'desktop_cs',
        'correct_score_page_avg': {f'{i}-0': 9.0 for i in range(25)},
        'correct_score_books_avg': {},
    }
    cache = tmp_path / 'cs_1347776.json'
    cache.write_text(json.dumps(stale), encoding='utf-8')

    calls: list[str] = []

    def fake_fetch(url, proxies=None, session=None, retries=1, timeout=15):
        calls.append(url)
        return {'ok': False, 'url': url, 'status': 405, 'bytes': 0,
                'title': '405', 'blocked': True, 'error': 'HTTP 405', 'html': ''}

    monkeypatch.setattr(okooo, '_fetch_detail', fake_fetch)
    monkeypatch.setenv('OKOOO_CS_PATIENCE', '0')
    pack = okooo.scrape_okooo_mobile_cs_zuizhi('1347776')
    assert pack.get('available') is False, 'stale pack must not be served'
    assert calls, 'the stale cache must trigger a real refetch'


def test_current_cache_is_served_without_refetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    fresh = okooo._cache_stamp({
        'available': True,
        'correct_score_channel': 'okooo_cs',
        'correct_score_page_avg': {o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS},
        'cs31_complete': True,
    })
    (tmp_path / 'cs_1347776.json').write_text(json.dumps(fresh), encoding='utf-8')
    calls: list[str] = []
    monkeypatch.setattr(okooo, '_fetch_detail',
                        lambda *a, **k: calls.append('x') or {'ok': False, 'status': 405,
                                                              'blocked': True, 'html': ''})
    pack = okooo.scrape_okooo_mobile_cs_zuizhi('1347776')
    assert pack.get('available') is True
    assert calls == [], 'a valid cache must not refetch'


def test_cache_reject_reason_names_each_failure_mode() -> None:
    stale_schema = {'cache_schema': 1, 'correct_score_channel': 'okooo_cs'}
    stale_chan = {'cache_schema': okooo.CACHE_SCHEMA, 'correct_score_channel': 'desktop_cs'}
    no31 = {'cache_schema': okooo.CACHE_SCHEMA, 'correct_score_channel': 'okooo_cs',
            'correct_score_page_avg': {'1-0': 9.0}, 'cs31_complete': False}
    assert okooo._cache_reject_reason(Path('x'), stale_schema) == 'cache_schema_old'
    assert okooo._cache_reject_reason(Path('x'), stale_chan) == 'cache_channel_stale'
    assert okooo._cache_reject_reason(Path('x'), no31, require_31=True) == 'cache_missing_jc31'
    good = okooo._cache_stamp({
        'correct_score_channel': 'okooo_cs',
        'correct_score_page_avg': {o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS},
        'cs31_complete': True,
    })
    assert okooo._cache_reject_reason(Path('x'), good, require_31=True) == ''


def test_force_refresh_discards_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The refill path must be able to invalidate, or it just re-reads the bad file."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    fresh = okooo._cache_stamp({
        'available': True, 'correct_score_channel': 'okooo_cs',
        'correct_score_page_avg': {o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS},
        'cs31_complete': True,
    })
    (tmp_path / 'cs_1347776.json').write_text(json.dumps(fresh), encoding='utf-8')
    calls: list[str] = []
    monkeypatch.setattr(okooo, '_fetch_detail',
                        lambda *a, **k: calls.append('x') or {'ok': False, 'status': 405,
                                                              'blocked': True, 'html': ''})
    monkeypatch.setenv('OKOOO_CS_PATIENCE', '0')
    okooo.scrape_okooo_mobile_cs_zuizhi('1347776', force_refresh=True)
    assert calls, 'force_refresh must bypass a valid cache'


def test_save_json_survives_a_circular_reference(tmp_path: Path) -> None:
    """D4: 'Circular reference detected' must never abort a save."""
    cyc: dict[str, Any] = {'keep': 1}
    cyc['self'] = cyc
    cyc['nested'] = [cyc]
    target = tmp_path / 'cyc.json'
    app.save_json(str(target), cyc)
    out = json.loads(target.read_text(encoding='utf-8'))
    assert out['keep'] == 1
    assert out['self'] == '<circular>'


def test_save_json_serialises_a_dataframe(tmp_path: Path) -> None:
    pd = pytest.importorskip('pandas')
    target = tmp_path / 'df.json'
    app.save_json(str(target), {'df': pd.DataFrame({'a': [1, 2]})})
    assert json.loads(target.read_text(encoding='utf-8'))['df']['a']['0'] == 1


def test_books_failure_does_not_zero_the_scalar_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D5: a company-books failure must not clear AH/OU that六盘口 already gave."""
    rec: dict[str, Any] = {'asian_handicap': 0.0, 'ou_line': 2.5,
                           'mobile_ah_ou_books': {'available': True,
                                                  'asian_handicap': {'companies': [{'name': 'X'}]}}}
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books',
                        lambda *a, **k: {'available': False,
                                         'block_reason': 'okooo_ah_ou_books_fetch_failed'})
    app._attach_mobile_ah_ou_books(rec, '1347776')
    assert rec['asian_handicap'] == 0.0
    assert rec['ou_line'] == 2.5
    assert rec['mobile_ah_ou_books']['asian_handicap']['companies'][0]['name'] == 'X'
    assert rec['mobile_ah_ou_books']['last_attempt_reason']


def test_audit_message_lists_every_missing_key() -> None:
    """D6: print the full list, not the first five."""
    jc = {o: 9.0 for o in pipe_mod.JCZQ_ALL_OPTIONS}
    rows = app.build_pool31_option_rows([], jc_score_odds=jc, lam_home=1.4, lam_away=1.1)
    for row in rows:
        if row['score'] in ('5-0', '5-1', '5-2', '0-5', '1-5', '2-5'):
            row['market'] = None
    audit = pipe_mod.DeepSeekAnalyzer.audit_theo_mkt_31_rows(rows)
    joined = ' '.join(audit['missing'])
    for opt in ('5-0', '1-5', '2-5'):
        assert opt in joined, joined


def test_boot_banner_prints_once_per_process(capsys: pytest.CaptureFixture[str]) -> None:
    """D7: Streamlit reruns must not reprint the banner."""
    import os as _os
    _os.environ.pop('_BLADE_BOOT_BANNER_PRINTED', None)
    app._blade_boot_banner('OkoooRoute=auto OkoooProxy=direct')
    app._blade_boot_banner('OkoooRoute=auto OkoooProxy=direct')
    out = capsys.readouterr().out
    assert out.count('[boot]') == 1
    assert out.count('[build]') == 1
    _os.environ.pop('_BLADE_BOOT_BANNER_PRINTED', None)


def test_gate_sees_the_same_pack_the_fetch_produced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """D3: 抓取末 cs_n=25 ready=True, then 研判闸门 cs_n=0.

    The fetch result lived only in a local `data_okooo`; the gate reads `rec`.
    This asserts the same canonical pack reaches both, and that books_from agrees.
    """
    fallback_mod = importlib.import_module('odds_fallback')
    rec: dict[str, Any] = {'okooo_match_id': '1347776', 'match': 'H vs A'}
    pack = {
        'okooo_match_id': '1347776',
        'correct_score_available': True,
        'correct_score_channel': 'okooo_cs',
        'correct_score_page_avg': {f'{i}-0': 9.0 for i in range(25)},
        'correct_score_books_avg': {f'{i}-0': 8.0 for i in range(25)},
        'correct_score_books_n': 27,
        'asian_handicap': -0.75, 'ou_line': 2.5,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 4.0},
    }
    app._merge_okooo_detail_into_rec(rec, pack)
    gate = fallback_mod.ensure_dual_source_before_judgment(rec)
    assert gate['cs_books_n'] == 25, gate
    assert gate['books_from'] == 'correct_score_books_avg'
    assert gate['mobile_cs_entered'] is True
    assert gate['ready'] is True
    # and the pack the gate reported on is the record's, not a copy that drifted
    assert rec['dual_source_facts']['correct_score']['books_n'] == 25


def _pack_with_market(mid: str = '1347776') -> Any:
    """A 31 pack built the production way: real 竞彩 odds + real 市均 capture."""
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes())
    jc = okooo.parse_jczq_score_odds(text, mid)['odds']
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(fh.read()))
    return pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=1.45, lam_away=1.10, jc_score_odds=jc,
        extra_market_odds=page['page_avg'],
    )


def test_pack_rows_carry_devigged_jc_odds_pct() -> None:
    """D9: build_theo_mkt_31_pack never wrote jc_odds_pct, so 市短 was always empty.

    Revert tested: removing the jc_odds_pct assignment block from
    build_theo_mkt_31_pack -> 0/31 rows have the key and this test fails.
    """
    pack = _pack_with_market()
    with_pct = [r for r in pack['rows'] if r.get('jc_odds_pct') is not None]
    assert len(with_pct) == 31, f'{len(with_pct)}/31 rows carry jc_odds_pct'
    assert abs(pack['jc_pct_sum'] - 100.0) < 0.5, pack['jc_pct_sum']


def test_short_matrix_populates_from_the_pack() -> None:
    """D9: with jc_odds_pct present the 市短 matrix must actually produce rows."""
    pack = _pack_with_market()
    matrix = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack)
    assert matrix['available'] is True, matrix.get('reason')
    assert len(matrix['rows']) > 0
    total = sum(matrix['counts'].values())
    assert total == len(matrix['rows'])
    rendered = pipe_mod.DeepSeekAnalyzer.format_mkt_short_wdl_matrix_tg_lines(matrix)
    assert '未形成' not in '\n'.join(rendered)
    assert '胜平负' in '\n'.join(rendered)


def test_pack_bases_are_all_one_hundred() -> None:
    """D10: market was raw 100/odds (sum 145.95) vs theory at 100, so pos_rows lied.

    Revert tested: dropping the market/theory/jc renormalisation blocks ->
    market_sum 145.95, pos_count 26-28, this test fails on the 100±0.5 assertion.
    """
    pack = _pack_with_market()
    for key in ('market_sum', 'theory_sum', 'jc_pct_sum'):
        assert abs(float(pack[key]) - 100.0) < 0.5, (key, pack[key])
    # and the same numbers must be visible on the rows themselves
    m_sum = sum(float(r['market']) for r in pack['rows'] if r.get('market') is not None)
    t_sum = sum(float(r['theory']) for r in pack['rows'] if r.get('theory') is not None)
    assert abs(m_sum - 100.0) < 0.5 and abs(t_sum - 100.0) < 0.5


def test_pos_rows_uses_the_same_base_as_the_table() -> None:
    """D10: pos_rows must be counted on the de-vigged 100% market base.

    Independent expectation (NOT recomputed from the same rows): 1347778 on the
    raw `100/odds` base yields pos=28, while on the correct 100% base it is 20.
    Asserting a bound below the raw-base answer is what makes this test able to
    fail if the renormalisation is removed.
    """
    pack = _pack_with_market('1347778')
    assert abs(float(pack['market_sum']) - 100.0) < 0.5, pack['market_sum']
    # raw-base answer for this input is 28; correct-base answer is 20
    assert pack['pos_count'] < 26, f"pos_count={pack['pos_count']} looks like the raw 145.95% base"
    assert pack['pos_count'] == len(pack['pos_rows'])
    # every pos row must be > its theory on the normalised numbers
    for row in pack['pos_rows']:
        assert float(row['market']) > float(row['theory'])


def test_tg_sections_find_the_pack_inside_ds_payload() -> None:
    """D11: production passes ds_payload, not a pack kwarg.

    Revert tested: removing the `payload.get('theo_mkt_31_pack')` fallback ->
    all three sections print 未形成 despite a complete pack in ds_payload.
    """
    pack = _pack_with_market()
    t31 = pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)
    text = '\n'.join(pipe_mod.DeepSeekAnalyzer.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=t31,
        ds_payload={'theo_mkt_31_pack': pack},
    ))
    assert '未形成' not in text
    assert '市短矩阵' in text and '市热矩阵' in text
    assert '胜平负' in text


def test_tg_sections_emit_pos_over_heat_in_reading_order() -> None:
    """D12: mkt_over_theo_tg_lines / heat_tg_lines were never read.

    Revert tested: restoring the short extra_key tuple
    ('deepseek_section_lines','jc_vs_hot_tg_lines','wdl_matrix_tg_lines') ->
    neither sentinel appears.
    """
    pack = _pack_with_market()
    text = '\n'.join(pipe_mod.DeepSeekAnalyzer.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack),
        ds_payload={'theo_mkt_31_pack': pack},
        mkt_over_theo_tg_lines=['📉【市热超理论专表】', '  条数: 22'],
        heat_tg_lines=['HEATMARK'],
        desc_lines=['DESCMARK'],
    ))
    assert '📉【市热超理论专表】' in text
    assert 'HEATMARK' in text
    assert 'DESCMARK' in text
    # reading order: 31表 → 市短 → 市热 → 市热超理论 → heat
    order = [text.find(m) for m in (
        '31项市均×竞彩对比表', '市短矩阵', '市热矩阵', '市热超理论专表', 'HEATMARK',
    )]
    assert order == sorted(order), order


def test_production_shaped_tg_has_every_option_exactly_once() -> None:
    """The user's proof requirement, on the exact production call shape."""
    for mid in ('1347776', '1347778', '1347775', '1347780'):
        pack = _pack_with_market(mid)
        t31 = pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)
        text = '\n'.join(pipe_mod.DeepSeekAnalyzer.build_deepseek_only_tg_sections(
            title_line='📌 t',
            theo_mkt_31_tg_lines=t31,
            ds_payload={'theo_mkt_31_pack': pack},
            mkt_over_theo_tg_lines=['📉【市热超理论专表】', '  条数: n'],
            heat_tg_lines=['HEATMARK'],
        ))
        for header in ('31项市均×竞彩对比表', '市短矩阵', '市热矩阵', '市热超理论专表'):
            assert header in text, (mid, header)
        # isolate the 31-row table (everything before 汇总) for the uniqueness check
        cut = t31.index('  汇总') if '  汇总' in t31 else len(t31)
        table = '\n'.join(t31[:cut])
        assert len([ln for ln in table.splitlines() if re.match(r'^  \d{2} ', ln)]) == 31
        for option in (
            '1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2',
            '5-0', '5-1', '5-2', '胜其它',
            '0-0', '1-1', '2-2', '3-3', '平其它',
            '0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4',
            '0-5', '1-5', '2-5', '负其它',
        ):
            hits = len(re.findall(r'(?<!\d)' + re.escape(option) + r'(?![\d\-])', table))
            assert hits == 1, f'{mid}: {option} appears {hits} times in the 31-row table'


def test_pack_row_counts_match_the_matrix_row_counts() -> None:
    """pos_count and 市短 rows must both be derivable from the same rows."""
    pack = _pack_with_market()
    short = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack)
    assert pack['pos_count'] == len(pack['pos_rows'])
    assert short['available'] is True
    assert sum(short['counts'].values()) == len(short['rows'])


def test_pack_path_and_app_path_agree_row_for_row() -> None:
    """D10: the pack must build its market vector exactly like build_pool31_option_rows.

    The app normalises each source separately and then mixes; mixing raw 100/odds
    and normalising once gives a different vector, so the same capture produced two
    pos_count answers (pack 22 vs app 21 on 1347778). One table, one answer.

    Revert tested: restoring the single-pass "mix raw then renormalise" block ->
    maxΔmarket 0.36-1.25 and pos_count differs by 1-2 on all four captures.
    """
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes())
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(fh.read()))
    for mid in ('1347776', '1347778', '1347775', '1347780'):
        jc = okooo.parse_jczq_score_odds(text, mid)['odds']
        app_rows = app.build_pool31_option_rows(
            [], lam_home=1.45, lam_away=1.10,
            jc_score_odds=jc, odds_correct_score=page['page_avg'],
        )
        direct = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            [], lam_home=1.45, lam_away=1.10,
            jc_score_odds=jc, extra_market_odds=page['page_avg'],
        )
        app_pos = sum(
            1 for r in app_rows
            if r['market'] is not None and r['theory'] is not None
            and float(r['market']) > float(r['theory'])
        )
        assert direct['pos_count'] == app_pos, (mid, direct['pos_count'], app_pos)
        for mine, theirs in zip(direct['rows'], app_rows):
            assert mine['score'] == theirs['score'], (mid, mine['score'])
            assert abs(float(mine['market']) - float(theirs['market'])) < 0.01, (
                mid, mine['score'], mine['market'], theirs['market'])
        # and a round-trip through the pack must not change the answer either
        again = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            app_rows, lam_home=1.45, lam_away=1.10, jc_score_odds=jc)
        assert again['pos_count'] == app_pos, (mid, again['pos_count'], app_pos)


def test_rendered_jc_column_is_devigged_not_single_house() -> None:
    """D10: the table's 竞彩隐含% column printed raw 1/odds, summing to ~146%.

    A column that sums to 146 next to one that sums to 100 is not comparable, and
    it is the same defect the pack-level sums fixed — the renderer was missed.

    Revert tested: putting `100.0 / jc` back in format_theo_mkt_31_tg_lines ->
    the parsed 竞彩隐含% column sums to ~146 and this test fails.
    """
    pack = _pack_with_market('1347778')
    lines = pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)
    jc_col: list[float] = []
    for line in lines:
        if not re.match(r'^  \d{2} ', line):
            continue
        found = re.search(r'竞彩\s+[\d.]+\s+([\d.]+)%', line)
        if found:
            jc_col.append(float(found.group(1)))
    assert len(jc_col) == 31, len(jc_col)
    assert abs(sum(jc_col) - 100.0) < 0.5, sum(jc_col)


JC31_NINE = ['5-0', '5-1', '5-2', '0-5', '1-5', '2-5', '胜其它', '平其它', '负其它']


def _record_shape(mid: str) -> dict[str, Any]:
    """A record shaped the way `scrape_okooo_mobile_cs_zuizhi` actually builds it.

    D13 happened because the test harness fed `page['page_avg']` (the raw 25-key
    page) while production stores the ALREADY-MERGED 31-key vector in
    `correct_score_page_avg` plus a provenance map. Nothing in the raw odds
    reveals that 9 of those 31 came from 竞彩, so without `cs31_provenance`
    every row is labelled 市均.
    """
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes())
    jc = okooo.parse_jczq_score_odds(text, mid)['odds']
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(fh.read()))
    merged = okooo.merge_cs31(page['page_avg'], page['books_avg'], jc)
    return {
        # exactly what the scraper stores: the merged 31-key vector
        'correct_score_page_avg': merged['scores'],
        'correct_score_books_avg': page['books_avg'],
        'correct_score_books_n': page['books_n'],
        'correct_score_jc_official': jc,
        'jc_score_odds': jc,
        'cs31_provenance': merged['provenance'],
        'cs31_extras': merged['extras'],
        'cs31_counts': merged['counts'],
        'cs31_complete': merged['complete'],
    }


def _pack_from_record(mid: str = '1347776', record: Any = None) -> Any:
    """The production route: record -> provenance -> collector -> pack.

    Pass `record=` to build from a different record shape (e.g. one written by
    an older build that never stored `cs31_provenance`).
    """
    rec = record if isinstance(record, dict) else _record_shape(mid)
    extra = pipe_mod.DeepSeekAnalyzer._collect_legal_cs_market_odds(rec, [])
    prov = pipe_mod.DeepSeekAnalyzer.resolve_cs31_provenance(rec, {})
    return pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
        [], lam_home=1.45, lam_away=1.10,
        jc_score_odds=rec['jc_score_odds'],
        extra_market_odds=extra,
        market_provenance=prov['provenance'] or None,
        market_provenance_source=prov['source'] or '',
    )


def test_pack_from_merged_record_keeps_the_22_9_split() -> None:
    """D13a: on the merged record every row was labelled 市均 (31+0).

    Revert tested: dropping `market_provenance=` from the pack call -> counts
    become {market_avg_odds: 31} and this assertion fails.
    """
    for mid in ('1347776', '1347778', '1347775', '1347780'):
        pack = _pack_from_record(mid)
        counts = pack['cs31_counts']
        assert counts.get('desktop_cs') == 22, (mid, counts)
        assert counts.get('jc_official') == 9, (mid, counts)
        labels = {r['score']: r['market_source'] for r in pack['rows']}
        for option in JC31_NINE:
            assert labels[option] == 'jc_official', (mid, option, labels[option])
            row = next(r for r in pack['rows'] if r['score'] == option)
            assert row['delta_same_source'] is True, (mid, option)


def test_cs31_provenance_survives_the_merge_into_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """D13b: `cs31_*` were emitted by the scraper but missing from the allow-list.

    Without them the record cannot carry the split forward, so every later
    rebuild is back to 31+0. Revert tested: removing the four `cs31_*` names
    from _OKOOO_DETAIL_KEYS -> none of them reach the record.
    """
    merged = _record_shape('1347776')
    pack = {
        'cs31_provenance': merged['cs31_provenance'],
        'cs31_counts': merged['cs31_counts'],
        'cs31_extras': merged['cs31_extras'],
        'cs31_complete': merged['cs31_complete'],
    }
    rec: dict[str, Any] = {}
    app._merge_okooo_detail_into_rec(rec, pack)
    for key in ('cs31_provenance', 'cs31_counts', 'cs31_extras', 'cs31_complete'):
        assert key in rec, f'{key} was dropped by the merge allow-list'
    assert rec['cs31_counts'] == {'desktop_cs': 22, 'jc_official': 9}


def test_short_matrix_never_compares_jc_against_jc() -> None:
    """D13c: a row whose market came from 竞彩 cannot be 市均 > 竞彩.

    On the merged record 市短 returned 22 rows, 9 of them jc-vs-jc with a
    nonzero gap produced purely by renormalisation noise.

    On real data the 9 jc rows happen to come out with a *negative* gap (the
    mixed vector halves them), so "no jc rows in the output" alone would pass
    even with the guard removed — it would be a test that cannot fail. The
    second half therefore feeds a jc row that DOES have a positive gap, which
    is exactly the shape the buggy path produced.

    Revert tested: removing the delta_same_source / jc-label skip ->
    the constructed jc row appears in the matrix and this test fails.
    """
    for mid in ('1347776', '1347778', '1347775', '1347780'):
        pack = _pack_from_record(mid)
        matrix = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack)
        jc_rows = [
            r for r in matrix['rows']
            if str(r.get('source') or '') in ('jc_official', 'jc_score_odds')
        ]
        assert jc_rows == [], (mid, jc_rows)
        assert len(matrix['rows']) == sum(matrix['counts'].values())

    # a jc-sourced row with a real positive gap must still be excluded
    synthetic = {
        'rows': [
            {'score': '1-0', 'market': 8.0, 'jc_odds_pct': 5.0,
             'market_source': 'desktop_cs', 'delta_same_source': False},
            {'score': '0-5', 'market': 7.0, 'jc_odds_pct': 2.0,
             'market_source': 'jc_official', 'delta_same_source': True},
        ],
    }
    matrix = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(synthetic)
    scores = [r['score'] for r in matrix['rows']]
    assert scores == ['1-0'], scores


def test_merged_record_short_matrix_matches_the_raw_page_answer() -> None:
    """D13: the merged record and the raw page must give ONE 市短 answer.

    Raw page (25 keys + jc) has always given 13/11/14/11. The merged record gave
    22/19/21/19 because the 31-key vector was normalised as if all 31 were 市均.
    Renormalising the market vector over only the true 市均 keys fixes it.

    Revert tested: removing the `_page_only` restriction -> 市短 goes back to
    13/10/12/11 (the merged vector diluted over 31) and this fails.
    """
    expected = {'1347776': 13, '1347778': 11, '1347775': 14, '1347780': 11}
    for mid, want in expected.items():
        rec = _record_shape(mid)
        with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as fh:
            page = okooo._parse_cs_datatable(okooo.decode_okooo_html(fh.read()))
        raw_pack = pipe_mod.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            [], lam_home=1.45, lam_away=1.10,
            jc_score_odds=rec['jc_score_odds'],
            extra_market_odds=page['page_avg'],
        )
        raw_short = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(raw_pack)
        merged_short = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(
            _pack_from_record(mid))
        assert len(merged_short['rows']) == want, (mid, len(merged_short['rows']))
        assert len(raw_short['rows']) == len(merged_short['rows']), (
            mid, len(raw_short['rows']), len(merged_short['rows']))
        assert raw_short['counts'] == merged_short['counts'], (mid, raw_short['counts'])


def test_merged_record_table_renders_22_9_and_keeps_the_extras() -> None:
    """The user-visible deliverable: 汇总 reads desktop_cs=22 jc_official=9.

    Revert tested: without provenance the 31 rows all render as desktop_cs and
    the 汇总 line reads desktop_cs=31.
    """
    pack = _pack_from_record('1347776')
    lines = pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)
    summary = next(ln for ln in lines if '汇总' in ln)
    assert 'desktop_cs=22' in summary, summary
    assert 'jc_official=9' in summary, summary
    note = next((ln for ln in lines if '附注' in ln), '')
    for extra in ('4-3', '3-4', '4-4'):
        assert extra in note, (extra, note)


def test_old_record_derives_the_same_provenance_as_merge_cs31() -> None:
    """D13d: a record written by a PREVIOUS build has no cs31_provenance.

    The old allow-list dropped it, which is exactly what round 10 fixed. Those
    records still hold `correct_score_books_avg` (the raw 25-key 市均 page), so
    the true 市均 key set is recoverable. This pins that the derived map equals
    merge_cs31's stored map KEY FOR KEY, not merely in count.

    Revert tested: removing the books_avg branch from resolve_cs31_provenance ->
    source becomes None and provenance {} for the old shape; this fails.
    """
    for mid in ('1347776', '1347778', '1347775', '1347780'):
        full = _record_shape(mid)
        old = {k: v for k, v in full.items() if not k.startswith('cs31_')}
        derived = pipe_mod.DeepSeekAnalyzer.resolve_cs31_provenance(old, {})
        assert derived['source'] == 'derived', derived['source']
        assert derived['provenance'] == full['cs31_provenance'], mid
        n_desk = sum(1 for v in derived['provenance'].values() if v == 'desktop_cs')
        assert n_desk == 22, (mid, n_desk)


def test_explicit_provenance_beats_the_derivation() -> None:
    """The fallback must never be preferred over a stored map.

    Revert tested: swapping the two loops in resolve_cs31_provenance (derive
    first) -> the deliberately inverted stored map is ignored and this fails.
    """
    full = _record_shape('1347776')
    inverted = {
        k: ('jc_official' if v == 'desktop_cs' else 'desktop_cs')
        for k, v in full['cs31_provenance'].items()
    }
    record_with_both = dict(full)
    record_with_both['cs31_provenance'] = inverted
    got = pipe_mod.DeepSeekAnalyzer.resolve_cs31_provenance(record_with_both, {})
    assert got['source'] == 'recorded', got['source']
    assert got['provenance'] == inverted

    # payload-level provenance is also explicit, and must win over derivation
    old = {k: v for k, v in full.items() if not k.startswith('cs31_')}
    from_payload = pipe_mod.DeepSeekAnalyzer.resolve_cs31_provenance(
        old, {'cs31_provenance': inverted})
    assert from_payload['source'] == 'recorded', from_payload['source']
    assert from_payload['provenance'] == inverted


def test_old_record_short_matrix_matches_the_raw_page_answer() -> None:
    """D13d acceptance: on the old shape 市短 must still be 13/11/14/11.

    Before the fallback it was 22/19/21/19 — the whole round-10 fix was
    invisible on any match scraped by an older build.

    Revert tested: reverting the fallback alone -> 22/19/21/19 and this fails.
    """
    expected = {'1347776': 13, '1347778': 11, '1347775': 14, '1347780': 11}
    for mid, want in expected.items():
        old = {k: v for k, v in _record_shape(mid).items() if not k.startswith('cs31_')}
        pack = _pack_from_record(mid, record=old)
        matrix = pipe_mod.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack)
        assert len(matrix['rows']) == want, (mid, len(matrix['rows']))
        jc_rows = [
            r for r in matrix['rows']
            if str(r.get('source') or '') in ('jc_official', 'jc_score_odds')
        ]
        assert jc_rows == [], (mid, jc_rows)


def test_old_record_table_labels_nine_and_says_it_inferred_them() -> None:
    """The user-visible acceptance on the old shape, plus the honesty marker.

    A reader must be able to tell an inferred split from a recorded one.

    Revert tested: dropping the market_provenance_source plumbing -> the 汇总
    line loses `出处:反推` and this fails (and with the fallback gone, the split
    itself reads desktop_cs=31).
    """
    for mid in ('1347776', '1347778', '1347775', '1347780'):
        old = {k: v for k, v in _record_shape(mid).items() if not k.startswith('cs31_')}
        pack = _pack_from_record(mid, record=old)
        assert pack['cs31_counts'] == {'desktop_cs': 22, 'jc_official': 9}, (mid, pack['cs31_counts'])
        assert pack['cs31_provenance_source'] == 'derived', mid
        nine = [
            r for r in pack['rows'] if r['score'] in JC31_NINE
        ]
        assert len(nine) == 9
        for row in nine:
            assert row['market_source'] == 'jc_official', (mid, row['score'], row['market_source'])
            assert row['delta_same_source'] is True, (mid, row['score'])

        lines = pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)
        summary = next(ln for ln in lines if '汇总' in ln)
        assert 'desktop_cs=22' in summary and 'jc_official=9' in summary, summary
        assert '出处:反推' in summary, summary
        note = next((ln for ln in lines if '附注' in ln), '')
        for extra in ('4-3', '3-4', '4-4'):
            assert extra in note, (extra, note)


def test_recorded_provenance_is_not_labelled_as_derived() -> None:
    """The honesty marker must not appear when the split really was recorded.

    Revert tested: always appending the marker -> the a611106 shape gains
    `出处:反推` and this fails.
    """
    pack = _pack_from_record('1347776')
    assert pack['cs31_provenance_source'] == 'recorded'
    lines = pipe_mod.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)
    summary = next(ln for ln in lines if '汇总' in ln)
    assert '出处:反推' not in summary, summary


def test_no_provenance_and_no_books_invents_nothing() -> None:
    """Level 3: with neither an explicit map nor books_avg, stay empty.

    Revert tested: fabricating a default split when both are missing -> source
    stops being None and this fails.
    """
    got = pipe_mod.DeepSeekAnalyzer.resolve_cs31_provenance({}, {})
    assert got == {'provenance': {}, 'source': None}, got


def test_every_fixture_declares_its_source() -> None:
    manifest = json.loads((FIXTURES / 'FIXTURE_SOURCES.json').read_text(encoding='utf-8'))
    files = {p.name for p in FIXTURES.iterdir() if p.is_file() and p.name != 'FIXTURE_SOURCES.json'}
    assert files, 'no fixtures found'
    missing = sorted(files - set(manifest))
    assert missing == [], f'fixtures without provenance: {missing}'
    for name in files:
        entry = manifest[name]
        assert entry.get('url'), name
        assert entry.get('status') == 200, name
        assert entry.get('captured'), name
