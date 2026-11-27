"""Headless replay of the 2026-11-25 Windows run log.

Reproduces the exact shipped-build conditions for all eight in-sale matches with
NO network for okooo: every okooo/mobile/scrape entry point is stubbed to fail the
same way the shipped build did (`external_okooo_mobile_cs_fetch_failed`), and the
knowledge_base is deliberately absent so the degrade path is exercised too.

The point is the LOG CONTRACT, not the model output. This script fails (exit 1)
if any of these regress:

  * `系统已挂起` appears at all                                  (must be 0)
  * `[智能技能匹配引擎] 异常(已降级跳过): ` with empty message      (must be 0)
  * `has no attribute` / `AttributeError` on the judgment path     (must be 0)
  * `reason=None` / `ready=None` / `mobile_cs_entered=None`        (must be 0)
  * `[水位] mid=` lines per match per pass                        (must be <= 1)
  * `重复抓取后仍无`                                              (must be 0)
  * `[水位重抓]` lines per match                                  (must be <= 1)
  * `RankTrackingMatrix.run失败: 未知`                            (must be 0)
  * DTI low-value warning implying market anomaly without cause    (must be 0)

Usage:
    python scripts/replay_live_log.py [--show]

`--show` prints the whole captured log. Without it only the summary is printed.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Eight in-sale matches from the log, in kickoff order.
MATCHES = [
    ('1347776', '竞彩-周四002', '萨格勒布迪纳摩 vs 勒沃库森'),
    ('1347778', '竞彩-周四003', '奥林匹亚科斯 vs AC米兰'),
    ('1347775', '竞彩-周四004', '布拉格斯拉维亚 vs 费伦茨瓦罗斯'),
    ('1347780', '竞彩-周四005', '里尔 vs 塞维利亚'),
    ('1347785', '竞彩-周四006', '法兰克福 vs 罗马'),
    ('1347783', '竞彩-周四007', '皇家社会 vs 阿贾克斯'),
    ('1347784', '竞彩-周四008', '本菲卡 vs 波尔图'),
    ('1347786', '竞彩-周四009', '凯尔特人 vs 加拉塔萨雷'),
]

BANNED = [
    (r'系统已挂起', 'knowledge_base 挂起块'),
    (r'异常\(已降级跳过\):\s*$', '空异常信息'),
    (r'has no attribute', 'AttributeError 泄漏'),
    (r'reason=None', 'reason=None'),
    (r'ready=None', 'ready=None'),
    (r'mobile_cs_entered=None', 'mobile_cs_entered=None'),
    (r'重复抓取后仍无', '水位重复抓取'),
    (r'RankTrackingMatrix\.run失败: 未知', '视角7 空原因'),
    (r'数据可能异常', 'DTI 暗示市场异动'),
]


def _install_no_network(okooo: Any, app: Any) -> dict[str, int]:
    """Make every external fetch fail the way the shipped build's did."""
    calls: dict[str, int] = {'cs': 0, 'six': 0, 'detail': 0, 'betfair': 0}

    reason = (
        'url=https://www.okooo.com/soccer/match/{mid}/cs/ → HTTP 405, '
        'title=405, blocked; stage=fetch_failed'
    )

    def _cs(match_id, proxies=None, session=None, **kwargs):
        calls['cs'] += 1
        return {
            'available': False,
            'channel': 'mobile',
            'reason': 'external_okooo_mobile_cs_fetch_failed',
            'block_reason': 'external_okooo_mobile_cs_fetch_failed',
            'error': reason.format(mid=match_id),
            'match_id': str(match_id or ''),
        }

    def _six(match_id, proxies=None, session=None, **kwargs):
        calls['six'] += 1
        mid = str(match_id or '')
        return {
            'available': False,
            'channel': 'mobile',
            'match_id': mid,
            'ou_line': None,
            'asian_handicap': None,
            'odds_1x2': {},
            'boards_hit': {},
            'missing': ['asian_handicap', 'ou_line', 'odds_1x2'],
            'block_reason': 'external_okooo_six_market_fetch_failed',
            'source_report': {
                'ah': {'url': f'https://www.okooo.com/soccer/match/{mid}/ah/',
                       'status': 405, 'bytes': 4681, 'title': '405',
                       'blocked': True, 'error': 'HTTP 405', 'stage': 'fetch_failed'},
                'ou': {'url': f'https://www.okooo.com/soccer/match/{mid}/overunder/',
                       'status': 405, 'bytes': 2657, 'title': '405',
                       'blocked': True, 'error': 'HTTP 405', 'stage': 'fetch_failed'},
                '1x2': {'url': f'https://www.okooo.com/soccer/match/{mid}/odds/',
                        'status': 405, 'bytes': 2657, 'title': '405',
                        'blocked': True, 'error': 'HTTP 405', 'stage': 'fetch_failed'},
            },
            'fetch_urls': [],
        }

    def _empty(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def _betfair(match_info):
        calls['betfair'] += 1
        return {
            'available': False,
            'source': '',
            'home_profit_loss': None,
            'draw_profit_loss': None,
            'away_profit_loss': None,
            'analysis_hint': '必发盈亏数据暂不可用',
        }

    okooo.scrape_okooo_mobile_cs_zuizhi = _cs  # type: ignore[assignment]
    okooo.scrape_okooo_mobile_six_markets = _six  # type: ignore[assignment]
    app.scrape_okooo_mobile_cs_zuizhi = _cs  # type: ignore[assignment]
    app.scrape_okooo_mobile_six_markets = _six  # type: ignore[assignment]
    app.scrape_betfair_profit_loss = _betfair  # type: ignore[assignment]
    # 盈亏/亚大书两个挂接器现在会真的联网；重放必须保持离线，全部替换掉。
    app._attach_mobile_exchange_pnl = lambda rec, mid, proxies=None: {
        'available': False, 'channel': 'mobile', 'reason': 'replay_offline',
        'block_reason': 'replay_offline', 'betfair_available': False,
        'betfair_note': '重放离线：未请求交易所页',
    }
    app._attach_mobile_ah_ou_books = lambda rec, mid, proxies=None: {
        'available': False, 'reason': 'replay_offline',
    }
    app._attach_mobile_lineup = lambda rec, mid, proxies=None: {
        'available': False, 'reason': 'replay_offline',
    }
    for name in (
        'enrich_okooo_cs_facts', 'attach_okooo_ids_to_matches', 'scrape_okooo_cs_facts',
        'fetch_okooo_mobile_schedule', 'fetch_okooo_jingcai_list',
    ):
        if hasattr(app, name):
            setattr(app, name, _empty)
    return calls


def _payload_for(mid: str, no: str, name: str) -> dict[str, Any]:
    """A record shaped like the shipped history entry for a not-yet-played match."""
    return {
        'id': mid,
        'fixture_id': mid,
        'no': no,
        'cn_name': f'[{no}] {name}',
        'match': name,
        'home': name.split(' vs ')[0],
        'away': name.split(' vs ')[1],
        'time': '2026-11-27 01:45',
        'match_time': '2026-11-27 01:45',
        'fixture_date': '2026-11-27T01:45:00',
        'kickoff_source': 'okooo_match_time',
        'league': '竞彩赛事',
        'okooo_match_id': mid,
        'status': 'pending',
        'pipeline_scrape_done': True,
        # 市均/六盘口：与日志一致——全部抓不到。
        'correct_score_available': False,
        'correct_score_channel': '',
        'cs_block_reason': 'external_okooo_mobile_cs_fetch_failed',
        'pool31_forbidden': True,
        'six_market_mobile': {'available': False, 'channel': 'mobile',
                              'block_reason': 'external_okooo_six_market_fetch_failed'},
        'water_gaps': ['asian_handicap', 'ou_line', 'odds_1x2'],
    }


def _stage2(payload: dict[str, Any]) -> dict[str, Any]:
    """Drive the one call that used to raise AttributeError, exactly as 阶段2 does."""
    analyzer = app._ensure_deepseek_analyzer()
    if analyzer is None:
        return {'stage': 'analyzer_missing', 'ok': False}
    pack = payload.get('theo_mkt_31_pack') if isinstance(payload.get('theo_mkt_31_pack'), dict) else {}
    audit = analyzer.audit_theo_mkt_31_rows(pack.get('rows'))
    judgment = analyzer.analyze_match_judgment(payload, timeout=5)
    dual = pack.get('first') if False else None  # keep the shape explicit
    return {'stage': 'ok', 'ok': True, 'audit': audit, 'judgment_available': bool(judgment.get('available')),
            'judgment_error': judgment.get('error') or '', 'dual': dual}


def run() -> tuple[int, str, dict[str, Any]]:
    workdir = Path(tempfile.mkdtemp(prefix='blade-replay-'))
    os.environ['BLADE_HEADLESS'] = '1'
    os.environ['OKOOO_ROUTE'] = 'direct'
    os.environ['MODELS_DIR'] = str(workdir / 'models')
    os.environ['OKOOO_CACHE_DIR'] = str(workdir / 'okooo_cache')
    os.environ.pop('DEEPSEEK_API_KEY', None)
    os.environ.pop('DEEPSEEK_KEY', None)
    # knowledge_base 缺失：走真实降级路径，而不是仓库里已有的那份。
    os.environ['BLADE_KB_DIR'] = str(workdir / 'absent_kb')

    buffer = io.StringIO()
    stats: dict[str, Any] = {}
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        global app
        app = importlib.import_module('app_v84_hermes')
        okooo = importlib.import_module('okooo_mobile_cs')
        app.SkillMatchingEngine._loaded = False
        app.SkillMatchingEngine._seeded = False
        app.SkillMatchingEngine._stat_skills = None
        app.SkillMatchingEngine._strat_skills = None
        app.SkillMatchingEngine._kb_base_dirs = classmethod(lambda cls: [str(workdir)])
        calls = _install_no_network(okooo, app)
        stats['calls'] = calls

        water_lines: list[str] = []
        retry_lines: list[str] = []
        stage2_results: list[dict[str, Any]] = []
        for mid, no, name in MATCHES:
            rec = _payload_for(mid, no, name)
            # 1) 抓取阶段：真实 scrape_okooo_detail（内含本场唯一一次整场重抓）
            pack = app.scrape_okooo_detail(mid) or {}
            app._merge_okooo_detail_into_rec(rec, pack)
            # 2) 下游模块会再问两遍水位。真实运行里它们不该再抓、不该再打一行。
            app._ensure_mobile_six(rec, mid)
            app._ensure_mobile_six(rec, mid)
            app._ensure_mobile_six(rec, mid, force=True)
            app._ensure_mobile_six(rec, mid)
            # 3) 双源闸门
            from odds_fallback import ensure_dual_source_before_judgment
            gate = ensure_dual_source_before_judgment(rec)
            stats.setdefault('gates', []).append(gate)
            # 4) 技能匹配（知识库缺失路径）
            skills = app.SkillMatchingEngine().match_skills({'pool15_enriched': []})
            stats.setdefault('skills', []).append(
                {'available': skills.get('available'), 'error': skills.get('error')}
            )
            # 5) 阶段2 研判
            bundle = {'ds_payload': {
                'home': rec['home'], 'away': rec['away'], 'match': rec['match'],
                'kickoff': rec['time'],
                'theo_mkt_31_pack': {'rows': []},
                'pool15_enriched': [],
                'betfair_data': {},
                'water_gaps': list(rec.get('water_gaps') or []),
            }}
            stage2_results.append(_stage2(bundle['ds_payload']))
            # 6) 31池图谱 + 视角7 + DTI 文案
            from app_v84_hermes import generate_31pool_theory_vs_market_chart
            chart = generate_31pool_theory_vs_market_chart(
                {'lam_h': None, 'lam_a': None, 'prob_dict': None, 'odds_correct_score': {}}
            )
            stats.setdefault('charts', []).append(chart.splitlines()[:2])
            stats['rtm'] = app.RankTrackingMatrix.run([])
            dti = app.calc_dti(None, 0, lam_home=None, lam_away=None)
            stats.setdefault('dti', []).append(dti)
        stats['stage2'] = stage2_results

    log = buffer.getvalue()
    # 把水位/重抓行单独抽出来计数，便于逐场核对
    for line in log.splitlines():
        if '[水位] mid=' in line or '[水位] 重复抓取后仍无' in line:
            water_lines.append(line)
        if '[水位重抓]' in line:
            retry_lines.append(line)
    stats['water_lines'] = water_lines
    stats['retry_lines'] = retry_lines

    failures: list[str] = []
    for pattern, label in BANNED:
        hit = re.search(pattern, log, re.MULTILINE)
        if hit:
            failures.append(f'{label}: 命中 {hit.group(0)!r}')
    per_match = {}
    for mid, _no, _name in MATCHES:
        per_match[mid] = sum(1 for line in water_lines if f'mid={mid}' in line)
    stats['water_per_match'] = per_match
    for mid, count in per_match.items():
        if count > 1:
            failures.append(f'水位结论重复: mid={mid} 出现 {count} 次')
    retry_per_match = {mid: sum(1 for line in retry_lines if f'mid={mid}' in line) for mid, _n, _nm in MATCHES}
    stats['retry_per_match'] = retry_per_match
    for mid, count in retry_per_match.items():
        if count > 1:
            failures.append(f'水位重抓重复: mid={mid} 出现 {count} 次')
    if len(retry_lines) > len(MATCHES):
        failures.append(f'水位重抓总次数 {len(retry_lines)} > 场次数 {len(MATCHES)}')
    for result in stage2_results:
        if not result.get('ok'):
            failures.append(f'阶段2 未走通: {result}')
        # 这个回放刻意不配 DEEPSEEK_API_KEY（见 run() 里的 environ.pop）。
        # 自 (e) 起，没有 key 时 deepseek 只返回本地占位，**不算**研判：
        # available 必须为 False、error 必须点名原因。这正是在验证修复本身，
        # 所以「有 error」是对的；错的只有「声称可用却给了占位」。
        if result.get('judgment_available'):
            failures.append(f'阶段2 无 key 却报 available=True: {result}')
        elif result.get('judgment_error') != 'deepseek_api_key_missing':
            failures.append(f'阶段2 研判 error={result.get("judgment_error")!r}（应为 deepseek_api_key_missing）')
    stats['failures'] = failures
    return (0 if not failures else 1), log, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--show', action='store_true', help='print the captured log')
    args = parser.parse_args()
    code, log, stats = run()
    if args.show:
        print(log)
    print('=' * 72)
    print(f'REPLAY {"PASS" if code == 0 else "FAIL"} | matches={len(MATCHES)}')
    print(f'  水位结论行: {len(stats["water_lines"])} (每场 {stats["water_per_match"]})')
    print(f'  水位重抓行: {len(stats["retry_lines"])} (每场 {stats["retry_per_match"]})')
    print(f'  抓取调用: {stats["calls"]}')
    print(f'  阶段2: {[r.get("stage") for r in stats["stage2"]]}')
    print(f'  技能库: {stats["skills"][0]}')
    print(f'  31池图谱首行: {stats["charts"][0]}')
    print(f'  RTM ok={stats["rtm"].get("ok")} error={stats["rtm"].get("error")!r}')
    print(f'  DTI 样本: {stats["dti"][:2]}')
    if stats['failures']:
        for failure in stats['failures']:
            print(f'  FAIL {failure}')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
