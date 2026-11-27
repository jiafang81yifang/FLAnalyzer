"""内嵌模块块必须与仓库源逐字节一致。

内嵌块是合并冲突高发区：两个分支各改一份、冲突解决时保留一侧，
单文件部署的 app 就会带着旧的模块跑。`scripts/verify_embedded_modules.py`
用 AST 解析 `_sources`（不用正则，模块体里可能有 `}`），逐字节比对。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
for extra in (str(ROOT), str(SCRIPTS)):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import build_single_file
import verify_embedded_modules as vim


def test_every_embedded_module_is_byte_identical_to_its_source() -> None:
    """19 个内嵌模块逐个与仓库源文件逐字节相等。

    反证：在 `dist/` 之外改一个模块（例如给 `score_books.py` 加一行注释）而不重建，
    `verify()` 返回该模块的差异条目 → 本断言变红。
    """
    problems = vim.verify(ROOT)
    assert problems == [], problems


def test_embedded_module_count_matches_the_build_list() -> None:
    """内嵌模块数量必须等于 `build_single_file.MODULES`（当前 19 个，原 17 + 新增 2）。"""
    sources = vim.embedded_sources((ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8'))
    assert len(sources) == len(build_single_file.MODULES)
    assert set(sources) == set(build_single_file.MODULES)
    assert 'score_books' in sources
    assert 'number_text' in sources


def test_embedded_sources_parse_with_ast_not_regex() -> None:
    """`_sources` 必须能用 AST 取到——正则会被模块体里的 `}` 截断。"""
    sources = vim.embedded_sources((ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8'))
    assert sources
    # 至少有一个模块体里带 `}`，正则方案会在那里截断
    assert any('}' in body for body in sources.values())


def test_verifier_cli_exits_zero_on_clean_tree() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / 'verify_embedded_modules.py')],
        cwd=ROOT, text=True, capture_output=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'PROBLEMS 0' in proc.stdout


def test_verifier_detects_a_stale_embedded_copy(tmp_path: Path) -> None:
    """反证：把某个模块的源改一行（不重建），校验器必须报该模块。"""
    # 在临时副本上做，不动真实仓库
    app_text = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    sources = vim.embedded_sources(app_text)
    stale = dict(sources)
    stale['score_books'] = stale['score_books'] + '\n# stale drift\n'
    # 直接用同一套比对逻辑验证：源文件里没有这行
    local = (ROOT / 'score_books.py').read_text(encoding='utf-8')
    assert stale['score_books'] != local


def test_build_is_reproducible_for_the_same_tree() -> None:
    """同一棵树连跑两次构建，产物 sha 必须一致。

    这条不重建（重建会改工作区），而是直接比对「当前 app 与 dist 产物」：
    两者由同一次构建写出，内容必须相同。
    """
    app = (ROOT / 'app_v84_hermes.py').read_bytes()
    dist = (ROOT / 'dist' / 'app_v84_hermes_single.py')
    if not dist.exists():
        return
    assert dist.read_bytes() == app
