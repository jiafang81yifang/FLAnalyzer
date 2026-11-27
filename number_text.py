"""One place where a number becomes display text, and it cannot print non-finite.

The defect class is not three sites: 293 f-strings in the app apply a numeric
format spec, 39 of them inside the TG report path. Patching the three that were
reached first leaves the class open. This module gives the renderers a single
entry point whose contract is "a non-finite value is never a number", so the
guarantee is structural rather than a spot fix.
"""
from __future__ import annotations

import math
from typing import Any

MISSING = '-'


def num_text(value: Any, spec: str = '.2f', *, missing: str = MISSING,
             suffix: str = '') -> str:
    """Format a number for display; a non-finite or unusable value prints `missing`.

    `float('1e400')` returns inf rather than raising, so an exception guard alone
    lets `inf` reach the page. This function checks finiteness explicitly.
    """
    if value is None:
        return missing
    if isinstance(value, bool):
        return missing
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return missing
    if not math.isfinite(number):
        return missing
    try:
        return format(number, spec) + suffix
    except (TypeError, ValueError):
        return missing


def pct_text(value: Any, digits: int = 1, *, missing: str = MISSING) -> str:
    """A percentage cell, or `missing`."""
    return num_text(value, f'.{int(digits)}f', missing=missing, suffix='%')


def assert_finite_text(text: str, *, allow_tokens: tuple[str, ...] = ()) -> None:
    """Fail fast if a rendered body contains a non-finite number.

    Guards the whole class at the boundary: whatever path produced the number,
    `inf`/`nan` must never appear as a figure in a body about to be sent.
    """
    import re
    for token in ('inf', 'nan'):
        for match in re.finditer(rf'(?<![\w.])-?{token}(?![\w])', text, re.IGNORECASE):
            start = max(0, match.start() - 25)
            snippet = text[start:match.end() + 10].replace('\n', ' ')
            if any(a in snippet for a in allow_tokens):
                continue
            raise AssertionError(f'non-finite value rendered: …{snippet}…')
