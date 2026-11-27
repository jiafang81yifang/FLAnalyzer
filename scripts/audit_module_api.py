"""Static audit: every call into a sibling runtime module must match its signature.

app_v84_hermes.py imports nine sibling modules that stand in for an unavailable
private package. When a call site drifts from the module signature the failure is
invisible, because nearly every call sits inside `except Exception`, so the app
degrades silently instead of crashing. This script catches that drift offline.

Usage:
    python scripts/audit_module_api.py [repo_root]

Exit code 0 when every call matches, 1 otherwise.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

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
    'unified_odds_evaluator',
    'score_convergence_engine',
    'score_matrices',
    'market_guardrails',
]

APP = 'app_v84_hermes.py'


def _signatures(root: Path) -> dict[str, dict[str, tuple]]:
    """Map module name -> {function name: (positional count, names, *args, kwonly, **kwargs)}."""
    out: dict[str, dict[str, tuple]] = {}
    for mod in MODULES:
        path = root / f'{mod}.py'
        if not path.exists():
            out[mod] = {}
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        funcs: dict[str, tuple] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                required = len(positional) - len(args.defaults)
                funcs[node.name] = (
                    len(positional),
                    [a.arg for a in positional],
                    args.vararg is not None,
                    [a.arg for a in args.kwonlyargs],
                    args.kwarg is not None,
                    required,
                    [a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is None],
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        funcs.setdefault(target.id, ())
        out[mod] = funcs
    return out


def _imported_names(tree: ast.AST) -> dict[str, str]:
    """Map local name -> sibling module it came from."""
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in MODULES:
            for alias in node.names:
                mapping[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in MODULES:
                    mapping[alias.asname or alias.name] = alias.name
    return mapping


def audit(root: Path) -> list[str]:
    signatures = _signatures(root)
    tree = ast.parse((root / APP).read_text(encoding='utf-8'))
    imported = _imported_names(tree)
    problems: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        module = func_name = None
        if isinstance(func, ast.Name) and func.id in imported:
            module, func_name = imported[func.id], func.id
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in imported
        ):
            module, func_name = imported[func.value.id], func.attr
        if module is None or func_name is None:
            continue

        signature = signatures.get(module, {}).get(func_name)
        if not isinstance(signature, tuple) or not signature:
            continue
        (
            positional_count,
            names,
            has_varargs,
            kwonly,
            has_kwargs,
            required_count,
            required_kwonly,
        ) = signature

        if len(node.args) > positional_count and not has_varargs:
            problems.append(
                f'{module}.{func_name}: {len(node.args)} positional args at line {node.lineno}, '
                f'signature accepts {positional_count} ({names})'
            )
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            if keyword.arg not in names and keyword.arg not in kwonly and not has_kwargs:
                problems.append(
                    f'{module}.{func_name}: unexpected keyword {keyword.arg!r} at line '
                    f'{node.lineno}, accepts ({names}) + kwonly {kwonly}'
                )

        supplied = {kw.arg for kw in node.keywords if kw.arg is not None}
        missing = [
            name
            for name in names[:required_count][len(node.args):]
            if name not in supplied
        ]
        missing += [name for name in required_kwonly if name not in supplied]
        if missing and not has_varargs:
            problems.append(
                f'{module}.{func_name}: missing required argument(s) {missing} at line '
                f'{node.lineno}'
            )
    return sorted(set(problems))


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    problems = audit(root)
    if problems:
        print(f'FAIL module-api ({len(problems)} mismatches)')
        for problem in problems:
            print(f'  - {problem}')
        return 1
    print('PASS module-api (all sibling module calls match their signatures)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
