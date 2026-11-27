"""「竞彩」与「市场」两套比分盘口径必须进到真实提示词里。

守卫驱动的是真实调用点（`DeepSeekAnalyzer.analyze_match_judgment` 拼出的 messages），
不是在测试里复制一份提示词再断言自己。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import blade_deepseek_pipeline as bd


def _captured_messages(monkeypatch) -> list[dict[str, str]]:
    """跑一次真实的阶段2 研判入口，截下它真正发给模型的 messages。"""
    captured: list[dict[str, str]] = []

    def fake_completion(messages, **_kwargs):
        captured.extend(messages)
        return 'DeepSeek 数据分析研判报告\n正文 2-1'

    monkeypatch.setattr(bd, 'deepseek_chat_completion', fake_completion)
    bd.DeepSeekAnalyzer.analyze_match_judgment({'home': '主队', 'away': '客队'})
    assert captured, '阶段2 研判入口没有拼出任何 messages'
    return captured


def _joined(messages: list[dict[str, str]]) -> str:
    return '\n'.join(str(m.get('content') or '') for m in messages)


def test_judgment_prompt_carries_score_book_terminology(monkeypatch) -> None:
    """真实研判提示词里必须有口径说明，且说明逐条对上事实。

    反证：把 `analyze_match_judgment` 里那行 `task += '\\n\\n' + SCORE_BOOK_TERMINOLOGY_NOTE`
    删掉 → 本测试第一条断言变红。
    """
    text = _joined(_captured_messages(monkeypatch))
    assert bd.SCORE_BOOK_TERMINOLOGY_NOTE in text or '口径约定' in text

    # 术语定义：竞彩 = 31 项，市场 = 26/25 项
    assert '竞彩' in text and '31' in text
    assert '市场' in text and ('26' in text or '25' in text)
    # 两条核心差距必须都点到
    assert '5 球' in text and '4 球' in text
    # 6 个 5 球比分必须逐字出现，模型不能自己拼一个别的清单
    for score in ('5:0', '5:1', '5:2', '0:5', '1:5', '2:5'):
        assert score in text, f'遗漏 {score}'
    # 「其他」不得当同义词
    assert '不是同义词' in text or '不得直接相减' in text


def test_terminology_note_is_one_shared_constant_not_repeated_inline() -> None:
    """口径说明只有一个常量；正文里不允许再手写第二份（否则改一处漏一处）。

    反证：在 `assemble_prompt` 或 `analyze_match_judgment` 里内联一段
    「竞彩专指中国竞彩足球 31 项比分盘」→ 出现次数 > 1，本断言变红。
    """
    source = (ROOT / 'blade_deepseek_pipeline.py').read_text(encoding='utf-8')
    definition = '竞彩」专指中国竞彩足球 31 项比分盘'
    assert source.count(definition) == 1, (
        f'口径定义在 blade_deepseek_pipeline.py 里出现 {source.count(definition)} 次，'
        '应当只有 SCORE_BOOK_TERMINOLOGY_NOTE 一处'
    )


def test_terminology_note_stays_short() -> None:
    """控制篇幅：这段要塞进每一份研判提示词，不能变成整篇分析框架。

    反证：把整份四维框架（含 KL 公式、回测分组）贴进常量 → 长度断言变红。
    """
    assert len(bd.SCORE_BOOK_TERMINOLOGY_NOTE) <= 700
    # 它是口径说明，不是分析方法论：不该出现分析步骤措辞
    for banned in ('第一步', 'KL 散度', 'Dixon-Coles', '回测'):
        assert banned not in bd.SCORE_BOOK_TERMINOLOGY_NOTE, banned


def test_terminology_note_matches_the_module_tables() -> None:
    """提示词里的清单必须和 `score_books` 的真实表逐项一致。

    反证：在常量里把 5:2 写成 5:3 → 本断言变红（提示词与模块口径脱节）。
    """
    import score_books as sb

    note = bd.SCORE_BOOK_TERMINOLOGY_NOTE
    for score in sb.JC_FIVE_GOAL_CELLS:
        assert score in note, f'提示词漏了 {score}'
    # 竞彩三个「其他」与市场四个桶都要点名
    for key in sb.JC_OTHER_KEYS:
        assert key in note, f'提示词漏了 {key}'
    for key in sb.MARKET_BUCKET_KEYS_26:
        assert key in note, f'提示词漏了 {key}'


def test_note_is_not_injected_into_local_fallback_text(monkeypatch) -> None:
    """口径说明只进提示词，不能漏进降级正文（否则没联网也会「分析」出口径）。"""
    monkeypatch.setattr(bd, 'deepseek_chat_completion', lambda *_a, **_k: {})
    result = bd.DeepSeekAnalyzer.analyze_match_judgment({'home': '主队', 'away': '客队'})
    assert result['available'] is False
    assert bd.SCORE_BOOK_TERMINOLOGY_NOTE not in str(result.get('content') or '')
