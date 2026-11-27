"""Shared test isolation.

`ModelPerformanceAnalyzer.analyze_and_generate` ends every review by writing
`code_improvements/improvements_<date>.py` into the **working directory** — on a user's
Windows install that is the program folder. Several tests reach that code incidentally,
and two do so indirectly, which is why patching only the obvious call sites was not
enough:

  * `tests/test_six_market_live_failure.py` calls `analyze_and_generate` directly (3 sites);
  * `tests/test_sweep_hardening.py` and `tests/test_runtime.py` call
    `scripts.scan_report_surfaces.scan()`, which drives `post_match_review` — the
    post-match review path that calls `analyze_and_generate`.

Before this fixture, every full gate run left an untracked `code_improvements/` in the
repository root, so the side effect dirtied every diff inspected afterwards. The
directory is gitignored now, but a test must not write into the checkout at all: this
fixture points the constant at the test's own tmp dir, so the write still happens
(some tests assert the content) and simply lands somewhere harmless.
"""
from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _redirect_code_improvements_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """Keep the review's dated file out of the working tree."""
    app = importlib.import_module('app_v84_hermes')
    monkeypatch.setattr(
        app, 'CODE_IMPROVEMENTS_DIR', str(tmp_path / 'code_improvements'), raising=False
    )
    yield
