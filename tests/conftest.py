"""Shared test isolation.

Only one thing is left here: putting the repository root on `sys.path`.

That is load-bearing. Many tests import a sibling runtime module by bare name
(`import score_matrices`, `import okooo_mobile_cs`, …) instead of
`spec_from_file_location`, and `pytest` does not add the rootdir to `sys.path` — it
adds the test file's own directory. Running the suite without this file fails at
collection with `ModuleNotFoundError: No module named 'score_convergence_engine'`.

The old autouse fixture that redirected `CODE_IMPROVEMENTS_DIR` is gone with the
write it redirected: `analyze_and_generate` no longer touches the filesystem, so
there is nothing to keep out of the tree. `tests/test_side_effect_hygiene.py` proves
that directly, and from an empty directory rather than from a patched constant.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
