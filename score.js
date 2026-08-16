var mongoose = require('mongoose');
var program  = require('commander');

var dbconfig = require('./app/configs/database');
var Match    = require('./app/models/match');
var time     = require('./app/utils/time');
var analyzer = require('./app/analyzer/score');

var parseIntOption = function (value, fallback){
  var parsed = parseInt(value, 10);
  if(isNaN(parsed) || parsed <= 0){
    return fallback;
  }
  return parsed;
};

program
  .version('0.5.0')
  .option('-m, --mid [value]', '按比赛唯一编号[value]分析')
  .option('-d, --date [value]', '按竞彩日期[value]筛选，默认今天')
  .option('-g, --game [value]', '按联赛[value]筛选')
  .option('-t, --team [value]', '按球队[value]筛选')
  .option('-H, --home [value]', '按主场球队[value]筛选')
  .option('-a, --away [value]', '按客场球队[value]筛选')
  .option('-l, --limit [value]', '最多分析[value]场比赛，默认20场')
  .option('-r, --recent [value]', '每队参考近期[value]场比赛，默认10场')
  .option('--top [value]', '输出[value]个比分候选，默认5个')
  .option('--all', '不使用默认日期，按其他条件查询全部比赛')
  .parse(process.argv);

var limit = parseIntOption(program.limit, 20);
var options = {
  recent: parseIntOption(program.recent, 10),
  top: parseIntOption(program.top, 5)
};

var close = function (code){
  mongoose.connection.close(function (){
    process.exit(code);
  });
};

var buildQuery = function (){
  var query = { simple: false };

  if(!program.all){
    query.date = program.date || time.today();
  }else if(program.date){
    query.date = program.date;
  }

  ['game','home','away'].forEach(function (type){
    if(program[type]){
      if(type === 'home' || type === 'away'){
        query[type + '.name'] = program[type];
      }else{
        query[type + '.name'] = program[type];
      }
    }
  });

  if(program.team){
    query.$or = [
      { 'home.name': program.team },
      { 'away.name': program.team }
    ];
  }

  return query;
};

var runAnalyses = function (matches){
  var index = 0;
  var next = function (){
    if(index >= matches.length){
      close(0);
      return;
    }

    analyzer.analyzeMatch(matches[index], options, function (err, analysis){
      if(err){
        console.error('比分分析失败：' + err.message);
        close(1);
        return;
      }
      console.log(analyzer.formatAnalysis(analysis));
      console.log('');
      index++;
      next();
    });
  };
  next();
};

mongoose.connect(dbconfig.url);
mongoose.connection.on('error', function (err){
  console.error('数据库连接失败：' + err.message);
  close(1);
});

mongoose.connection.once('open', function (){
  if(program.mid){
    Match.getById(parseInt(program.mid, 10), function (err, match){
      if(err){
        console.error('数据库读取失败：' + err.message);
        close(1);
        return;
      }
      if(!match){
        console.log('未找到指定比赛。');
        close(0);
        return;
      }
      runAnalyses([match]);
    });
    return;
  }

  Match.getByQuery(buildQuery(), limit, { time: 1 }, function (err, matches){
    if(err){
      console.error('数据库读取失败：' + err.message);
      close(1);
      return;
    }
    if(!matches || matches.length === 0){
      console.log('未找到可分析的比赛数据。');
      close(0);
      return;
    }
    runAnalyses(matches);
  });
});
