var converter = require('../utils/converter');
var time      = require('../utils/time');
var Match     = require('../models/match');

var DEFAULTS = {
  recent: 10,
  top: 5,
  maxGoal: 6
};

var RESULT_LABELS = {
  home: '主胜',
  draw: '平局',
  away: '客胜'
};

var STRATEGIES = [
  {
    key: 'attack',
    label: '主动进攻',
    goalFor: 1.16,
    goalAgainst: 1.12,
    risk: 0.16
  },
  {
    key: 'balance',
    label: '均衡控制',
    goalFor: 1,
    goalAgainst: 1,
    risk: 0.07
  },
  {
    key: 'defend',
    label: '收缩防守',
    goalFor: 0.86,
    goalAgainst: 0.82,
    risk: 0.03
  }
];

var isNumber = function (value){
  return typeof value === 'number' && isFinite(value);
};

var round = function (value, digits){
  var factor = Math.pow(10, digits === undefined ? 2 : digits);
  return Math.round(value * factor) / factor;
};

var clamp = function (value, min, max){
  return Math.max(min, Math.min(max, value));
};

var percent = function (value){
  if(!isNumber(value)){
    return '-';
  }
  return round(value * 100, 1) + '%';
};

var fullScore = function (match){
  if(match.score && match.score.full && isNumber(match.score.full.home) && isNumber(match.score.full.away)){
    return match.score.full;
  }
  return null;
};

var latestOdds = function (market){
  if(market && market.now){
    return market.now;
  }
  if(market && market.sp && market.sp.length > 0){
    return market.sp[market.sp.length - 1].data;
  }
  return null;
};

var sameTeam = function (left, right){
  if(!left || !right){
    return false;
  }
  if(left.tid !== undefined && left.tid !== null && right.tid !== undefined && right.tid !== null){
    return left.tid === right.tid;
  }
  return left.name && right.name && left.name === right.name;
};

var teamGoals = function (match, team){
  var score = fullScore(match);
  if(!score || !team){
    return null;
  }
  if(sameTeam(match.home, team)){
    return {
      scored: score.home,
      conceded: score.away,
      points: score.home > score.away ? 3 : (score.home === score.away ? 1 : 0)
    };
  }
  if(sameTeam(match.away, team)){
    return {
      scored: score.away,
      conceded: score.home,
      points: score.away > score.home ? 3 : (score.away === score.home ? 1 : 0)
    };
  }
  return null;
};

var summarize = function (matches, team){
  var summary = {
    count: 0,
    goalsFor: 0,
    goalsAgainst: 0,
    wins: 0,
    draws: 0,
    losses: 0,
    formPoints: 0,
    goalsForAvg: null,
    goalsAgainstAvg: null,
    pointsAvg: null
  };

  (matches || []).forEach(function (match, index){
    var goals = teamGoals(match, team);
    var weight = Math.pow(0.92, index);
    if(!goals){
      return;
    }
    summary.count++;
    summary.goalsFor += goals.scored;
    summary.goalsAgainst += goals.conceded;
    summary.formPoints += goals.points * weight;
    if(goals.points === 3){
      summary.wins++;
    }else if(goals.points === 1){
      summary.draws++;
    }else{
      summary.losses++;
    }
  });

  if(summary.count > 0){
    summary.goalsForAvg = summary.goalsFor / summary.count;
    summary.goalsAgainstAvg = summary.goalsAgainst / summary.count;
    summary.pointsAvg = (summary.wins * 3 + summary.draws) / summary.count;
  }
  return summary;
};

var pairExpected = function (attackAvg, defenseAvg, fallback){
  var values = [];
  if(isNumber(attackAvg)){
    values.push(attackAvg);
  }
  if(isNumber(defenseAvg)){
    values.push(defenseAvg);
  }
  if(values.length === 0){
    return fallback;
  }
  return values.reduce(function (sum, value){
    return sum + value;
  }, 0) / values.length;
};

var weightedAverage = function (items, fallback){
  var sum = 0;
  var weight = 0;
  items.forEach(function (item){
    if(isNumber(item.value) && item.weight > 0){
      sum += item.value * item.weight;
      weight += item.weight;
    }
  });
  if(weight === 0){
    return fallback;
  }
  return sum / weight;
};

var marketSignal = function (match){
  var odds = latestOdds(match.jingcai && match.jingcai.spf);
  if(!odds || odds.length < 3 || !odds[0] || !odds[1] || !odds[2]){
    return null;
  }

  var probabilities = converter.probability(odds);
  var diff = clamp((probabilities[0] - probabilities[2]) * 2.2, -2.4, 2.4);
  var total = clamp(2.45 + (Math.abs(probabilities[0] - probabilities[2]) * 0.7) - (probabilities[1] * 0.75), 1.35, 3.8);
  var homeGoals = clamp((total + diff) / 2, 0.15, 4.8);
  var awayGoals = clamp((total - diff) / 2, 0.15, 4.8);
  var rqOdds = latestOdds(match.jingcai && match.jingcai.rqspf);

  return {
    odds: odds,
    probabilities: probabilities,
    homeGoals: homeGoals,
    awayGoals: awayGoals,
    rq: match.jingcai && match.jingcai.rqspf ? match.jingcai.rqspf.rq : undefined,
    rqOdds: rqOdds
  };
};

var factorial = function (n){
  var result = 1;
  for(var i = 2; i <= n; i++){
    result *= i;
  }
  return result;
};

var poisson = function (lambda, k){
  return Math.exp(-lambda) * Math.pow(lambda, k) / factorial(k);
};

var scoreProbabilities = function (homeGoals, awayGoals, maxGoal){
  var home = [];
  var away = [];
  var candidates = [];
  var outcomes = {
    home: 0,
    draw: 0,
    away: 0
  };
  var total = 0;

  for(var h = 0; h <= maxGoal; h++){
    home[h] = poisson(homeGoals, h);
  }
  for(var a = 0; a <= maxGoal; a++){
    away[a] = poisson(awayGoals, a);
  }

  for(var i = 0; i <= maxGoal; i++){
    for(var j = 0; j <= maxGoal; j++){
      var probability = home[i] * away[j];
      total += probability;
      if(i > j){
        outcomes.home += probability;
      }else if(i === j){
        outcomes.draw += probability;
      }else{
        outcomes.away += probability;
      }
      candidates.push({
        score: i + ':' + j,
        home: i,
        away: j,
        probability: probability
      });
    }
  }

  candidates.forEach(function (item){
    item.probability = item.probability / total;
  });
  outcomes.home = outcomes.home / total;
  outcomes.draw = outcomes.draw / total;
  outcomes.away = outcomes.away / total;

  candidates.sort(function (a, b){
    return b.probability - a.probability;
  });

  return {
    candidates: candidates,
    outcomes: outcomes
  };
};

var outcomePick = function (outcomes){
  var pick = 'home';
  ['draw','away'].forEach(function (key){
    if(outcomes[key] > outcomes[pick]){
      pick = key;
    }
  });
  return pick;
};

var adjustedByStrategies = function (expected, homeStrategy, awayStrategy){
  return {
    home: round(clamp(expected.home * homeStrategy.goalFor * awayStrategy.goalAgainst, 0.1, 5.8), 2),
    away: round(clamp(expected.away * awayStrategy.goalFor * homeStrategy.goalAgainst, 0.1, 5.8), 2)
  };
};

var payoff = function (side, strategy, opponentStrategy, goals, probabilities, market){
  var isHome = side === 'home';
  var winProbability = isHome ? probabilities.outcomes.home : probabilities.outcomes.away;
  var loseProbability = isHome ? probabilities.outcomes.away : probabilities.outcomes.home;
  var scored = isHome ? goals.home : goals.away;
  var conceded = isHome ? goals.away : goals.home;
  var marketProbability = 0;

  if(market){
    marketProbability = isHome ? market.probabilities[0] : market.probabilities[2];
  }

  return (winProbability * 3) +
    probabilities.outcomes.draw -
    (loseProbability * 0.6) +
    (scored * 0.16) -
    (conceded * 0.14) -
    (strategy.risk * (conceded + opponentStrategy.goalFor)) +
    (market ? (winProbability - marketProbability) * 0.45 : 0);
};

var selectEquilibrium = function (matrix){
  var equilibria = [];
  var fallback = null;

  matrix.forEach(function (row){
    row.forEach(function (cell){
      var homeBest = matrix.reduce(function (best, candidateRow){
        var candidate = candidateRow[cell.awayIndex];
        return candidate.homePayoff > best ? candidate.homePayoff : best;
      }, -Infinity);
      var awayBest = row.reduce(function (best, candidate){
        return candidate.awayPayoff > best ? candidate.awayPayoff : best;
      }, -Infinity);
      var score = cell.homePayoff + cell.awayPayoff - Math.abs(cell.homePayoff - cell.awayPayoff) * 0.12;

      cell.isNash = cell.homePayoff >= homeBest - 0.0001 && cell.awayPayoff >= awayBest - 0.0001;
      cell.stabilityScore = score;
      if(cell.isNash){
        equilibria.push(cell);
      }
      if(!fallback || score > fallback.stabilityScore){
        fallback = cell;
      }
    });
  });

  if(equilibria.length > 0){
    equilibria.sort(function (a, b){
      return b.stabilityScore - a.stabilityScore;
    });
    equilibria[0].equilibriumType = '纳什均衡';
    return {
      selected: equilibria[0],
      equilibria: equilibria
    };
  }

  fallback.equilibriumType = '最优稳态';
  return {
    selected: fallback,
    equilibria: []
  };
};

var gameTheoryAnalysis = function (expected, market, maxGoal){
  var matrix = [];

  STRATEGIES.forEach(function (homeStrategy, homeIndex){
    var row = [];
    STRATEGIES.forEach(function (awayStrategy, awayIndex){
      var goals = adjustedByStrategies(expected, homeStrategy, awayStrategy);
      var probabilities = scoreProbabilities(goals.home, goals.away, maxGoal);
      var pick = outcomePick(probabilities.outcomes);
      row.push({
        homeIndex: homeIndex,
        awayIndex: awayIndex,
        homeStrategy: homeStrategy,
        awayStrategy: awayStrategy,
        expectedGoals: goals,
        outcomes: probabilities.outcomes,
        pick: {
          key: pick,
          label: RESULT_LABELS[pick],
          probability: probabilities.outcomes[pick]
        },
        homePayoff: payoff('home', homeStrategy, awayStrategy, goals, probabilities, market),
        awayPayoff: payoff('away', awayStrategy, homeStrategy, goals, probabilities, market)
      });
    });
    matrix.push(row);
  });

  var equilibrium = selectEquilibrium(matrix);
  return {
    strategies: STRATEGIES,
    matrix: matrix,
    equilibria: equilibrium.equilibria,
    selected: equilibrium.selected
  };
};

var expectedGoals = function (summaries, market){
  var homeBase = pairExpected(summaries.homeRecent.goalsForAvg, summaries.awayRecent.goalsAgainstAvg, 1.25);
  var awayBase = pairExpected(summaries.awayRecent.goalsForAvg, summaries.homeRecent.goalsAgainstAvg, 1.05);
  var homeVenue = pairExpected(summaries.homeVenue.goalsForAvg, summaries.awayVenue.goalsAgainstAvg, null);
  var awayVenue = pairExpected(summaries.awayVenue.goalsForAvg, summaries.homeVenue.goalsAgainstAvg, null);
  var homeH2h = summaries.homeH2h.count > 0 ? summaries.homeH2h.goalsForAvg : null;
  var awayH2h = summaries.awayH2h.count > 0 ? summaries.awayH2h.goalsForAvg : null;

  var home = weightedAverage([
    { value: homeBase, weight: 0.46 },
    { value: homeVenue, weight: summaries.homeVenue.count >= 3 && summaries.awayVenue.count >= 3 ? 0.22 : 0.08 },
    { value: homeH2h, weight: summaries.homeH2h.count >= 2 ? 0.12 : 0.04 },
    { value: market ? market.homeGoals : null, weight: market ? 0.20 : 0 }
  ], homeBase);

  var away = weightedAverage([
    { value: awayBase, weight: 0.46 },
    { value: awayVenue, weight: summaries.homeVenue.count >= 3 && summaries.awayVenue.count >= 3 ? 0.22 : 0.08 },
    { value: awayH2h, weight: summaries.awayH2h.count >= 2 ? 0.12 : 0.04 },
    { value: market ? market.awayGoals : null, weight: market ? 0.20 : 0 }
  ], awayBase);

  return {
    home: round(clamp(home, 0.15, 5.2), 2),
    away: round(clamp(away, 0.15, 5.2), 2)
  };
};

var confidence = function (summaries, market, recent){
  var recentScore = Math.min(1, (summaries.homeRecent.count + summaries.awayRecent.count) / (recent * 2));
  var venueScore = Math.min(1, (summaries.homeVenue.count + summaries.awayVenue.count) / (recent * 2));
  var h2hScore = Math.min(1, summaries.homeH2h.count / 6);
  var oddsScore = market ? 1 : 0;
  return round((recentScore * 0.48) + (venueScore * 0.18) + (h2hScore * 0.14) + (oddsScore * 0.20), 2);
};

var combineExpectedGoals = function (baseExpected, gameTheory){
  var strategyExpected = gameTheory.selected.expectedGoals;
  return {
    home: round((baseExpected.home * 0.68) + (strategyExpected.home * 0.32), 2),
    away: round((baseExpected.away * 0.68) + (strategyExpected.away * 0.32), 2)
  };
};

var analyzeMatch = function (match, options, callback){
  options = options || {};
  var recent = parseInt(options.recent, 10) || DEFAULTS.recent;
  var maxGoal = parseInt(options.maxGoal, 10) || DEFAULTS.maxGoal;
  var samples = {};
  var tasks = [
    {
      key: 'homeRecent',
      run: function (done){
        Match.getByTeam(match.home, { time: match.time }, recent, done);
      }
    },
    {
      key: 'awayRecent',
      run: function (done){
        Match.getByTeam(match.away, { time: match.time }, recent, done);
      }
    },
    {
      key: 'homeVenue',
      run: function (done){
        Match.getByTeam(match.home, { time: match.time, home: !match.neutral }, recent, done);
      }
    },
    {
      key: 'awayVenue',
      run: function (done){
        Match.getByTeam(match.away, { time: match.time, away: !match.neutral }, recent, done);
      }
    },
    {
      key: 'h2h',
      run: function (done){
        Match.getByTeams([match.home, match.away], { time: match.time }, recent, done);
      }
    }
  ];
  var remaining = tasks.length;
  var failed = false;

  tasks.forEach(function (task){
    task.run(function (err, matches){
      if(failed){
        return;
      }
      if(err){
        failed = true;
        callback(err);
        return;
      }
      samples[task.key] = matches || [];
      remaining--;
      if(remaining === 0){
        var summaries = {
          homeRecent: summarize(samples.homeRecent, match.home),
          awayRecent: summarize(samples.awayRecent, match.away),
          homeVenue: summarize(samples.homeVenue, match.home),
          awayVenue: summarize(samples.awayVenue, match.away),
          homeH2h: summarize(samples.h2h, match.home),
          awayH2h: summarize(samples.h2h, match.away)
        };
        var market = marketSignal(match);
        var baseExpected = expectedGoals(summaries, market);
        var gameTheory = gameTheoryAnalysis(baseExpected, market, maxGoal);
        var expected = combineExpectedGoals(baseExpected, gameTheory);
        var probabilities = scoreProbabilities(expected.home, expected.away, maxGoal);
        var pick = outcomePick(probabilities.outcomes);

        callback(null, {
          match: match,
          generatedAt: new Date(),
          baseExpectedGoals: baseExpected,
          expectedGoals: expected,
          gameTheory: gameTheory,
          summaries: summaries,
          market: market,
          topScores: probabilities.candidates.slice(0, parseInt(options.top, 10) || DEFAULTS.top),
          outcomes: probabilities.outcomes,
          pick: {
            key: pick,
            label: RESULT_LABELS[pick],
            probability: probabilities.outcomes[pick]
          },
          confidence: confidence(summaries, market, recent)
        });
      }
    });
  });
};

var formatSummary = function (summary){
  if(!summary || summary.count === 0){
    return '样本不足';
  }
  return summary.count + '场 ' +
    summary.wins + '胜' + summary.draws + '平' + summary.losses + '负 ' +
    '进' + round(summary.goalsForAvg, 2) + '/失' + round(summary.goalsAgainstAvg, 2);
};

var formatAnalysis = function (analysis){
  var match = analysis.match;
  var lines = [];
  var matchTime = match.time ? time.dateToString(match.time) + ' ' + time.timeToString(match.time) : '-';
  var score = fullScore(match);

  lines.push('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  lines.push('中国竞彩足球比分分析系统');
  lines.push(matchTime + '  ' + (match.game ? match.game.name : '-') + '  ' + (match.shortcut || ''));
  lines.push(match.home.name + (match.neutral ? '(中)' : '') + ' VS ' + match.away.name + (score ? '  已赛比分 ' + score.home + ':' + score.away : ''));
  lines.push('');
  lines.push('预测倾向：' + analysis.pick.label + '（' + percent(analysis.pick.probability) + '）  置信度：' + percent(analysis.confidence));
  lines.push('预期进球：' + match.home.name + ' ' + analysis.expectedGoals.home + '  -  ' + match.away.name + ' ' + analysis.expectedGoals.away);
  lines.push('胜平负概率：主胜 ' + percent(analysis.outcomes.home) + ' / 平局 ' + percent(analysis.outcomes.draw) + ' / 客胜 ' + percent(analysis.outcomes.away));
  lines.push('比分候选：' + analysis.topScores.map(function (item){
    return item.score + ' ' + percent(item.probability);
  }).join('，'));
  lines.push('博弈论判断：' + match.home.name + ' ' + analysis.gameTheory.selected.homeStrategy.label +
    ' / ' + match.away.name + ' ' + analysis.gameTheory.selected.awayStrategy.label +
    '（' + analysis.gameTheory.selected.equilibriumType + '，倾向' + analysis.gameTheory.selected.pick.label + ' ' + percent(analysis.gameTheory.selected.pick.probability) + '）');
  lines.push('博弈预期：' + match.home.name + ' ' + analysis.gameTheory.selected.expectedGoals.home +
    ' - ' + match.away.name + ' ' + analysis.gameTheory.selected.expectedGoals.away +
    '；收益 ' + round(analysis.gameTheory.selected.homePayoff, 2) + ' / ' + round(analysis.gameTheory.selected.awayPayoff, 2));
  lines.push('');
  lines.push('近期状态：');
  lines.push('  ' + match.home.name + '：' + formatSummary(analysis.summaries.homeRecent));
  lines.push('  ' + match.away.name + '：' + formatSummary(analysis.summaries.awayRecent));
  lines.push('主客场：');
  lines.push('  ' + match.home.name + '主场：' + formatSummary(analysis.summaries.homeVenue));
  lines.push('  ' + match.away.name + '客场：' + formatSummary(analysis.summaries.awayVenue));
  lines.push('交锋记录：' + formatSummary(analysis.summaries.homeH2h));

  if(analysis.market){
    lines.push('竞彩信号：');
    lines.push('  胜平负赔率：' + analysis.market.odds.join(' / '));
    lines.push('  赔率概率：主胜 ' + percent(analysis.market.probabilities[0]) + ' / 平局 ' + percent(analysis.market.probabilities[1]) + ' / 客胜 ' + percent(analysis.market.probabilities[2]));
    if(analysis.market.rqOdds){
      lines.push('  让球胜平负：让球 ' + (analysis.market.rq === undefined ? '-' : analysis.market.rq) + '，赔率 ' + analysis.market.rqOdds.join(' / '));
    }
  }

  return lines.join('\n');
};

module.exports = {
  analyzeMatch: analyzeMatch,
  formatAnalysis: formatAnalysis,
  _private: {
    summarize: summarize,
    marketSignal: marketSignal,
    scoreProbabilities: scoreProbabilities,
    expectedGoals: expectedGoals,
    gameTheoryAnalysis: gameTheoryAnalysis
  }
};
