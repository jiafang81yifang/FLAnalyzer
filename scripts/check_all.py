"""Run reproducible FLAnalyzer quality gates."""
from __future__ import annotations

# The check script runs a fixed list of local tool commands.
import subprocess  # nosec B404
import sys
from collections.abc import Sequence

PYTHON_FILES = [
    'app_v84_hermes.py',
    'blade_deepseek_pipeline.py',
    'blade_ml_engine.py',
    'okooo_mobile_cs.py',
    'odds_index_math.py',
    'odds_fallback.py',
    'sporttery_jc.py',
    'convergence_arbitrator.py',
    'ProductionRunnerBridge.py',
    'blade_monitor_panel.py',
    'scripts/build_single_file.py',
    'scripts/runtime_module_self_check.py',
]

GATES: list[tuple[str, list[str]]] = [
    ('ruff', [sys.executable, '-m', 'ruff', 'check', '.']),
    ('bandit', [sys.executable, '-m', 'bandit', '-q', '-r', '.']),
    ('pylint-errors-only', [sys.executable, '-m', 'pylint', '--errors-only', *PYTHON_FILES]),
    ('pyright', [sys.executable, '-m', 'pyright', *PYTHON_FILES]),
    ('runtime-self-check', [sys.executable, 'scripts/runtime_module_self_check.py']),
]


def run_gate(name: str, cmd: Sequence[str]) -> bool:
    print(f'== {name} ==')
    print('+ ' + ' '.join(cmd))
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)  # nosec B603
    if proc.stdout:
        print(proc.stdout, end='' if proc.stdout.endswith('\n') else '\n')
    if proc.stderr:
        print(proc.stderr, end='' if proc.stderr.endswith('\n') else '\n', file=sys.stderr)
    ok = proc.returncode == 0
    print(f'{"PASS" if ok else "FAIL"} {name}')
    return ok


def main() -> int:
    ok = True
    for name, cmd in GATES:
        ok = run_gate(name, cmd) and ok
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
