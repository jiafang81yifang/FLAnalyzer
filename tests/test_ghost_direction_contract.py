"""Guard: the ghost-node direction lock must filter under either classifier contract.

`DeepSeekAnalyzer._classify_score_wdl` returns the bare direction string on the
viscosity branch and `(normalised_score, direction)` once the direction-consistency
work lands. The injector's lock filter used to compare the whole return value to a
direction, which matches nothing as soon as the return becomes a tuple — the lock
then silently keeps every ghost instead of dropping the wrong-direction ones.

Every call sits inside `except Exception`, so nothing raises and no assertion
catches it. Deleting the defensive read left the whole suite green; these tests
pin it from both ends.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_app():
    module = sys.modules.get('_ghost_direction_contract_app')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(
        '_ghost_direction_contract_app', ROOT / 'app_v84_hermes.py'
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules['_ghost_direction_contract_app'] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 1. Structural: no call site may compare the raw return value
# --------------------------------------------------------------------------- #
def test_no_call_site_compares_the_raw_classifier_return() -> None:
    """A bare `_classify_score_wdl(...)` must never be a comparison operand.

    The injector's filter is the one this branch introduced; it must read the
    direction slot (or go through a helper that does) rather than the whole value.
    """
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            func = getattr(operand, 'func', None)
            if getattr(func, 'attr', None) == '_classify_score_wdl':
                offenders.append(node.lineno)
    assert not offenders, (
        f'bare _classify_score_wdl(...) compared at lines {offenders}; read the direction slot'
    )


# --------------------------------------------------------------------------- #
# 2. Behaviour: the lock filters correctly under BOTH contracts
# --------------------------------------------------------------------------- #
class _TupleClassifier:
    """The post-merge shape: (score, direction)."""

    @staticmethod
    def classify(score: str) -> tuple[str, str]:
        text = str(score or '').replace(':', '-').strip()
        if '-' not in text:
            return text, ''
        h, a = (int(x) for x in text.split('-', 1))
        return text, ('胜' if h > a else ('平' if h == a else '负'))


class _BareClassifier:
    """This branch's shape: the direction string alone."""

    @staticmethod
    def classify(score: str) -> str:
        return _TupleClassifier.classify(score)[1]


@pytest.mark.parametrize('classifier_cls', [_BareClassifier, _TupleClassifier])
def test_ghost_direction_reads_the_direction_under_either_contract(classifier_cls) -> None:
    """Whatever the classifier returns, the lock must see 负 for an away score."""
    app = _load_app()
    classifier = classifier_cls()

    ghosts = [
        {'score': '0-1'},   # away -> 负
        {'score': '2-0'},   # home -> 胜
        {'score': '1-1'},   # draw -> 平
    ]
    kept = []
    for ghost in ghosts:
        raw = classifier.classify(ghost['score'])
        direction = raw[1] if isinstance(raw, (tuple, list)) and len(raw) > 1 else str(raw or '')
        if direction == '负':
            kept.append(ghost['score'])
    assert kept == ['0-1'], f'{classifier_cls.__name__} produced {kept}'


def test_the_defensive_read_is_used_by_the_injector() -> None:
    """The injector must route through a helper that handles both shapes.

    Without this, the parametrized test above would still pass while the real
    injector compared the raw value — exactly the gap that shipped.
    """
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    start = source.index('def apply_ghost_node_injection(')
    end = source.index('\ndef ', start + 10)
    body = source[start:end]
    assert 'isinstance(raw, (tuple, list))' in body, (
        'the direction lock must handle a tuple/list return, not compare the raw value'
    )
