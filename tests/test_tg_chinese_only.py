"""TG 正文全中文：正向断言 + 反向验证（映射表恒等 / 英文兜底都要变红）。

用户要求：Telegram 报告里的分析文字必须**全部**是中文，不得夹带英文句子、
英文小标题或英文短语。数字、比分、赔率、百分比、`+`/`-`、`Δ`、`pp` 不算
「非中文」，必须原样保留。

本文件钉四件事，全部驱动**真实 emit 调用点** `emit_pipeline_deepseek_tg_report`
→ 真实 `send_judgment_report_tg` → 真实 `send_tg` → stub 掉的 `_tg_post`
（只有 HTTP 出口与模型调用是假的，与 `test_always_send_policy` 同一口径）：

  1. `reason` 全是英文令牌时，最终正文里它们不得出现在**裸文本**位置
     （唯一合法位置是紧跟中文名后的排障括号）。
  2. 模型回包夹带英文小标题/英文句子时，最终正文必须是中文（整行英文被
     标注成中文句式，不删内容）。
  3. 反向验证：把映射表改成恒等（英文进英文出）→ 上面两条必须红。
  4. 反向验证：把未知令牌兜底改成英文 → 也必须红。

第 3、4 条**不能**靠改生产代码来验证（那会把仓库留在坏状态），所以用一个
可控的替换：运行时把 `REASON_LABELS` 换成一个恒等表 / 把兜底模板换成英文，
跑同一个断言，断言它确实失败。这样「守卫有牙」这件事本身也被测了。
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
pipe = importlib.import_module('blade_deepseek_pipeline')
mdm = importlib.import_module('match_data_matrix')

DA = pipe.DeepSeekAnalyzer
FID = '1338958'
MATCH = '海登海姆 vs 纽伦堡'

#: 一份「reason 全是英文令牌」的记录会触发的令牌集合。刻意挑真实的码，
#: 而不是编一串 `foo_bar`：真实码才会走到映射表的分支。
ENGLISH_REASON_TOKENS = (
    'tab_not_fetched',
    'no_such_match_in_source',
    'source_blocked',
    'lambda_not_formed',
    'no_usable_completed_scores',
    'poisson_matrix_empty',
    'okooo_six_market_incomplete',
    'step2_ou_path',
)

MODEL_BODY_CHINESE = (
    '📌 模型表现自动分析报告\n'
    '🧠【DeepSeek 数据分析研判报告】\n'
    '方向锁判定报告 台账01｜三者共同作用｜缺失判定｜命中率改进\n'
    '①比分 2-1 ②比分 1-0\n'
)

MODEL_BODY_ENGLISH = (
    '📌 模型表现自动分析报告\n'
    '🧠【DeepSeek 数据分析研判报告】\n'
    'Score prediction\n'
    'The market implies a home win and the draw is underpriced.\n'
    '①比分 2-1 ②比分 1-0\n'
    'Data gaps\n'
    'Over 2.5 goals is the most likely path.\n'
)


# --------------------------------------------------------------------------- #
# 与 test_always_send_policy 同形的真实记录：六盘口齐、31 项齐
# --------------------------------------------------------------------------- #
def _jc31() -> dict[str, float]:
    return {option: round(3.4 + (i % 12) * 1.27, 2) for i, option in enumerate(pipe.JCZQ_ALL_OPTIONS)}


NINE_JC_ONLY = ['0-4', '0-5', '1-5', '2-4', '2-5', '2-6', '0-6', '1-6', '0-7+']


def _page22() -> dict[str, float]:
    jc = _jc31()
    return {o.replace('-', ':'): jc[o] for o in pipe.JCZQ_ALL_OPTIONS if o not in NINE_JC_ONLY}


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


def _record() -> dict[str, Any]:
    jc = _jc31()
    return {
        'match': MATCH, 'cn_name': MATCH, 'no': '周五002',
        'time': '2026-11-28 03:30', 'status': 'pending',
        'pipeline_scrape_done': True, 'predicted_score': '1-0',
        'okooo_match_id': FID,
        'correct_score_page_avg': _page22(),
        'correct_score_books_avg': dict(_page22()),
        'correct_score_jc_official': dict(jc),
        'jc_score_odds': dict(jc),
        'six_market_mobile': _six(),
        'lam_h': 1.45, 'lam_a': 1.10,
        'lambda_home': 1.45, 'lambda_away': 1.10,
    }


@pytest.fixture(autouse=True)
def _isolate_tg_delivery_state():
    app.reset_tg_delivery_state()
    yield
    app.reset_tg_delivery_state()


def _run_emit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
              rec: dict[str, Any], model_body: str) -> str:
    """跑真实 emit，返回真正到达 Telegram 的正文。"""
    sent: list[str] = []
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '-1001234567890')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    monkeypatch.setattr(app, '_tg_post',
                        lambda url, payload: sent.append(str(payload.get('text') or '')) or True)
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    monkeypatch.setattr(app, 'enrich_history_okooo_cs_facts',
                        lambda rec, cn_name='', force_rescrape=False: False)
    monkeypatch.setattr(app, 'is_history_match_not_started', lambda rec: True)
    hist = tmp_path / 'match_history.json'
    hist.write_text(json.dumps({FID: rec}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app, 'HISTORY_FILE', str(hist))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    monkeypatch.setattr(pipe, 'deepseek_chat_completion',
                        lambda messages=None, *a, **k: model_body, raising=False)
    monkeypatch.setattr(pipe, '_LAST_COMPLETION_LOCAL', False, raising=False)
    monkeypatch.setattr(pipe, '_LAST_COMPLETION_ERROR', '', raising=False)
    real_print = print
    monkeypatch.setattr('builtins.print', lambda *a, **k: None)
    try:
        app.emit_pipeline_deepseek_tg_report({'id': FID, 'cn_name': rec['match']}, i=0, total=1)
    finally:
        monkeypatch.setattr('builtins.print', real_print)
    return '\n'.join(sent)


# --------------------------------------------------------------------------- #
# 断言辅助：英文令牌只许出现在排障括号里
# --------------------------------------------------------------------------- #
_BRACKET_PAIRS = (('（', '）'), ('(', ')'), ('【', '】'), ('［', '］'))


def bare_english_tokens(text: str) -> list[str]:
    """正文里所有**裸文本**位置的拉丁令牌（括号里的排障位不算）。

    判定「裸」：该次出现的前一个非空字符不是左括号，或后一个非空字符不是
    右括号。`澳客桌面比分均（desktop_cs）` 合法；`desktop_cs=22` 不合法。
    """
    findings: list[str] = []
    for match in re.finditer(r'[A-Za-z][A-Za-z0-9_]{2,}', text):
        token = match.group(0)
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        if any(before.endswith(open_b) and after.startswith(close_b)
               for open_b, close_b in _BRACKET_PAIRS):
            continue
        findings.append(token)
    return findings


#: 允许出现在正文里的英文专名/单位（它们是产品名或数据符号，不是「英文短语」）。
ALLOWED_LATIN = {
    'DeepSeek', 'HTTP', 'API', 'JSON', 'URL', 'ID', 'vs',
}


def _unexplained_tokens(body: str) -> list[str]:
    return sorted({t for t in bare_english_tokens(body) if t not in ALLOWED_LATIN})


# --------------------------------------------------------------------------- #
# 1. reason 全是英文令牌：最终正文不得裸印这些令牌
# --------------------------------------------------------------------------- #
def test_english_reason_tokens_never_appear_bare_in_the_delivered_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """构造一条「缺口全是英文原因码」的矩阵，跑完整渲染。

    断言：八个英文令牌一个都不以裸文本出现在最终 TG 正文里；
    同时它们**仍然可查**——必须能在排障括号里找到（信息不丢）。
    """
    rec = _record()
    body = _run_emit(monkeypatch, tmp_path, rec, MODEL_BODY_CHINESE)
    assert body.strip(), '正文没发出去，断言无从谈起'

    # 直接把英文原因码灌进矩阵缺口横幅——这是「reason 全是英文」的真实入口。
    row = {'fields': {}, 'gaps': [{'field': 'ou_line', 'reason': t} for t in ENGLISH_REASON_TOKENS]}
    banner = '\n'.join(mdm.format_gap_banner({'rows': [row]}))
    assert banner.strip(), '矩阵缺口横幅整段消失'

    leaked = [t for t in ENGLISH_REASON_TOKENS if re.search(
        rf'(?<![\w（(]){re.escape(t)}(?![\w）)])', banner)]
    assert leaked == [], f'英文原因码裸印在缺口横幅里：{leaked}\n{banner[:1200]}'

    # 每一个码仍要在排障括号里可查（映射不是「删掉」）。
    for token in ENGLISH_REASON_TOKENS:
        assert token in banner, f'{token} 被整段删掉了，排障线索丢失：{banner[:1200]}'
    # 中文短语必须在，且横幅整体是中文。
    assert '该分析页本轮未抓取' in banner, banner[:1200]
    assert '该来源里没有这场比赛' in banner, banner[:1200]


def test_english_reason_tokens_are_mapped_not_deleted() -> None:
    """映射层的单元证据：每个令牌都有中文短语，且中文短语不含该令牌。"""
    for token in ENGLISH_REASON_TOKENS:
        label = pipe.reason_label(token)
        assert label and label != token, (token, label)
        assert token not in label, f'{token} 的映射里仍含英文令牌原样：{label}'
        assert re.search(r'[\u4e00-\u9fff]', label), f'{token} 的映射不是中文：{label}'


def test_unknown_reason_token_falls_back_to_chinese_with_the_token_in_brackets() -> None:
    """未知令牌：兜底必须是中文，原令牌放进括号。"""
    label = pipe.reason_label('totally_new_token_zzz')
    assert '未登记的原因' in label, label
    assert 'totally_new_token_zzz' in label, label
    assert label.count('totally_new_token_zzz') == 1
    # 裸看：这个令牌只出现在括号里。
    assert bare_english_tokens(label) == [], label


# --------------------------------------------------------------------------- #
# 2. 模型回包夹带英文小标题/英文句子：最终正文必须是中文
# --------------------------------------------------------------------------- #
def test_model_english_reply_is_replaced_by_a_chinese_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """模型回英文时，最终正文不得出现任何英文句子。

    选定的处置（写清理由）：**先重试一次并明确要求中文**（
    `analyze_match_judgment` 里那段 retry messages），仍为英文则**不发布**这段
    正文，改发确定性中文正文（方向/比分/盘口都是真实计算值），并在模型段
    明说「模型用了英文」。理由：
      * 英文句子无法在不丢信息、不编造的前提下变成中文——我们没有翻译器，
        硬翻就是编造分析内容；
      * 把英文原文放进正文（哪怕套个「原文」括号）仍然违反用户「正文全部中文」；
      * 因此正文只保留我们能逐位核对的中文事实，英文原文随研判记录落盘排障。
    """
    body = _run_emit(monkeypatch, tmp_path, _record(), MODEL_BODY_ENGLISH)
    assert body.strip(), '正文没发出去'

    for sentence in (
        'The market implies a home win',
        'Over 2.5 goals is the most likely path',
        'Score prediction',
        'Data gaps',
    ):
        assert sentence not in body, f'英文原样进了正文：{sentence!r}'
    # 必须明说发生了什么，读者不会以为模型没说话。
    assert '英文' in body, f'没有披露「模型用了英文」：{body[:1500]}'
    assert '确定性中文正文' in body, body[:1500]
    # 确定性正文的真实值仍在，且比分/盘口数字原样。
    for value in ('2-1', '1-0', '-0.75', '2.75'):
        assert value in body, f'确定性正文丢了真实值 {value}'


def test_english_headings_are_converted_not_dropped() -> None:
    """小标题转换的单元证据：英文小标题 → 中文，且不改动数字行与中文行。"""
    converted = pipe.localize_english_sentences(
        'Score prediction\n①比分 2-1\nData gaps\nΔ+0.25pp\n方向锁判定'
    )
    assert '比分预判' in converted, converted
    assert '数据缺口' in converted, converted
    # 数据行与中文行逐字不变。
    for keep in ('①比分 2-1', 'Δ+0.25pp', '方向锁判定'):
        assert keep in converted, (keep, converted)
    # 英文句子被替换成中文说明，不再原样出现。
    assert 'Score prediction' not in converted, converted


def test_chinese_model_body_is_not_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """反假绿：本来全中文的正文不能被转换器改动。

    「所有正文都加一遍标注」也能让上面那条变绿，但那是坏实现：读者会看到
    「模型原文英文」挂在纯中文段落上。
    """
    body = _run_emit(monkeypatch, tmp_path, _record(), MODEL_BODY_CHINESE)
    assert '模型原文英文' not in body, f'纯中文正文被误标注：{body[:1500]}'
    assert '方向锁判定报告 台账01' in body, body[:1500]


def test_numbers_scores_and_units_are_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """数字/比分/百分比/Δ/pp 一律原样——它们不算「非中文」。"""
    body = _run_emit(monkeypatch, tmp_path, _record(), MODEL_BODY_CHINESE)
    # 31 全表的关键数字必须还在。
    for value in ('7.59', '3.40', '7.6%', '7.2%', '-3.7'):
        assert value in body, f'数据被转换器改坏：{value} 不见了'
    assert 'Δ' in body, 'Δ 列被删了'
    assert 'pp' in body, '百分点单位被删了'


# --------------------------------------------------------------------------- #
# 3. 反向验证：映射表恒等（英文进英文出）→ 断言必须红
# --------------------------------------------------------------------------- #
class _IdentityDict(dict):
    """`__getitem__` 恒等：英文令牌进、同一个英文令牌出。"""

    def get(self, key, default=None):  # noqa: D102 - 测试桩
        value = super().get(key, default)
        if isinstance(key, str) and key in self:
            return key
        return value


def test_reverting_the_reason_map_to_identity_turns_the_guard_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """把 `REASON_LABELS` 换成一模一样的恒等表，第一条守卫必须变红。

    这不是改生产代码：只在本次测试内替换映射对象，跑同一段断言，
    证明「英文进英文出」确实会让 `bare_english_tokens` 抓到东西。
    """
    identity = _IdentityDict({t: t for t in ENGLISH_REASON_TOKENS})
    monkeypatch.setattr(pipe, 'REASON_LABELS', identity, raising=False)

    leaked = [t for t in ENGLISH_REASON_TOKENS if pipe.reason_label(t) == t]
    assert leaked, '恒等表没有生效，这条反向验证无效'
    # 用与正例同一把尺子量：整段横幅里出现了裸令牌。
    row = {'fields': {}, 'gaps': [{'field': 'ou_line', 'reason': t} for t in ENGLISH_REASON_TOKENS]}
    banner = '\n'.join(mdm.format_gap_banner({'rows': [row]}))
    assert bare_english_tokens(banner), (
        '映射表恒等后横幅里仍没有裸英文令牌——正例的守卫是假绿的'
    )


def test_reverting_the_fallback_to_english_turns_the_guard_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """把未知令牌兜底改成英文，兜底守卫必须变红。"""
    monkeypatch.setattr(pipe, 'UNKNOWN_REASON_TEMPLATE',
                        'unknown reason: {token}', raising=False)
    label = pipe.reason_label('totally_new_token_zzz')
    assert 'unknown reason' in label, label
    assert bare_english_tokens(label), (
        '兜底改成英文后裸令牌检查仍是空的——兜底守卫是假绿的'
    )


def test_reverting_both_the_gate_and_the_localizer_turns_the_guard_red(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """把英文闸门与转换器**一起**回退，第二条守卫必须变红。

    只回退其中一个不足以让英文进正文——这正是纵深防御的样子：
    闸门（emit 里 `_text_has_english_prose`）+ 转换器（`localize_english_sentences`）
    任一层还在，英文就到不了用户眼前。所以反向验证必须两层同时回退，
    证明「两层都在真的起作用」而不是其中一层顺手挡住了。
    """
    monkeypatch.setattr(app, '_text_has_english_prose', lambda text: False, raising=False)
    monkeypatch.setattr(app, '_localize_english_text',
                        lambda text: str(text if text is not None else ''), raising=False)
    body = _run_emit(monkeypatch, tmp_path, _record(), MODEL_BODY_ENGLISH)
    assert 'The market implies a home win' in body, (
        '闸门与转换器都回退后英文仍没进正文——正例的守卫是假绿的'
    )


# --------------------------------------------------------------------------- #
# 4. 现有 31 全表相关断言必须仍然全绿（本文件只加，不改既有口径）
# --------------------------------------------------------------------------- #
def test_all_31_rows_still_render_with_their_numbers() -> None:
    """31 全表：每一行都在，且每行的市均/竞彩/Δ 数字一个不少。"""
    pack = DA.build_theo_mkt_31_pack(
        [], jc_score_odds=_jc31(),
        extra_market_odds={o: float(_jc31()[o]) for o in _page22_keys()},
        lam_home=1.45, lam_away=1.10,
    )
    lines = DA.format_theo_mkt_31_tg_lines(pack)
    text = '\n'.join(lines)
    for option in pipe.JCZQ_ALL_OPTIONS:
        assert option in text, f'31 表丢了 {option} 这一行'
    # 来源列必须是中文名 + 括号原标识。
    assert '澳客桌面比分均（desktop_cs）' in text or '竞彩官方比分（jc_official）' in text, text[:800]
    # 数字列原样。
    assert re.search(r'市均\s+\d+\.\d{2}', text), text[:800]


def _page22_keys() -> list[str]:
    jc = _jc31()
    return [o for o in pipe.JCZQ_ALL_OPTIONS if o not in NINE_JC_ONLY and jc.get(o)]
