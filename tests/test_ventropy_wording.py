"""Guard: the ventropy log must not carry a banned pick word.

The ventropy route log is a report surface. `同向次选不足2个` reads as guidance
("the second pick is missing"), and 次选 is banned on report surfaces by the
repository's own rule (scripts/scan_report_surfaces.py: BANNED), which this
branch's BANNED_PICK_WORDS mirrors. Nothing caught it on this branch because the
surface-wide scan lives with the direction-consistency work, so a regression here
would only surface after both branches met.

This drives the real pipeline on a pool that takes the 防守层 branch with no
same-direction runner-up — exactly the path that produced the banned wording.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED = ('首选', '次选', '双选', '强推', '推荐')
ALLOWED = ('不做推荐',)


def _load_app():
    module = sys.modules.get('_ventropy_wording_app')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location('_ventropy_wording_app', ROOT / 'app_v84_hermes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_ventropy_wording_app'] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    return module


def _hits(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for word in BANNED:
        n = text.count(word) - sum(text.count(ok) for ok in ALLOWED)
        if n > 0:
            out[word] = n
    return out


def _pool_with_two_defense_tiers():
    """One TIER_2 (冰封防御层) row and no same-direction runner-up."""
    return [
        {'score': '2-0', 'theory': 5.0, 'market': 15.0, 'ibi': 0.5},
        {'score': '1-1', 'theory': 4.0, 'market': 6.0, 'ibi': 1.5},
    ]


def test_defense_tier_log_carries_no_banned_pick_word() -> None:
    app = _load_app()
    pool = _pool_with_two_defense_tiers()
    ranked = [(p['score'], {'prob': p['theory']}) for p in pool]
    with contextlib.redirect_stdout(io.StringIO()):
        result = app.run_ventropy_pipeline(ranked, pool, '英超', {}, match_name='A vs B')
    log = str((result or {}).get('log') or '')
    assert log, 'the pipeline produced no log; this test would be vacuous'
    assert not _hits(log), f'log carries pick wording {_hits(log)}: {log}'


def test_no_source_string_uses_the_banned_phrase() -> None:
    """The literal that shipped. Pinned so the wording cannot come back."""
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert '同向次选不足' not in source, (
        'the ventropy log uses 次选, a banned pick word; say 不同比分的候选不足 instead'
    )
