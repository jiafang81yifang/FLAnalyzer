'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const parser = require('../app/utils/parser');
const converter = require('../app/utils/converter');
const normalizer = require('../app/utils/normalizer');
const time = require('../app/utils/time');

test('parser keeps valid paths and degrades malformed scraped values', () => {
  assert.equal(parser.int('12'), 12);
  assert.equal(parser.handicap(null), 0);
  assert.equal(parser.number(null), 0);
  assert.equal(parser.number(undefined), null);
  assert.deepEqual(
    Array.from(parser.score('全场 (2:1) 3:2 半场')),
    ['(2:1) 3:2', '2', '1', '3', '2'],
  );
  assert.equal(parser.score('bad score'), null);
  assert.equal(parser.price(null), 0);
  assert.equal(parser.price(123), 0);
  assert.equal(parser.price('球队身价 €123万'), 123);
  assert.equal(parser.date(null), 0);
  assert.equal(parser.trtd(null, 0, 0), '');
  assert.equal(parser.jsonfix('supportData({"a":1}});'), '{"a":1}}');
});

test('parser extracts team and league ids from cheerio-like nodes safely', () => {
  const node = (href) => ({
    find: () => ({ attr: () => href }),
  });
  assert.equal(parser.tid(node('https://liansai.500.com/team/123/')), 123);
  assert.equal(parser.sid(node('https://liansai.500.com/zuqiu-456/')), 456);
  assert.equal(parser.tid(node(undefined)), 0);
  assert.equal(parser.sid(node('not-a-url')), 0);
});

test('converter protects against missing and zero denominators', () => {
  assert.equal(converter.claimRatio(), 0);
  assert.equal(converter.claimRatio([0, 0, 0]), 0);
  assert.deepEqual(converter.probability(), [0, 0, 0]);
  assert.deepEqual(converter.probability([0, 0, 0]), [0, 0, 0]);
  assert.equal(converter.claimRatio([2, 3, 4]), 24 / 26);
  assert.deepEqual(converter.probability([2, 3, 4]), [12 / 26, 8 / 26, 6 / 26]);
});

test('normalizer degrades bad inputs without changing valid shapes', () => {
  assert.deepEqual(normalizer.score(null), []);
  assert.deepEqual(normalizer.score({ full: { home: 2, away: 1 } }), [0.5, 0.25]);
  assert.deepEqual(normalizer.profit(undefined), [0, 0, 0]);
  assert.deepEqual(normalizer.percent(undefined), [0, 0, 0]);
  assert.deepEqual(normalizer.percent([0, 0, 0]), [0, 0, 0]);
  assert.deepEqual(normalizer.percent([1, 1, 2]), [0.25, 0.25, 0.5]);
  assert.deepEqual(normalizer.diff(undefined, undefined), []);
  assert.deepEqual(normalizer.diff([1, 2], [2, 4]), [1, 1]);
});

test('time utilities handle missing dates and preserve valid formatting', () => {
  const d = new Date('2026-01-02T03:04:05Z');
  assert.equal(time.dateToString(), '');
  assert.equal(time.timeToString(), '');
  assert.equal(time.dateToString(d), '2026-01-02');
  assert.match(time.timeToString(d), /^\d{2}:\d{2}$/);
  assert.equal(time.tomorrow('2026-01-02', 2), '2026-01-04');
  assert.equal(time.yesterday('2026-01-02', 2), '2025-12-31');
  assert.equal(time.compare(), 0);
});
