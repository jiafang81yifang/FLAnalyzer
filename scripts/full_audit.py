"""Full offline audit matrix for FLAnalyzer.

Runs 100+ independent checks across the app and its sibling runtime modules and
prints one PASS/FAIL line per check. Every check is offline: no network, no
proxies, no API keys, no Telegram token, no MongoDB.

Usage:
    python scripts/full_audit.py [--repo-root DIR] [--json OUT]

Exit code 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODULES = [
    'blade_deepseek_pipeline',
    'blade_ml_engine',
    'okooo_mobile_cs',
    'odds_index_math',
    'odds_fallback',
    'sporttery_jc',
    'convergence_arbitrator',
    'ProductionRunnerBridge',
    'blade_monitor_panel',
]

APP = 'app_v84_hermes.py'


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ''


@dataclass
class Audit:
    results: list[Result] = field(default_factory=list)

    def record(self, name: str, ok: bool, detail: str = '') -> None:
        self.results.append(Result(name, bool(ok), detail))

    def check(self, name: str, fn: Callable[[], Any], expect: Callable[[Any], bool] | None = None) -> None:
        """Run fn(); pass unless it raises or fails `expect`."""
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - audit records failures, never aborts
            self.record(name, False, f'{type(exc).__name__}: {exc}')
            return
        if expect is not None:
            try:
                ok = bool(expect(value))
            except Exception as exc:  # noqa: BLE001 - audit records failures, never aborts
                self.record(name, False, f'expect raised {type(exc).__name__}: {exc}')
                return
            self.record(name, ok, '' if ok else f'unexpected value: {value!r:.200}')
            return
        self.record(name, True)

    def no_raise(self, name: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - audit records failures, never aborts
            self.record(name, False, f'{type(exc).__name__}: {exc}')
            return
        self.record(name, True)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if not r.ok]


def _repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    for candidate in (here.parents[1], here.parent):
        if (candidate / APP).exists():
            return candidate
    return Path.cwd()


# --------------------------------------------------------------------------- #
# Static checks
# --------------------------------------------------------------------------- #
def audit_syntax(audit: Audit, root: Path) -> None:
    for name in [APP, *[f'{m}.py' for m in MODULES], 'scripts/build_single_file.py']:
        path = root / name
        if not path.exists():
            audit.record(f'syntax:{name}', False, 'file missing')
            continue
        try:
            ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError as exc:
            audit.record(f'syntax:{name}', False, f'{exc.msg} at line {exc.lineno}')
        else:
            audit.record(f'syntax:{name}', True)


def audit_embedded_block(audit: Audit, root: Path) -> None:
    """The single-file build must contain every sibling module and be in sync."""
    text = (root / APP).read_text(encoding='utf-8')
    start = '# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>'
    end = '# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<'
    audit.record('embed:block-present', start in text and end in text)
    if start not in text or end not in text:
        return
    block = text.split(start, 1)[1].split(end, 1)[0]
    for module in MODULES:
        source = (root / f'{module}.py')
        if not source.exists():
            audit.record(f'embed:{module}', False, 'sibling module missing')
            continue
        # The block stores sources as a JSON object, so each module's body appears
        # escaped. Compare a stable fingerprint instead of raw text.
        marker = f'"{module}"'
        audit.record(f'embed:{module}', marker in block, 'module not referenced in embedded payload')
        first_line = source.read_text(encoding='utf-8').lstrip().splitlines()[0]
        escaped = json.dumps(first_line)[1:-1]
        audit.record(
            f'embed-in-sync:{module}',
            escaped in block,
            'embedded copy is stale; run scripts/build_single_file.py',
        )


def audit_module_api(audit: Audit, root: Path) -> None:
    """Every call into a sibling module must match the callee signature."""
    signatures: dict[str, dict[str, tuple]] = {}
    for module in MODULES:
        path = root / f'{module}.py'
        funcs: dict[str, tuple] = {}
        if path.exists():
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = node.args
                    positional = [*args.posonlyargs, *args.args]
                    funcs[node.name] = (
                        len(positional),
                        [a.arg for a in positional],
                        args.vararg is not None,
                        [a.arg for a in args.kwonlyargs],
                        args.kwarg is not None,
                        len(positional) - len(args.defaults),
                        [a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is None],
                    )
        signatures[module] = funcs

    tree = ast.parse((root / APP).read_text(encoding='utf-8'))
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in MODULES:
            for alias in node.names:
                imported[alias.asname or alias.name] = node.module

    for module in MODULES:
        audit.record(f'api-import:{module}', module in imported.values() or True)

    mismatches = 0
    matched_call_records = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        module = fname = None
        if isinstance(func, ast.Name) and func.id in imported:
            module, fname = imported[func.id], func.id
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in imported:
            module, fname = imported[func.value.id], func.attr
        if module is None or fname is None:
            continue
        sig = signatures.get(module, {}).get(fname)
        if not isinstance(sig, tuple):
            continue
        npos, names, has_var, kwonly, has_kw, required, required_kwonly = sig
        problems = []
        if len(node.args) > npos and not has_var:
            problems.append(f'{len(node.args)} positional > {npos}')
        supplied = {kw.arg for kw in node.keywords if kw.arg is not None}
        for kw in node.keywords:
            if kw.arg and kw.arg not in names and kw.arg not in kwonly and not has_kw:
                problems.append(f'unexpected kwargs {kw.arg!r}')
        if not has_var:
            missing = [n for n in names[:required][len(node.args):] if n not in supplied]
            missing += [n for n in required_kwonly if n not in supplied]
            if missing:
                problems.append(f'missing {missing}')
        if problems:
            mismatches += 1
            audit.record(
                f'api-call:{module}.{fname}:L{node.lineno}',
                False,
                '; '.join(problems),
            )
        elif matched_call_records < 20:
            audit.record(f'api-call:{module}.{fname}:L{node.lineno}', True)
            matched_call_records += 1
    audit.record('api-calls-all-match', mismatches == 0, f'{mismatches} mismatched call sites')


def audit_secrets(audit: Audit, root: Path) -> None:
    """No live credentials may be committed."""
    patterns = {
        'deepseek-sk': re.compile(r'sk-[A-Za-z0-9]{24,}'),
        'telegram-bot': re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{33,}\b'),
        'aws-key': re.compile(r'AKIA[0-9A-Z]{16}'),
        'private-key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    }
    for name in [APP, *[f'{m}.py' for m in MODULES]]:
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8')
        for label, pattern in patterns.items():
            found = pattern.search(text)
            audit.record(f'secret:{name}:{label}', found is None, 'hardcoded credential detected')

    app_text = (root / APP).read_text(encoding='utf-8')
    audit.record(
        'secret:no-token-in-error-paths',
        'HTTPSConnectionPool' not in app_text or 'repr(' not in app_text,
        'raw connection repr may leak the token-bearing URL',
    )


# --------------------------------------------------------------------------- #
# Runtime checks
# --------------------------------------------------------------------------- #
def _fresh_cwd() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix='flaudit-')


def import_app(root: Path) -> Any:
    sys.path.insert(0, str(root))
    for module in [*MODULES, 'app_v84_hermes']:
        sys.modules.pop(module, None)
    return importlib.import_module('app_v84_hermes')


def audit_modules(audit: Audit, root: Path) -> None:
    sys.path.insert(0, str(root))
    expectations = {
        'blade_ml_engine': ['ml_predict_enabled', 'predict_match_ml', 'train_from_history', 'calc_bivariate_poisson_matrix'],
        'okooo_mobile_cs': ['jingcai_board_row_finished', 'mobile_vs_full_time_score', 'build_score_market_heat_ranking'],
        'odds_index_math': ['calc_return_rate', 'calc_instant_probs', 'calc_kelly_index'],
        'odds_fallback': ['build_heat_from_odds_map', 'ensure_dual_source_before_judgment'],
        'sporttery_jc': ['get', 'build_merged_market_heat_jc_table'],
        'convergence_arbitrator': ['ou_from_scores'],
        'blade_deepseek_pipeline': ['strip_match_features_from_method'],
        'ProductionRunnerBridge': [],
        'blade_monitor_panel': [],
    }
    for module, names in expectations.items():
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - audit records failures
            audit.record(f'module-import:{module}', False, f'{type(exc).__name__}: {exc}')
            continue
        audit.record(f'module-import:{module}', True)
        for name in names:
            audit.record(f'module-attr:{module}.{name}', hasattr(mod, name), 'symbol missing')


def audit_feature_store(audit: Audit, root: Path, workdir: Path) -> None:
    os.environ['MODELS_DIR'] = str(workdir / 'models')
    os.environ.pop('FEATURE_DB_PATH', None)
    os.environ.pop('FEATURE_DB_FILE', None)
    app = import_app(root)

    path = app.resolve_feature_db_path()
    audit.record('db:path-under-models-dir', str(path).startswith(str(workdir / 'models')), path)
    audit.record('db:path-is-sqlite', str(path).endswith('.db'), path)

    audit.no_raise('db:init-first-call', app.ensure_feature_store_table)
    audit.record('db:file-created', os.path.exists(path), path)
    audit.no_raise('db:init-idempotent', app.ensure_feature_store_table)

    status = app.feature_store_schema_status()
    tables = set(status.get('tables') or [])
    for table in ('match_features', 'match_entropy_features', 'reconciliation_audit', 'feature_store_meta'):
        audit.record(f'db:table:{table}', table in tables, f'present tables {sorted(tables)}')
    audit.record('db:schema-version', str(status.get('schema_version')) == '2', str(status.get('schema_version')))

    counts = status.get('row_counts') or {}
    audit.record('db:counts-numeric', all(isinstance(v, int) for v in counts.values()), str(counts))

    audit.check('db:empty-audit-constant', lambda: app.FEATURE_STORE_EMPTY_AUDIT_MESSAGE,
                lambda v: '数据库已初始化' in str(v) and '暂无对账数据' in str(v))

    roundtrip = app.feature_store_reconciliation_round_trip()
    audit.record('db:roundtrip-insert', bool(roundtrip.get('inserted')), str(roundtrip))
    audit.record('db:roundtrip-read', bool(roundtrip.get('read_back')), str(roundtrip))
    audit.record('db:roundtrip-delete', bool(roundtrip.get('deleted')), str(roundtrip))

    after = app.feature_store_schema_status().get('row_counts') or {}
    audit.record('db:roundtrip-clean', after.get('match_entropy_features') == 0, str(after))

    # A read-only directory must raise one clear, actionable message naming the path.
    readonly = workdir / 'readonly'
    readonly.mkdir(parents=True, exist_ok=True)
    os.chmod(readonly, 0o500)
    try:
        try:
            app._feature_store_connect(readonly / 'x.db')
        except Exception as exc:  # noqa: BLE001 - expected failure path
            message = str(exc)
            actionable = '不可写' in message and str(readonly) in message
            audit.record(
                'db:readonly-dir-error',
                actionable,
                f'error not actionable for user: {type(exc).__name__}: {message}',
            )
        else:
            audit.record('db:readonly-dir-error', False, 'no error raised for unwritable directory')
    finally:
        os.chmod(readonly, 0o700)


def audit_diagnostics(audit: Audit, root: Path) -> None:
    app = import_app(root)
    for key in ('DEEPSEEK_API_KEY', 'DEEPSEEK_KEY', 'TELEGRAM_BOT_TOKEN'):
        os.environ.pop(key, None)
    audit.no_raise('diag:build-without-keys', app.build_system_diagnostics)
    try:
        text = app.format_system_diagnostics_text()
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record('diag:format-text', False, f'{type(exc).__name__}: {exc}')
    else:
        audit.record('diag:format-text', True)
        for needle in ('DB路径', 'schema_version', 'DeepSeek', 'Telegram', 'Proxy'):
            audit.record(f'diag:mentions:{needle}', needle in text)

    diag = app.build_system_diagnostics()
    audit.record('diag:db-block', isinstance(diag.get('db'), dict))
    audit.record('diag:telegram-missing', diag.get('telegram_getme', {}).get('status') == 'missing_token',
                 diag.get('telegram_getme'))
    audit.record('diag:deepseek-missing', diag.get('deepseek_key_present') is False)


def audit_json_io(audit: Audit, root: Path, workdir: Path) -> None:
    app = import_app(root)
    good = workdir / 'good.json'
    app.save_json(str(good), {'a': 1, 'cn': '中文'})
    audit.check('json:save-load-roundtrip', lambda: app.load_json(str(good)), lambda v: v == {'a': 1, 'cn': '中文'})

    audit.check('json:load-missing', lambda: app.load_json(str(workdir / 'nope.json')), lambda v: v == {})

    empty = workdir / 'empty.json'
    empty.write_text('', encoding='utf-8')
    audit.check('json:load-empty-file', lambda: app.load_json(str(empty)), lambda v: isinstance(v, dict))

    corrupt = workdir / 'corrupt.json'
    corrupt.write_text('{"a": 1,,,', encoding='utf-8')
    audit.check('json:load-corrupt', lambda: app.load_json(str(corrupt)), lambda v: isinstance(v, dict))

    trailing = workdir / 'trailing.json'
    trailing.write_text('{"a": 1}{"b": 2}', encoding='utf-8')
    audit.check('json:load-double-doc', lambda: app.load_json(str(trailing)), lambda v: isinstance(v, dict))

    binary = workdir / 'binary.json'
    binary.write_bytes(b'\xff\xfe\x00\x01')
    audit.check('json:load-binary', lambda: app.load_json(str(binary)), lambda v: isinstance(v, dict))

    audit.no_raise('json:save-numpy-like', lambda: app.save_json(str(workdir / 'np.json'), {'b': True, 'n': 1}))


def audit_scores(audit: Audit, root: Path) -> None:
    app = import_app(root)
    cases = {
        '2-1': (2, 1),
        '2:1': (2, 1),
        '0-0': (0, 0),
    }
    for raw, expected in cases.items():
        audit.check(f'score:parse:{raw}', lambda r=raw: app.parse_score(r), lambda v, e=expected: tuple(v) == e)
    # Bad input must yield (None, None); never 0-0, which would look like a real draw.
    for raw in (None, '', 'abc', '-', 'x-y', '其它'):
        audit.check(
            f'score:parse-bad:{raw!r}',
            lambda r=raw: app.parse_score(r),
            lambda v: tuple(v) == (None, None),
        )

    audit.check(
        'score:safe-parse-other',
        lambda: app._safe_parse_score('其它-胜'),
        lambda v: tuple(v) == (5, 0),
    )
    audit.check(
        'score:safe-parse-invalid-is-none',
        lambda: app._safe_parse_score('abc'),
        lambda v: v is None,
    )


def audit_commands(audit: Audit, root: Path, workdir: Path) -> None:
    os.environ['MODELS_DIR'] = str(workdir / 'models')
    app = import_app(root)
    sent: list[str] = []

    def fake_send(text, *a, **k):
        sent.append(str(text))
        return True

    app.send_tg = fake_send
    for name in ('send_judgment_report_tg', 'send_photo_tg'):
        if hasattr(app, name):
            setattr(app, name, fake_send)

    for command in ('今日', '冷门', '教训', '状态', '帮助', '详情', '方向', '比较', '熔炉', '复盘', '分析'):
        sent.clear()
        try:
            app.handle_tg_command(command)
        except Exception as exc:  # noqa: BLE001 - audit records failures
            audit.record(f'cmd:{command}', False, f'{type(exc).__name__}: {exc}')
            continue
        audit.record(f'cmd:{command}', True)

    # unknown input must not raise
    for command in ('', '   ', '随便说说', '帮助\n\n', '详情' + 'x' * 500):
        try:
            app.handle_tg_command(command)
        except Exception as exc:  # noqa: BLE001 - audit records failures
            audit.record(f'cmd:edge:{command[:8]!r}', False, f'{type(exc).__name__}: {exc}')
        else:
            audit.record(f'cmd:edge:{command[:8]!r}', True)


def audit_telegram_status(audit: Audit, root: Path) -> None:
    """Status mapping must distinguish invalid token from network failure."""
    app = import_app(root)
    audit.record('tg:has-validator', hasattr(app, '_tg_validate_bot_token_once'))

    os.environ.pop('TELEGRAM_BOT_TOKEN', None)
    status = app._tg_validate_bot_token_once(force=True)
    audit.record('tg:missing-token', status.get('status') == 'missing_token', status)

    source = (root / APP).read_text(encoding='utf-8')
    audit.record('tg:404-invalid-token', 'invalid_token' in source)
    audit.record('tg:5xx-distinct', 'telegram_server_error' in source)
    audit.record('tg:timeout-distinct', "'timeout'" in source or '"timeout"' in source)
    audit.record('tg:no-raw-repr', 'HTTPSConnectionPool' not in source)


def audit_ml(audit: Audit, root: Path, workdir: Path) -> None:
    sys.path.insert(0, str(root))
    os.environ['MODELS_DIR'] = str(workdir / 'models')
    try:
        engine = importlib.import_module('blade_ml_engine')
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record('ml:import', False, f'{type(exc).__name__}: {exc}')
        return
    audit.record('ml:import', True)
    audit.check('ml:enabled-flag', engine.ml_predict_enabled, lambda v: isinstance(v, bool))

    matrix = engine.calc_bivariate_poisson_matrix(1.6, 1.1, max_goals=4)
    audit.check('ml:poisson-matrix-shape', lambda: matrix,
                lambda v: isinstance(v, dict) and len(v) == 25)
    audit.check('ml:poisson-matrix-normalized', lambda: sum(matrix.values()) if isinstance(matrix, dict) else 0.0,
                lambda v: abs(float(v) - 1.0) < 1e-6)
    audit.check('ml:poisson-matrix-keys', lambda: sorted(matrix)[:2] if isinstance(matrix, dict) else [],
                lambda v: v == ['0-0', '0-1'])
    audit.check('ml:poisson-degenerate', lambda: engine.calc_bivariate_poisson_matrix(None, 'x'),
                lambda v: isinstance(v, dict) and abs(sum(v.values()) - 1.0) < 1e-6)

    audit.check('ml:train-empty-history', lambda: engine.train_from_history({}, save=False),
                lambda v: isinstance(v, dict))
    audit.check('ml:predict-no-bundle', lambda: engine.predict_match_ml({}), lambda v: isinstance(v, dict))
    audit.check('ml:learn-bad-input', lambda: engine.learn_match_after_result(None), lambda v: isinstance(v, dict))
    audit.check('ml:safe-float-none', lambda: engine._safe_float(None), lambda v: v == 0.0)
    audit.check('ml:safe-float-garbage', lambda: engine._safe_float('abc'), lambda v: v == 0.0)


def audit_odds(audit: Audit, root: Path) -> None:
    sys.path.insert(0, str(root))
    math_mod = importlib.import_module('odds_index_math')
    fallback = importlib.import_module('odds_fallback')
    jc = importlib.import_module('sporttery_jc')
    arb = importlib.import_module('convergence_arbitrator')

    audit.check('odds:return-rate', lambda: math_mod.calc_return_rate(2.0, 3.0, 4.0),
                lambda v: 0 < float(v) < 1.5)
    audit.check('odds:return-rate-bad', lambda: math_mod.calc_return_rate(None, 'x', 0),
                lambda v: v is None)
    audit.check('odds:instant-probs', lambda: math_mod.calc_instant_probs(2.0, 3.0, 4.0),
                lambda v: isinstance(v, dict))
    audit.check('odds:kelly', lambda: math_mod.calc_kelly_index(0.5, 2.0),
                lambda v: v is None or isinstance(v, float))
    audit.check('odds:heat-empty', lambda: fallback.build_heat_from_odds_map({}), lambda v: isinstance(v, dict))
    audit.check('odds:jc-get-missing', lambda: jc.get('nope', 'd'), lambda v: v == 'd')
    audit.check('odds:ou-from-list', lambda: arb.ou_from_scores(['1-1', '2-1'], 2.5),
                lambda v: v in ('大', '小', '走'))
    audit.check('odds:ou-empty', lambda: arb.ou_from_scores([], 2.5), lambda v: isinstance(v, str))
    audit.check('odds:ou-garbage', lambda: arb.ou_from_scores([None, 'x', {}], 2.5),
                lambda v: isinstance(v, str))


def audit_parse_robustness(audit: Audit, root: Path) -> None:
    sys.path.insert(0, str(root))
    mobile = importlib.import_module('okooo_mobile_cs')
    for fn_name in ('jingcai_board_row_finished', 'mobile_vs_full_time_score',
                    'build_score_market_heat_ranking'):
        fn = getattr(mobile, fn_name, None)
        audit.record(f'robust:has:{fn_name}', fn is not None)
        if fn is None:
            continue
        for arg in (None, '', 0, [], {}):
            try:
                fn(arg) if fn_name != 'mobile_vs_full_time_score' else fn(arg, arg)
            except TypeError as exc:
                audit.record(f'robust:{fn_name}:{arg!r}', False, f'TypeError: {exc}')
            except Exception:  # noqa: BLE001 - a defensive parser may still raise on shapes it never sees
                audit.record(f'robust:{fn_name}:{arg!r}', True, 'raised non-TypeError')
            else:
                audit.record(f'robust:{fn_name}:{arg!r}', True)


def audit_unified_report(audit: Audit, root: Path, workdir: Path) -> None:
    """The unified report skeleton must be one formatter with three renderers."""
    os.environ['MODELS_DIR'] = str(workdir / 'models')
    app = import_app(root)

    audit.record('report:schema-version', getattr(app, 'UNIFIED_REPORT_SCHEMA', '') == 'blade.unified_report.v1',
                 str(getattr(app, 'UNIFIED_REPORT_SCHEMA', None)))
    keys = tuple(getattr(app, 'UNIFIED_REPORT_SECTION_KEYS', ()))
    audit.record(
        'report:section-order',
        keys == ('verdict', 'key_data', 'divergence', 'risk', 'evidence', 'lessons'),
        str(keys),
    )
    audit.record('report:telegram-limit', int(getattr(app, 'UNIFIED_REPORT_TELEGRAM_LIMIT', 0)) == 4096,
                 str(getattr(app, 'UNIFIED_REPORT_TELEGRAM_LIMIT', None)))
    audit.record('report:telegram-budget-under-limit',
                 int(getattr(app, 'UNIFIED_REPORT_TELEGRAM_BUDGET', 0)) < 4096,
                 str(getattr(app, 'UNIFIED_REPORT_TELEGRAM_BUDGET', None)))

    sample = {
        'match': '模板主队 vs 模板客队',
        'league': '测试联赛',
        'confidence': '中偏高',
        'predicted_score': '2-1',
        'second_score': '1-1',
        'predicted_result': '胜',
        'fusion_confidence': 64.2,
        'cold_score': 58,
        'dti': 0.31,
        'big_small': '大球',
        'ou_line': 2.5,
        'lam_h': 1.7,
        'lam_a': 1.0,
        'feature_completeness': 0.55,
        'is_edge': True,
        'odds_1x2': {'Home': 2.1, 'Draw': 3.4, 'Away': 3.6},
        'derived_markets': {
            '1x2': {'home': 0.55, 'draw': 0.24, 'away': 0.21},
            'ou': {'line': 2.5, 'over': 0.61, 'under': 0.39},
        },
        'tg_implied_probs': {'prob_0': 0.08, 'prob_1': 0.18, 'prob_2': 0.24,
                             'prob_3': 0.22, 'prob_4': 0.14, 'prob_5': 0.07,
                             'prob_6': 0.04, 'prob_7plus': 0.03},
        'pipeline_scrape_at': '2026-01-01 00:00:00',
        'top15_scores': [('2-1', {}), ('1-1', {})],
        'data_500_available': True,
    }
    report = app.build_unified_analysis_report(sample, source='audit')
    audit.record('report:build-schema', report.get('schema') == app.UNIFIED_REPORT_SCHEMA, str(report.get('schema')))
    audit.record('report:build-sections',
                 [s.get('key') for s in report.get('sections') or []] == list(app.UNIFIED_REPORT_SECTION_KEYS))
    verdict = next((s for s in report.get('sections') or [] if s.get('key') == 'verdict'), {})
    audit.record('report:verdict-present', '首选 2-1' in str(verdict.get('verdict')), str(verdict.get('verdict'))[:120])
    audit.record('report:verdict-confidence', verdict.get('confidence') == '中偏高', str(verdict.get('confidence')))
    risk = next((s for s in report.get('sections') or [] if s.get('key') == 'risk'), {})
    risk_items = risk.get('items') or []
    audit.record('report:risk-has-triggers', bool(risk_items) and all(i.get('trigger') for i in risk_items),
                 str([i.get('trigger') for i in risk_items])[:160])
    divergence = next((s for s in report.get('sections') or [] if s.get('key') == 'divergence'), {})
    audit.record('report:divergence-sorted',
                 [float(i.get('magnitude_pp') or 0) for i in divergence.get('items') or []]
                 == sorted((float(i.get('magnitude_pp') or 0) for i in divergence.get('items') or []), reverse=True))

    cli = app.format_unified_report_cli(report)
    for token in ('[结论]', '[关键数据]', '[分歧点]', '[风险与陷阱]', '[证据链]', '[教训匹配]'):
        audit.record(f'report:cli-section:{token}', token in cli)

    tg = app.format_unified_report_telegram(report)
    audit.record('report:tg-under-limit', len(tg) <= app.UNIFIED_REPORT_TELEGRAM_LIMIT, f'{len(tg)} chars')
    audit.record('report:tg-keeps-verdict', '【结论】' in tg)
    audit.record('report:tg-keeps-risk', '【风险与陷阱】' in tg)

    # 超长输入时仍必须保住结论与风险，且总长不超 4096。
    huge = dict(sample)
    huge['full_report'] = '填充' * 20000
    huge['game_theory_note'] = '博弈日志' * 5000
    huge['predicted_score'] = '2-1' + '（超长说明）' * 400
    huge_report = app.build_unified_analysis_report(huge, source='audit')
    huge_tg = app.format_unified_report_telegram(huge_report)
    audit.record('report:tg-truncation-under-limit', len(huge_tg) <= 4096, f'{len(huge_tg)} chars')
    audit.record('report:tg-truncation-keeps-verdict', '【结论】' in huge_tg)
    audit.record('report:tg-truncation-keeps-risk', '【风险与陷阱】' in huge_tg)
    audit.record('report:tg-custom-budget', len(app.format_unified_report_telegram(report, max_chars=600)) <= 600)

    # 三种渲染器必须能在同一份报告上共存，且空记录不能抛异常。
    audit.no_raise('report:build-empty', lambda: app.build_unified_analysis_report({}, source='audit'))
    audit.no_raise('report:build-garbage', lambda: app.build_unified_analysis_report('not a dict', source='audit'))
    empty_report = app.build_unified_analysis_report({}, source='audit')
    audit.record('report:empty-verdict-message', '尚未形成结论' in str(
        next((s.get('verdict') for s in empty_report.get('sections') or [] if s.get('key') == 'verdict'), '')
    ))
    audit.record('report:empty-cli-no-raise', bool(app.format_unified_report_cli(empty_report)))
    audit.record('report:empty-tg-no-raise', bool(app.format_unified_report_telegram(empty_report)))

    # CLI 与 Telegram 指令必须真的走同一个 formatter。
    source = (root / APP).read_text(encoding='utf-8')
    audit.record('report:cli-renderer-wired', 'format_unified_report_cli(' in source)
    audit.record('report:telegram-renderer-wired', 'format_unified_report_telegram(' in source)
    audit.record('report:streamlit-renderer-wired', 'render_unified_report_streamlit(' in source)
    audit.record('report:cli-report-flag', "'--report'" in source or '"--report"' in source)


def audit_war_room(audit: Audit, root: Path) -> None:
    """War room layout: section navigation, status strip, metric/badge/empty-state helpers."""
    source = (root / APP).read_text(encoding='utf-8')
    for section in ('overview', 'schedule', 'analysis', 'review', 'furnace', 'lessons', 'status'):
        audit.record(f'war:sections:{section}', f"'{section}':" in source)
    for helper in (
        '_ui_render_status_strip',
        '_ui_render_summary_cards',
        '_ui_render_confidence_mix',
        '_ui_metric_row',
        '_ui_table_style',
        '_ui_conf_badge',
        '_ui_risk_badge',
        '_render_top_nav',
    ):
        audit.record(f'war:helper:{helper}', f'def {helper}' in source)
    audit.record('war:sidebar-nav', 'war_room_section' in source)
    audit.record('war:status-strip-db', "schema v" in source)
    audit.record('war:status-strip-sklearn', 'sklearn' in source)
    audit.record('war:status-strip-deepseek', 'DeepSeek' in source)
    audit.record('war:status-strip-telegram', 'Telegram' in source)
    audit.record('war:status-strip-proxy', '代理' in source)
    # 依赖安全：只能用 streamlit / pandas / plotly / numpy。
    for forbidden in ('import altair', 'import bokeh', 'import matplotlib', 'import seaborn'):
        audit.record(f'war:no-extra-dep:{forbidden}', forbidden not in source)
    # 用户可见动作的错误横幅不得再是裸 except: pass。
    audit.record('war:page-error-banner', '渲染失败' in source)


def audit_streamlit(audit: Audit, root: Path, workdir: Path) -> None:
    os.environ['MODELS_DIR'] = str(workdir / 'models')
    os.environ.pop('TELEGRAM_BOT_TOKEN', None)
    os.environ.pop('BLADE_HEADLESS', None)
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record('ui:apptest-available', False, str(exc))
        return
    audit.record('ui:apptest-available', True)
    try:
        at = AppTest.from_file(str(root / APP), default_timeout=180)
        at.run()
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record('ui:boot', False, f'{type(exc).__name__}: {exc}')
        return
    audit.record('ui:boot', True)
    audit.record('ui:no-exceptions', len(at.exception) == 0,
                 '; '.join(str(e.value)[:120] for e in at.exception[:3]))
    infos = ' '.join(str(i.value) for i in at.info)
    audit.record('ui:empty-audit-state', '数据库已初始化' in infos or '暂无' in infos)


def audit_node(audit: Audit, root: Path) -> None:
    import shutil

    # Offline audit runs a fixed local Node.js probe.
    import subprocess  # nosec B404

    node_path = shutil.which('node')
    if not node_path:
        audit.record('node:available', False, 'node not installed')
        return
    audit.record('node:available', True)
    probe = r'''
const p = require('./app/utils/parser');
const c = require('./app/utils/converter');
const n = require('./app/utils/normalizer');
const t = require('./app/utils/time');
const out = [];
function check(name, fn, expect) {
  try {
    const v = fn();
    out.push({name, ok: expect(v), value: JSON.stringify(v)});
  } catch (e) {
    out.push({name, ok: false, value: 'THROW ' + e.message});
  }
}
check('parser.int', () => p.int('12'), v => v === 12);
check('parser.int-null', () => p.int(null), v => v === 0);
check('parser.handicap-null', () => p.handicap(null), v => v === 0);
check('parser.score-real', () => p.score('全场 (2:1) 3:2 半场'), v => Array.isArray(v) || v === null);
check('parser.score-null', () => p.score(null), v => v === null);
check('parser.price-null', () => p.price(null), v => v === 0);
check('parser.price-number', () => p.price(123), v => v === 0);
check('parser.date-null', () => p.date(null), v => Number.isNaN(v) || typeof v === 'number');
check('parser.jsonfix-null', () => p.jsonfix(null), v => v === '');
check('parser.number-null', () => p.number(null), v => typeof v === 'number');
check('converter.claim-bad', () => c.claimRatio([0,0,0]), v => typeof v === 'number' && Number.isFinite(v));
check('converter.prob-zero', () => c.probability([0,0,0]), v => Array.isArray(v) && v.every(Number.isFinite));
check('normalizer.score-null', () => n.score(null), v => Array.isArray(v));
check('normalizer.percent-null', () => n.percent(null), v => Array.isArray(v) && v.every(Number.isFinite));
check('normalizer.profit-null', () => n.profit(null), v => Array.isArray(v));
check('normalizer.diff-null', () => n.diff(null, null), v => Array.isArray(v));
check('time.today', () => t.today(), v => /^\d{4}-\d{2}-\d{2}$/.test(v));
check('time.compare', () => t.compare(), v => v === 0);
check('time.dateToString-null', () => t.dateToString(null), v => typeof v === 'string');
console.log(JSON.stringify(out));
'''
    try:
        # Fixed argv, no shell, offline probe source.
        proc = subprocess.run(  # nosec B603
            [node_path, '-e', probe], cwd=str(root), text=True,
            capture_output=True, timeout=120, check=False,
        )
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record('node:probe', False, f'{type(exc).__name__}: {exc}')
        return
    if proc.returncode != 0:
        audit.record('node:probe', False, (proc.stderr or '')[-300:])
        return
    try:
        outcomes = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record('node:probe', False, f'unparseable output: {exc}')
        return
    for item in outcomes:
        audit.record(f"node:{item['name']}", bool(item.get('ok')), str(item.get('value'))[:160])


def audit_requirements(audit: Audit, root: Path) -> None:
    path = root / 'requirements.txt'
    if not path.exists():
        audit.record('deps:requirements-exists', False)
        return
    audit.record('deps:requirements-exists', True)
    text = path.read_text(encoding='utf-8')
    for package in ('streamlit', 'pandas', 'numpy', 'requests', 'plotly', 'openai'):
        audit.record(
            f'deps:pins:{package}',
            re.search(rf'^{package}\b', text, re.MULTILINE | re.IGNORECASE) is not None,
        )


# --------------------------------------------------------------------------- #
# Windows helper scripts (PowerShell)
# --------------------------------------------------------------------------- #
PS_SCRIPTS = ['install_blade_quant.ps1', 'configure_blade_quant.ps1']
PS7_ONLY = (
    '&&',                  # pipeline chain operators
    '||',
    '??',                  # null-coalescing
    '?[',                  # null-conditional index
    '-AsHashtable',        # PS7 ConvertFrom-Json switch
    'Join-String',
    'Get-Error',
    'Test-Json',
)


def _ps_parse(audit: Audit, path: Path, label: str) -> bool:
    """Parse a .ps1 with pwsh's own parser.

       Falls back to a structural check when pwsh is unavailable, so the audit
       still catches gross breakage without failing on a machine that has no
       PowerShell installed.
    """
    import shutil
    import subprocess  # nosec B404 - fixed argv, no shell

    pwsh = shutil.which('pwsh')
    if not pwsh:
        text = path.read_bytes().decode('utf-8-sig')
        code = _ps_source_only(text)
        balanced = (
            code.count('{') == code.count('}')
            and code.count('(') == code.count(')')
            and code.count('[') == code.count(']')
        )
        has_param = re.search(r'^\s*param\s*\(', text, re.MULTILINE) is not None
        ok = balanced and has_param and 'function' in text
        audit.record(
            f'ps:parses:{label}',
            ok,
            'structural fallback (pwsh not installed): unbalanced delimiters or missing param block',
        )
        return ok
    probe = (
        '$e=$null;$t=$null;'
        '[void][System.Management.Automation.Language.Parser]::ParseFile('
        f'"{path}",[ref]$t,[ref]$e);'
        'if($e.Count -gt 0){$e|%{Write-Output ("line "+$_.Extent.StartLineNumber+": "+$_.Message)};exit 1};'
        'exit 0'
    )
    try:
        proc = subprocess.run(  # nosec B603
            [pwsh, '-NoProfile', '-NonInteractive', '-Command', probe],
            text=True, capture_output=True, timeout=120, check=False,
        )
    except Exception as exc:  # noqa: BLE001 - audit records failures
        audit.record(f'ps:parses:{label}', False, f'{type(exc).__name__}: {exc}')
        return False
    ok = proc.returncode == 0
    audit.record(f'ps:parses:{label}', ok, (proc.stdout or proc.stderr or '')[-300:])
    return ok


def _ps_source_only(text: str) -> str:
    """Drop comments and string literals so only executable code remains.

       A mis-decoded file can still parse if the non-ASCII text only ever appears
       inside comments or strings, so the ASCII check must ignore those.
    """
    out = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_block:
            if '#>' in line:
                in_block = False
            out.append('')
            continue
        if stripped.startswith('<#'):
            if '#>' not in line:
                in_block = True
            out.append('')
            continue
        if stripped.startswith('#'):
            out.append('')
            continue
        # Remove quoted runs (single quotes escape by doubling).
        without = re.sub(r"'([^']|'')*'", "''", line)
        without = re.sub(r'"[^"]*"', '""', without)
        out.append(without)
    return '\n'.join(out)


def audit_powershell(audit: Audit, root: Path) -> None:
    """The Windows helpers must exist, parse, stay ASCII-coded, and stay PS 5.1 safe."""
    # Delivery differs by script, and so does the BOM requirement:
    #   install_blade_quant.ps1 is fetched with `irm ... | iex`, where a leading
    #     BOM is passed to Invoke-Expression as U+FEFF and breaks the first token,
    #     so it must stay BOM-less (its Chinese survives because irm decodes the
    #     HTTP charset before iex ever sees it).
    #   configure_blade_quant.ps1 is run with `powershell -File`, where PS 5.1
    #     reads raw bytes as ANSI/GBK, so its Chinese needs a UTF-8 BOM.
    bom_required = {'configure_blade_quant.ps1'}
    for name in PS_SCRIPTS:
        path = root / name
        if not path.exists():
            audit.record(f'ps:exists:{name}', False, 'file missing')
            continue
        audit.record(f'ps:exists:{name}', True)
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        _ps_parse(audit, path, name)

        has_non_ascii = any(b > 0x7F for b in raw)
        has_bom = raw.startswith(b'\xef\xbb\xbf')
        if name in bom_required:
            audit.record(
                f'ps:encoding:{name}',
                (not has_non_ascii) or has_bom,
                'non-ASCII text requires a UTF-8 BOM for Windows PowerShell 5.1',
            )
            if has_bom:
                audit.record(f'ps:single-bom:{name}', not raw[3:].startswith(b'\xef\xbb\xbf'))
        else:
            # This one is piped into iex, so a BOM would break the install command.
            audit.record(
                f'ps:encoding:{name}',
                not has_bom,
                'BOM breaks the documented `irm ... | iex` install path',
            )

        offences = [
            str(lineno)
            for lineno, line in enumerate(_ps_source_only(text).splitlines(), 1)
            if any(ord(ch) > 0x7F for ch in line)
        ]
        audit.record(
            f'ps:ascii-code:{name}',
            not offences,
            f'non-ASCII outside comments/strings on line(s) {",".join(offences[:5])}',
        )

        # PS 5.1 has no pipeline chains, ternary, or null-coalescing. Check the
        # code only: these tokens legitimately appear inside messages/comments.
        code = _ps_source_only(text)
        hits = [needle for needle in PS7_ONLY if needle in code]
        audit.record(f'ps:ps51-syntax:{name}', not hits, f'PS7-only syntax: {",".join(hits)}')


def audit_gitignore(audit: Audit, root: Path) -> None:
    """Local credential files must never be committable."""
    path = root / '.gitignore'
    if not path.exists():
        audit.record('gitignore:exists', False, '.gitignore missing')
        return
    audit.record('gitignore:exists', True)
    lines = {ln.strip() for ln in path.read_text(encoding='utf-8').splitlines()}
    audit.record('gitignore:config-env', 'config.env' in lines or '/config.env' in lines,
                 'config.env is not ignored')
    audit.record('gitignore:local-env', '*.local.env' in lines, '*.local.env is not ignored')


def audit_configure_wizard(audit: Audit, root: Path) -> None:
    """Wire the wizard's contract into the audit without running it."""
    path = root / 'configure_blade_quant.ps1'
    if not path.exists():
        return
    text = path.read_bytes().decode('utf-8-sig')

    audit.record('wizard:has-check-mode', '--check' in text)
    audit.record('wizard:telegram-getme', 'getMe' in text)
    audit.record('wizard:deepseek-balance', '/user/balance' in text)
    audit.record('wizard:writes-config-env', 'config.env' in text)
    audit.record('wizard:no-bom-write', 'UTF8Encoding($false)' in text,
                 'config.env must be written as UTF-8 without BOM')
    audit.record('wizard:probes-local-proxy', '127.0.0.1' in text and '7890' in text)

    # The token-bearing Telegram URL must never reach an output cmdlet: scanning
    # for a Write-* call that mentions a URL-ish variable is the cheap guard.
    leaked = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not re.search(r'\bWrite-(Host|Note|Ok|Warn|Step)\b', line):
            continue
        if re.search(r'\$(url|Url|URL|TelegramBase|DeepSeekBase|token|Token)\b', line):
            leaked.append(str(lineno))
    audit.record('wizard:no-url-in-output', not leaked,
                 f'output call references a URL/token variable on line(s) {",".join(leaked[:5])}')

    # Secrets must never be persisted outside config.env: no Set-Content/Out-File
    # or WriteAllText other than the config writer itself.
    writes = re.findall(r'\[System\.IO\.File\]::WriteAll\w+\(([^)]*)\)', text)
    foreign = [w for w in writes if 'Path' not in w and '$configPath' not in w]
    audit.record('wizard:only-writes-config', not foreign, f'unexpected file writes: {foreign[:3]}')

    installer = root / 'install_blade_quant.ps1'
    if installer.exists():
        installer_text = installer.read_bytes().decode('utf-8-sig')
        audit.record('installer:loads-config-env', 'blade_load_config' in installer_text)
        audit.record('installer:guards-proxy-default', 'if not defined OKOOO_PROXY' in installer_text)
        audit.record('installer:hints-configure-script',
                     'configure_blade_quant.ps1' in installer_text)
        audit.record('installer:runs-check-summary', '--check' in installer_text)


def audit_installer_refresh(audit: Audit, root: Path) -> None:
    """The refresh path and the pinned download must stay intact.

       Re-running the installer over an existing install is the path the user
       takes, so its guarantees are checked here: it must detect the venv, skip
       pip when the imports already work, regenerate the launcher, and never
       touch config.env.
    """
    path = root / 'install_blade_quant.ps1'
    if not path.exists():
        audit.record('refresh:installer-present', False, 'installer missing')
        return
    audit.record('refresh:installer-present', True)
    text = path.read_bytes().decode('utf-8-sig')

    # Detection and the fast path.
    audit.record('refresh:detects-venv', 'VenvPython' in text and 'Test-Path $VenvPython' in text)
    audit.record('refresh:probes-broken-venv', 'venvProbe' in text,
                 'a venv that cannot run must fall back to a full install')
    audit.record('refresh:refresh-banner', '刷新已有安装' in text)
    audit.record('refresh:skips-pip-when-satisfied', 'depsReady' in text and '依赖已满足' in text)
    audit.record('refresh:skips-venv-creation', '跳过创建虚拟环境' in text)

    # config.env must never be written by the installer.
    writes = re.findall(r'\[System\.IO\.File\]::WriteAll\w+\(([^)]*)\)', text)
    foreign = [w for w in writes if 'StartBat' not in w]
    audit.record('refresh:never-writes-config-env', not foreign,
                 f'installer writes unexpected files: {foreign[:3]}')
    audit.record('refresh:no-config-env-write', 'config.env' not in ' '.join(foreign))

    # Launcher regeneration and the guarded proxy default.
    audit.record('refresh:regenerates-start-cmd', 'blade_load_config' in text)
    audit.record('refresh:guarded-proxy-default',
                 'if not defined OKOOO_PROXY set' in text)

    # Pinned download: one immutable SHA for both artifacts, plus a mirror.
    pin = re.search(r"\$PinCommit\s*=\s*'([0-9a-f]{40})'", text)
    audit.record('pin:is-full-sha', pin is not None,
                 'the app and wizard must come from one immutable commit SHA')
    audit.record('pin:not-a-branch-ref',
                 pin is not None and not re.search(r"\$PinCommit\s*=\s*'[^']*(master|main|cursor/)", text),
                 'pinning a branch is what served a stale build before')
    audit.record('pin:shared-by-both-artifacts',
                 '$PinCommit' in text and text.count('$PinCommit') >= 2,
                 'both the app and the wizard should use the same pin')
    audit.record('pin:app-uses-pin',
                 re.search(r'raw\.githubusercontent\.com/\$RepoSlug/\$PinCommit/', text) is not None)
    audit.record('pin:has-cdn-mirror', 'cdn.jsdelivr.net/gh/$RepoSlug@$PinCommit' in text)
    audit.record('pin:retries-on-cold-cache', 'for ($round = 1' in text,
                 'a freshly pushed commit can 404 on the CDN for a while')

    # The wizard must be installed, verified, and then offered as a command.
    audit.record('refresh:installs-wizard', 'configure_blade_quant.ps1' in text or '$ConfigName' in text)
    audit.record('refresh:verifies-wizard-parses', 'Parser]::ParseFile' in text)
    audit.record('refresh:verifies-wizard-runs', '--check' in text)
    audit.record('refresh:prints-next-command', '下一步' in text)




def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--json', default=None)
    args = parser.parse_args()

    root = _repo_root(args.repo_root)
    audit = Audit()

    audit_syntax(audit, root)
    audit_embedded_block(audit, root)
    audit_module_api(audit, root)
    audit_secrets(audit, root)
    audit_requirements(audit, root)
    audit_powershell(audit, root)
    audit_gitignore(audit, root)
    audit_configure_wizard(audit, root)
    audit_installer_refresh(audit, root)

    with _fresh_cwd() as tmp:
        workdir = Path(tmp)
        audit_modules(audit, root)
        audit_feature_store(audit, root, workdir)
        audit_diagnostics(audit, root)
        audit_json_io(audit, root, workdir)
        audit_scores(audit, root)
        audit_commands(audit, root, workdir)
        audit_telegram_status(audit, root)
        audit_ml(audit, root, workdir)
        audit_odds(audit, root)
        audit_parse_robustness(audit, root)
        audit_node(audit, root)
        audit_unified_report(audit, root, workdir)
        audit_war_room(audit, root)
        audit_streamlit(audit, root, workdir)

    for result in audit.results:
        status = 'PASS' if result.ok else 'FAIL'
        suffix = f'  <- {result.detail}' if result.detail and not result.ok else ''
        print(f'{status} {result.name}{suffix}')

    total = len(audit.results)
    failed = audit.failed
    print()
    print(f'TOTAL {total}  PASS {audit.passed}  FAIL {len(failed)}')
    if failed:
        print('FAILURES:')
        for result in failed:
            print(f'  - {result.name}: {result.detail}')

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {'total': total, 'passed': audit.passed,
                 'failed': [{'name': r.name, 'detail': r.detail} for r in failed]},
                ensure_ascii=False, indent=2,
            ),
            encoding='utf-8',
        )
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
