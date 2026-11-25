var validator   = require('validator');

var score = /\((\d+):(\d+)\) (\d+):(\d+)/;

var fixFloat = function (number){
  return Math.round(number*10000)/10000;
}

var int = exports.int = function (str){
  str = validator.trim(String(str || ''));
  return parseInt(str)||0;
}

exports.number = function (str){
  if(str === undefined){
    return null;
  }
  if(str === null){
    return 0;
  }
  str = validator.trim(String(str || ''));
  str = str.replace(/,/g,'');
  var num;
  //如果包含百分号
  if(str.indexOf('%')!=-1){
    num = parseFloat(str.substring(0,str.indexOf("%")))/100;
  }else{
    num = parseFloat(str);
  }
  if(Number.isNaN(num)){
    return null;
  }
  return fixFloat(num);
}

exports.tid = function (el){
  var href = el.find('a').attr('href');
  var match = href && href.match(/https?:\/\/liansai\.500\.com\/team\/([0-9]+)\/?/);
  if(match){
    return int(match[1]);
  }
  return 0;
}

exports.sid = function (el){
  var href = el.find('a').attr('href');
  var match = href && href.match(/https?:\/\/liansai\.500\.com\/zuqiu-([0-9]+)\/?/);
  if(match){
    return int(match[1]);
  }
  return 0;
}

exports.trtd = function (el,tr,td){
  if(!el || typeof el.find !== 'function'){
    return '';
  }
  return exports.trim(el.find('tr').eq(tr).find('td').eq(td).text());
}

exports.date = function (str){
  str = String(str || '');
  if(!str){
    return 0;
  }
  return Date.parse(str.replace(/-/g,'/'));
}

exports.trim = function (str){
  return validator.trim(String(str || '').replace(/ /g,''));
}

exports.score = function (str){
  return String(str || '').match(score);
}

exports.handicap = function (str){
  return parseInt(String(str || '').substr(1,2)) || 0;
}

exports.price = function (str){
  str = String(str || '');
  //如果有球队身价
  if(str.indexOf('球队身价')!=-1&&str.indexOf('--')==-1&&str.indexOf('€')!==-1){
    var match = str.match(/€\s*([0-9]+)/);
    return match ? parseInt(match[1]) : 0;
  }
  return 0;
}

exports.jsonfix = function (str){
  return String(str || '').replace('supportData(','').replace('}});','}}');
}