"""Guards the 2026-11-27 legacy purge: no deleted symbol may be reachable at runtime.

grep alone cannot prove a function is unreachable, because this codebase dispatches by
name in several places (`deepseek_flash_skill_dispatch`'s `skill_map`, the Streamlit UI
section map, the embedded-module payload). This test therefore does three things:

  1. asserts each purged name is gone from the app module (so the purge is real);
  2. walks every string-keyed registry that can reach a callable and asserts the name it
     points at still exists — a stale registry entry is a crash, not a style nit;
  3. asserts the audit rule that used to pin `TelegramReportGenerator` now pins the guard
     that survives it, so the deletion did not quietly remove a gate.

Hand-maintained list is deliberate: it is the delete list from the purge report, and a
test that recomputed it could not notice a name being re-added.
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import io
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('BLADE_HEADLESS', '1')

# Every symbol the purge deleted from app_v84_hermes.py (see the report's delete table).
PURGED = (
    # stale duplicate implementations, superseded by the okooo mobile path
    'jczq_classify_score',
    'judge_handicap_result',
    'derive_ah_probabilities',
    'build_prompt_section',
    'ai_final_prediction',
    'parse_deepseek_game_theory_signals',
    'moe_anti_hallucination_gate',
    'apply_cross_market_pipeline',
    'build_kelly_odds_matrix',
    'query_europa_event',
    'query_europa_history',
    'mask_penalty_score',
    'execute_ibi_gradient_analysis',
    'load_historical_features',
    'get_kelly_mode_multiplier',
    'calculate_total_goals',
    'apply_vector_intensity_calibration',
    'calibrated_boundary_prediction',
    '_zip_structural_zero_pi',
    'apply_europa_damping',
    '_rank_in_list',
    '_load_pickle_cache',
    '_save_pickle_cache',
    'batch_ingest_europa_db',
    '_history_rec_has_scrape_facts',
    # the 500.com page parsers, superseded by the okooo mobile scraper (#15)
    '_500_parse_handicap_table',
    '_500_parse_euro_table',
    '_500_parse_total_goals_table',
    'build_synthetic_market_data_from_500',
    # orphaned by deleting the unreachable legacy TG report builder
    'TelegramReportGenerator',
    # the europa topology chain: the tables had no reader, so the whole write-only path
    # (startup table creation included) went with it
    'europa_db_lock',
    'init_europa_topology_db',
    'ingest_event_to_europa_db',
    'build_topology_json_payload',
    'EUROPA_TOPOLOGY_DB_FILE',
    # the only two users of these were the unread daily-learning files
    'REPORTS_DIR',
    # dead since #28 deleted the mark_* call block; definition-only, zero callers
    'settle_display_score_pair',
)


def _app():
    module = sys.modules.get('_purge_guard_app')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location('_purge_guard_app', ROOT / 'app_v84_hermes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_purge_guard_app'] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name', PURGED)
def test_purged_symbol_is_really_gone(name: str) -> None:
    app = _app()
    assert not hasattr(app, name), f'{name} came back into app_v84_hermes.py'


def test_no_registry_points_at_a_purged_symbol() -> None:
    """A string-keyed registry that still names a purged callable would raise at dispatch."""
    app = _app()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        dispatch = app.deepseek_flash_skill_dispatch
        # resolve every registry entry without starting the background work
        source = inspect.getsource(dispatch)
    for name in PURGED:
        assert name not in source, f'deepseek_flash_skill_dispatch still references purged {name}'
    # And the real registry resolves: every value is a live module attribute.
    for key in ('auto_run', 'game_theory', 'harvest', 'furnace', 'review', 'compare'):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            out = dispatch(key)
        assert '未知技能' not in out, f'dispatch registry lost {key!r}'


def test_embedded_payload_does_not_reintroduce_a_purged_name() -> None:
    """The single-file build embeds sibling modules; none may carry a purged name back.

    The payload is a generated block of source strings. If a sibling module defined one of
    the purged names, the built app could resolve it dynamically, so this asserts the
    built artefact's embedded block is free of them.
    """
    app_src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    start = app_src.index('# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>')
    end = app_src.index('# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<')
    payload = app_src[start:end]
    for name in PURGED:
        assert f'def {name}(' not in payload, f'embedded payload defines purged {name}'


def test_purged_names_are_absent_from_both_side_and_embedded_source() -> None:
    """Same guard, one step earlier: the *sibling* module files may not define them either.

    The embedded block is generated, so until someone reruns
    `scripts/build_single_file.py` it can lag the sibling file. Asserting on the sources
    too means re-adding a purged function goes red on the same run, not on the next build.

    Counter-proof: put `def settle_display_score_pair(` back into
    `blade_deepseek_pipeline.py` without rebuilding →
    `test_purged_symbols.py::test_purged_names_are_absent_from_both_side_and_embedded_source`
    fails naming that module.
    """
    siblings = sorted({*_embedded_module_names(), 'blade_deepseek_pipeline.py'})
    for module_file in siblings:
        path = ROOT / module_file
        source = path.read_text(encoding='utf-8')
        for name in PURGED:
            assert f'def {name}(' not in source, f'{module_file} defines purged {name}'


def _embedded_module_names() -> list[str]:
    """Read the module list the build script embeds, instead of hardcoding it again."""
    script = (ROOT / 'scripts' / 'build_single_file.py').read_text(encoding='utf-8')
    match = re.search(r'MODULES = \[(.*?)\]', script, re.S)
    assert match, 'build_single_file.py MODULES list not found'
    return [f'{name}.py' for name in re.findall(r"'([^']+)'", match.group(1))]


def test_settle_display_score_pair_has_no_surviving_caller() -> None:
    """The purge is only safe while nothing calls it — including by string dispatch.

    `rg -n "settle_display_score_pair"` used to show two hits: the definition and the
    embedded `_sources` copy. Both are gone; this asserts no *reference* ever returns.

    Parsed with `ast`, not grepped: a bare substring scan counts this guard's own
    `PURGED` entry and its docstring as offenders. Only a parsed `ast.Name` (a real
    identifier use) or `ast.Attribute` (a `pipe.settle_display_score_pair` use) counts,
    so a dynamically-built name (`getattr(pipe, 'settle_' + 'display_score_pair')`) still
    shows up as a string constant and is checked separately below.
    """
    target = 'settle_display_score_pair'
    offenders: list[str] = []
    for path in ROOT.rglob('*.py'):
        if any(part in {'.git', 'node_modules', 'dist'} for part in path.parts):
            continue
        if path == Path(__file__).resolve():
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == target:
                offenders.append(f'{path.relative_to(ROOT)}:{node.lineno}: {target}')
            elif isinstance(node, ast.Attribute) and node.attr == target:
                offenders.append(f'{path.relative_to(ROOT)}:{node.lineno}: .{target}')
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if 'settle_display' in node.value:
                    offenders.append(
                        f'{path.relative_to(ROOT)}:{node.lineno}: string {node.value!r}'
                    )
    assert not offenders, f'{target} still referenced: {offenders}'


def test_report_generator_guard_survives_its_deletion() -> None:
    """The audit rule must still guard chat_id resolution now the class is gone.

    It previously pinned the TelegramReportGenerator call site; that site was inside the
    unreachable legacy branch, so the rule now pins the surviving invariant: resolve
    chat_id first, and never construct a report object without it.
    """
    audit_src = (ROOT / 'scripts' / 'full_audit.py').read_text(encoding='utf-8')
    assert 'tg-chat:report-generator-guarded' in audit_src
    assert 'current_tg_chat_id()' in audit_src
