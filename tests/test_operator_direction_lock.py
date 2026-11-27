"""守卫：`/方向 胜|平|负` 必须真的落一把能读出方向的锁。

命令的承诺是「比分只从指定方向里出」。这条链上有两个空闸，各自都足以让它在
报告里完全不存在，而用户看到的却是「已指定方向，开始重算」：

  A. `apply_operator_direction` 的实现是 `return str(text or '')`。调用点把它当
     「就地改写 payload」用、并且丢弃返回值，于是 payload 的 `direction_lock`
     原地不动，指定方向从未写进任何地方。
  B. `build_operator_direction_lock` 的实现是
     `return {'available': False, 'direction': ''}`。`handle_tg_command` 把它的
     返回值**直接赋给** `info['direction_lock']`，于是记录里原本有效的锁被一个
     没有 `locked` 键的桩顶掉——比不做更差：`seed_direction_lock_from_cs` 之后
     读回 `{}`。

所以这里的断言全部落在**真实函数**上：`handle_tg_command`、真实的
`_tg_run_operator_direction`、真实的 `emit_pipeline_deepseek_tg_report`，以及
真实的 `seed_direction_lock_from_cs` / `matrix_direction_lock`。没有任何一条
断言去 grep 源码字符串——那样只能证明代码长什么样，证明不了锁真的读得出来。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe = importlib.import_module('blade_deepseek_pipeline')
DA = pipe.DeepSeekAnalyzer

FID = '1338958'
MATCH_NO = '周五002'
MATCH_NAME = '海登海姆 vs 纽伦堡'
ALL_31 = list(pipe.JCZQ_ALL_OPTIONS)
NINE_JC_ONLY = ['0-4', '0-5', '1-5', '2-4', '2-5', '2-6', '0-6', '1-6', '0-7+']


def _jc31() -> dict[str, float]:
    return {option: round(3.4 + (i % 12) * 1.27, 2) for i, option in enumerate(ALL_31)}


def _record(**overrides: Any) -> dict[str, Any]:
    """一条「真的抓过、还没开赛」的记录，形状与 test_always_send_policy 一致。"""
    jc = _jc31()
    kickoff = (datetime.now() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M')
    rec: dict[str, Any] = {
        'match': MATCH_NAME,
        'cn_name': MATCH_NAME,
        'no': MATCH_NO,
        'time': kickoff,
        'status': 'pending',
        'pipeline_scrape_done': True,
        'predicted_score': '2-1',
        'second_score': '2-0',
        'okooo_match_id': FID,
        'correct_score_page_avg': {
            option.replace('-', ':'): jc[option] for option in ALL_31 if option not in NINE_JC_ONLY
        },
        'correct_score_books_avg': {
            option.replace('-', ':'): jc[option] for option in ALL_31 if option not in NINE_JC_ONLY
        },
        'correct_score_jc_official': dict(jc),
        'jc_score_odds': dict(jc),
        'six_market_mobile': {
            'asian_handicap': -0.75,
            'ou_line': 2.75,
            'odds_1x2': {'home': 1.91, 'draw': 3.86, 'away': 2.8},
            'jc_handicap': -1.0,
            'total_goals_odds': {str(i): float(4 + i) for i in range(8)},
            'htft_odds': {k: float(5 + i) for i, k in enumerate(
                ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负'))},
        },
        'lam_h': 1.45,
        'lam_a': 1.10,
        'lambda_home': 1.45,
        'lambda_away': 1.10,
    }
    rec.update(overrides)
    return rec


@pytest.fixture(autouse=True)
def _isolate_tg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_KEY', raising=False)
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '-1001234567890')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    app.reset_tg_delivery_state()
    yield
    app.reset_tg_delivery_state()


def _seed_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rec: dict[str, Any]):
    """把记录写进真实的 history 文件，并让所有网络/抓取入口都短路。"""
    history_file = tmp_path / 'match_history.json'
    history_file.write_text(json.dumps({FID: rec}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app, 'HISTORY_FILE', str(history_file))
    monkeypatch.setattr(app, 'add_script_run_ctx', lambda thread: None)
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    monkeypatch.setattr(app, 'enrich_history_okooo_cs_facts',
                        lambda rec, cn_name='', force_rescrape=False: False)
    monkeypatch.setattr(app, '_pull_jczq31_into_record', lambda *a, **k: {'available': False})
    return history_file


class _ImmediateThread:
    """`/方向` 起的是守护线程；测试里同步跑完，异常不被吞掉。"""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args or ()
        self.kwargs = kwargs or {}

    def start(self):
        if self.target:
            self.target(*self.args, **self.kwargs)


def _run_direction_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                           rec: dict[str, Any], direction: str,
                           *, reports: list[str] | None = None,
                           model=None) -> tuple[dict, list[str]]:
    """驱动真实的 `handle_tg_command` → 真实的 `_tg_run_operator_direction` → 真实 emit。

    只有三个东西是假的：Telegram 的 HTTP 出口（`_tg_post`）、模型调用，以及
    线程（改同步）。其余全部是生产代码，包括 emit 里那段把方向写进 payload 的块。
    """
    history_file = _seed_history(monkeypatch, tmp_path, rec)
    sent: list[str] = []
    monkeypatch.setattr(app, '_tg_post',
                        lambda url, payload: sent.append(str(payload.get('text') or '')) or True)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    monkeypatch.setattr(app.threading, 'Thread', _ImmediateThread)
    monkeypatch.setattr(app, '_clear_finished_match_scrape_cache', lambda *a, **k: None)

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    if model is None:
        # 默认：照常返回一段带①②的正文，且方向就是命令要的那个。
        directions = {'胜': ('2-1', '3-1'), '平': ('1-1', '0-0'), '负': ('0-1', '0-2')}
        first, second = directions[direction]

        def model(messages=None, *a, **k):  # noqa: F811 - 默认实现
            return f'🧠【DeepSeek 数据分析研判报告】\n①比分 {first} ②比分 {second}\n'

    monkeypatch.setattr(pipe, 'deepseek_chat_completion', model, raising=False)
    monkeypatch.setattr(pipe, '_LAST_COMPLETION_LOCAL', False, raising=False)
    monkeypatch.setattr(pipe, '_LAST_COMPLETION_ERROR', '', raising=False)

    if reports is not None:
        real_print = print

        def _capture(*args, **kwargs):
            reports.append(' '.join(str(a) for a in args))
            if kwargs.get('file') is None:
                real_print(*args, **kwargs)

        monkeypatch.setattr('builtins.print', _capture)

    with contextlib.redirect_stdout(io.StringIO()):
        app.handle_tg_command(f'方向 {MATCH_NO} {direction}')
    saved = json.loads(history_file.read_text(encoding='utf-8'))[FID]
    return saved, sent


# --------------------------------------------------------------------------- #
# 1. 两个函数各自的契约
# --------------------------------------------------------------------------- #
def test_build_operator_direction_lock_passes_matrix_direction_lock() -> None:
    """返回值必须过 `matrix_direction_lock`，且 `locked=True`、方向就是命令方向。

    这是所有下游过滤器（`filter_scores_to_direction_lock`、`lock_probability_cluster`
    和十几处 `.get('direction')`）唯一的读取契约。桩版本没有 `locked` 键，
    于是每一处都当成「无锁」放行。
    """
    lock = DA.build_operator_direction_lock('负', {})
    read_back = DA.matrix_direction_lock({'direction_lock': lock})
    assert read_back['locked'] is True, read_back
    assert read_back['direction'] == '负', read_back
    assert read_back['source'] == 'operator', read_back
    for direction in ('胜', '平', '负'):
        one = DA.build_operator_direction_lock(direction, {})
        assert DA.matrix_direction_lock({'direction_lock': one})['direction'] == direction


def test_build_operator_direction_lock_inherits_and_filters_existing_scores() -> None:
    """scores 从旧锁继承，按新方向过滤；旧方向的分不搬过来。

    过滤走的是 `matrix_direction_lock` 里那一份 `_score_wdl`，本函数不另写一遍。
    """
    existing = {'locked': True, 'direction': '胜', 'scores': ['2-1', '3-1', '0-1', '1-1']}
    lock = DA.build_operator_direction_lock('负', existing)
    assert lock['scores'] == ['0-1'], lock
    same = DA.build_operator_direction_lock('胜', existing)
    assert same['scores'] == ['2-1', '3-1'], same


def test_apply_operator_direction_mutates_the_payload_in_place() -> None:
    """调用点丢弃返回值，所以必须就地生效；同时把 payload 交还，便于串联。

    桩版本返回字符串、payload 原地不动——这条断言在桩版本下直接红。
    """
    payload = {'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-1']}}
    before = json.loads(json.dumps(payload, ensure_ascii=False))
    returned = DA.apply_operator_direction(payload, '负')
    assert payload['direction_lock'] != before['direction_lock'], (
        'apply_operator_direction 没有就地改写 payload：'
        f"before={before['direction_lock']} after={payload['direction_lock']}"
    )
    assert DA.matrix_direction_lock(payload)['direction'] == '负', payload
    assert returned is payload, '返回值应当是同一个 payload（调用点可忽略，但名字要对）'
    # 非 dict 输入原样返回，不抛（每个调用点都在 except 里）
    assert DA.apply_operator_direction('2-1', '负') == '2-1'


def test_apply_operator_direction_reaches_the_model_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """方向必须真的进提示词——payload 改了但模型不知道，等于没改。"""
    captured: dict[str, Any] = {}

    def _spy(messages=None, *a, **k):
        captured['messages'] = messages
        return '🧠【DeepSeek 数据分析研判报告】\n①比分 0-1 ②比分 0-2\n'

    _run_direction_command(monkeypatch, tmp_path, _record(), '负', model=_spy)
    messages = captured.get('messages')
    assert messages, '模型调用没有发生'
    prompt = '\n'.join(str(m.get('content', '')) for m in messages)
    assert '"direction": "负"' in prompt or '"direction":"负"' in prompt, prompt[-1500:]


# --------------------------------------------------------------------------- #
# 2. 端到端：`/方向 负` → history 里的锁 → `seed_direction_lock_from_cs`
# --------------------------------------------------------------------------- #
def test_direction_command_seeds_a_lock_that_reads_back_as_the_command_direction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """真实 `handle_tg_command` + 真实 `_tg_run_operator_direction` + 真实 seed。"""
    rec = _record(direction_lock={
        'locked': True, 'direction': '胜', 'scores': ['2-1', '3-1'], 'source': 'operator',
    })
    saved, sent = _run_direction_command(monkeypatch, tmp_path, rec, '负')
    assert sent, '指定方向后必须回一条 TG 消息'

    lock = saved.get('direction_lock')
    assert isinstance(lock, dict), f'history 里没有锁：{lock!r}'
    read_back = DA.matrix_direction_lock({'direction_lock': lock})
    assert read_back['locked'] is True, f"锁读不出 locked=True：{lock!r}"
    assert read_back['direction'] == '负', f"锁方向不是命令方向：{lock!r}"

    seeded = app.seed_direction_lock_from_cs(saved)
    assert seeded.get('direction_lock', {}).get('direction') == '负', seeded


@pytest.mark.parametrize('direction', ['胜', '平', '负'])
def test_direction_command_works_for_every_direction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, direction: str,
) -> None:
    saved, _sent = _run_direction_command(monkeypatch, tmp_path, _record(), direction)
    seeded = app.seed_direction_lock_from_cs(saved)
    assert seeded.get('direction_lock', {}).get('direction') == direction, seeded


def test_direction_command_builds_a_lock_when_the_record_had_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """原本没有锁时也必须建得出来——不许只支持「覆盖已有锁」。"""
    rec = _record()
    rec.pop('direction_lock', None)
    saved, _sent = _run_direction_command(monkeypatch, tmp_path, rec, '平')
    read_back = DA.matrix_direction_lock({'direction_lock': saved.get('direction_lock')})
    assert read_back['locked'] is True, saved.get('direction_lock')
    assert read_back['direction'] == '平', saved.get('direction_lock')
    assert app.seed_direction_lock_from_cs(saved).get('direction_lock', {}).get('direction') == '平'


def test_direction_command_keeps_scores_when_the_direction_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """方向相同不得丢 scores：那是一份已经算出来的同向比分清单。"""
    rec = _record(direction_lock={
        'locked': True, 'direction': '胜', 'scores': ['2-1', '3-1'], 'source': 'operator',
    })
    saved, _sent = _run_direction_command(monkeypatch, tmp_path, rec, '胜')
    lock = saved.get('direction_lock') or {}
    assert lock.get('scores') == ['2-1', '3-1'], lock
    assert lock.get('direction') == '胜', lock


def test_direction_command_does_not_replace_a_valid_lock_with_an_unlocked_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """旧锁的方向与命令不同时，新锁必须是**命令方向**，不能保留旧方向。

    桩版本在这里把整把锁换成 `{'available': False, 'direction': ''}`：
    既不是旧方向也不是新方向，`locked` 直接消失。
    """
    rec = _record(direction_lock={
        'locked': True, 'direction': '胜', 'scores': ['2-1'], 'source': 'operator',
    })
    saved, _sent = _run_direction_command(monkeypatch, tmp_path, rec, '负')
    lock = saved.get('direction_lock')
    assert isinstance(lock, dict) and lock.get('locked') is True, lock
    assert lock.get('direction') == '负', lock
    # 胜方向的比分不能跟着新锁留下来
    assert lock.get('scores') == [], lock


# --------------------------------------------------------------------------- #
# 3. 第 3 条取舍：旧比分保留 + 正文标注（不是静默清空）
# --------------------------------------------------------------------------- #
def test_apply_operator_direction_overrides_a_stale_lock_already_in_the_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """payload 里带着旧锁时，`apply_operator_direction` 是唯一改写它的东西。

    `handle_tg_command` 会 pop 掉 bundle，所以在命令这条路上 payload 一般是重建的。
    但 emit 还有别的入口（阶段1落盘后的即时研判），那时记录里的 `operator_direction`
    已经写了、bundle 却可能还是上一轮那份、`direction_lock` 还是旧方向。
    如果 `apply_operator_direction` 不就地改写 payload，模型拿到的就是旧方向，
    报告会照着旧方向出——这正是本次要修的那个空闸。
    """
    captured: dict[str, Any] = {}

    def _spy(messages=None, *a, **k):
        captured['messages'] = messages
        return '🧠【DeepSeek 数据分析研判报告】\n①比分 0-1 ②比分 0-2\n'

    rec = _record(
        operator_direction='负',
        direction_lock={'locked': True, 'direction': '负', 'scores': ['0-1'], 'source': 'operator'},
        # 上一轮遗留的 bundle，payload 里的锁还是 胜
        pipeline_report_bundle={'ds_payload': {
            'match': MATCH_NAME,
            'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-1', '3-1'],
                               'source': 'operator'},
        }},
    )
    history_file = _seed_history(monkeypatch, tmp_path, rec)
    monkeypatch.setattr(app, '_tg_post', lambda url, payload: True)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(pipe, 'deepseek_chat_completion', _spy, raising=False)
    monkeypatch.setattr(pipe, '_LAST_COMPLETION_LOCAL', False, raising=False)
    monkeypatch.setattr(pipe, '_LAST_COMPLETION_ERROR', '', raising=False)

    with contextlib.redirect_stdout(io.StringIO()):
        app.emit_pipeline_deepseek_tg_report({'id': FID, 'cn_name': MATCH_NAME}, i=0, total=1)

    prompt = '\n'.join(str(m.get('content', '')) for m in (captured.get('messages') or []))
    assert prompt, '模型调用没有发生'
    assert '"direction": "负"' in prompt or '"direction":"负"' in prompt, prompt[-1200:]
    assert '"direction": "胜"' not in prompt and '"direction":"胜"' not in prompt, prompt[-1200:]
    _ = history_file


def test_report_discloses_the_pre_command_score_instead_of_showing_bare_no_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """重算没产出新方向比分时，正文必须说明「这是指令前的比分」。

    取舍理由见 `emit_pipeline_deepseek_tg_report` 里 `operator_direction_recalc`
    的注释：清空 payload 比分对报告没有任何影响（正文读 bundle/记录），
    而真去清空记录会让重算失败变成「无数据」，同时把一份真实比分抹掉。
    """
    monkeypatch.setattr('requests.post', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('ConnectionError: boom')))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')

    def _fail(*a, **k):
        raise RuntimeError('ConnectionError: boom')

    rec = _record(direction_lock={
        'locked': True, 'direction': '胜', 'scores': ['2-1', '2-0'], 'source': 'operator',
    })
    saved, sent = _run_direction_command(monkeypatch, tmp_path, rec, '负', model=_fail)
    assert sent, '模型失败也必须发正文'
    body = '\n'.join(sent)
    assert '操作员已指定方向【负】' in body, body[:800]
    assert '指令下达前' in body or '未被清空' in body, body[:800]
    # 记录里的真实比分没有被抹掉（这是取舍的一半：不销毁数据）
    assert saved.get('predicted_score') == '2-1', saved.get('predicted_score')


def test_report_claims_recomputed_only_when_the_shown_scores_match_the_direction() -> None:
    """标注只在「展示的比分确实是这个方向」时才说「已按…重算」。"""
    recalc = {'direction': '负', 'previous_first': '2-1', 'previous_second': '2-0'}
    done = DA.format_operator_direction_recalc_line(recalc, '0-1', '0-2')
    assert '已按操作员指定方向【负】重算' in done, done
    assert '指令下达前' not in done, done

    stale = DA.format_operator_direction_recalc_line(recalc, '2-1', '2-0')
    assert '未产出该方向的比分' in stale, stale
    assert '2-1' in stale and '2-0' in stale, stale

    nothing = DA.format_operator_direction_recalc_line(recalc, '', '')
    assert '无数据' in nothing, nothing
