"""Guards the two side effects plus the undefined-name class of bug found while cleaning up.

1. The review's byproduct files — `ModelPerformanceAnalyzer.analyze_and_generate` used to
   drop `code_improvements/improvements_<date>.py` into the working directory on every
   review, with no reader anywhere in the repo, the history, or the installer. The write
   is gone; the *content* is not. One test below pins both halves at once: a completed
   review must leave the working directory byte-for-byte unchanged **and** must still
   hand the generated code back in its return value, because that is what the Telegram
   review embeds. Either regression — re-adding the write, or dropping the content with
   it — fails that one assert pair.

2. Undefined names inside `app_v84_hermes` — the legacy purge deleted the module-level
   `sporttery_jc` import shim, which removed the only top-level binding of
   `build_merged_market_heat_jc_table`. One call site used it without a local import,
   raised `NameError` into a bare `except Exception: return {}`, and the whole path went
   silently empty with ruff's F821 as the only signal.

   The concrete call site (`seed_direction_lock_from_cs`) is gone now — that function's
   market-short derivation was deleted because it could never produce a lock (see its
   docstring). The guard is kept in the shape that still matters:

   * the single-name AST check below still runs over the whole app for
     `build_merged_market_heat_jc_table` (four live call sites remain), and
   * `seed_direction_lock_from_cs` itself is checked the way F821 checks it — every name
     it reads must be bound somewhere it can see — plus every one of its return paths is
     driven for real.

   The property being pinned is the one that actually failed: a name that resolves to
   nothing, inside a handler that turns the `NameError` into an empty result.
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


def _imported_names(fn: ast.AST) -> set[str]:
    """Names a function binds for itself: `import x` and `from x import y [as z]`."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_review_leaves_the_working_directory_untouched_but_still_returns_the_code(
    monkeypatch, tmp_path,
) -> None:
    """One guard against both regressions: a revived write, or a dropped payload.

    `analyze_and_generate` used to write `code_improvements/improvements_<date>.py` into
    the process working directory — on a Windows install, the program folder — and
    nothing anywhere read it back. Deleting the write is only correct because the text
    still reaches the user through the return value, which `post_match_review` and the
    daily-learning report embed.

    So the guard is a pair, not a deletion:

      * run a review from an empty working directory and assert the directory is still
        empty (no `improvements_*.py`, no new subdirectory of any name — moving the
        debris to `logs/` is not a fix);
      * assert the same call still returns the model's code text.

    Add the write back and the first half fails. Drop `code_improvements` from the
    return value and the second half fails.
    """
    app = _app()
    marker = 'IMPROVEMENT_MARKER_7f3a = {"strategy": "late_pressure"}'
    monkeypatch.setattr(
        app, '_ds_flash_takeover',
        lambda *a, **k: f'模型表现自动分析报告\n台账01 真实正文\n模型改进代码建议\n{marker}\n',
    )
    workdir = tmp_path / 'install_dir'
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    with contextlib.redirect_stdout(io.StringIO()):
        report, code, _skill = app.ModelPerformanceAnalyzer.analyze_and_generate(
            {'rows': [], 'total': 1, 'hits': 0}
        )

    leftovers = sorted(str(p.relative_to(workdir)) for p in workdir.rglob('*'))
    assert leftovers == [], (
        'the review wrote into the program directory: '
        f'{leftovers} — the improvement text belongs in the return value, not on disk'
    )
    assert not list(workdir.rglob('improvements_*.py')), 'an improvements_*.py came back'
    assert marker in code, (
        'the review no longer returns the improvement code, so the report lost content '
        f'along with the file write; got {code!r}'
    )
    assert '真实正文' in report, 'the report text itself must survive too'


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
        imported_locally = target in _imported_names(fn)
        if not imported_locally:
            offenders.append(f'{fn.name}:{fn.lineno}')
    assert not offenders, (
        f'{target} used without a local import in: {offenders} '
        '(a module-level shim no longer guarantees the binding)'
    )


def _undefined_names_in_function(func_name: str) -> list[str]:
    """Names `func_name` reads that nothing binds: no local, no module global, no builtin.

    `symtable` is the interpreter's own scoping pass, so this is the same question ruff's
    F821 asks, narrowed to one function. A name that only resolves through a *deleted*
    module-level shim shows up here as a global with no entry in the module table.
    """
    import builtins
    import symtable

    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    module_table = symtable.symtable(src, 'app_v84_hermes.py', 'exec')
    module_names = {sym.get_name() for sym in module_table.get_symbols()}
    builtin_names = set(dir(builtins))

    def _walk(table: symtable.SymbolTable):
        yield table
        for child in table.get_children():
            yield from _walk(child)

    target = next(
        (t for t in _walk(module_table)
         if t.get_type() == 'function' and t.get_name() == func_name),
        None,
    )
    assert target is not None, f'{func_name} not found in the module symbol table'

    offenders = []
    for sym in target.get_symbols():
        name = sym.get_name()
        if not sym.is_global():
            continue  # local, parameter, or free variable: bound inside the function
        if name in module_names or name in builtin_names:
            continue
        offenders.append(name)
    return offenders


def test_seed_direction_lock_has_no_undefined_names() -> None:
    """The F821 guard that mattered, kept where it still bites.

    The regression was `build_merged_market_heat_jc_table` used without a local import
    inside `except Exception: return {}` — a `NameError` that read exactly like "no lock
    today". The call site is deleted, so the single-name AST test above no longer covers
    this function; this asserts the property that actually failed: every name
    `seed_direction_lock_from_cs` reads is bound somewhere it can see.

    Kept as a name-resolution check rather than a "does it call the builder" check
    because the builder call is what was removed on purpose. A future edit that reaches
    for a sibling module without importing it fails here even if it swallows the error.
    """
    offenders = _undefined_names_in_function('seed_direction_lock_from_cs')
    assert not offenders, (
        'seed_direction_lock_from_cs reads names nothing binds '
        f'(a swallowed NameError would look like an empty lock): {offenders}'
    )


def test_seed_direction_lock_reads_the_lock_that_is_already_there() -> None:
    """The function's whole remaining job: hand back an existing lock, or nothing.

    Both paths are driven for real. The regression this replaces patched a table builder
    and asserted it was reached; with the dead derivation gone there is no builder to
    reach, so what needs pinning is that every name the function does use still resolves
    (a swallowed `NameError` would look exactly like the `{}` that a missing lock
    returns).
    """
    app = _app()
    lock = {'locked': True, 'direction': '负', 'scores': ['0-1', '0-2'], 'source': 'operator'}
    rec = {'direction_lock': lock, 'wdl_discrete_vector': {'0-1': 0.2}}
    with contextlib.redirect_stdout(io.StringIO()):
        built = app.seed_direction_lock_from_cs(rec)
    assert built['direction_lock']['direction'] == '负', built
    assert built['wdl_discrete_vector'] == {'0-1': 0.2}, built

    # a payload bundle wins over the record, and carries its own vector
    bundle_rec = {
        'pipeline_report_bundle': {'ds_payload': {
            'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['1-0']},
            'wdl_discrete_vector': {'1-0': 0.3},
        }},
        'direction_lock': lock,
    }
    with contextlib.redirect_stdout(io.StringIO()):
        from_payload = app.seed_direction_lock_from_cs(bundle_rec)
    assert from_payload['direction_lock']['direction'] == '胜', from_payload
    assert from_payload['wdl_discrete_vector'] == {'1-0': 0.3}, from_payload

    # no lock anywhere -> {} (and no exception)
    with contextlib.redirect_stdout(io.StringIO()):
        assert app.seed_direction_lock_from_cs({'score_market_heat': {'ranking': []}}) == {}
    assert app.seed_direction_lock_from_cs(None) == {}


def test_seed_direction_lock_no_longer_derives_one_from_the_cs_tables() -> None:
    """A record full of market data but no lock must still return {}.

    This is the semantic the deletion bought: the old body would have walked the heat
    table, built a 市短 matrix and asked `matrix_direction_lock` to invent a direction.
    Nothing in that chain could ever succeed, but a future reintroduction would show up
    here as a non-empty result.
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
        'correct_score_page_avg': {'1-0': 5.4, '0-1': 9.1},
        'correct_score_books_avg': {'1-0': 5.6, '2-0': 8.2},
        'correct_score_jc_official': {'1-0': 5.0, '2-0': 9.0},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        built = app.seed_direction_lock_from_cs(rec)
    assert built == {}, f'market data alone must not produce a lock, got {built}'


def test_seed_direction_lock_never_walks_the_derivation_chain(monkeypatch) -> None:
    """The deleted derivation must stay deleted, and the return value cannot show it.

    Both the old body and the new one answer `{}` for a real record (nothing ever sets
    `mark_neg`, and `matrix_direction_lock` refuses `mkt_short_wdl_matrix` anyway), so a
    value-only assertion cannot tell them apart. Pin the *reach* instead: none of the
    four collaborators the derivation used may be called.

    Re-adding the block makes this fail on the first spy that fires — that is the
    counter-proof for the deletion, not a source grep.
    """
    app = _app()
    pipe = importlib.import_module('blade_deepseek_pipeline')
    jc_mod = importlib.import_module('sporttery_jc')

    touched: list[str] = []

    def _spy(name, result=None):
        def _inner(*args, **kwargs):
            touched.append(name)
            return result if result is not None else {}
        return _inner

    monkeypatch.setattr(app, '_merge_jc_odds_sources', _spy('_merge_jc_odds_sources'))
    monkeypatch.setattr(app, 'build_score_market_heat_ranking', _spy('build_score_market_heat_ranking'))
    monkeypatch.setattr(app, '_fixed_bonus_from_okooo_jc_official',
                        _spy('_fixed_bonus_from_okooo_jc_official'))
    monkeypatch.setattr(jc_mod, 'build_merged_market_heat_jc_table',
                        _spy('build_merged_market_heat_jc_table', {'available': True, 'rows': []}))
    monkeypatch.setattr(pipe.DeepSeekAnalyzer, 'build_mkt_short_wdl_matrix',
                        staticmethod(_spy('build_mkt_short_wdl_matrix')))

    rec = {
        'okooo_match_id': '1338958',
        'score_market_heat': {'ranking': [{'score': '1-0', 'odds': 6.0, 'heat': 1 / 6}]},
        'correct_score_page_avg': {'1-0': 5.4},
        'correct_score_books_avg': {'1-0': 5.6},
        'correct_score_site_avg': {'1-0': 5.5},
        'correct_score_okooo': [{'score': '1-0', 'odds': 5.4}],
        'correct_score_jc_official': {'1-0': 5.0},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        built = app.seed_direction_lock_from_cs(rec)
    assert built == {}, built
    assert touched == [], (
        'seed_direction_lock_from_cs reached into the deleted derivation chain: '
        f'{touched}')


def test_preload_direction_lock_writes_history_only_when_a_lock_exists() -> None:
    """The write-back helper writes both the holder and `history`, or neither."""
    app = _app()
    holder = {'okooo_match_id': '1338958'}
    payload = {
        'direction_lock': {'locked': True, 'direction': '平', 'scores': ['1-1']},
        'wdl_discrete_vector': {'1-1': 0.4},
    }
    history: dict[str, object] = {'1338958': {
        'match': 'H vs A',
        'pipeline_report_bundle': {'ds_payload': {'match': 'H vs A'}},
    }}
    with contextlib.redirect_stdout(io.StringIO()) as out:
        returned = app.preload_direction_lock(holder, payload, history, '1338958')

    assert returned is history, 'the helper must hand back the dict it wrote into'
    written = history['1338958']
    assert written['direction_lock']['direction'] == '平', written
    assert written['wdl_discrete_vector'] == {'1-1': 0.4}, written
    assert written['pipeline_report_bundle']['ds_payload']['direction_lock']['direction'] == '平', written
    assert holder['direction_lock']['direction'] == '平', holder
    assert holder['wdl_discrete_vector'] == {'1-1': 0.4}, holder
    assert '🔒 [方向锁] 赛前装载 方向=平' in out.getvalue(), out.getvalue()
    assert '市短' not in out.getvalue(), 'the log must not still claim 市短 counts'

    # no lock -> nothing is written, and nothing is created
    empty_holder: dict[str, object] = {}
    empty_history: dict[str, object] = {}
    with contextlib.redirect_stdout(io.StringIO()):
        app.preload_direction_lock(empty_holder, {}, empty_history, 'x')
    assert empty_holder == {} and empty_history == {}, 'the no-lock path must not invent anything'


def _call_site_lines() -> list[str]:
    """The exact call-site block from `analyze_match`, dedented and evaluated below."""
    import inspect
    import textwrap

    app = _app()
    source = inspect.getsource(app.analyze_match)
    lines = source.splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if 'seed_direction_lock_from_cs(' in line and 'def ' not in line
    )
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip().startswith(('except ', 'else:', 'elif ')) and \
                len(line) - len(line.lstrip()) <= len(lines[start]) - len(lines[start].lstrip()):
            break
        block.append(line)
    return textwrap.dedent('\n'.join(block)).splitlines()


def test_the_analyze_match_call_site_actually_fires() -> None:
    """Run the call-site block itself, at its real indentation, against a real record.

    This is the reachability answer for the write-back branch. `analyze_match` cannot be
    driven end to end (it runs the whole scrape chain), so the block is lifted out of the
    function and executed with the same locals it has at that point. If the branch could
    not be entered — e.g. the seed is handed a pack that never carries a lock — this fails
    with an empty `history` rather than passing on a source-grep.
    """
    app = _app()
    rec = {
        'id': '1338958',
        'match': 'H vs A',
        'operator_direction': '负',
        'direction_lock': {'locked': True, 'direction': '负',
                           'scores': ['0-1', '0-2'], 'source': 'operator'},
        'wdl_discrete_vector': {'0-1': 0.21},
    }
    block = '\n'.join(_call_site_lines())
    assert 'preload_direction_lock(' in block, f'call site changed shape: {block}'

    def _run(rec_obj, holder, hist):
        scope = {
            'rec': rec_obj, 'data_okooo': holder, 'history': hist,
            'fid': '1338958', 'locals': lambda: {'history': hist},
            # the call-site block only names module-level functions from the app
            'seed_direction_lock_from_cs': app.seed_direction_lock_from_cs,
            'preload_direction_lock': app.preload_direction_lock,
        }
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            exec(compile(block, '<call-site>', 'exec'), {'__builtins__': __builtins__}, scope)  # noqa: S102 - test-local source
        return scope, captured.getvalue()

    data_okooo = {'okooo_match_id': '1338958', 'correct_score_available': False}
    history: dict[str, object] = {'1338958': {'match': 'H vs A'}}
    scope, printed = _run(rec, data_okooo, history)

    written = history['1338958']
    assert written.get('direction_lock', {}).get('direction') == '负', (
        f'the write-back branch did not fire; history={written}')
    assert written['wdl_discrete_vector'] == {'0-1': 0.21}, written
    assert data_okooo['direction_lock']['direction'] == '负', data_okooo
    assert '🔒 [方向锁] 赛前装载 方向=负' in printed, printed
    # the call site must keep the rebound history in the enclosing scope, or every later
    # consumer in analyze_match reads the stale pre-write dict
    assert scope['history'] is history, 'the call site must bind the returned history back'

    # negative control: with no lock on the record the branch must stay shut
    bare_history: dict[str, object] = {}
    _run({'id': 'x'}, {'okooo_match_id': 'x'}, bare_history)
    assert bare_history == {}, 'a record with no lock must not create one'
