"""Verify the embedded `_sources` block in app_v84_hermes.py matches repo bytes.

The embedded block is the merge-conflict hotspot: two branches can both touch it
and a resolution can silently keep one side's copy of a module. This parses the
block with `ast` (not a regex) and compares every module's full text byte-for-byte
against the sibling file on disk.

Usage:
    python scripts/verify_embedded_modules.py [repo_root]

Exit code 0 when every module matches, 1 otherwise.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] if __name__ == '__main__' else None
APP = 'app_v84_hermes.py'
START = '# >>> FLANALYZER_EMBEDDED_RUNTIME_MODULES >>>'
END = '# <<< FLANALYZER_EMBEDDED_RUNTIME_MODULES <<<'


def embedded_sources(app_text: str) -> dict[str, str]:
    """Pull `_sources = {...}` out of the embedded function via AST.

    Parsing the whole app and walking to the literal is the only check that cannot
    be fooled by a `}` inside a module body (a brace-counting scan can) and cannot
    be fooled by a stale duplicate copy elsewhere in the file.
    """
    if START not in app_text or END not in app_text:
        raise SystemExit(f'embedded block sentinels missing: {START!r} / {END!r}')
    block = app_text.split(START, 1)[1].split(END, 1)[0]
    tree = ast.parse(block)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id == '_sources' for t in targets):
                value = ast.literal_eval(node.value)
                if not isinstance(value, dict):
                    raise SystemExit('_sources is not a dict literal')
                return {str(k): str(v) for k, v in value.items()}
    raise SystemExit('_sources assignment not found in the embedded block')


def verify(root: Path) -> list[str]:
    """Return a list of human-readable problems; empty means clean."""
    app_text = (root / APP).read_text(encoding='utf-8')
    sources = embedded_sources(app_text)
    problems: list[str] = []

    on_disk = {
        p.stem: p.read_text(encoding='utf-8')
        for p in sorted(root.glob('*.py'))
        if p.stem in sources
    }
    missing_from_disk = sorted(set(sources) - set(on_disk))
    for name in missing_from_disk:
        problems.append(f'{name}: embedded but no sibling {name}.py on disk')

    for name, embedded in sources.items():
        local = on_disk.get(name)
        if local is None:
            continue
        if embedded != local:
            problems.append(
                f'{name}: embedded copy differs from {name}.py '
                f'({len(embedded)} vs {len(local)} bytes) — run scripts/build_single_file.py'
            )

    # Every embedded module must also be listed in the build script's MODULES.
    build_text = (root / 'scripts' / 'build_single_file.py').read_text(encoding='utf-8')
    for name in sources:
        if f"'{name}'" not in build_text:
            problems.append(f'{name}: embedded but not listed in build_single_file.MODULES')

    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parents[1]
    try:
        sources = embedded_sources((root / APP).read_text(encoding='utf-8'))
    except SystemExit as exc:
        print(f'FAIL {exc}')
        return 1
    problems = verify(root)
    print(f'embedded modules: {len(sources)}')
    for name in sorted(sources):
        print(f'  {"FAIL" if any(p.startswith(name + ":") for p in problems) else "PASS"} {name}')
    for problem in problems:
        print(f'FAIL {problem}')
    print(f'TOTAL {len(sources)}  PROBLEMS {len(problems)}')
    return 0 if not problems else 1


if __name__ == '__main__':
    raise SystemExit(main())
