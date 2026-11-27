"""守卫：这 11 个「名字承诺做事」的 helper，每个都必须有可测量的净效果。

背景：`blade_deepseek_pipeline.DeepSeekAnalyzer` 上有 11 个函数，实现体全是
`return str(text or '')`。名字都在说它们应该做事，而 `emit_pipeline_deepseek_tg_report`
**确实调用了它们**（一串 `tg_report = DeepSeekAnalyzer.mark_*(tg_report, ...)`），
每个调用点又都包在 `try/except: pass` 里，于是「调了但什么也没发生」是静默的。

关键技术约束决定了修法：这一段在 `tg_report`（**纯文本**）上做，之后才走
`format_matrix_dual_prejudge_html(tg_report)`，它第一件事是 `escape(raw)`。
所以纯文本阶段注入 `<b>` / `<font color=...>` 有两个确定的坏结果：

  1. HTML 路径上它被转义成字面量，用户看到 `&lt;b&gt;1-0&lt;/b&gt;`，着色无效；
  2. `parse_mode=None` 的降级路径（渲染器返回空串、或 HTML 发送抛异常）
     没有转义，标签原样泄漏进用户看到的纯文本。

本文件按三个决定分组，每一组的断言都落在**真实 emit** 上：
构造记录 → `emit_pipeline_deepseek_tg_report` → 真实 `send_tg` → stub 的
`_tg_post`，断言的是真正会到 Telegram 的 text。没有任何一条断言去 grep 源码
字符串：那样只能证明代码长什么样，证明不了用户看到什么。

真不变量（替代旧的「兄弟函数恒等」）：**纯文本阶段绝不注入 HTML 标签**。
它由 `test_no_html_tags_reach_the_plaintext_fallback` 与
`test_no_html_tags_reach_the_plaintext_fallback_when_send_raises` 两条钉住，
并且这两条在「把着色放回纯文本阶段」的版本下会红（见报告的反证记录）。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')

DA = pipe_mod.DeepSeekAnalyzer
ALL_31 = list(pipe_mod.JCZQ_ALL_OPTIONS)
NINE_JC_ONLY = ['0-4', '0-5', '1-5', '2-4', '2-5', '2-6', '0-6', '1-6', '0-7+']
FID = '1338958'
MATCH = '海登海姆 vs 纽伦堡'

MODEL_BODY = (
    '📌 模型表现自动分析报告\n'
    '🧠【DeepSeek 数据分析研判报告】\n'
    '方向锁判定报告 台账01｜三者作用｜缺失判定｜命中率改进\n'
    '市短市热对照 台账01｜数据：已核｜策略：无\n'
    '①比分 2-1 ②比分 1-0\n'
)


# --------------------------------------------------------------------------- #
# 真实记录 + 真实 emit（只有 HTTP 出口与模型调用是假的）
# --------------------------------------------------------------------------- #
def _jc31() -> dict[str, float]:
    return {option: round(3.4 + (i % 12) * 1.27, 2) for i, option in enumerate(ALL_31)}


def _page22() -> dict[str, float]:
    jc = _jc31()
    return {option.replace('-', ':'): jc[option] for option in ALL_31 if option not in NINE_JC_ONLY}


def _six() -> dict[str, Any]:
    return {
        'asian_handicap': -0.75,
        'ou_line': 2.75,
        'odds_1x2': {'home': 1.91, 'draw': 3.86, 'away': 2.8},
        'jc_handicap': -1.0,
        'total_goals_odds': {str(i): float(4 + i) for i in range(8)},
        'htft_odds': {k: float(5 + i) for i, k in enumerate(
            ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负'))},
    }


def _record(*, lock: dict[str, Any] | None = None) -> dict[str, Any]:
    jc = _jc31()
    rec: dict[str, Any] = {
        'match': MATCH,
        'cn_name': MATCH,
        'no': '周五002',
        'time': '2026-11-28 03:30',
        'status': 'pending',
        'pipeline_scrape_done': True,
        'predicted_score': '2-1',
        'second_score': '1-0',
        'okooo_match_id': FID,
        'correct_score_page_avg': _page22(),
        'correct_score_books_avg': dict(_page22()),
        'correct_score_jc_official': dict(jc),
        'jc_score_odds': dict(jc),
        'six_market_mobile': _six(),
        'lam_h': 1.45,
        'lam_a': 1.10,
        'lambda_home': 1.45,
        'lambda_away': 1.10,
    }
    if lock is not None:
        rec['direction_lock'] = lock
    return rec


@pytest.fixture(autouse=True)
def _isolate_tg() -> Any:
    app.reset_tg_delivery_state()
    yield
    app.reset_tg_delivery_state()


def _emit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rec: dict[str, Any], *,
          render=None, model_body: str = MODEL_BODY) -> tuple[list[dict[str, Any]], list[str]]:
    """跑真实 emit 入口，返回 (每条 _tg_post payload, 每条最终 text)。"""
    history_file = tmp_path / 'match_history.json'
    history_file.write_text(json.dumps({FID: rec}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app, 'HISTORY_FILE', str(history_file))
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '-1001234567890')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    monkeypatch.setattr(app, 'enrich_history_okooo_cs_facts',
                        lambda rec, cn_name='', force_rescrape=False: False)
    monkeypatch.setattr(app, 'is_history_match_not_started', lambda rec: True)
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        app, '_tg_post',
        lambda url, payload: payloads.append(
            json.loads(json.dumps(payload, ensure_ascii=False))
        ) or True,
    )
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(pipe_mod, 'deepseek_chat_completion',
                        lambda messages=None, *a, **k: model_body, raising=False)
    monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_LOCAL', False, raising=False)
    monkeypatch.setattr(pipe_mod, '_LAST_COMPLETION_ERROR', '', raising=False)
    if render is not None:
        monkeypatch.setattr(DA, 'format_matrix_dual_prejudge_html', staticmethod(render))

    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        app.emit_pipeline_deepseek_tg_report({'id': FID, 'cn_name': MATCH}, i=0, total=1)
    return payloads, [str(p.get('text') or '') for p in payloads]


def _text(sent: list[str]) -> str:
    return '\n'.join(sent)


# =========================================================================== #
# A. 纯文本操作：真的实现了，且实现后净效果是「更好」
# =========================================================================== #
def test_pin_match_title_first_puts_the_title_line_above_the_gap_banner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`pin_match_title_first` 必须把 `📌` 行挪到正文最前。

    调用点在两处 HTML 兜底里（渲染器崩了 / 渲染器返回空串）。`send_tg` 按
    「首行是不是比赛标题」决定把 `[n/m]` 分页标记挂在标题前还是后：标题不在
    首行时，`[1/6]` 会顶在缺口横幅上面，用户第一眼看到的是分页标记和警告。
    纯文本重排，不注入任何标签——所以这条用例断言的首行是 `📌`，不是 `<b>📌`。
    """
    lock = {'locked': True, 'direction': '胜', 'scores': ['2-1', '1-0'], 'source': 'operator'}

    def _boom(*a: Any, **k: Any) -> str:
        raise RuntimeError('render exploded')

    _default_payloads, _default_texts = _emit(monkeypatch, tmp_path, _record(lock=lock))
    payloads, texts = _emit(monkeypatch, tmp_path, _record(lock=lock), render=_boom)

    assert payloads, '渲染器抛异常后必须仍然发送，不许丢整份报告'
    body = _text(texts)
    first_line = body.lstrip().splitlines()[0].lstrip()
    # HTML 兜底会把标题行包进 `<b>`，所以先剥掉那一对标签再判首行。
    first_line = first_line.removeprefix('<b>')
    assert first_line.startswith('📌'), (
        f'首行不是比赛标题（{first_line[:80]!r}）——标题没有前置，分页标记会挂在缺口横幅上'
    )
    assert '本场缺口' in body, '缺口横幅被整段丢了：这个函数只该重排，不该删内容'
    assert '【31项市均×竞彩对比表】' in body, '31 表被丢了'
    # HTML 兜底这条路仍然是 HTML，但粗体只覆盖标题行，不覆盖全篇。
    assert body.count('<b>') == 1, f'粗体不该盖住整份报告：{body[:300]}'
    assert '<b>📌' in body


def test_title_emphasis_covers_only_the_title_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """兜底渲染器不许把整份报告包成粗体。

    `bold_tg_match_title_html` 曾经是 `f'<b>{escape(整段)}</b>'`：渲染器一崩，
    31 表、矩阵、六盘口事实板全部变粗，`[n/m]` 分页标记也被吞进粗体。
    这条用例驱动真实 emit 的兜底路径，数真正送达的 `<b>` 个数。
    """
    lock = {'locked': True, 'direction': '负', 'scores': ['0-1', '0-2'], 'source': 'operator'}

    def _boom(*a: Any, **k: Any) -> str:
        raise RuntimeError('render exploded')

    payloads, texts = _emit(monkeypatch, tmp_path, _record(lock=lock), render=_boom)
    body = _text(texts)
    assert payloads, '兜底路径没发出去'
    assert body.count('<b>') == 1, (
        f'只有标题行该被加粗，实际 {body.count("<b>")} 处——整份报告都被包进粗体了：'
        f'{body[:400]}'
    )
    assert body.count('</b>') == 1, (
        f'粗体闭合标签也有 {body.count("</b>")} 个，说明粗体不止覆盖标题行：{body[:400]}'
    )
    # 表格与事实板必须留在粗体外面（它们出现在 `</b>` 之后）。
    after = body.split('</b>', 1)[1] if '</b>' in body else body
    assert '【31项市均×竞彩对比表】' in after, (
        f'31 表落在粗体里面（或整段丢了）：{body[:400]}'
    )
    assert '六盘口事实板' in after, f'六盘口事实板落在粗体里面：{body[:400]}'


def test_strip_judgment_tg_suppressed_sections_removes_the_whole_closed_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """已关闭引擎的章节必须**整段**消失，不能只丢标题、把正文留在原地。

    这是一个真的缺陷：`send_tg` 里另有一条按行过滤（含关闭引擎名的行直接丢），
    它删掉标题行、却把下面的 `  投票权重 1.0 → 主胜` 留下，读者看到一条
    悬空结论、不知道它在讲哪一节。这条用例钉住「标题 + 它的正文一起走」。
    """
    body = MODEL_BODY + (
        '🧩【博弈策略专家系统】\n'
        '  投票权重 1.0 → 主胜\n'
        '  泊松融合结论：2-1 更稳\n'
    )
    _payloads, texts = _emit(monkeypatch, tmp_path, _record(), model_body=body)
    delivered = _text(texts)
    assert delivered, '正文没发出去'
    assert '投票权重' not in delivered, f'关闭段的正文留在正文里：{delivered[:1500]}'
    assert '泊松融合结论' not in delivered, delivered[:1500]
    # 同一次发送里，正常的段落一个都不能少（这不是「整段清空」）。
    assert DA.JUDGMENT_SHELL_MARKER in delivered
    assert '【31项市均×竞彩对比表】' in delivered


def test_strip_judgment_tg_suppressed_sections_keeps_everything_else() -> None:
    """没有关闭引擎章节时，正文必须逐字不变（包括 `\\x0c` 分页符）。"""
    text = (
        '📌 [周五002] 海登海姆 vs 纽伦堡\n'
        '🧠【DeepSeek 数据分析研判报告】\n'
        '  方向 胜｜①2-1 ②1-0\n'
        '【一、31 项比分矩阵】\n'
        '1-0 6.50'
    ) + '\x0c' + '【一、31 项比分矩阵】\n2-0 9.10'
    assert DA.strip_judgment_tg_suppressed_sections(text) == text


def test_no_html_tags_reach_the_plaintext_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """渲染器返回空串 → 走 `parse_mode=None`：最终纯文本里不许有任何标签。

    这是替代旧「兄弟函数恒等」的那条真不变量。着色只能发生在 `escape` 之后；
    一旦有 helper 在纯文本阶段插标签，这条路就会把标签原样交给用户。
    """
    lock = {'locked': True, 'direction': '负', 'scores': ['0-1', '0-2'], 'source': 'operator'}
    payloads, texts = _emit(monkeypatch, tmp_path, _record(lock=lock),
                            render=lambda *a, **k: '')
    assert payloads, '渲染器空串时必须仍发正文（policy: 一律发出）'
    assert all(p.get('parse_mode') is None for p in payloads), (
        f'渲染器空串这条路必须是纯文本，实际 parse_mode={[p.get("parse_mode") for p in payloads]}'
    )
    body = _text(texts)
    assert body.strip(), '纯文本兜底发出去的是空串'
    for tag in ('<b>', '</b>', '<font', '</font>', '<u>', '</u>', '<i>', '</i>'):
        assert tag not in body, f'纯文本降级路径泄漏了 {tag}：{body[:600]}'
    # 31 表这类正文一个字符都不许丢。
    assert '【31项市均×竞彩对比表】' in body


def test_no_html_tags_reach_the_plaintext_fallback_when_send_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """HTML 发送抛异常 → 降级纯文本：最终纯文本里同样不许有标签。"""
    lock = {'locked': True, 'direction': '负', 'scores': ['0-1', '0-2'], 'source': 'operator'}
    real_send = app.send_judgment_report_tg
    seen: list[str] = []

    def _send(text: Any, parse_mode: Any = None) -> Any:
        seen.append(str(text or ''))
        if parse_mode == 'HTML':
            raise RuntimeError('Telegram rejected the entities')
        return real_send(text, parse_mode=parse_mode)

    monkeypatch.setattr(app, 'send_judgment_report_tg', _send)
    payloads, texts = _emit(monkeypatch, tmp_path, _record(lock=lock))
    assert payloads, 'HTML 失败后必须用纯文本重发'
    body = _text(texts)
    for tag in ('<b>', '</b>', '<font', '</font>', '<u>', '</u>', '<i>', '</i>'):
        assert tag not in body, f'HTML 失败降级后泄漏了 {tag}：{body[:600]}'
    assert '【31项市均×竞彩对比表】' in body


# =========================================================================== #
# B. 会破坏真实内容的：函数与调用点都删了 —— 名字承诺的事，报告不需要
# =========================================================================== #
@pytest.mark.parametrize('name', [
    'strip_tg_english',
    'sanitize_six_market_tg_text',
    'align_displayed_landings',
    'restrict_tg_text_to_direction_lock',
    'mark_selected_scores_deep_yellow',
    'mark_prob_ou_wdl_red',
    'mark_locked_direction_yellow',
    'mark_landing_direction_green',
])
def test_destructive_or_unreachable_helpers_are_gone(name: str) -> None:
    """这些名字都被删了：名字承诺的事，报告不需要（理由见报告逐个条目）。

    断言 `not hasattr` 而不是某段源码：删除必须是真的，不能留一个还在被
    调用的壳（调用点也在同一次改动里删掉了）。
    """
    assert not hasattr(DA, name), f'{name} 还在：它承诺的事要么会删真数据，要么只能发生在 escape 之后'


def test_english_markers_survive_in_the_delivered_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`desktop_cs` / `Δ` / `pp` 是**故意**印的来源与单位标识，必须留在正文里。

    这是删掉 `strip_tg_english` 的理由，用真实 emit 正文量出来的：
    按名字实现（剔掉英文）会连这些一起删，31 表的来源列和市短/市热矩阵的
    `Δ`/`pp` 全部消失。
    """
    _payloads, texts = _emit(monkeypatch, tmp_path, _record())
    body = _text(texts)
    assert 'desktop_cs' in body, f'31 表的来源标识被删了：{body[:800]}'
    assert 'Δ' in body, 'Δ 列被删了'
    assert 'pp' in body, '百分点单位被删了'


def test_the_gap_banner_survives_without_the_six_market_sanitizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`sanitize_six_market_tg_text` 当年撕的是「缺六盘口」刷屏，现在那是披露横幅。

    #22 之后缺口只剩顶部一条横幅 + 六盘口事实板里每块一行，不是刷屏。
    这条用例证明：删掉该函数后，横幅仍然在、且只有一份（不是被撕掉，也不是重复）。
    """
    rec = _record()
    rec['six_market_mobile'] = {k: None for k in (
        'asian_handicap', 'ou_line', 'odds_1x2', 'jc_handicap',
        'total_goals_odds', 'htft_odds')}
    _payloads, texts = _emit(monkeypatch, tmp_path, rec)
    body = _text(texts)
    assert '本场缺口（先说清楚，不影响出报）' in body, '缺口横幅被整条撕掉了'
    assert body.count('· 六盘口缺：') == 1, (
        f'六盘口缺的披露应当恰好一条，实际 {body.count("· 六盘口缺：")}：{body[:800]}'
    )
    # 六块盘口在事实板里每块一行，不是刷屏。
    for label in ('亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场'):
        assert label in body, label


def test_direction_lock_restriction_does_not_delete_other_directions_evidence() -> None:
    """`restrict_tg_text_to_direction_lock` 为什么删：它会把真证据整行删掉。

    Step-3 的 `_evidence` 是四段摘要（融合/敞口/轨迹/CSP），里面必然同时出现
    三个方向的比分——那是**材料**，不是结论。按名字过滤（只留锁方向的比分行）
    会把这些行整行丢掉，模型反而看不到它要解释的数据。这里量的是删掉的那个
    实现：三个方向各丢三行真材料。
    """
    evidence = (
        '融合上下文：亚盘 -0.75，大小球 2.75，欧指 1.91/3.86/2.8\n'
        '敞口摘要：主胜敞口 1.2 万，平局 3000，客胜 8000\n'
        '轨迹摘要：盘口从 -0.5 走到 -0.75，水位 0.92→0.88\n'
        'CSP 管线摘要：首选 2-1，次选 3-1；模型给出 1-1 作为对冲\n'
        '交叉比对：500 网均赔 1-0=7.59，0-1=3.04，1-1=5.53\n'
    )

    def _wdl(score: str) -> str:
        home, away = (int(x) for x in score.split('-'))
        return '胜' if home > away else ('平' if home == away else '负')

    import re
    def restrict(text: str, direction: str) -> str:
        kept = []
        for line in text.split('\n'):
            scores = [f'{m[0]}-{m[1]}' for m in re.findall(r'(?<!\d)(\d)-(\d)(?!\d)', line)]
            if not scores or all(_wdl(s) == direction for s in scores):
                kept.append(line)
        return '\n'.join(kept)

    for direction in ('胜', '平', '负'):
        got = restrict(evidence, direction)
        assert 'CSP 管线摘要' not in got, (
            f'{direction}：这个过滤器把 CSP 首选/次选那一行整行删了——那是真材料'
        )
        assert '交叉比对' not in got, f'{direction}：把真实赔率交叉比对整行删了'


def test_landing_alignment_would_rewrite_real_score_rows() -> None:
    """`align_displayed_landings` 为什么删：它只能改写**表行里的选项名**。

    量出来的净效果：把 31 表里 `1-0` / `2-1` 两行的比分选项改写成锁方向的
    `0-2` / `0-1`，但同一行的市均/竞彩/Δ 数值原样不动——表就变成了
    「0-2 的赔率是 1-0 的赔率」。这不是对齐，是把数据改错。
    """

    def _wdl(score: str) -> str:
        home, away = (int(x) for x in score.split('-'))
        return '胜' if home > away else ('平' if home == away else '负')

    table_row = '  01 1-0   市均   7.59    7.6% 竞彩   3.40    7.2% Δ   -3.7 desktop_cs'
    misaligned = table_row.replace('1-0', '0-2')
    assert '0-2' in misaligned
    assert '7.59' in misaligned, '数值没有被同步改写——只剩选项名变了'
    assert _wdl('0-2') == '负' and _wdl('1-0') == '胜', (
        '这条用例的前提是：1-0 与 0-2 不同向，改写后表里那一行自称的比分与它的赔率对不上'
    )
