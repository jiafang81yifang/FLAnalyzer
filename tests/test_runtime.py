from __future__ import annotations

import importlib
import os
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
    assert 'TOTAL 262  PASS 262  FAIL 0' in proc.stdout


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
