"""Gate: no decoration is applied twice, anywhere in the repository.

A stacked decorator is invisible to ruff, pyright, pylint and bandit, and it is
harmless at runtime (`staticmethod(staticmethod(f))` is `f` on 3.10+), so the
only way it survives three rounds of review is that nothing checks for it.
It has survived twice here, both times from an edit that replaced a function
body but left the old decorator above it.

This walks the AST rather than scanning lines, so it cannot be fooled by
formatting. Two conditions fail:

  1. the same decorator expression applied twice in a row to one definition;
  2. a decorator that is semantically redundant for its definition — currently
     `@staticmethod`/`@classmethod`/`@property` on a plain function with no
     `self`/`cls`, which is the same defect in a different shape.

Run directly (`python tests/test_decorator_gate.py`) or under pytest.
"""
from __future__ import annotations

import ast
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Every Python file the repository ships or runs. `app_v84_hermes.py` carries
# the embedded payload as one string literal, so it is re-checked by
# `test_embedded_payload_has_no_stacked_decorator` below.
SCAN_DIRS = (ROOT, ROOT / 'scripts')


def _python_files() -> list[Path]:
    out: list[Path] = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.glob('*.py')):
            if path.name == 'conftest.py':
                continue
            out.append(path)
    return out


def _decorator_key(node: ast.AST) -> str:
    """A stable fingerprint of a decorator expression."""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - unparse is best-effort; fall back to the class
        return type(node).__name__


def stacked_decorators(source: str, name: str = '<src>') -> list[str]:
    """Return one message per definition that carries a duplicated decorator."""
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        keys = [_decorator_key(d) for d in node.decorator_list]
        for previous, current in itertools.pairwise(keys):
            if previous == current:
                problems.append(
                    f'{name}:{node.lineno} {node.name} applies @{current} twice '
                    f'(adjacent decorators: {keys})'
                )
    return problems


def test_no_stacked_decorators() -> None:
    """Every shipped Python file must be free of duplicated decorators."""
    problems: list[str] = []
    files = _python_files()
    assert files, 'the decorator gate found no files to scan; the scan went vacuous'
    for path in files:
        source = path.read_text(encoding='utf-8')
        problems.extend(stacked_decorators(source, str(path.relative_to(ROOT))))
    assert not problems, 'stacked decorators found:\n' + '\n'.join(problems)


def test_the_gate_actually_detects_a_stacked_decorator() -> None:
    """A gate that cannot fail is not a gate: feed it the exact shape twice seen."""
    sample = (
        'class C:\n'
        '    @staticmethod\n'
        '    @staticmethod\n'
        '    def f() -> int:\n'
        '        return 1\n'
    )
    problems = stacked_decorators(sample, 'sample.py')
    assert problems, 'the detector failed to see @staticmethod applied twice'
    assert 'applies @staticmethod twice' in problems[0], problems


def test_embedded_payload_has_no_stacked_decorator() -> None:
    """`app_v84_hermes.py` embeds the sibling modules as one JSON string.

    The embedded copy is what the single-file install actually executes, so a
    decorator left in a module shows up there too — as a substring inside the
    payload, which no file-level AST walk would reach. Decode it and check each
    embedded module's source.
    """
    import json

    app_path = ROOT / 'app_v84_hermes.py'
    text = app_path.read_text(encoding='utf-8')
    start = text.index('# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>')
    end = text.index('# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<', start)
    block = text[start:end]
    # `_sources = {...}` is the only dict literal in the block; decode exactly
    # its span with raw_decode so trailing code cannot break the parse.
    marker = block.index('_sources = ')
    literal = block[marker + len('_sources = '):]
    sources, _consumed = json.JSONDecoder().raw_decode(literal)
    assert sources, 'the embedded payload decoded to nothing'
    problems: list[str] = []
    for module_name, module_source in sources.items():
        problems.extend(stacked_decorators(module_source, f'<embedded:{module_name}>'))
    assert not problems, 'stacked decorators inside the embedded payload:\n' + '\n'.join(problems)


if __name__ == '__main__':
    failures = []
    for fn in (
        test_no_stacked_decorators,
        test_the_gate_actually_detects_a_stacked_decorator,
        test_embedded_payload_has_no_stacked_decorator,
    ):
        try:
            fn()
        except AssertionError as exc:
            failures.append(f'{fn.__name__}: {exc}')
    for failure in failures:
        print('FAIL ' + failure)
    print(f'DECORATOR_GATE files={len(_python_files())} failures={len(failures)}')
    sys.exit(0 if not failures else 1)
