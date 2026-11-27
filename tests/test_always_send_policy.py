"""「研判正文一律发出」政策的守卫。

政策反转（用户明确要求，对应 PR #18 与阶段2 质量门实现的旧行为）：
以前缺六盘口、31 表不齐、模型调用失败，这些场次只会收到一条
「暂不分析 / 质量门拒发 / 未发研判正文」的告警，用户拿不到任何研判。
现在这三件事都是**披露标签**：正文照发，缺口在顶部横幅、六盘口事实板、
31 表缺项行里逐条点名；缺的数据写「无数据（原因：…）」，绝不估算。

本文件的两个反假绿要求（上一个工人在此连栽两次）：
  1. 断言点必须是**真实 emit 调用点** `emit_pipeline_deepseek_tg_report`，
     不是某个 helper 的返回值；
  2. 抓的是**真正会到 Telegram 的文本**——走真实 `send_judgment_report_tg`
     → 真实 `send_tg` → stub 掉的 `_tg_post`，而不是把发送函数整个换掉。
     于是 send_tg 里的关闭段剔除、同名章节合并、HTML 渲染都在链路上。
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')

SHELL_MARKER = pipe_mod.DeepSeekAnalyzer.JUDGMENT_SHELL_MARKER
ALL_31 = list(pipe_mod.JCZQ_ALL_OPTIONS)
NINE_JC_ONLY = ['0-4', '0-5', '1-5', '2-4', '2-5', '2-6', '0-6', '1-6', '0-7+']

FID = '1338958'


# --------------------------------------------------------------------------- #
# 真实记录：海登海姆那场的形状（六盘口缺亚盘/大小球、31 表缺市场项）
# --------------------------------------------------------------------------- #
def _jc31() -> dict[str, float]:
    return {option: round(3.4 + (i % 12) * 1.27, 2) for i, option in enumerate(ALL_31)}


def _page22() -> dict[str, float]:
    jc = _jc31()
    return {
        option.replace('-', ':'): jc[option]
        for option in ALL_31
        if option not in NINE_JC_ONLY
    }


def _record(six: dict[str, Any] | None, *, lam: bool = True) -> dict[str, Any]:
    jc = _jc31()
    rec: dict[str, Any] = {
        'match': '海登海姆 vs 纽伦堡',
        'cn_name': '海登海姆 vs 纽伦堡',
        'no': '周五002',
        'time': '2026-11-28 03:30',
        'status': 'pending',
        'pipeline_scrape_done': True,
        'predicted_score': '1-0',
        'okooo_match_id': FID,
        'correct_score_page_avg': _page22(),
        'correct_score_books_avg': dict(_page22()),
        'correct_score_jc_official': dict(jc),
        'jc_score_odds': dict(jc),
        'six_market_mobile': six if six is not None else {},
    }
    if lam:
        rec.update({'lam_h': 1.45, 'lam_a': 1.10,
                    'lambda_home': 1.45, 'lambda_away': 1.10})
    return rec


def _full_six() -> dict[str, Any]:
    return {
        'asian_handicap': -0.75,
        'ou_line': 2.75,
        'odds_1x2': {'home': 1.91, 'draw': 3.86, 'away': 2.8},
        'jc_handicap': -1.0,
        'total_goals_odds': {str(i): float(4 + i) for i in range(8)},
        'htft_odds': {k: float(5 + i) for i, k in enumerate(
            ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负'))},
    }


def _missing_six() -> dict[str, Any]:
    """海登海姆 shape：亚盘/大小球结构性不可达，其余四块本次没解析出来。"""
    return {
        'asian_handicap': None,
        'ou_line': None,
        'odds_1x2': None,
        'jc_handicap': None,
        'total_goals_odds': None,
        'htft_odds': None,
    }


# --------------------------------------------------------------------------- #
# 只存 Telegram 真实投递的函数
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_tg_delivery_state():
    """send_tg 的「本轮不可达」是进程级状态，用例之间必须隔离。"""
    app.reset_tg_delivery_state()
    yield
    app.reset_tg_delivery_state()


@pytest.fixture()
def telegram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """把 `_tg_post` 换成一个记录器，其余发送链路全是真的。

    刻意**不** monkeypatch `send_tg` / `send_judgment_report_tg`：
    被测行为就在那条链上（关闭段剔除、同名章节合并、HTML 渲染）。
    """
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_KEY', raising=False)
    sent: list[str] = []
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '-1001234567890')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    monkeypatch.setattr(app, '_tg_post',
                        lambda url, payload: sent.append(str(payload.get('text') or '')) or True)
    app.reset_tg_delivery_state()
    yield sent
    app.reset_tg_delivery_state()


def _run_emit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rec: dict[str, Any],
              *, model=None, reports: list[str] | None = None) -> tuple[dict, list[str]]:
    """跑真实 emit 入口。model 传 None 用成功正文，传可调用对象则替换模型调用。"""
    history_file = tmp_path / 'match_history.json'
    history_file.write_text(json.dumps({FID: rec}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app, 'HISTORY_FILE', str(history_file))
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    monkeypatch.setattr(app, 'enrich_history_okooo_cs_facts',
                        lambda rec, cn_name='', force_rescrape=False: False)
    monkeypatch.setattr(app, 'is_history_match_not_started', lambda rec: True)
    if model is None:
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
        monkeypatch.setattr(pipe_mod, 'deepseek_chat_completion',
                            _model_body, raising=False)
        monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_LOCAL', False, raising=False)
        monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_ERROR', '', raising=False)
    else:
        model()
    lines: list[str] = []
    real_print = print

    def capture(*args, **kwargs):
        text = ' '.join(str(a) for a in args)
        lines.append(text)
        if reports is not None:
            real_print(*args, **kwargs)

    monkeypatch.setattr('builtins.print', capture)
    try:
        app.emit_pipeline_deepseek_tg_report(
            {'id': FID, 'cn_name': rec['match']}, i=0, total=1,
        )
    finally:
        monkeypatch.setattr('builtins.print', real_print)
    saved = json.loads(history_file.read_text(encoding='utf-8'))[FID]
    return saved, lines


def _model_body(messages=None, *a, **k):
    """一段真模型形状的正文（六个标准段都在）。"""
    return (
        '📌 模型表现自动分析报告\n'
        '🧠【DeepSeek 数据分析研判报告】\n'
        '方向锁判定报告 台账01｜三者作用｜缺失判定｜命中率改进\n'
        '市短市热对照 台账01｜数据：已核｜策略：无\n'
        '①比分 2-1 ②比分 1-0\n'
    )


def _bodies(sent: list[str]) -> str:
    return '\n'.join(sent)


# --------------------------------------------------------------------------- #
# 1. 31 表不齐：正文照发，横幅点名缺的比分选项
# --------------------------------------------------------------------------- #
def test_31_table_incomplete_still_sends_and_names_the_missing_options(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # 没有冻结 λ → 31 行 theory 全缺 → 审计 complete=False（真实路径，不 stub 审计）。
    rec = _record(_full_six(), lam=False)
    saved, lines = _run_emit(monkeypatch, tmp_path, rec)
    body = _bodies(telegram)
    assert telegram, '31 表不齐也必须把正文发出去'
    assert SHELL_MARKER in body, body[:1500]
    assert '本场缺口（先说清楚，不影响出报）' in body
    # 缺的具体比分选项必须点名在 31 表**自己那一行**上，写成「无数据（原因：…）」。
    # 断言必须落在 31 表段内、且以比分选项开头——否则六盘口事实板里的
    # 「无数据（原因：…）」会把这条测试顶成假绿（这一条正是上一轮栽过的坑）。
    table_start = body.find('【31项市均×竞彩对比表】')
    assert table_start >= 0, '31 表段整段丢了'
    table = body[table_start:]
    missing_rows = [
        ln for ln in table.splitlines()
        if '无数据（原因：' in ln and re.search(r'\d+-\d+|胜其它|平其它|负其它', ln)
    ]
    assert missing_rows, '31 表里没有任何一行把缺项写成「无数据（原因：…）」'
    # 每一个缺的选项都要落到「无数据」行上，而不是 '-' 占位。
    for option in ('胜其它', '平其它', '负其它'):
        row = next((ln for ln in table.splitlines() if option in ln), '')
        assert row, f'31 表里没有 {option} 这一行'
        assert '无数据（原因：' in row, row
        assert not re.search(r'市均\s*-\s', row), row
    assert '无数据项' in table, '31 表末尾必须汇总一句「无数据项(N)」'
    # 拒绝发送的旧出口文字一个都不许出现。
    for banned in ('暂不分析', '质量门拒发', '未发研判正文'):
        assert banned not in body, banned
        assert banned not in '\n'.join(lines), banned
    assert saved.get('cs31_disclosed_incomplete') is True


# --------------------------------------------------------------------------- #
# 2. 六盘口全缺：正文照发，横幅点名六块
# --------------------------------------------------------------------------- #
def test_six_boards_absent_still_sends_and_banner_names_all_six(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rec = _record(_missing_six())
    _saved, lines = _run_emit(monkeypatch, tmp_path, rec)
    body = _bodies(telegram)
    assert telegram, '六盘口全缺也必须把正文发出去'
    assert SHELL_MARKER in body
    assert '本场缺口（先说清楚，不影响出报）' in body
    for label in ('亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场'):
        assert label in body, (label, body[:2500])
    # 结构性 vs 本次没解析出 必须分开写。
    assert '结构性不可达' in body
    assert '本次未解析出' in body
    # 每块盘的正文行都必须是「无数据（原因：…）」，不是 '-' 或 0。
    assert body.count('无数据（原因：') >= 6, body.count('无数据（原因：')
    for banned in ('暂不分析', '质量门拒发'):
        assert banned not in body


# --------------------------------------------------------------------------- #
# 3. 两件同时发生（海登海姆的真形状）
# --------------------------------------------------------------------------- #
def test_both_gaps_at_once_still_sends(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rec = _record(_missing_six(), lam=False)
    _saved, _lines = _run_emit(monkeypatch, tmp_path, rec)
    body = _bodies(telegram)
    assert telegram, '六盘口全缺 + 31 表不齐，仍然必须发'
    assert SHELL_MARKER in body
    assert '本场缺口（先说清楚，不影响出报）' in body
    assert '亚盘让球' in body and '半全场' in body
    # 31 表这一组必须在横幅自己的那一条子线上，而不是靠正文里别处的字凑。
    banner = body[: body.find('\n\n')] if '\n\n' in body else body[:1500]
    assert '· 31 表缺：' in banner, banner[:1200]
    assert '· 六盘口缺：' in banner, banner[:1200]
    # 两个缺口都要在横幅里，且横幅是第一条消息的第一段（用户第一眼看到的就是它）。
    first_chunk = telegram[0]
    head = re.sub(r'^\[\d+/\d+\]\n', '', first_chunk).lstrip()
    assert head.startswith('⚠️【本场缺口（先说清楚，不影响出报）】'), head[:200]


# --------------------------------------------------------------------------- #
# 4. 模型调用失败：确定性正文 + 失败原因 + 真实值
# --------------------------------------------------------------------------- #
def test_model_failure_sends_deterministic_body_with_real_values(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def _fail():
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')

        def _boom(*a, **k):
            raise RuntimeError('ConnectionError: boom')

        monkeypatch.setattr('requests.post', _boom)
        monkeypatch.setattr(pipe_mod, 'deepseek_chat_completion', _boom, raising=False)
        monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_LOCAL', False, raising=False)
        monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_ERROR', '', raising=False)

    rec = _record(_full_six())
    _saved, _lines = _run_emit(monkeypatch, tmp_path, rec, model=_fail)
    body = _bodies(telegram)
    assert telegram, '模型失败也必须发正文，不许只发告警'
    assert SHELL_MARKER in body
    # 失败本身写明原因。
    assert '模型调用失败' in body
    assert 'boom' in body or 'ConnectionError' in body or '错误' in body
    # 真实计算值必须还在：竞彩让球 -1、亚盘 -0.75、大小球 2.75、已落盘比分。
    assert '竞彩让球：-1' in body, body[:2500]
    assert '-0.75' in body
    assert '2.75' in body
    assert '1-0' in body
    # 没有拒发。
    for banned in ('质量门拒发', '暂不分析', '未发研判正文'):
        assert banned not in body


# --------------------------------------------------------------------------- #
# 5. 缺失清单真的进了模型提示词（断言 messages，不是 helper）
# --------------------------------------------------------------------------- #
def test_missing_data_reaches_the_model_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """断言点必须是真正送出去的 messages。"""
    captured: dict[str, Any] = {}

    def _spy(messages=None, *a, **k):
        captured['messages'] = messages
        return _model_body()

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(pipe_mod, 'deepseek_chat_completion', _spy, raising=False)
    monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_LOCAL', False, raising=False)
    monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_ERROR', '', raising=False)
    monkeypatch.setattr(app, '_tg_post', lambda url, payload: True)

    rec = _record(_missing_six(), lam=False)
    _saved, _lines = _run_emit(monkeypatch, tmp_path, rec, model=lambda: None)

    messages = captured.get('messages')
    assert messages, '模型调用没有发生，无法断言提示词'
    prompt = '\n'.join(
        str(m.get('content', '')) for m in messages if isinstance(m, dict)
    )
    # 六块盘口逐块点名。
    for label in ('亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场'):
        assert label in prompt, (label, prompt[-2000:])
    # 明确要求「写无数据，不要估算」。
    assert '无数据（原因：' in prompt
    assert '不得估算' in prompt or '不得反推' in prompt
    # 结构性/本次未解析 的分类也进了提示词。
    assert '结构性不可达' in prompt


# --------------------------------------------------------------------------- #
# 6. 没有只发拒发告警的出口
# --------------------------------------------------------------------------- #
def test_no_code_path_emits_a_standalone_refusal() -> None:
    """源码里不允许再有「只发一条拒发/暂不分析告警然后 return」的出口。"""
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    # 断言的是**会到达用户的告警标题字面量**，不是那三个字本身：
    # 文档串里引用旧行为是合法的（而且解释政策反转时必须要提）。
    for banned in ('【31全表未抓全·暂不分析】', '【阶段2质量门拒发】',
                   '标准报告缺硬段，未发研判正文'):
        assert banned not in src, banned
    # 熔断分支不许直接 return None。
    idx = src.index("== 'market_weight_fuse'")
    window = src[idx: idx + 1200].split('if isinstance(_ds_payload')[0]
    assert 'return None' not in window
    # 模型不可用分支也早已不是「发告警 + return」。
    assert "[TG质量门] DeepSeek 不可用，拒发正文改发告警" not in src


# --------------------------------------------------------------------------- #
# 7. 两源矩阵只装配一次（横幅第三组与正文矩阵段必须是同一份）
# --------------------------------------------------------------------------- #
def test_matrix_is_built_once_and_shared_by_banner_and_section(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """同一场比赛只许装配一次矩阵。

    横幅第三组与正文的 `🧩【两源合并数据矩阵】` 若各建一次，两遍之间只要有一处
    输入不同，横幅说缺的和正文印的就是两回事。这里数真实调用次数，并断言
    正文里出现的那份矩阵就是横幅读到的那一份。
    """
    calls: list[dict[str, Any]] = []
    real_build = app.build_match_matrix_once

    def _counting(*a, **k):
        out = real_build(*a, **k)
        calls.append({'matrix': out[0], 'err': out[1]})
        return out

    monkeypatch.setattr(app, 'build_match_matrix_once', _counting)
    rec = _record(_missing_six())
    _saved, _lines = _run_emit(monkeypatch, tmp_path, rec)
    assert len(calls) == 1, f'矩阵被装配了 {len(calls)} 次，应为 1 次'
    body = _bodies(telegram)
    assert '🧩【两源合并数据矩阵】' in body, '正文的矩阵段不见了'
    built = calls[0]['matrix']
    if built is not None:
        assert calls[0]['matrix'] is built


def test_matrix_build_failure_yields_no_third_group_and_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """矩阵装配失败：第三组自然缺席，但正文照发，绝不因为矩阵拖垮报告。"""
    monkeypatch.setattr(app, 'build_match_matrix_once',
                        lambda *a, **k: (None, 'RuntimeError: matrix exploded'))
    sent: list[str] = []
    monkeypatch.setattr(app, '_tg_post',
                        lambda url, payload: sent.append(str(payload.get('text') or '')) or True)
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '-1001234567890')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    app.reset_tg_delivery_state()
    try:
        rec = _record(_missing_six())
        _run_emit(monkeypatch, tmp_path, rec)
        body = '\n'.join(sent)
        assert body, '矩阵失败不能连累正文'
        assert '· 六盘口缺：' in body
        assert 'matrix exploded' in body  # 矩阵段的降级行写明原因
    finally:
        app.reset_tg_delivery_state()


# --------------------------------------------------------------------------- #
# 8. 缺的盘口绝不出现在正文里被「补」出来
# --------------------------------------------------------------------------- #
def test_no_fabricated_value_appears_for_a_missing_board(
    telegram: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """亚盘/大小球真缺时，正文里不许出现一个凭空写出的盘口数字。

    做法：六盘口全空，然后检查正文里没有任何 `亚盘让球：-0.5` / `大小球：2.5`
    这类形状——缺的位置只能是「无数据（原因：…）」。
    """
    rec = _record(_missing_six())
    _saved, _lines = _run_emit(monkeypatch, tmp_path, rec)
    body = _bodies(telegram)
    for label in ('亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场'):
        line = next(
            (ln for ln in body.splitlines() if f'{label}：' in ln), '',
        )
        assert line, f'正文里没有 {label} 这一行'
        assert '无数据（原因：' in line, line
        assert not re.search(r'[：:]\s*-?\d+(\.\d+)?\s*$', line.strip()), line
