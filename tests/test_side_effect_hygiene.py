"""Guards the two side effects and one import regression found while cleaning up.

1. `code_improvements/` — `ModelPerformanceAnalyzer.analyze_and_generate` writes a dated
   file into the working directory on every review. Tests must not write into the
   checkout; `tests/conftest.py` redirects it and this file proves the redirect holds
   for the whole suite, not just for the tests that call the analyser directly.

2. `build_merged_market_heat_jc_table` — deleting the module-level `sporttery_jc`
   import shim in the legacy purge removed the only top-level binding of this name.
   One call site (`seed_direction_lock_from_cs`) had no local import, so it raised
   `NameError` into a bare `except Exception: return {}`: the market-short direction
   lock went silently empty and ruff's F821 was the only signal. Pinned here so the
   next import tidy-up cannot reintroduce it.
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _app():
    module = sys.modules.get('_side_effects_app')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location('_side_effects_app', ROOT / 'app_v84_hermes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_side_effects_app'] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    return module


def test_code_improvements_dir_is_gitignored() -> None:
    """The runtime byproduct must never be committable by accident."""
    ignored = (ROOT / '.gitignore').read_text(encoding='utf-8')
    assert '/code_improvements/' in ignored


def test_analyser_honours_a_redirected_output_dir(monkeypatch, tmp_path) -> None:
    """The output dir is read from the module constant, so a test can redirect it.

    If the write path hard-coded the literal instead of reading `CODE_IMPROVEMENTS_DIR`,
    the conftest redirect would silently stop working and the tree would get dirty again.
    """
    app = _app()
    target = tmp_path / 'redirected'
    monkeypatch.setattr(app, 'CODE_IMPROVEMENTS_DIR', str(target))
    monkeypatch.setattr(app, '_ds_flash_takeover', lambda *a, **k: '模型表现自动分析报告\n台账01 真实正文')
    with contextlib.redirect_stdout(io.StringIO()):
        app.ModelPerformanceAnalyzer.analyze_and_generate({'rows': [], 'total': 1, 'hits': 0})
    assert target.is_dir(), 'the redirected directory was not used'
    assert list(target.glob('improvements_*.py')), 'no improvement file was written'


def test_market_heat_table_name_is_importable_where_it_is_used() -> None:
    """Every use of `build_merged_market_heat_jc_table` must have it in scope.

    AST check rather than a runtime call: the purge regression was a *scope* problem, so
    this asserts each enclosing function either imports the name locally or receives it.
    """
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    target = 'build_merged_market_heat_jc_table'
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        used = any(
            isinstance(n, ast.Name) and n.id == target for n in ast.walk(fn)
        )
        if not used:
            continue
        imported_locally = any(
            isinstance(n, ast.ImportFrom) and any(a.name == target or a.asname == target for a in n.names)
            for n in ast.walk(fn)
        )
        if not imported_locally:
            offenders.append(f'{fn.name}:{fn.lineno}')
    assert not offenders, (
        f'{target} used without a local import in: {offenders} '
        '(a module-level shim no longer guarantees the binding)'
    )


def test_seed_direction_lock_survives_without_the_shim() -> None:
    """It must not raise, and must not depend on a name that no longer exists.

    The function legitimately returns {} when the heat table has no `mark_neg` rows —
    that predates this fix. What must not happen is a swallowed NameError; the call has
    to complete with the table actually built.
    """
    app = _app()
    rec = {
        'okooo_match_id': '1338958',
        'score_market_heat': {
            'ranking': [
                {'score': '1-0', 'odds': 6.0, 'heat': 1 / 6},
                {'score': '2-0', 'odds': 8.0, 'heat': 1 / 8},
            ],
            'channel': 'mobile', 'source': 'okooo',
        },
        'correct_score_jc_official': {'1-0': 5.0, '2-0': 9.0},
    }
    names = set()
    with contextlib.redirect_stdout(io.StringIO()):
        built = app.seed_direction_lock_from_cs(rec)
        # Reaching the table builder is the point: patch it to observe the call.
        def _spy(heat, jc):
            names.add('called')
            return {'available': True, 'rows': []}
        real = sys.modules['sporttery_jc'].build_merged_market_heat_jc_table
        sys.modules['sporttery_jc'].build_merged_market_heat_jc_table = _spy
        try:
            app.seed_direction_lock_from_cs(rec)
        finally:
            sys.modules['sporttery_jc'].build_merged_market_heat_jc_table = real
    assert 'called' in names, 'the heat table builder was never reached (swallowed import error)'
    assert isinstance(built, dict)
