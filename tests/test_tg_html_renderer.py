"""The TG HTML renderer must never silently blank a report.

`format_matrix_dual_prejudge_html` was seeded as `return ''`. It blanked the
finished report *without raising*, so the call site's `except` fallback was dead
code: `send_tg` dropped the empty body ("消息内容为空") and the caller still set
`_tg_pushed` True. Every match looked rendered, sent and recorded, and nothing
reached Telegram.

The contract is therefore a hard requirement, not a style choice: if the input
has any non-whitespace content, the output must too. Restoring `return ''` makes
every test here fail.
"""
from __future__ import annotations

import importlib
import io
import contextlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot), contextlib.redirect_stderr(_boot):
    pipe = importlib.import_module('blade_deepseek_pipeline')

DSA = pipe.DeepSeekAnalyzer

GOOD_REPORT = (
    '📌 周日001 曼城 vs 阿森纳\n'
    '🏆 方向锁定：主胜\n'
    '📊 31项市均×竞彩对比表\n'
    '1:0  6.5  7.0\n'
)


@pytest.mark.parametrize('text', [
    '📌 t',
    GOOD_REPORT,
    'no title at all\nsecond line',
    '  \n📌 leading blank\nbody',
    'a & b < c > d',
])
def test_non_empty_in_means_non_empty_out(text: str) -> None:
    """Restoring `return ''` fails all five of these."""
    out = DSA.format_matrix_dual_prejudge_html(text)
    assert isinstance(out, str)
    assert out.strip(), f'renderer blanked a non-empty report ({len(text)} chars)'
    assert len(out) >= len(text.strip())


def test_escapes_markup() -> None:
    """Telegram HTML mode rejects unbalanced entities, so the body must be escaped."""
    out = DSA.format_matrix_dual_prejudge_html('📌 a < b & c')
    assert '&lt;' in out and '&amp;' in out
    assert '< b' not in out


def test_empty_only_for_empty_input() -> None:
    for empty in ('', '   ', '\n\n', None):
        assert DSA.format_matrix_dual_prejudge_html(empty) == ''


def test_bolds_the_title_line_and_keeps_the_body() -> None:
    """The 31-row table must survive rendering; only the title gets markup."""
    out = DSA.format_matrix_dual_prejudge_html(GOOD_REPORT)
    assert '<b>📌' in out
    assert '31项市均×竞彩对比表' in out
    assert '1:0  6.5  7.0' in out
