from __future__ import annotations

import importlib
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


def test_six_markets_prefers_mobile_and_records_every_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each board tries mobile first; a 405 on mobile falls through to desktop."""
    pages = {
        'handicap.php': ('okooo_mobile_ah.html', 200),
        'ah.php?type=daxiao': ('okooo_mobile_ou.html', 200),
        'match/odds.php': ('okooo_mobile_1x2.html', 200),
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
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(FIXTURES / '_cache_six'))
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
