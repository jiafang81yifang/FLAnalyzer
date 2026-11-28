"""阶段2「已开赛」闸门不得让测试依赖运行时刻。

`is_history_match_not_started`（`app_v84_hermes.py:5799`）用 `datetime.now()` 与记录里的
开球时间比较；阶段2 补发闸门（`:32650` 附近）据此决定「跳过整场」。任何测试只要：

1. 把**写死的**日期送进 `emit_pipeline_deepseek_tg_report` / `scrape_okooo_detail` 这类
   会过闸门的路径，并且
2. 断言「发出去了 / 被处理了」，

就会在某个日期之后自己变红。`tests/test_title_once.py` 的 `KICKOFF = '2026-11-28 03:30'`
在 2026-11-28 03:30 之后正是这样红的：三条标题测试拿到空投递，`assert first` 失败，
日志里是 `⏭️ [阶段2] 已开赛/完场，跳过补发`。

**「把日期往后挪」不算修好**：同一文件下方 `SWEEP_FROZEN_NOW` 的注释已经写明这是同一
缺陷模式的第 N 次重现——「两次 100k 跑在 17:00 UTC 全绿、同样的 case 在 18:00 失败了
2,853 次」。挪日期只是把炸弹延后。

正确的做法是让闸门判断与真实时钟解耦：
  * 桩 `app.is_history_match_not_started` 为恒 True（被测点与「发不发」正交时），或
  * 用开球时间相对 `datetime.now()` 动态生成。

本文件把这些约定钉成断言，并在需要时真的把系统时钟推到很远未来跑一遍。
"""
from __future__ import annotations

import ast
import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')

TESTS = ROOT / 'tests'
# 会经过阶段2「已开赛」闸门的入口。
GATED_ENTRYPOINTS = ('emit_pipeline_deepseek_tg_report',)
GATE_PREDICATE = 'is_history_match_not_started'


def _test_functions(path: Path) -> list[tuple[str, str, int]]:
    """返回 [(name, body_source, lineno)]，只取 test_ 开头的函数。"""
    src = path.read_text(encoding='utf-8')
    tree = ast.parse(src)
    lines = src.splitlines()
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
            out.append((node.name, '\n'.join(lines[node.lineno - 1: node.end_lineno]), node.lineno))
    return out


def _calls_gated_entrypoint(body: str) -> bool:
    return any(f'{name}(' in body for name in GATED_ENTRYPOINTS)


def _stubs_gate(body: str) -> bool:
    return GATE_PREDICATE in body


def _dynamic_kickoff(body: str) -> bool:
    """开球时间相对真实时钟生成（`now() + ...` / `timedelta`），不会过期。"""
    return 'timedelta(' in body and ('now()' in body or 'utcnow()' in body)


# --------------------------------------------------------------------------- #
# 1. 静态扫描：调用闸门入口的测试必须解耦时钟
# --------------------------------------------------------------------------- #
def _file_sources(path: Path) -> tuple[str, dict[str, str]]:
    """返回 (整份源码, {本地函数名: 函数源码})，用于追踪同文件内的辅助函数。"""
    src = path.read_text(encoding='utf-8')
    tree = ast.parse(src)
    lines = src.splitlines()
    funcs: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = '\n'.join(lines[node.lineno - 1: node.end_lineno])
    return src, funcs


def _has_dynamic_kickoff(body: str, local_funcs: dict[str, str], depth: int = 2) -> bool:
    """函数体内**或它调用的同文件辅助函数里**是否动态生成开球时间。

    例：`test_apply_operator_direction_*` 自己只写 `_record(...)`，而 `_record`
    用 `datetime.now() + timedelta(hours=6)` —— 那已经与真实时钟解耦，不该被点名。
    """
    if _dynamic_kickoff(body):
        return True
    if depth <= 0:
        return False
    for name, fsrc in local_funcs.items():
        if name.startswith('test_'):
            continue
        if f'{name}(' in body and _has_dynamic_kickoff(fsrc, {}, depth - 1):
            return True
    return False


def test_gated_entrypoint_callers_do_not_depend_on_the_wall_clock() -> None:
    """凡调用 `emit_pipeline_deepseek_tg_report` 的测试，必须桩闸门或动态生成开球时间。

    这条取代了「靠人记得」：新增一条打真实日期又断言投递的测试，会在这里被点名。
    """
    offenders: list[str] = []
    for path in sorted(TESTS.glob('test_*.py')):
        _src, local_funcs = _file_sources(path)
        for name, body, lineno in _test_functions(path):
            if not _calls_gated_entrypoint(body):
                continue
            if _stubs_gate(body) or _has_dynamic_kickoff(body, local_funcs):
                continue
            offenders.append(f'{path.name}::{name} (:{lineno})')
    assert offenders == [], (
        '这些测试调用阶段2入口但既没桩闸门、也没动态生成开球时间，'
        f'会随时间自己变红: {offenders}'
    )


# --------------------------------------------------------------------------- #
# 2. 已确认受影响的三处必须真的带桩
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('filename', [
    'test_title_once.py',
    'test_gate_teeth.py',
    'test_pack_absent_states.py',
])
def test_known_clock_sensitive_files_stub_the_gate(filename: str) -> None:
    """这三处都曾把写死日期送进闸门并断言「发出去了」。"""
    src = (TESTS / filename).read_text(encoding='utf-8')
    assert GATE_PREDICATE in src, f'{filename} 没有桩 {GATE_PREDICATE}'
    # 桩必须打在模块属性上：阶段2 是直接按模块全局调用的。
    assert f"'{GATE_PREDICATE}'" in src, f'{filename} 的桩没打 app 的模块属性'


def test_title_once_helper_pins_the_gate() -> None:
    """3 条标题测试共用的 `_deliver` 里必须有那条桩（一次修 3 条）。"""
    src = (TESTS / 'test_title_once.py').read_text(encoding='utf-8')
    start = src.index('def _deliver(')
    helper = src[start: src.index('\ndef _first_line', start)]
    assert GATE_PREDICATE in helper, (
        '_deliver 的 patches 列表里缺闸门桩：3 条标题测试会随开球时间过期而变红'
    )


# --------------------------------------------------------------------------- #
# 3. 动态时钟实跑：把「现在」推到很远未来，标题测试仍须绿
# --------------------------------------------------------------------------- #
def test_title_delivery_still_works_when_the_clock_is_far_future(tmp_path, monkeypatch) -> None:
    """把 `app.datetime.now()` 推到 2030 年，`_deliver` 仍能投出标题行。

    这条不依赖机器当前时间：无论今天几号，写死的 KICKOFF 都已远在过去，
    只有闸门桩生效时才可能通过。它是「不再随时间变红」的直接证据。
    """
    import test_title_once as title_mod

    real_datetime = app.datetime

    class _FarFuture(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2030, 1, 1, 12, 0)

    monkeypatch.setattr(app, 'datetime', _FarFuture)
    sent = title_mod._deliver(tmp_path)
    first = ''
    for _pm, text in sent:
        for line in (text or '').splitlines():
            if '📌' in line:
                first = line.strip()
                break
        if first:
            break
    assert first, 'clock pushed to 2030: nothing was delivered'
    assert first.count('📌') == 1, first
    assert first.count(title_mod.KICKOFF) == 1, first


def test_the_gate_itself_still_works_for_a_started_match() -> None:
    """对照：闸门桩只存在于测试里，真实谓词对已开赛的比赛仍返回 False。

    防止有人把「桩闸门」理解成「把闸门改宽松」。
    """
    started = {'status': 'pending', 'match_time': '2020-01-01 20:00',
               'kickoff_source': 'okooo_match_time'}
    assert app.is_history_match_not_started(started) is False
    future = (datetime.now() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M')
    not_started = {'status': 'pending', 'match_time': future,
                   'kickoff_source': 'okooo_match_time'}
    assert app.is_history_match_not_started(not_started) is True
