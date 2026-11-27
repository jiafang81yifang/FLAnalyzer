"""`collapse_repeated_judgment_blocks` 的守卫：它必须真的合并同名章节。

背景：这个名字下的实现曾经是 `return str(text or '')`。`send_tg` 的门禁写得
完全正确（`parse_mode != 'HTML'` 时调用、结果再跑 `sanitize_bot_output`），
但函数体是空闸，于是「同名章节只留第一次」全仓库一次都没发生过：
模型把同一段写两遍、或标题行被拼两次，都会原样发到 Telegram
（100k sweep 的「标题重复」即此）。

这里**不**直接调静态方法：全部经真实 `send_tg` → stub 的 `_tg_post`，
断言「真正会到 Telegram 的文本」，因为「测 helper 不测调用点」正是
上一轮两次假绿的成因。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')

DA = pipe_mod.DeepSeekAnalyzer


@pytest.fixture()
def deliver(monkeypatch: pytest.MonkeyPatch):
    """真实 send_tg，只把最后一步 HTTP 换成记录器。"""
    sent: list[str] = []
    monkeypatch.setattr(app, 'SNIPER_BOT_TOKEN', '123456789:' + 'A' * 30)
    monkeypatch.setattr(app, 'AUTO_CHAT_ID', '-1001234567890')
    monkeypatch.setattr(app, 'TG_PROXIES', None)
    monkeypatch.setattr(app, '_TG_CHAT_CAPTURE_STATE',
                        {'captured': '-1001234567890', 'printed': ''})
    monkeypatch.setattr(app, '_tg_post',
                        lambda url, payload: sent.append(str(payload.get('text') or '')) or True)
    app.reset_tg_delivery_state()
    yield sent
    app.reset_tg_delivery_state()


def _text_of(sent: list[str]) -> str:
    return '\n'.join(sent)


# --------------------------------------------------------------------------- #
# 1. 同名章节真的合并，且保留第一段正文
# --------------------------------------------------------------------------- #
def test_duplicate_section_collapses_to_the_first_occurrence(deliver: list[str]) -> None:
    body = '\n'.join([
        '📌 [周五002] 海登海姆 vs 纽伦堡',
        '🧭【六步收敛】',
        '  第一步：1X2 定向 → 胜',
        '  第二步：O/U 路径 → 大',
        '🧭【六步收敛】',
        '  第一步：1X2 定向 → 负',
        '  第二步：O/U 路径 → 小',
        '🔎【六盘口事实板】',
        '  亚盘让球：无数据（原因：结构性不可达）',
    ])
    assert app.send_tg(body) is True
    delivered = _text_of(deliver)
    assert delivered.count('🧭【六步收敛】') == 1, delivered
    # 保留的是**第一段**，不是最后一段。
    assert '定向 → 胜' in delivered
    assert '定向 → 负' not in delivered
    # 其它章节不受影响。
    assert '🔎【六盘口事实板】' in delivered
    assert '无数据（原因：结构性不可达）' in delivered
    # 比赛标题行不在章节内，必须原地保留。
    assert '📌 [周五002]' in delivered


# --------------------------------------------------------------------------- #
# 2. 三种标题重复的真实现场
# --------------------------------------------------------------------------- #
def test_duplicated_match_title_line_collapses(deliver: list[str]) -> None:
    """sweep 报的 `📌 [周六001] 📌 [周六001] …` 那种标题复制。"""
    body = (
        '【31项市均×竞彩对比表】\n'
        '  01 1-0 市均 6.50\n'
        '【31项市均×竞彩对比表】\n'
        '  01 1-0 市均 9.99\n'
    )
    assert app.send_tg(body) is True
    delivered = _text_of(deliver)
    assert delivered.count('【31项市均×竞彩对比表】') == 1, delivered
    assert '6.50' in delivered and '9.99' not in delivered


def test_repeated_model_section_is_collapsed(deliver: list[str]) -> None:
    body = '\n'.join([
        '🧠【DeepSeek 数据分析研判报告】',
        '  方向 胜｜①2-1 ②1-0',
        '📈【31项市均×竞彩对比表】',
        '  01 1-0 市均 6.50',
        '🧠【DeepSeek 数据分析研判报告】',
        '  方向 负｜①0-1 ②1-2',
    ])
    assert app.send_tg(body) is True
    delivered = _text_of(deliver)
    assert delivered.count('🧠【DeepSeek 数据分析研判报告】') == 1, delivered
    assert '①2-1' in delivered
    assert '①0-1' not in delivered


# --------------------------------------------------------------------------- #
# 3. 分页重挂表头是设计，绝不能被吃掉
# --------------------------------------------------------------------------- #
def test_pagination_headers_are_not_collapsed(deliver: list[str]) -> None:
    """`paginate_tg_sections` 每页都重挂所属小节头行，那是**设计**。

    如果 collapse 是「裸的同名标题一律合并」，31 表第二、三页就会丢表头，
    读者不知道自己在看哪张表。所以这里锁死跨分页保留。
    """
    page1 = '\n'.join([
        '📊 赛事 M1 三套比分矩阵',
        '【一、31 项比分矩阵】',
        '1-0  胜  市均 6.50',
    ])
    page2 = '\n'.join([
        '📊 赛事 M1 三套比分矩阵',
        '【一、31 项比分矩阵】',
        '2-0  胜  市均 9.10',
    ])
    body = page1 + '\x0c' + page2
    assert app.send_tg(body) is True
    delivered = _text_of(deliver)
    assert delivered.count('【一、31 项比分矩阵】') == 2, delivered
    # 两页的数据行都在，一条都不能丢。
    assert '市均 6.50' in delivered and '市均 9.10' in delivered


def test_real_matrix_pagination_survives_send_tg(deliver: list[str]) -> None:
    """用真实矩阵渲染器生成多页，再走 send_tg：每页表头仍在。"""
    import score_matrices as sm
    avg = {sc: round(6.5 + i * 1.4, 2) for i, sc in enumerate(sm.THIRTY_ONE_SCORES)}
    jc = {
        sc: {'odds': round(avg[sc] + (i % 7) * 0.35 + 0.25, 2), 'heat_seats': (i % 6) + 1}
        for i, sc in enumerate(sm.THIRTY_ONE_SCORES)
    }
    bundle = sm.build_score_matrices('M1', avg, jc)
    pages = sm.format_matrices_telegram(bundle, max_len=700)
    assert len(pages) > 2, '夹具必须真的分页，否则这条测试是假绿'
    body = '\x0c'.join(pages)
    assert app.send_tg(body) is True
    delivered = _text_of(deliver)
    first_header = '【一、31 项比分矩阵】'
    assert delivered.count(first_header) == sum(
        1 for p in pages if first_header in p
    ), delivered
    assert delivered.count(first_header) >= 2


# --------------------------------------------------------------------------- #
# 4. 没有标题的正文原样发出，不做猜测性合并
# --------------------------------------------------------------------------- #
def test_prose_without_headings_is_untouched(deliver: list[str]) -> None:
    body = '这是一段纯散文。\n第二行意思相同的句子。\n第三行还是散文。'
    assert app.send_tg(body) is True
    assert _text_of(deliver).strip() == body.strip()


def test_unnamed_continuation_lines_are_never_dropped(deliver: list[str]) -> None:
    """章节之外的表头/脚注/续行不参与合并，一个字符都不许少。"""
    body = '\n'.join([
        '【一、31 项比分矩阵】',
        '表头 市均 竞彩 Δ',
        '1-0 6.50 7.10 +0.6',
        '脚注: 市均无报价项标 —',
        '【二、15 战术池矩阵】',
        '表头 市均 竞彩 Δ',
        'P01 5.0 6.0 +1.0',
    ])
    assert app.send_tg(body) is True
    delivered = _text_of(deliver)
    assert delivered.count('表头 市均 竞彩 Δ') == 2, delivered
    assert '脚注: 市均无报价项标 —' in delivered


# --------------------------------------------------------------------------- #
# 5. 兄弟函数仍是恒等（另一个工人正在为它们写改名提案）
# --------------------------------------------------------------------------- #
def test_sibling_identity_functions_stay_identity() -> None:
    """`strip_tg_english` / `pin_match_title_first` 等仍是恒等，别顺手改。

    其中一条发送路径是 `parse_mode=None`：让它们注入 HTML 会把标签漏成字面量。
    """
    sample = '🧠【DeepSeek 数据分析研判报告】\n  正文 2-1'
    assert DA.pin_match_title_first(sample) == sample
    assert DA.strip_tg_english(sample) == sample
    assert DA.sanitize_six_market_tg_text(sample) == sample
    for name in ('mark_selected_scores_deep_yellow', 'mark_landing_direction_green',
                 'mark_locked_direction_yellow', 'mark_prob_ou_wdl_red'):
        fn = getattr(DA, name, None)
        assert fn is not None, name
        assert fn(sample) == sample, name
