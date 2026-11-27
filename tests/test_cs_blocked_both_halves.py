"""The two halves of the okooo CS blocked-page handling must both survive.

PR #15 added the explicit `cs_origin_ip_blocked` unavailable reason for the case
where the origin returns a WAF 405 to the machine's egress IP. PR #16 added the
竞彩31 side channel: even when 市均 is unavailable, the 31 official score odds are
a different real source and must still be fetched and written.

Merging them is the one place where the two pull requests genuinely disagree:
#16's version of the blocked path reported `external_okooo_mobile_cs_fetch_failed`
with the side channel, and #15's reported `cs_origin_ip_blocked` without it. Taking
either side alone loses something a user notices:

  * drop the blocked reason  -> the log no longer distinguishes "WAF blocked my IP"
    from "the page structure changed", which is the whole point of #15;
  * drop the side channel    -> the nine 市均-uncoverable options are never filled,
    so "重新抓全" loops forever, which is the whole point of #16.

These tests drive the real scrape function through the blocked branch and assert
both facts at once. Run with the embedded copy regenerated from this file.
"""
from __future__ import annotations

import pytest

import okooo_mobile_cs as cs

JC31 = {
    '1:0': 6.5, '2:0': 9.0, '2:1': 8.5, '3:0': 14.0, '3:1': 13.0, '3:2': 26.0,
    '4:0': 40.0, '4:1': 34.0, '4:2': 60.0, '4:3': 90.0,
    '0:0': 9.5, '1:1': 6.0, '2:2': 14.0, '3:3': 40.0, '4:4': 120.0,
    '0:1': 8.0, '0:2': 13.0, '1:2': 9.5, '0:3': 26.0, '1:3': 21.0, '2:3': 34.0,
    '0:4': 70.0, '1:4': 55.0, '2:4': 90.0, '3:4': 150.0,
    # the nine options 市均 never covers
    '胜其它': 30.0, '平其它': 60.0, '负其它': 45.0,
    '5:0': 120.0, '5:1': 150.0, '5:2': 200.0,
}


def _blocked_fetch(url, proxies=None, session=None, retries=1, timeout=15):
    """Every URL answers like the Aliyun WAF: 405 with a block message."""
    return {
        'ok': False, 'blocked': True, 'origin_blocked': True, 'status': 405,
        'error': 'HTTP 405', 'title': 'blocked', 'html': '',
        'x_cache_status': 'MISS', 'age': '', 'traceid': '', 'cache_hit': False,
    }


@pytest.fixture()
def blocked_with_jc31(monkeypatch, tmp_path):
    """市均 blocked at the origin, 竞彩31 available from its own source."""
    monkeypatch.setenv('OKOOO_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(cs, '_fetch_detail', _blocked_fetch)
    monkeypatch.setattr(
        cs, 'fetch_jczq31',
        lambda match_id, proxies=None, session=None: {
            'available': True, 'odds': dict(JC31), 'orderid': '5006',
            'raw_count': len(JC31), 'stage': 'ok',
            'report': {'status': 200, 'bytes': 213729, 'blocked': False, 'cached': False},
        },
    )


def test_blocked_page_names_the_ip_block_and_still_fills_jc31(blocked_with_jc31):
    """Both halves at once: the explicit blocked reason AND the 31 backfill."""
    out = cs.scrape_okooo_mobile_cs_zuizhi('1347776', force_refresh=True)

    # Half 1 (#15): the reason must name the origin-IP block, not a generic failure.
    assert out['available'] is False, out
    assert str(out['reason']).startswith('cs_origin_ip_blocked'), out['reason']
    assert out['origin_blocked'] is True, out
    assert out['parse_stage'] == 'origin_blocked', out

    # Half 2 (#16): the 31 odds must still be there, from their own source.
    assert out.get('jc_score_odds'), f'竞彩31 not filled on the blocked path: {out.keys()}'
    assert len(out['jc_score_odds']) == len(JC31), out.get('jc_score_odds')
    assert out.get('correct_score_jc_official'), out.keys()
    assert out.get('jc_score_source'), out.keys()


def test_blocked_page_still_reports_jc31_as_its_own_source(blocked_with_jc31):
    """市均 stays unavailable and is never faked from the 31 odds."""
    out = cs.scrape_okooo_mobile_cs_zuizhi('1347776', force_refresh=True)

    # 市均 genuinely missing: no fabricated ranking/heat from the 31 source.
    assert out['ranking'] == [], out['ranking']
    assert out['top_scores'] == [], out['top_scores']
    assert out['source'] == '', out['source']
    # and the 31 odds carry their own provenance.
    assert out['jc_score_source'] == '手机竞足→更多玩法→胜平负比分'
